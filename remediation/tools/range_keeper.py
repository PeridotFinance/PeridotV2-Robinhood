"""Off-chain keeper for the concentrated-liquidity vault: asks the contract whether a recenter
is due (by simulating the real call) and, only with --execute, sends it.

The contract decides everything: eligibility, cooldown, rolling budget, range, floors and loss
bound are enforced on-chain, so this script cannot make the vault do anything it would not allow
anyone holding the keeper role to do. Default is read-only. Signing stays with the user's encrypted
Foundry keystore: `cast send --account` prompts for the password in the user's terminal, so the
script never sees a key or password.

    python3 remediation/tools/range_keeper.py --sender 0xKEEPER                    # status + simulation, sends nothing
    python3 remediation/tools/range_keeper.py --sender 0xKEEPER --account NAME --execute
    python3 remediation/tools/range_keeper.py --sender 0xKEEPER --account NAME --watch 300 --execute

There is no default sender or keystore account: both must name the keeper wallet explicitly. The
retired deployer 0x9469...5d2 (possibly compromised, 2026-11-05) is refused outright.
"""
import argparse
import json
import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path

RPC = 'https://rpc.mainnet.chain.robinhood.com'
UNLOCKED = False  # local rehearsal only (anvil with impersonation)
SENDER = None  # required: set by configure() or --sender; there is deliberately no default
KEYSTORE = None  # (keystore path, password file) for an unattended dedicated keeper wallet
VAULT = '0x280825b2d856706Ff7E0d6351CcB2e935E1a9A2f'
ADAPTER = '0xadA73211711e4790bc83B5d6B39f47fE04D276f3'
# Retired signers this keeper must never simulate or send from. The deployer below may have been
# compromised (2026-11-05); admin moved to the Ledger and the keeper role to a dedicated wallet.
RETIRED_SIGNERS = frozenset({'0x94696d767e65a75581145646960fa0ec886ce5d2'})
RETIRED_ACCOUNTS = frozenset({'robinhood-deployer'})  # the retired deployer's Foundry keystore name
ACCOUNT = None  # Foundry keystore name for interactive --execute; required, no default
GUARD = '0xaE4D4DdB8dD646951d54fE9B13BE23DcB61C6741'
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
    'AccessControlUnauthorizedAccount(address,bytes32)': True,  # the keeper role is not granted (yet)
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


def check_sender(address):
    """Returns the address if it is a usable sender; raises otherwise. Refuses the retired signer."""
    if not isinstance(address, str) or not re.fullmatch(r'0x[0-9a-fA-F]{40}', address):
        raise RuntimeError('keeper sender is required: pass --sender / KEEPER_ADDRESS (a 0x address)')
    if address.lower() in RETIRED_SIGNERS:
        raise RuntimeError('refusing the retired signer ' + address + ' as keeper sender')
    return address


def sender():
    if not SENDER:
        raise RuntimeError('keeper sender is required: pass --sender / KEEPER_ADDRESS (no default)')
    return check_sender(SENDER)


def configure(rpc=None, sender=None, keystore=None, max_gas_price_wei=None, deadline_seconds=None, journal=None):
    """Used by the unattended worker: a dedicated sender and keystore, no interactive prompt."""
    global RPC, SENDER, KEYSTORE, MAX_GAS_PRICE_WEI, DEADLINE_SECONDS, JOURNAL
    if journal:
        JOURNAL = Path(journal)
    if rpc:
        RPC = rpc
    if sender is not None:
        SENDER = check_sender(sender)
    if keystore:
        KEYSTORE = keystore
    if max_gas_price_wei:
        MAX_GAS_PRICE_WEI = max_gas_price_wei
    if deadline_seconds:
        DEADLINE_SECONDS = deadline_seconds


def chain_deadline():
    """Deadline from the later of the chain's head time and this machine's clock. A node whose head is
    old (a quiet local fork) would otherwise hand out a deadline that is already past."""
    head = int(cast('block', 'latest', '--field', 'timestamp').stdout.strip())
    return max(head, int(time.time())) + DEADLINE_SECONDS


