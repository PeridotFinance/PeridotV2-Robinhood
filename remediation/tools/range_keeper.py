"""Off-chain keeper for the concentrated-liquidity vault: asks the contract whether a recenter
is due (by simulating the real call) and, only with --execute, sends it.

The contract decides everything: eligibility, cooldown, rolling budget, range, floors and loss
bound are enforced on-chain, so this script cannot make the vault do anything it would not allow
anyone holding the keeper role to do. Default is read-only. Signing stays with the user's encrypted
Foundry keystore: `cast send --account` prompts for the password in the user's terminal, so the
script never sees a key or password.

    python3 remediation/tools/range_keeper.py              # status + simulation, sends nothing
    python3 remediation/tools/range_keeper.py --execute    # sends one recenter if due
    python3 remediation/tools/range_keeper.py --watch 300 --execute
"""
import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

RPC = 'https://rpc.mainnet.chain.robinhood.com'
UNLOCKED = False  # local rehearsal only (anvil with impersonation)
VAULT = '0x280825b2d856706Ff7E0d6351CcB2e935E1a9A2f'
ADAPTER = '0xadA73211711e4790bc83B5d6B39f47fE04D276f3'
GOVERNOR = '0x94696d767e65a75581145646960FA0eC886cE5d2'
ACCOUNT = 'robinhood-deployer'
PAIR = '0xe2050352f4346597cc69d2776d99ae60c9440dfc28f9406ce66be0bbe3fb6b06'
GAS_LIMIT = 3_000_000
# The vault rejects deadlines more than maxDeadlineDelay (300s) ahead, so this is the longest the
# operator has to answer the keystore password prompt before the transaction reverts on-chain.
DEADLINE_SECONDS = 290
MAX_GAS_PRICE_WEI = 100_000_000  # 0.1 gwei, as for the margin keeper
JOURNAL = Path(__file__).resolve().parents[1] / 'evidence' / 'range-keeper-journal.json'

# selector -> (name, is_waiting). Waiting means "nothing to do right now", not an error.
REASONS = {
    'RecenterNotNeeded()': True,
    'RecenterCooldown()': True,
    'RecenterRateLimited()': True,
    'AllocationPaused()': True,
    'EmergencyMode()': True,
    'RangePolicyDisabled()': True,
    'StaleOracle(address,uint256)': True,
    'RecenterLossTooHigh()': False,
    'DeployLossTooHigh()': False,
    'PriceDeviation(uint256,uint256)': True,
    'InsufficientLiquidity()': True,
    'InvalidConfiguration()': False,  # see DUST_REBALANCE below
    'InvalidDeadline()': False,
    'CheckpointStale()': False,
}


def sh(*args, check=True):
    result = subprocess.run(args, capture_output=True, text=True)
    if check and result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip())
    return result


def cast(*args, check=True):
    return sh('cast', *args, '--rpc-url', RPC, check=check)


def selector(signature):
    return sh('cast', 'sig', signature).stdout.strip()


def classify(output):
    """Maps a revert payload from a simulation to (name, waiting)."""
    match = re.search(r'0x[0-9a-fA-F]{8}', output or '')
    if not match:
        return 'unknown', False
    code = match.group(0).lower()
    for signature, waiting in REASONS.items():
        if selector(signature).lower() == code:
            return signature.split('(')[0], waiting
    return 'unknown(' + code + ')', False


def status():
    try:
        return _status()
    except RuntimeError:
        return {'upgraded': False, 'note': 'V3 is not live on this chain yet; nothing to do'}


def _status():
    lower_upper_ranged = cast('call', ADAPTER, 'positionTicks(bytes32)(int24,int24,bool)', PAIR).stdout.split()
    cfg = cast('call', VAULT, 'pairConfig(bytes32)', PAIR).stdout
    state = cast('call', VAULT, 'rangeState(bytes32)(int24,bool,uint64,uint8)', PAIR).stdout.split()
    return {'upgraded': True, 'ticks': lower_upper_ranged, 'rangeState': state, 'pairConfigHex': cfg.strip()[:20] + '...'}


