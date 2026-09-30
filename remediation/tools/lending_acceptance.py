"""Small user-signed lending acceptance test. Default: full round-trip simulation only."""
import argparse
import fcntl
import json
import os
import subprocess
import sys
from state import ROOT, GOVERNOR, CONTROLLER, MARKETS, call, cast, save
sys.path.insert(0, str(ROOT / 'contracts/robinhood-vaults/margin-mainnet/tools'))
from rpc import rpc, RPC


STOCK = '0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec'
USDG = '0x5fc5360d0400a0fd4f2af552add042d716f1d168'
ENTRYPOINTS = {'roundtrip': 'run', 'open': 'supplyAndBorrow', 'close': 'repayAndRedeem'}


def snapshot():
    block = rpc('eth_getBlockByNumber', ['latest', False])
    tag = block['number']
    def number(target, signature, *args):
        return int(call(target, signature, *args, block=tag), 16)
    result = {'block': int(tag, 16), 'hash': block['hash'],
              'stockWallet': number(STOCK, 'balanceOf(address)', GOVERNOR),
              'dollarWallet': number(USDG, 'balanceOf(address)', GOVERNOR),
              'stockShares': number(MARKETS['pNVDA'], 'balanceOf(address)', GOVERNOR),
              'stockAllowance': number(STOCK, 'allowance(address,address)', GOVERNOR, MARKETS['pNVDA']),
              'dollarAllowance': number(USDG, 'allowance(address,address)', GOVERNOR, MARKETS['pUSDG'])}
    for name, market in MARKETS.items():
        result[name] = {'debt': number(market, 'borrowBalanceStored(address)', GOVERNOR),
                        'member': bool(number(CONTROLLER, 'checkMembership(address,address)', GOVERNOR, market))}
    if rpc('eth_getBlockByNumber', [tag, False])['hash'] != block['hash']:
        raise RuntimeError('Snapshot block changed')
    return result