def simulate(fn='recenter'):
    """Simulates `fn(bytes32,uint256)` exactly as the keeper would send it."""
    deadline = chain_deadline()
    result = cast('call', VAULT, fn + '(bytes32,uint256)', PAIR, str(deadline),
                  '--from', sender(), '--gas-limit', str(GAS_LIMIT), check=False)
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
    if KEYSTORE:
        signer = ['--keystore', KEYSTORE[0], '--password-file', KEYSTORE[1]]
    else:
        if UNLOCKED:
            signer = ['--unlocked']
        elif ACCOUNT:
            signer = ['--account', ACCOUNT]
        else:
            raise RuntimeError('a keystore account is required to send: pass --account (no default)')
    started = time.time()
    result = cast('send', VAULT, fn + '(bytes32,uint256)', PAIR, str(deadline),
                  *signer, '--from', sender(), '--gas-limit', str(GAS_LIMIT),
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

# A rebalance is only worth sending when what it can pair is worth several times its gas. Matched
# value is what the vault itself pairs (RangeLib.deploy): the smaller idle side at oracle prices.
REBALANCE_MIN_VALUE_MULTIPLE = 10.0  # override with the optional env var of the same name
REBALANCE_GAS_ESTIMATE = 750_000  # measured over 79 live rebalances: median 721k, mean about 770k
ETH_USD_FEED = '0x78F3556b67E17Df817D51Ef5a990cDaF09E8d3A9'
ETH_USD_MAX_AGE = 86_400
STOCK_DECIMALS = 18
USDG_DECIMALS = 6


def rebalance_min_value_multiple():
    try:
        value = float(os.environ.get('REBALANCE_MIN_VALUE_MULTIPLE', REBALANCE_MIN_VALUE_MULTIPLE))
    except ValueError:
        return REBALANCE_MIN_VALUE_MULTIPLE
    return value if math.isfinite(value) and value > 0 else REBALANCE_MIN_VALUE_MULTIPLE


def _uints(output):
    return [int(line.split()[0]) for line in output.strip().splitlines()]


def rebalance_value():
    """(matched value, estimated gas cost), both USD with 18 decimals. Raises if any read is unusable."""
    ledger = _uints(cast('call', VAULT, 'ledger(bytes32)(uint256,uint256,uint256,uint256,uint256,uint64)', PAIR).stdout)
    guard = cast('call', VAULT, 'oracleGuard()(address)').stdout.strip()
    stock_price, usdg_price = _uints(cast('call', guard, 'pricesUSD18(bytes32)(uint256,uint256)', PAIR).stdout)
    _, eth_usd, _, eth_updated, _ = _uints(
        cast('call', ETH_USD_FEED, 'latestRoundData()(uint80,int256,uint256,uint256,uint80)').stdout)
    gas_price = int(cast('gas-price').stdout.strip())
    if min(stock_price, usdg_price, eth_usd, gas_price) <= 0 or time.time() - eth_updated > ETH_USD_MAX_AGE:
        raise ValueError('unusable price')
    matched = min(ledger[2] * stock_price // 10 ** STOCK_DECIMALS, ledger[3] * usdg_price // 10 ** USDG_DECIMALS)
    return matched, REBALANCE_GAS_ESTIMATE * gas_price * eth_usd // 10 ** 8


def rebalance_value_gate():
    """(send?, log fields). Fails safe: a failed read skips this optional rebalance and nothing else."""
    try:
        matched, cost = rebalance_value()
    except (RuntimeError, ValueError, IndexError, OSError) as error:  # no raw messages: they can carry call data
        return False, {'event': 'rebalance_value_read_failed', 'errorType': type(error).__name__,
                       'action': 'wait (rebalance value read failed)'}
    fields = {'matchedUsd': round(matched / 1e18, 6), 'gasCostUsd': round(cost / 1e18, 6)}
    if matched < rebalance_min_value_multiple() * cost:
        return False, dict(fields, event='rebalance_below_value_threshold',
                           action='wait (matched value below threshold)')
    return True, fields


# Cash held in the pTokens is redeemable at the last CHECKPOINTED claim, so an unrecorded LP loss can be
# escaped by a cash exit until the next checkpoint. The
# hourly checkpoint above is the backstop; this shrinks the window to minutes by checkpointing as soon as
# the oracle price has moved this far from the price at the last checkpoint.
PRICE_CHECKPOINT_BPS = 50
PRICE_CHECKPOINT_MIN_INTERVAL = 300
# Per pair (keyed by lower-case pairId), so a multi-pair keeper on one feed keeps independent baselines.
BASELINE_PRICES = {}  # pairId -> oracle stock price (USD 1e18) at the last checkpoint this process saw or sent
LAST_PRICE_CHECKPOINTS = {}  # pairId -> unix time of the last price-checkpoint attempt
LIQUIDITY_SKIP = {}  # pairId -> last journalled skip reason, so a persistent skip is journalled once, not every cycle


def _pair_key():
    return PAIR.lower()


def pair_liquidity():
    """Open LP liquidity of the current pair, or None when the read fails or is unusable."""
    try:
        result = cast('call', ADAPTER, 'positionState(bytes32)(uint256,uint128,uint256,uint256)', PAIR, check=False)
    except (RuntimeError, OSError):
        return None
    if result.returncode:
        return None
    try:
        return int(result.stdout.split('\n')[1].split()[0])
    except (IndexError, ValueError):
        return None


def _liquidity_skip(reason):
    """Logs every skipped cycle; journals only when the skip reason changes for this pair."""
    key = _pair_key()
    info = {'time': int(time.time()), 'pair': PAIR, 'priceCheckpoint': 'skipped', 'reason': reason}
    print(json.dumps(info))
    if LIQUIDITY_SKIP.get(key) != reason:
        LIQUIDITY_SKIP[key] = reason
        journal(dict(info))


def stock_price():
    """Oracle stock price in USD 1e18, or None when the guard cannot price (stale feed, paused token)."""
    try:
        result = cast('call', GUARD, 'pricesUSD18(bytes32)(uint256,uint256)', PAIR, check=False)
    except (RuntimeError, OSError):  # a failed read is "cannot price", never a crashed cycle
        return None
    if result.returncode:
        return None
    try:
        price = int(result.stdout.split()[0])
    except (IndexError, ValueError):
        return None
    return price or None


def maybe_price_checkpoint(execute):
    """Checkpoints when the oracle price moved >= PRICE_CHECKPOINT_BPS since the last checkpoint. Only acts
    when the real call succeeds; a closed market (stale feed) or any waiting revert is not a fault."""
    key = _pair_key()
    # Only while LP liquidity is open: with none, no LP loss can go unrecorded and a checkpoint is a no-op.
    liquidity = pair_liquidity()
    if liquidity is None:
        _liquidity_skip('liquidity read failed')
        return 0
    if liquidity == 0:
        BASELINE_PRICES.pop(key, None)  # start fresh when liquidity reopens
        _liquidity_skip('no open liquidity')
        return 0
    LIQUIDITY_SKIP.pop(key, None)
    price = stock_price()
    if price is None:
        return 0
    baseline = BASELINE_PRICES.get(key)
    if baseline is None:
        BASELINE_PRICES[key] = price
        return 0
    if abs(price - baseline) * 10000 < baseline * PRICE_CHECKPOINT_BPS:
        return 0
    info = {'time': int(time.time()), 'pair': PAIR,
            'priceCheckpoint': 'oracle moved >= ' + str(PRICE_CHECKPOINT_BPS) + ' bps',
            'baselinePrice': str(baseline), 'price': str(price)}
    if time.time() - LAST_PRICE_CHECKPOINTS.get(key, 0) < PRICE_CHECKPOINT_MIN_INTERVAL:
        info['action'] = 'wait (price checkpoint attempted less than 5 minutes ago)'
        print(json.dumps(info))
        return 0
    outcome, waiting, detail = simulate('checkpoint')
    if outcome != 'due':
        info.update(simulation=outcome, action='wait' if waiting else 'ATTENTION', detail=detail)
        print(json.dumps(info))
        return 0 if waiting else 2
    ok, gas_price = gas_price_ok()
    if not ok:
        info['action'] = 'wait (gas price above cap)'
    elif not execute:
        info['action'] = 'price checkpoint is due; rerun with --execute to send'
    else:
        LAST_PRICE_CHECKPOINTS[key] = time.time()
        sent = send('checkpoint')
        info['action'] = outcome_of(sent)
        info['function'] = 'checkpoint'
        info['output'] = (sent.stdout or sent.stderr).strip()[:600]
        journal(dict(info))
        print(json.dumps(info))
        if info['action'] == 'sent':
            BASELINE_PRICES[key] = price
            return 0
        return 3
    print(json.dumps(info))
    return 0


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
            info['action'] = 'checkpoint is due; rerun with --execute'
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
        price = stock_price()
        if price:
            BASELINE_PRICES[_pair_key()] = price
        outcome, waiting, detail = simulate('rebalance')
        waiting = waiting or outcome in DUST_REBALANCES
    if outcome != 'due':
        info.update(action='wait' if waiting else 'ATTENTION', detail=detail)
        print(json.dumps(info))
        return 0 if waiting else 2
    worth, fields = rebalance_value_gate()
    info.update(fields)
    if not worth:
        print(json.dumps(info))
        return 0
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
    price_code = maybe_price_checkpoint(execute)
    code = _step(execute)
    return code or price_code


def _step(execute):
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
    parser.add_argument('--sender', required=True, help='keeper wallet address (required; no default)')
    parser.add_argument('--account', default=None, help='Foundry keystore name to sign with (required for --execute)')
    args = parser.parse_args()
    global RPC, UNLOCKED, MAX_GAS_PRICE_WEI, SENDER, ACCOUNT
    try:
        SENDER = check_sender(args.sender)
    except RuntimeError as error:
        raise SystemExit(str(error))
    if args.account:
        if args.account in RETIRED_ACCOUNTS:
            raise SystemExit('refusing the retired keystore account ' + args.account)
        ACCOUNT = args.account
    if args.max_gas_price_wei:
        MAX_GAS_PRICE_WEI = args.max_gas_price_wei
    if args.rpc:
        RPC = args.rpc
    if args.unlocked:
        if not RPC.startswith(('http://127.0.0.1', 'http://localhost')):
            raise SystemExit('--unlocked is for a local rehearsal node only')
        UNLOCKED = True
    if args.execute and not (UNLOCKED or ACCOUNT):
        raise SystemExit('--execute needs --account (the keeper wallet keystore); there is no default')
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