def chain_deadline():
    """Deadlines are measured against the chain's clock, not this machine's."""
    return int(cast('block', 'latest', '--field', 'timestamp').stdout.strip()) + DEADLINE_SECONDS


def simulate(fn='recenter'):
    """Simulates `fn(bytes32,uint256)` exactly as the keeper would send it."""
    deadline = chain_deadline()
    result = cast('call', VAULT, fn + '(bytes32,uint256)', PAIR, str(deadline),
                  '--from', GOVERNOR, '--gas-limit', str(GAS_LIMIT), check=False)
    if result.returncode == 0:
        return 'due', False, ''
    name, waiting = classify(result.stderr + result.stdout)
    return name, waiting, (result.stderr or result.stdout).strip()[:300]


def gas_price_ok():
    price = int(cast('gas-price').stdout.strip())
    return price <= MAX_GAS_PRICE_WEI, price


def journal(entry):
    JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    entries = json.loads(JOURNAL.read_text()) if JOURNAL.exists() else []
    entries.append(entry)
    JOURNAL.write_text(json.dumps(entries, indent=2) + '\n')


def send(fn='recenter'):
    deadline = chain_deadline()
    signer = ['--unlocked'] if UNLOCKED else ['--account', ACCOUNT]
    started = time.time()
    result = cast('send', VAULT, fn + '(bytes32,uint256)', PAIR, str(deadline),
                  *signer, '--from', GOVERNOR, '--gas-limit', str(GAS_LIMIT),
                  '--json', check=False)
    result.elapsed = time.time() - started
    return result


def outcome_of(result):
    """'sent' only when the transaction was mined with status 1. `cast send` exits 0 for a mined
    revert, so the exit code alone is not enough."""
    if result.returncode != 0:
        return 'SEND FAILED'
    try:
        status = json.loads(result.stdout).get('status')
    except ValueError:
        return 'UNCONFIRMED'
    if str(status).lower() in ('0x1', '1'):
        return 'sent'
    hint = ''
    if getattr(result, 'elapsed', 0) > DEADLINE_SECONDS - 20:
        hint = ' (the password prompt was open for ' + str(int(result.elapsed)) + 's; the transaction deadline is ' \
               + str(DEADLINE_SECONDS) + 's, so it most likely expired - answer the prompt promptly)'
    return 'REVERTED ON-CHAIN' + hint


def last_sent(fn):
    if not JOURNAL.exists():
        return 0
    entries = [e for e in json.loads(JOURNAL.read_text()) if e.get('function') == fn and e.get('action') == 'sent']
    return max((e['time'] for e in entries), default=0)


REBALANCE_MIN_INTERVAL = 3600
# `rebalance` with an idle balance too small to round to a non-zero amount of the other token (for
# example a few hundred wei of NVDA against dollars of USDG) reaches the adapter with a zero amount
# and fails with InvalidConfiguration. That is "nothing deployable", not a fault.
DUST_REBALANCES = ('InvalidConfiguration', 'DeployLossTooHigh')
# DeployLossTooHigh on a rebalance is the same dust, one step later: the fees just collected leave
# a few billionths of a dollar to deploy, where rounding alone exceeds the 10 bp loss bound. The
# bound is working; idle fees accumulate until there is an amount worth deploying.


