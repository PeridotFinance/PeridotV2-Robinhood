"""Unattended Ramses SPY/USDG keeper (DigitalOcean App Platform worker).

Runs `ramses_keeper.py` on a timer with a DEDICATED wallet that holds only the vault's KEEPER_ROLE. The contracts decide
everything (eligibility, cooldown, budget, coverage, loss bounds), so this process can only do what any keeper-role holder
could already do.

It can start before the vault exists. Until the deployment journal's addresses are configured and the vault has code
and has granted KEEPER_ROLE, it only emits `awaiting_vault` / `awaiting_role` heartbeats (not errors). It refuses to send
if the vault proxy no longer points at the journal's router, or if the wallet holds any role beyond KEEPER_ROLE.

Environment (non-secret): KEEPER_ADDRESS, RAMSES_VAULT, RAMSES_PAIR, RAMSES_ROUTER, EXECUTE (default false),
INTERVAL_SECONDS, MAX_GAS_PRICE_WEI. Secret (only with EXECUTE=true): KEYSTORE_JSON_BASE64, KEYSTORE_PASSWORD.
Logs are JSON lines and never contain secrets or transaction bodies.
"""
import base64
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ramses_keeper as rk  # noqa: E402

RPC = 'https://rpc.mainnet.chain.robinhood.com'
IMPLEMENTATION_SLOT = '0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc'
MIN_GAS_WEI = 300_000_000_000_000  # 0.0003 ETH: a recenter is about 1.6M gas
STOP = False


def stop(*_):
    global STOP
    STOP = True


def emit(status, **fields):
    print(json.dumps(dict(status=status, checkedAtUnix=int(time.time()), **fields)), flush=True)


def is_address(value):
    return isinstance(value, str) and value.startswith('0x') and len(value) == 42


def cast(rpc, *args):
    r = subprocess.run(['cast', *args, '--rpc-url', rpc], capture_output=True, text=True, timeout=60)
    if r.returncode:
        raise RuntimeError('rpc read failed')
    return r.stdout.strip()


def configured_journal(env):
    """The journal subset the keeper needs, or None while the vault is not yet deployed/configured."""
    vault, pair, router = env.get('RAMSES_VAULT', ''), env.get('RAMSES_PAIR', ''), env.get('RAMSES_ROUTER', '')
    if not (vault or pair or router):
        return None
    if not (is_address(vault) and is_address(router) and pair.startswith('0x') and len(pair) == 66):
        raise RuntimeError('RAMSES_VAULT, RAMSES_PAIR and RAMSES_ROUTER must all be set and well formed')
    return {'vault': vault, 'pair': pair, 'router': router}


def readiness(rpc, journal, keeper):
    """Returns (state, detail). state: awaiting_vault | awaiting_role | ready. Raises on an unsafe configuration."""
    if journal is None:
        return 'awaiting_vault', 'journal addresses not configured'
    if int(cast(rpc, 'chain-id')) != 4663:
        raise RuntimeError('wrong chain')
    if int(cast(rpc, 'codesize', journal['vault'])) == 0:
        return 'awaiting_vault', 'vault has no code yet'
    slot = cast(rpc, 'storage', journal['vault'], IMPLEMENTATION_SLOT)
    if ('0x' + slot[-40:]).lower() != journal['router'].lower():
        raise RuntimeError('vault proxy does not point at the journal router; review before sending')
    return 'ready', ''


def prepare_signer(directory, keeper):
    encoded = os.environ.pop('KEYSTORE_JSON_BASE64', '')
    password = os.environ.pop('KEYSTORE_PASSWORD', '')
    if not encoded or not password:
        raise RuntimeError('signer is not provisioned')
    raw = base64.b64decode(encoded, validate=True)
    data = json.loads(raw)
    if data.get('version') != 3 or not (data.get('crypto') or data.get('Crypto')):
        raise RuntimeError('expected an encrypted V3 keystore')
    key = Path(directory) / 'keystore'
    key.write_bytes(raw)
    key.chmod(0o600)
    secret = Path(directory) / 'password'
    secret.write_text(password)
    secret.chmod(0o600)
    check = subprocess.run(['cast', 'wallet', 'address', '--keystore', str(key), '--password-file', str(secret)],
                           capture_output=True, text=True, timeout=60)
    if check.returncode or check.stdout.strip().lower() != keeper.lower():
        raise RuntimeError('keystore does not belong to KEEPER_ADDRESS')
    return ['--keystore', str(key), '--password-file', str(secret)]


def main():
    os.umask(0o077)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    keeper = os.environ.get('KEEPER_ADDRESS', '')
    if not is_address(keeper):
        raise RuntimeError('KEEPER_ADDRESS is required')
    execute = os.environ.get('EXECUTE', 'false') == 'true'
    interval = max(60, int(os.environ.get('INTERVAL_SECONDS', '300')))
    rpc = os.environ.get('RPC_URL', RPC)
    if rpc != RPC and not rpc.startswith(('http://127.0.0.1', 'http://localhost')):
        raise RuntimeError('RPC_URL may only override to a local rehearsal node')
    rk.MAX_GAS_PRICE_WEI = min(int(os.environ.get('MAX_GAS_PRICE_WEI', '100000000')), 2_000_000_000)
    journal = configured_journal(os.environ)
    errors = 0
    with tempfile.TemporaryDirectory(prefix='ramses-keeper-') as directory:
        signer = prepare_signer(directory, keeper) if execute else []
        journal_path = Path(directory) / 'journal.json'
        if journal:
            journal_path.write_text(json.dumps(journal))
        k = None
        emit('started', executionEnabled=execute, keeper=keeper, vaultConfigured=journal is not None)
        while not STOP:
            try:
                state, detail = readiness(rpc, journal, keeper)
                balance = int(cast(rpc, 'balance', keeper))
                funded = balance >= MIN_GAS_WEI
                if state == 'ready':
                    if k is None:
                        k = rk.Keeper(journal_path, keeper, signer, Path(directory) / 'state.json', rpc=rpc)
                    problem = k.check_roles()
                    if problem == 'missing KEEPER_ROLE':
                        state, detail = 'awaiting_role', problem
                    elif problem:
                        raise RuntimeError(problem)
                emit('heartbeat', executionEnabled=execute, keeper=keeper, state=state, detail=detail,
                     gasBalanceWei=str(balance), gasReady=funded)
                if state == 'ready':
                    if execute and not funded:
                        emit('awaiting_gas', keeper=keeper, gasBalanceWei=str(balance))
                    else:
                        lines, code = k.step(execute and funded)
                        for line in lines:
                            emit('step', **line)
                        if code:
                            emit('attention', code=code)
                errors = 0
            except Exception as error:  # no raw messages from cast: they can carry call data
                errors += 1
                emit('error', errorType=type(error).__name__,
                     reason=str(error)[:160] if type(error) is RuntimeError else '', consecutive=errors)
                if errors >= 10:
                    raise
            for _ in range(interval):
                if STOP:
                    break
                time.sleep(1)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        emit('fatal', errorType=type(error).__name__)
        sys.exit(2)