def verify(stage, pin):
    if stage not in ('open', 'close'):
        raise ValueError('Receipt verification requires open or close stage')
    journal_path = ROOT / ('broadcast/LendingAcceptance.s.sol/4663/' + ENTRYPOINTS[stage] + '-latest.json')
    journal = json.loads(journal_path.read_text())
    save(ROOT / ('remediation/evidence/lending-acceptance-' + stage + '-broadcast.json'), journal)
    stock, dollar = MARKETS['pNVDA'], MARKETS['pUSDG']
    if stage == 'open':
        expected = [(STOCK, 'approve(address,uint256)', [stock, '1000000000000000']),
                    (stock, 'mint(uint256)', ['1000000000000000']),
                    (CONTROLLER, 'enterMarkets(address[])', ['[' + stock + ']']),
                    (dollar, 'borrow(uint256)', ['50000'])]
        events = {1: 'Mint(address,uint256,uint256)', 3: 'Borrow(address,uint256,uint256,uint256)'}
    else:
        expected = [(USDG, 'approve(address,uint256)', [dollar, '51000']),
                    (dollar, 'repayBorrow(uint256)', [str(2**256-1)]),
                    (USDG, 'approve(address,uint256)', [dollar, '0']),
                    (stock, 'redeemUnderlying(uint256)', ['1000000000000000']),
                    (CONTROLLER, 'exitMarket(address)', [dollar]),
                    (CONTROLLER, 'exitMarket(address)', [stock])]
        events = {1: 'RepayBorrow(address,address,uint256,uint256,uint256)',
                  3: 'Redeem(address,uint256,uint256)'}
    if len(journal['transactions']) != len(expected):
        raise RuntimeError('Partial/unexpected transaction count; inspect before continuing')
    receipts = []
    failure_topic = cast('keccak', 'Failure(uint256,uint256,uint256)')
    for i, (entry, (target, signature, values)) in enumerate(zip(journal['transactions'], expected)):
        tx = rpc('eth_getTransactionByHash', [entry['hash']])
        receipt = rpc('eth_getTransactionReceipt', [entry['hash']])
        data = cast('calldata', signature, *values)
        if (not tx or not receipt or int(receipt['status'], 16) != 1
                or tx['from'].lower() != GOVERNOR.lower() or tx['to'].lower() != target.lower()
                or tx['input'].lower() != data.lower() or int(tx['chainId'], 16) != 4663
                or int(tx['value'], 16) != 0 or int(tx['nonce'], 16) != pin['nonceBefore'] + i
                or receipt['transactionHash'].lower() != entry['hash'].lower()):
            raise RuntimeError('Receipt/transaction mismatch: ' + signature)
        block = rpc('eth_getBlockByNumber', [receipt['blockNumber'], False])
        if receipt['blockHash'] != block['hash'] or tx['blockHash'] != block['hash']:
            raise RuntimeError('Noncanonical receipt')
        relevant = [log for log in receipt['logs'] if log['address'].lower() in
                    (CONTROLLER.lower(), stock.lower(), dollar.lower())]
        if any(log['topics'] and log['topics'][0] == failure_topic for log in relevant):
            raise RuntimeError('Compound Failure event despite successful receipt')
        if i in events:
            topic = cast('keccak', events[i])
            if not any(log['address'].lower() == target.lower() and log['topics']
                       and log['topics'][0] == topic for log in relevant):
                raise RuntimeError('Missing success event: ' + events[i])
        receipts.append(receipt)
    after = snapshot()
    before = pin['before']
    if after['block'] < int(receipts[-1]['blockNumber'], 16):
        raise RuntimeError('Post-state predates execution')
    if stage == 'open':
        ok = (after['stockWallet'] == before['stockWallet'] - 10**15
              and after['dollarWallet'] == before['dollarWallet'] + 50000
              and after['stockShares'] > before['stockShares']
              and 50000 <= after['pUSDG']['debt'] <= 51000
              and after['pNVDA']['member'])
    else:
        ok = (after['stockWallet'] == before['stockWallet'] + 10**15
              and 50000 <= before['dollarWallet'] - after['dollarWallet'] <= 51000
              and after['pUSDG']['debt'] == 0 and not after['pUSDG']['member']
              and not after['pNVDA']['member'] and after['dollarAllowance'] == 0)
    if not ok or after['pNVDA']['debt'] != 0 or after['stockAllowance'] != 0:
        raise RuntimeError('Unexpected final balances/debt/allowances/membership; inspect before continuing')
    pin.update(verified=True, after=after, receipts=receipts,
               scope='Canonical L2 receipts, calldata, success events and state deltas from one RPC; operator acceptance only, not independent adoption or L1 finality proof.')
    save(ROOT / ('remediation/evidence/lending-acceptance-' + stage + '-verified.json'), pin)
    print(stage.upper() + ' VERIFIED', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=('roundtrip', 'open', 'close'), default='roundtrip')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--broadcast', action='store_true')
    mode.add_argument('--verify', action='store_true', help='Verify the existing signed stage only; never signs or rebroadcasts')
    args = parser.parse_args()
    if args.broadcast and args.stage == 'roundtrip':
        raise RuntimeError('Broadcast separate --stage open and --stage close, with verification between them')
    if args.broadcast and not sys.stdin.isatty():
        raise RuntimeError('Run in your terminal; Foundry prompts for your keystore password locally')
    if int(rpc('eth_chainId', []), 16) != 4663:
        raise RuntimeError('Wrong chain')
    if args.verify:
        if args.stage not in ('open', 'close'):
            raise RuntimeError('--verify requires --stage open or --stage close')
        intent = ROOT / ('remediation/evidence/lending-acceptance-' + args.stage + '-intent.json')
        pin = json.loads(intent.read_text())
        if pin['stage'] != args.stage or pin['chainId'] != 4663 or not pin['broadcast']:
            raise RuntimeError('Intent stage/chain mismatch')
        verify(args.stage, pin)
        return
    block = rpc('eth_getBlockByNumber', ['latest', False])
    probe = '0x000000000000000000000000000000000000dead'
    native = int(rpc('eth_call', [{'to': probe, 'data': '0x'}, block['number'],
                     {probe: {'code': '0x4360005260206000f3'}}]), 16)
    if not 0 < native <= int(block['number'], 16):
        raise RuntimeError('Unexpected native clock')
    pin = {'chainId': 4663, 'stateBlock': int(block['number'], 16), 'blockHash': block['hash'],
           'nativeEvmBlockNumber': native, 'stage': args.stage, 'broadcast': args.broadcast,
           'secondaryReplaySkipped': native != int(block['number'], 16),
           'stockSupplyRaw': '1000000000000000', 'dollarBorrowRaw': '50000',
           'repaymentApprovalCapRaw': '51000'}
    print(json.dumps(pin, indent=2), flush=True)
    env = dict(os.environ, ACCEPTANCE_NATIVE_BLOCK=str(native), FOUNDRY_PROFILE='vault_upgrade')
    signature = ENTRYPOINTS[args.stage] + '()'
    command = ['forge', 'script', 'remediation/script/LendingAcceptance.s.sol:LendingAcceptance',
               '--sig', signature, '--rpc-url', RPC, '--sender', GOVERNOR, '-vvv']
    if pin['secondaryReplaySkipped']:
        # The initial script simulation remains mandatory and vm.roll uses the probed native clock.
        # Foundry's secondary replay uses the RPC height and invents excess accrued interest.
        command += ['--skip-simulation']
    if not args.broadcast:
        command += ['--fork-block-number', str(pin['stateBlock'])]
        result = subprocess.run(command, cwd=ROOT, env=env)
        pin['exitCode'] = result.returncode
        pin['scope'] = 'Local simulation only; no signing or mainnet transactions'
        save(ROOT / ('remediation/evidence/lending-acceptance-' + args.stage + '-simulation.json'), pin)
        if result.returncode:
            print('SIMULATION FAILED. Do not broadcast. Resolve the reported cause (including stale guard prices) and simulate again.', flush=True)
        raise SystemExit(result.returncode)
    # An interruption is deliberately not retried automatically: inspect public receipts first.
    intent = ROOT / ('remediation/evidence/lending-acceptance-' + args.stage + '-intent.json')
    lock_path = ROOT.parent / 'deployments/.robinhood-deployer-signing.lock'
    if not lock_path.parent.is_dir():
        lock_path = ROOT / 'remediation/evidence/.governor-signing.lock'
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if intent.exists():
            raise RuntimeError('Stage previously attempted. Review receipts/state; do not delete the intent or blindly resume')
        nonce = rpc('eth_getTransactionCount', [GOVERNOR, 'latest'])
        if rpc('eth_getTransactionCount', [GOVERNOR, 'pending']) != nonce:
            raise RuntimeError('Pending signer transaction; reconcile first')
        pin['nonceBefore'] = int(nonce, 16)
        pin['before'] = snapshot()
        if args.stage == 'close':
            opened_path = ROOT / 'remediation/evidence/lending-acceptance-open-verified.json'
            if not opened_path.exists() or not json.loads(opened_path.read_text()).get('verified'):
                raise RuntimeError('Opening must be independently verified before this close command')
        save(intent, pin)
        command += ['--account', 'robinhood-deployer', '--broadcast', '--slow']
        result = subprocess.run(command, cwd=ROOT, env=env)
        pin['exitCode'] = result.returncode
        pin['scope'] = 'User-local signing attempt. Independent receipt/event/state verification still required.'
        save(intent, pin)
        if result.returncode:
            raise RuntimeError('Stage failed or interrupted. Keep broadcast files; do not retry or use --resume blindly')
        verify(args.stage, pin)
        print('Stage complete. Keep all evidence and broadcast files; do not repeat this stage.', flush=True)


if __name__ == '__main__':
    main()
