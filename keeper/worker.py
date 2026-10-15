"""Unattended range keeper for the concentrated-liquidity vault (App Platform worker).

Runs `remediation/tools/range_keeper.py` on a timer with a DEDICATED wallet that holds only
KEEPER_ROLE on the vault. The contract decides everything (eligibility, cooldown, rolling budget,
range, floors, loss bound), so this process can only do what any keeper-role holder can do.

Safe by default: it only sends when EXECUTE=true. It refuses to send if the vault or adapter
implementation is not the reviewed one, if the wallet lacks KEEPER_ROLE, or if the wallet holds any
admin power. Logs are JSON lines and never contain secrets or transaction bodies.
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'remediation' / 'tools'))
import range_keeper as rk  # noqa: E402

RPC = 'https://rpc.mainnet.chain.robinhood.com'
VAULT = rk.VAULT
ADAPTER = rk.ADAPTER
PAIR = rk.PAIR
IMPLEMENTATION_SLOT = '0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc'
EXPECTED_VAULT_IMPL = '0x53A23Ef7A5639f237625Ff7c89Ff9e9Ecf1DD18a'
EXPECTED_ADAPTER_IMPL = '0xb24BA4dd6D1436e2D2eb2Cd054B07931C8BDD456'
# Roles the keeper must NOT hold (names are hashed on-chain; DEFAULT_ADMIN_ROLE is zero).
FORBIDDEN_ROLES = {'DEFAULT_ADMIN_ROLE': '0x' + '00' * 32}
FORBIDDEN_NAMED = ('CONFIG_ROLE', 'GUARDIAN_ROLE')
MIN_GAS_WEI = 300_000_000_000_000  # 0.0003 ETH: a recenter costs ~1.4M gas
STOP = False


def stop(*_):
    global STOP
    STOP = True


def emit(status, **fields):
    print(json.dumps(dict(status=status, checkedAtUnix=int(time.time()), **fields)), flush=True)


def cast(*args):
    return rk.cast(*args).stdout.strip()


def checksum(address):
    return rk.sh('cast', 'to-check-sum-address', address).stdout.strip()  # offline: no --rpc-url


def storage_address(contract):
    return checksum('0x' + cast('storage', contract, IMPLEMENTATION_SLOT)[-40:])


def role_id(name):
    return cast('call', VAULT, name + '()(bytes32)')


def has_role(role, account):
    return cast('call', VAULT, 'hasRole(bytes32,address)(bool)', role, account) == 'true'


def preflight(keeper, execute):
    """Raises with a short, secret-free reason when it is not safe to send."""
    if int(cast('chain-id')) != 4663:
        raise RuntimeError('wrong chain')
    if storage_address(VAULT).lower() != EXPECTED_VAULT_IMPL.lower():
        raise RuntimeError('vault implementation changed; review before sending')
    if storage_address(ADAPTER).lower() != EXPECTED_ADAPTER_IMPL.lower():
        raise RuntimeError('adapter implementation changed; review before sending')
    keeper_role = role_id('KEEPER_ROLE')
    holds_keeper = has_role(keeper_role, keeper)
    forbidden = [n for n, r in FORBIDDEN_ROLES.items() if has_role(r, keeper)]
    forbidden += [n for n in FORBIDDEN_NAMED if has_role(role_id(n), keeper)]
    if forbidden:
        raise RuntimeError('wallet holds roles beyond KEEPER_ROLE: ' + ','.join(forbidden))
    if execute and not holds_keeper:
        raise RuntimeError('wallet does not hold KEEPER_ROLE yet')
    return holds_keeper


def prepare_signer(directory, keeper):
    """Writes the provisioned keystore and password to owner-only files and checks the address."""
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
    return str(key), str(secret)


def balance_wei(keeper):
    return int(cast('balance', keeper))


def cycle(keeper, execute, funded):
    """One keeper pass. Returns the exit-code style result of range_keeper.step."""
    return rk.step(execute and funded)


def main():
    os.umask(0o077)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    keeper = os.environ.get('KEEPER_ADDRESS', '')
    if not (keeper.startswith('0x') and len(keeper) == 42):
        raise RuntimeError('KEEPER_ADDRESS is required')
    execute = os.environ.get('EXECUTE', 'false') == 'true'
    interval = max(60, int(os.environ.get('INTERVAL_SECONDS', '300')))
    rpc = os.environ.get('RPC_URL', RPC)
    if rpc != RPC and not rpc.startswith(('http://127.0.0.1', 'http://localhost')):
        raise RuntimeError('RPC_URL may only override to a local rehearsal node')
    max_gas_price = min(int(os.environ.get('MAX_GAS_PRICE_WEI', '100000000')), 2_000_000_000)  # default 0.1 gwei, never above 2
    rk.configure(rpc=rpc, sender=keeper, deadline_seconds=120, max_gas_price_wei=max_gas_price)
    errors = 0
    with tempfile.TemporaryDirectory(prefix='range-keeper-') as directory:
        rk.configure(journal=Path(directory) / 'journal.json')
        if execute:
            rk.configure(keystore=prepare_signer(directory, keeper))
        emit('started', executionEnabled=execute, keeper=keeper)
        while not STOP:
            try:
                holds = preflight(keeper, execute)
                balance = balance_wei(keeper)
                funded = balance >= MIN_GAS_WEI
                emit('heartbeat', executionEnabled=execute, keeper=keeper, holdsKeeperRole=holds,
                     gasBalanceWei=str(balance), gasReady=funded)
                if execute and not funded:
                    emit('awaiting_gas', keeper=keeper, gasBalanceWei=str(balance))
                else:
                    code = cycle(keeper, execute, funded)
                    if code:
                        emit('attention', code=code)
                errors = 0
            except Exception as error:  # no raw messages from cast: they can carry call data
                errors += 1
                emit('error', errorType=type(error).__name__, reason=str(error)[:160] if type(error) is RuntimeError else '',
                     consecutive=errors)
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