def maybe_rebalance(execute):
    """Idle assets (new deposits, an idle-exit recenter) are deployed by `rebalance`, which needs a
    checkpoint no older than the vault's maxCheckpointAge. Only acts when the real call succeeds."""
    outcome, waiting, detail = simulate('rebalance')
    if outcome in DUST_REBALANCES:
        waiting = True
    info = {'time': int(time.time()), 'rebalance': outcome}
    if outcome == 'CheckpointStale':
        outcome, waiting, detail = simulate('checkpoint')
        if outcome != 'due':
            info.update(action='wait' if waiting else 'ATTENTION', detail=detail, step='checkpoint')
            print(json.dumps(info))
            return 0 if waiting else 2
        if not execute:
            info['action'] = 'checkpoint then rebalance would run; rerun with --execute'
            print(json.dumps(info))
            return 0
        if time.time() - last_sent('rebalance') < REBALANCE_MIN_INTERVAL:
            info['action'] = 'wait (rebalance sent less than an hour ago)'
            print(json.dumps(info))
            return 0
        sent = send('checkpoint')
        result_text = outcome_of(sent)
        journal({'time': int(time.time()), 'function': 'checkpoint', 'action': result_text,
                 'output': (sent.stdout or sent.stderr).strip()[:600]})
        if result_text != 'sent':
            info['action'] = result_text + ' (checkpoint)'
            print(json.dumps(info))
            return 3
        outcome, waiting, detail = simulate('rebalance')
        waiting = waiting or outcome in DUST_REBALANCES
    if outcome != 'due':
        info.update(action='wait' if waiting else 'ATTENTION', detail=detail)
        print(json.dumps(info))
        return 0 if waiting else 2
    ok, price = gas_price_ok()
    if not ok:
        info['action'] = 'wait (gas price above cap)'
    elif not execute:
        info['action'] = 'rebalance is due; rerun with --execute to send'
    elif time.time() - last_sent('rebalance') < REBALANCE_MIN_INTERVAL:
        info['action'] = 'wait (rebalance sent less than an hour ago)'
    else:
        sent = send('rebalance')
        info['action'] = outcome_of(sent)
        info['function'] = 'rebalance'
        info['output'] = (sent.stdout or sent.stderr).strip()[:600]
        journal(dict(info))
        print(json.dumps(info))
        return 0 if info['action'] == 'sent' else 3
    print(json.dumps(info))
    return 0


def step(execute):
    outcome, waiting, detail = simulate()
    if outcome.startswith('unknown(0x)') or outcome == 'unknown':
        waiting = False
    info = {'time': int(time.time()), 'simulation': outcome}
    if outcome != 'due':
        info['action'] = 'wait' if waiting else 'ATTENTION'
        info['detail'] = detail
        print(json.dumps(info))
        if not waiting:
            return 2
        return maybe_rebalance(execute)
    ok, price = gas_price_ok()
    info['gasPriceWei'] = price
    if not ok:
        info['action'] = 'wait (gas price above cap)'
        print(json.dumps(info))
        return 0
    if not execute:
        info['action'] = 'recenter is due; rerun with --execute to send'
        print(json.dumps(info))
        return 0
    result = send()
    info['action'] = outcome_of(result)
    info['function'] = 'recenter'
    info['output'] = (result.stdout or result.stderr).strip()[:600]
    journal(info)
    print(json.dumps(info))
    return 0 if info['action'] == 'sent' else 3


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--execute', action='store_true', help='send recenter when due (prompts for the keystore password)')
    parser.add_argument('--watch', type=int, default=0, metavar='SECONDS', help='repeat every N seconds')
    parser.add_argument('--max-gas-price-wei', type=int, default=None, help='override the gas price cap (default 0.1 gwei)')
    parser.add_argument('--rpc', default=None, help='override the RPC (local rehearsal)')
    parser.add_argument('--unlocked', action='store_true', help='send from an impersonated account; localhost only')
    args = parser.parse_args()
    global RPC, UNLOCKED, MAX_GAS_PRICE_WEI
    if args.max_gas_price_wei:
        MAX_GAS_PRICE_WEI = args.max_gas_price_wei
    if args.rpc:
        RPC = args.rpc
    if args.unlocked:
        if not RPC.startswith(('http://127.0.0.1', 'http://localhost')):
            raise SystemExit('--unlocked is for a local rehearsal node only')
        UNLOCKED = True
    if int(cast('chain-id').stdout) != 4663:
        raise SystemExit('wrong chain')
    current = status()
    print(json.dumps({'status': current}))
    if not current.get('upgraded'):
        return 0
    while True:
        code = step(args.execute)
        if not args.watch:
            return code
        time.sleep(args.watch)


if __name__ == '__main__':
    sys.exit(main())
