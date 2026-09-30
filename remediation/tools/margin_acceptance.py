"""User-local staged margin acceptance. Default: fork simulation; --verify never signs."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from state import ROOT, GOVERNOR, MARKETS, call, cast, save
from record_vault_queue import rpc

RPC_URL = 'https://rpc.mainnet.chain.robinhood.com'
BASE = ROOT / 'contracts/robinhood-vaults'
MANIFEST = json.loads((BASE / 'frontend/margin-mainnet/manifest.json').read_text())
A = MANIFEST['marginAddresses']
PUSD = MARKETS['pUSDG']
PSTOCK = MARKETS['pNVDA']
SHARES = 1_000_000_000
ENTRY = {'roundtrip': 'run', 'open': 'openTest', 'close': 'closeTest', 'withdraw': 'withdrawTest'}
SLOT = '0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc'
OPEN_EVENT = 'PositionOpened(uint256,address,address,uint8,address,address,address,uint256,uint256,uint256,uint256,uint256)'
CLOSE_EVENT = 'PositionClosed(uint256,uint16,uint256,uint256,uint256,uint256,bool)'
SCRIPT = 'remediation/script/MarginAcceptance.s.sol:MarginAcceptance'


def path(side, stage, suffix):
    return ROOT / ('remediation/evidence/margin-acceptance-' + side + '-' + stage + '-' + suffix + '.json')


def words(target, signature, *args, block='latest'):
    data = call(target, signature, *args, block=block)[2:]
    if len(data) % 64:
        raise RuntimeError('Unexpected ABI words')
    return [int(data[i:i+64], 16) for i in range(0, len(data), 64)]


def address(word):
    return '0x' + format(word, '040x')


def snapshot(tag, position_id=None):
    block = rpc('eth_getBlockByNumber', [tag, False]); tag = block['number']
    def number(target, sig, *args):
        return words(target, sig, *args, block=tag)[0]
    out = {'block': int(tag, 16), 'hash': block['hash'],
           'walletShares': number(PUSD, 'balanceOf(address)', GOVERNOR),
           'free': number(A['marginVault'], 'freeBalance(address,address)', GOVERNOR, PUSD),
           'locked': number(A['marginVault'], 'lockedBalance(address,address)', GOVERNOR, PUSD),
           'allowance': number(PUSD, 'allowance(address,address)', GOVERNOR, A['marginVault']),
           'exchangeRate': number(PUSD, 'exchangeRateStored()')}
    if position_id is not None:
        p = words(A['executor'], 'positions(uint256)', position_id, block=tag)
        if len(p) != 12:
            raise RuntimeError('Unexpected position ABI')
        account = address(p[2])
        out['position'] = {'id': p[0], 'owner': address(p[1]), 'account': account,
                           'margin': address(p[3]), 'asset': address(p[4]), 'debtMarket': address(p[5]),
                           'locked': p[6], 'side': p[10], 'status': p[11], 'requestedLeverage': p[9],
                           'dollarDebt': number(PUSD, 'borrowBalanceStored(address)', account),
                           'stockDebt': number(PSTOCK, 'borrowBalanceStored(address)', account),
                           'dollarShares': number(PUSD, 'balanceOf(address)', account),
                           'stockShares': number(PSTOCK, 'balanceOf(address)', account),
                           'metrics': words(A['riskEngine'], 'getMetrics(address)', account, block=tag) if p[11] == 2 else None}
    if rpc('eth_getBlockByNumber', [tag, False])['hash'] != block['hash']:
        raise RuntimeError('Snapshot block changed')
    return out


def verify_identity(tag):
    baseline = json.loads((BASE / 'deployments/margin-mainnet-live/active-verification.json').read_text())
    contracts = baseline['contracts']
    def check(item):
        name, c = item
        if cast('keccak', rpc('eth_getCode', [c['address'], tag])) != c['runtimeCodeHash']:
            raise RuntimeError('Review changed margin runtime: ' + name)
        if name in A and A[name].lower() != c['address'].lower():
            raise RuntimeError('Manifest address changed: ' + name)
        if name + 'Implementation' in contracts:
            target = '0x' + rpc('eth_getStorageAt', [c['address'], SLOT, tag])[-40:]
            if target.lower() != contracts[name+'Implementation']['address'].lower():
                raise RuntimeError('Review changed margin implementation: ' + name)
        return name
    with ThreadPoolExecutor(max_workers=4) as pool:
        checked = list(pool.map(check, contracts.items()))
    historical = json.loads((BASE/'deployments/margin-mainnet-5x-live/status.json').read_text())
    for side, pair in MANIFEST['pairs'].items():
        actual = words(A['config'], 'getPairRisk(address,address,address)',
                       pair['marginPToken'], pair['positionPToken'], pair['debtPToken'], block=tag)
        if actual != historical['directions'][side]['risk']:
            raise RuntimeError('Review changed risk parameters: ' + side)
    return checked


def keeper_health():
    tool = ROOT.parent / 'infra/app-platform/signer.py'
    result = subprocess.run([sys.executable, str(tool), 'status'], capture_output=True, text=True, check=True)
    health = json.loads(result.stdout)
    if (not health.get('executionEnabled') or not health.get('gasReady')
            or health.get('status') != 'monitoring'
            or health.get('sender', '').lower() != '0x16aec17597e5224998e2043c9c83c4a35dd95a86'
            or not 0 <= time.time() - health['checkedAtUnix'] <= 120):
        raise RuntimeError('Keeper is not freshly ready; opening refused')
    return health


def journal(stage, dry=False):
    folder = ROOT / 'broadcast/MarginAcceptance.s.sol/4663'
    if dry:
        folder = folder / 'dry-run'
    return folder / (ENTRY[stage] + '-latest.json')


def normalize_plan(data):
    return [{'to': tx['transaction']['to'].lower(),
             'input': tx['transaction'].get('input', tx['transaction'].get('data')).lower(),
             'value': int(str(tx['transaction']['value']), 16) if isinstance(tx['transaction']['value'], str) else tx['transaction']['value']}
            for tx in data['transactions']]


def validate_plan(stage, plan, side, position_id=None, withdrawal=None):
    expected = {
        'open': [(PUSD, 'approve(address,uint256)', [A['marginVault'], str(SHARES)]),
                 (A['marginVault'], 'deposit(address,uint256)', [PUSD, str(SHARES)]),
                 (PUSD, 'approve(address,uint256)', [A['marginVault'], '0']),
                 (A['executor'], 'openPosition((address,address,address,uint256,uint16,uint256,uint256,uint8,bytes))', None)],
        'close': [(A['executor'], 'closePosition((uint256,uint16,uint256,uint256,uint256,bytes,bytes))', None)],
        'withdraw': [(A['marginVault'], 'withdraw(address,uint256)', None)]}[stage]
    if len(plan) != len(expected):
        raise RuntimeError('Unexpected planned transaction count')
    for item, (target, signature, args) in zip(plan, expected):
        expected_input = cast('calldata', signature, *args) if args is not None else cast('sig', signature)
        if (item['to'] != target.lower() or item['value'] != 0
                or not item['input'].startswith(expected_input.lower())
                or (args is not None and item['input'] != expected_input.lower())):
            raise RuntimeError('Unexpected action in simulated plan')
    # Independently constrain every dynamic parameter, as well as matching the exact simulated calldata later.
    target, signature, _ = expected[-1]
    decoded = json.loads(cast('decode-calldata', signature, plan[-1]['input'], '--json'))
    if stage == 'open':
        p = decoded[0]; pair = MANIFEST['pairs'][side]
        ok = ([x.lower() for x in p[:3]] == [pair[k].lower() for k in ('marginPToken','positionPToken','debtPToken')]
              and list(map(int,p[3:6])) == [SHARES,200,0] and int(p[6]) > 0
              and int(p[7]) == pair['side'] and p[8] == '0x')
    elif stage == 'close':
        p = decoded[0]
        ok = (list(map(int,p[:3])) == [position_id,10000,0] and int(p[3]) > 0
              and int(p[4]) == (0 if side == 'short' else 180000) and p[5:] == ['0x','0x'])
    else:
        ok = decoded[0].lower() == PUSD.lower() and int(decoded[1]) == withdrawal and withdrawal > 0
    if not ok:
        raise RuntimeError('Dynamic action parameters exceed the acceptance scope')


def match_event(receipt, emitter, signature):
    topic = cast('keccak', signature)
    matches = [log for log in receipt['logs'] if log['address'].lower() == emitter.lower()
               and log['topics'] and log['topics'][0] == topic]
    if len(matches) != 1:
        raise RuntimeError('Expected exactly one ' + signature)
    return matches[0]


def validate_post(side, stage, before, after, position_id, account):
    p = after['position']; pair = MANIFEST['pairs'][side]
    if (p['id'] != position_id or p['owner'] != GOVERNOR.lower() or p['account'] != account.lower()
            or p['margin'] != PUSD.lower() or p['asset'] != pair['positionPToken'].lower()
            or p['debtMarket'] != pair['debtPToken'].lower() or p['side'] != pair['side']
            or p['requestedLeverage'] != 200 or after['allowance'] != 0):
        raise RuntimeError('Position identity/direction/approval mismatch')
    if stage == 'open':
        metrics = p['metrics']
        debt = p['stockDebt'] if side == 'short' else p['dollarDebt']
        other_debt = p['dollarDebt'] if side == 'short' else p['stockDebt']
        if not (p['status'] == 2 and after['locked'] == SHARES and p['locked'] == SHARES
                and after['free'] == 0 and after['walletShares'] == before['walletShares'] - SHARES
                and debt > 0 and other_debt == 0 and 0 < metrics[1] <= 10**18
                and metrics[0] <= 2*10**18 and metrics[5] > 10000 and metrics[6] <= 200):
            raise RuntimeError('Opening state or risk bounds differ from plan')
    else:
        if (p['status'] != 5 or p['dollarDebt'] or p['stockDebt'] or p['locked']
                or p['dollarShares'] or p['stockShares'] or after['locked']):
            raise RuntimeError('Close left debt, position shares or locked margin')
        if stage == 'close':
            if not (after['free'] > 0 and after['walletShares'] == before['walletShares']
                    and after['free'] * after['exchangeRate'] // 10**18 >= 179999):
                raise RuntimeError('Close returned less than the stated floor (one raw USDG rounding unit allowed)')
        elif after['free'] or after['walletShares'] != before['walletShares'] + before['free']:
            raise RuntimeError('Withdrawal balance mismatch')


def verify(side, stage, intent):
    if (intent['side'] != side or intent['stage'] != stage or intent['chainId'] != 4663
            or not intent.get('broadcast')):
        raise RuntimeError('Intent mismatch')
    archive = path(side, stage, 'broadcast')
    data = json.loads((archive if archive.exists() else journal(stage)).read_text())
    plan = intent['plan']; validate_plan(stage, plan, side, intent.get('positionId'), intent['before']['free'])
    if normalize_plan(data) != plan:
        raise RuntimeError('Broadcast differs from the pre-signing simulated plan')
    save(archive, data)
    receipts = []
    for i, (entry, expected) in enumerate(zip(data['transactions'], plan)):
        tx = rpc('eth_getTransactionByHash', [entry['hash']])
        receipt = rpc('eth_getTransactionReceipt', [entry['hash']])
        if (not tx or not receipt or int(receipt['status'], 16) != 1
                or tx['from'].lower() != GOVERNOR.lower() or tx['to'].lower() != expected['to']
                or tx['input'].lower() != expected['input'] or int(tx['value'], 16) != 0
                or int(tx['chainId'], 16) != 4663 or int(tx['nonce'], 16) != intent['nonceBefore']+i
                or receipt['transactionHash'].lower() != entry['hash'].lower()
                or int(receipt['blockNumber'], 16) < intent['stateBlock']):
            raise RuntimeError('Receipt does not match the signed intent')
        block = rpc('eth_getBlockByNumber', [receipt['blockNumber'], False])
        if receipt['blockHash'] != block['hash'] or tx['blockHash'] != block['hash']:
            raise RuntimeError('Noncanonical receipt')
        receipts.append(receipt)
    if stage == 'open':
        event = match_event(receipts[-1], A['executor'], OPEN_EVENT)
        if len(event['topics']) != 4 or address(int(event['topics'][2],16)) != GOVERNOR.lower():
            raise RuntimeError('Unexpected opening owner/topics')
        position_id = int(event['topics'][1],16); account = address(int(event['topics'][3],16))
        deposited = match_event(receipts[1], A['marginVault'], 'Deposited(address,address,uint256)')
        if (len(deposited['topics']) != 3 or address(int(deposited['topics'][1],16)) != GOVERNOR.lower()
                or address(int(deposited['topics'][2],16)) != PUSD.lower() or int(deposited['data'],16) != SHARES):
            raise RuntimeError('Deposit event mismatch')
    else:
        position_id, account = intent['positionId'], intent['account']
        if stage == 'close':
            event = match_event(receipts[-1], A['executor'], CLOSE_EVENT)
            if len(event['topics']) != 2 or int(event['topics'][1],16) != position_id:
                raise RuntimeError('Close event position mismatch')
        else:
            event = match_event(receipts[-1], A['marginVault'], 'Withdrawn(address,address,uint256)')
            if (len(event['topics']) != 3 or address(int(event['topics'][1],16)) != GOVERNOR.lower()
                    or address(int(event['topics'][2],16)) != PUSD.lower()
                    or int(event['data'],16) != intent['before']['free']):
                raise RuntimeError('Withdraw event mismatch')
    after = snapshot('latest', position_id)
    if after['block'] < int(receipts[-1]['blockNumber'],16):
        raise RuntimeError('Post-state predates receipts')
    validate_post(side, stage, intent['before'], after, position_id, account)
    report = dict(intent, verified=True, positionId=position_id, account=account, after=after, receipts=receipts,
                  scope='Exact public receipts and state; single-RPC canonical L2 verification. Operator acceptance, not independent adoption or L1 finality proof.')
    save(path(side, stage, 'verified'), report)
    print(side.upper()+' '+stage.upper()+' VERIFIED; position '+str(position_id), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--side', choices=('long','short'), required=True)
    parser.add_argument('--stage', choices=tuple(ENTRY), default='roundtrip')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--broadcast', action='store_true')
    mode.add_argument('--verify', action='store_true')
    args = parser.parse_args(); side, stage = args.side, args.stage
    if int(rpc('eth_chainId', []),16) != 4663:
        raise RuntimeError('Wrong chain')
    if stage == 'roundtrip' and (args.broadcast or args.verify):
        raise RuntimeError('Roundtrip is simulation-only; sign open/close/withdraw separately')
    intent_path = path(side, stage, 'intent')
    if args.verify:
        verify(side,stage,json.loads(intent_path.read_text())); return
    if args.broadcast and not sys.stdin.isatty():
        raise RuntimeError('Only run --broadcast in your own terminal; Foundry prompts locally')
    if args.broadcast and intent_path.exists():
        raise RuntimeError('Existing attempt: use --verify or inspect partial receipts; never blindly retry')
    position_id, account = None, None
    if stage in ('close','withdraw'):
        prior = path(side, 'open' if stage == 'close' else 'close', 'verified')
        prev = json.loads(prior.read_text())
        if not prev.get('verified') or prev['side'] != side:
            raise RuntimeError('Prior stage not verified')
        position_id, account = prev['positionId'], prev['account']
    block = rpc('eth_getBlockByNumber', ['latest',False]); tag=block['number']
    probe='0x000000000000000000000000000000000000dead'
    native=int(rpc('eth_call',[{'to':probe,'data':'0x'},tag,{probe:{'code':'0x4360005260206000f3'}}]),16)
    if not 0 < native <= int(tag,16):
        raise RuntimeError('Unexpected native clock')
    checked=verify_identity(tag)
    before=snapshot(tag,position_id)
    pin={'chainId':4663,'side':side,'stage':stage,'stateBlock':int(tag,16),'blockHash':block['hash'],
         'nativeBlock':native,'broadcast':args.broadcast,'before':before,'positionId':position_id,
         'account':account,'verifiedRuntimeNames':checked,'collateralShares':SHARES,'requestedLeverageX100':200,
         'scriptSha256':hashlib.sha256((ROOT/'remediation/script/MarginAcceptance.s.sol').read_bytes()).hexdigest()}
    if stage in ('roundtrip','open'):
        if before['free'] or before['locked'] or before['allowance'] or before['walletShares'] < SHARES:
            raise RuntimeError('Existing custody/approval or insufficient shares: inspect before opening')
        if args.broadcast:
            pin['keeperHealth']=keeper_health()
    env=dict(os.environ,FOUNDRY_PROFILE='vault_upgrade',MARGIN_ACCEPTANCE_NATIVE_BLOCK=str(native),
             MARGIN_ACCEPTANCE_SHORT=str(side=='short').lower(),MARGIN_ACCEPTANCE_POSITION_ID=str(position_id or 0))
    command=['forge','script',SCRIPT,'--sig',ENTRY[stage]+'()','--rpc-url',RPC_URL,
             '--fork-block-number',str(int(tag,16)),'--sender',GOVERNOR,'--skip-simulation','-vvv']
    print(json.dumps({k:pin[k] for k in ('chainId','side','stage','stateBlock','nativeBlock','collateralShares','requestedLeverageX100')},indent=2),flush=True)
    # Mandatory initial script simulation, pinned native clock. Only incompatible secondary replay is skipped.
    result=subprocess.run(command,cwd=ROOT,env=env)
    simulation=dict(pin,broadcast=False,exitCode=result.returncode,scope='No signing; complete initial script simulation at native EVM clock')
    save(path(side,stage,'simulation'),simulation)
    if result.returncode:
        raise RuntimeError('Simulation failed. Nothing signed; do not broadcast or weaken guards')
    if not args.broadcast:
        return
    dry=json.loads(journal(stage,True).read_text()); plan=normalize_plan(dry)
    validate_plan(stage,plan,side,position_id,before['free'])
    lock_path=ROOT.parent/'deployments/.robinhood-deployer-signing.lock'
    if not lock_path.parent.is_dir():
        lock_path=ROOT/'remediation/evidence/.governor-signing.lock'
    with lock_path.open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if intent_path.exists():
            raise RuntimeError('Another attempt exists; inspect it')
        nonce=int(rpc('eth_getTransactionCount',[GOVERNOR,'latest']),16)
        if int(rpc('eth_getTransactionCount',[GOVERNOR,'pending']),16)!=nonce:
            raise RuntimeError('Pending signer transaction')
        if int(str(dry['transactions'][0]['transaction']['nonce']),16)!=nonce:
            raise RuntimeError('Signer nonce changed after simulation')
        # Snapshot includes block/hash: compare economically relevant values only.
        current=snapshot('latest',position_id)
        if {k:v for k,v in current.items() if k not in ('block','hash')} != {k:v for k,v in before.items() if k not in ('block','hash')}:
            raise RuntimeError('Account state changed after simulation; re-simulate')
        if stage=='open':
            pin['keeperHealth']=keeper_health()
        pin.update(plan=plan,nonceBefore=nonce)
        save(intent_path,pin)
        result=subprocess.run(command+['--account','robinhood-deployer','--broadcast','--slow'],cwd=ROOT,env=env)
        pin['exitCode']=result.returncode; save(intent_path,pin)
        if result.returncode:
            raise RuntimeError('Broadcast failed or partial. Keep journals and intent; do not repeat or blindly resume')
        verify(side,stage,pin)


if __name__=='__main__':
    main()
