"""Execute the reviewed stages ONLY on a disposable loopback Anvil fork.

No private keys are read. No mainnet transaction-submission RPC exists in this runner.
Wallet funding, time advances and the keeper feed shock are explicit local fixtures.
"""
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
from rpc import ROOT, call, cast, words, rpc
from preflight import ADDRESSES
from keeper import RpcBackend, run as run_keeper, loopback

URL = 'http://127.0.0.1:8556'
RECORD = ROOT/'deployments/robinhood-mainnet.margin-rehearsal-addresses.json'
HISTORY = ROOT/'deployments/robinhood-mainnet.margin-rehearsal-history.json'
RESULT = ROOT/'deployments/robinhood-mainnet.margin-rehearsal.json'
ACTOR = ADDRESSES['actor']


def local(method, params):
    assert loopback(URL)
    request = urllib.request.Request(URL, json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params}).encode(),
                                    headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request, timeout=40) as response:
        result = json.load(response)
    if 'error' in result:
        raise RuntimeError(result['error'])
    return result['result']


def native_height():
    target = ADDRESSES['usd']
    # A read-only call override probes NUMBER without deploying a contract.
    return int(local('eth_call', [{'to': target, 'data':'0x'}, 'latest',
                                 {target: {'code':'0x4360005260206000f3'}}]), 16)


def wallet_balance(token, amount):
    data = cast('calldata', 'balanceOf(address)', ACTOR)
    before = words(call(token, 'balanceOf(address)', ACTOR, url=URL))[0]
    assert before > 0, 'This trace-based fixture expects an existing funded actor'
    trace = local('debug_traceCall', [{'to':token,'data':data}, 'latest',
                                    {'disableMemory':True,'disableStorage':True}])
    slots = {'0x'+step['stack'][-1].removeprefix('0x').zfill(64)
             for step in trace['structLogs'] if step['op']=='SLOAD'}
    matches = [slot for slot in slots if int(local('eth_getStorageAt',[token,slot,'latest']),16)==before]
    assert len(matches)==1, 'Ambiguous actor balance slot'
    local('anvil_setStorageAt', [token,matches[0],'0x'+format(amount,'064x')])
    assert words(call(token,'balanceOf(address)',ACTOR,url=URL))[0]==amount
    return {'token':token,'actor':ACTOR,'before':before,'after':amount,'slot':matches[0]}


def advance(seconds):
    now=int(local('eth_getBlockByNumber',['latest',False])['timestamp'],16)
    local('evm_setNextBlockTimestamp',[now+seconds])
    local('evm_mine',[])


def phase(name, signature, args, results):
    print(name, 'simulate and execute on localhost', flush=True)
    env=os.environ.copy()
    env.update(FOUNDRY_PROFILE='margin_mainnet', MARGIN_EVM_BLOCK_NUMBER=str(native_height()),
               MARGIN_RECORD=str(RECORD.relative_to(ROOT)), MARGIN_HISTORY=str(HISTORY.relative_to(ROOT)),
               FOUNDRY_BROADCAST='/tmp/rh-mainnet-margin-local-broadcast')
    command=['forge','script','margin-mainnet/script/PrepareRobinhoodMainnetMargin.s.sol:PrepareRobinhoodMainnetMargin',
             '--sig',signature,*args,'--skip','test','--rpc-url',URL,'--sender',ACTOR,
             '--unlocked','--broadcast','--slow','--skip-simulation','--gas-estimate-multiplier','300','--non-interactive']
    with Path('/tmp/rh-mainnet-'+name+'.log').open('w') as log:
        subprocess.run(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
    broadcast=Path('/tmp/rh-mainnet-margin-local-broadcast/PrepareRobinhoodMainnetMargin.s.sol/4663')/(signature.split('(')[0]+'-latest.json')
    data=json.loads(broadcast.read_text())
    txs=[]
    for tx in data['transactions']:
        if not tx.get('hash'): continue
        receipt=local('eth_getTransactionReceipt',[tx['hash']])
        assert receipt and receipt['status']=='0x1', 'Failed or missing local receipt'
        txs.append({'hash':tx['hash'],'function':tx.get('function'),'contractName':tx.get('contractName'),
                    'transaction':tx['transaction'],'receipt':receipt})
    assert txs
    results['phases'].append({'phase':name,'transactions':txs,'nativeEvmBlockNumber':native_height()})
    RESULT.write_text(json.dumps(results,indent=2)+'\n')


def feed_code(price, timestamp):
    # Test-only feed fixture: decimals() -> 8; latestRoundData() -> the explicit scenario.
    push=lambda value: b'\x7f'+int(value).to_bytes(32,'big')
    body=bytes.fromhex('6001600052')+push(price)+bytes.fromhex('602052')+push(timestamp)+bytes.fromhex('604052')+push(timestamp)+bytes.fromhex('606052600160805260a06000f3')
    prefix=bytes.fromhex('60003560e01c63313ce5671460')
    destination=len(prefix)+2+len(body)
    return '0x'+(prefix+bytes([destination])+b'\x57'+body+bytes.fromhex('5b600860005260206000f3')).hex()


def main():
    assert not RESULT.exists(), 'Rehearsal record exists; inspect before an explicit fresh reset'
    pin=json.loads((ROOT/'deployments/robinhood-mainnet.margin-pin.json').read_text())
    history=json.loads((ROOT/'deployments/robinhood-mainnet.margin-borrower-history.json').read_text())
    assert history['throughBlock']==pin['stateBlock'] and history['borrowEventCount']==0
    local('anvil_reset',[{'forking':{'jsonRpcUrl':pin['rpc'],'blockNumber':pin['stateBlock']}}])
    assert int(local('eth_chainId',[]),16)==4663
    dumped=json.loads(gzip.decompress(bytes.fromhex(local('anvil_dumpState',[])[2:])))
    dumped['block']['number']=hex(pin['nativeEvmBlockNumber'])
    dumped['best_block_number']=pin['nativeEvmBlockNumber']
    local('anvil_loadState',['0x'+gzip.compress(json.dumps(dumped).encode()).hex()])
    local('evm_mine',[])
    assert native_height()==pin['nativeEvmBlockNumber']+1
    local('anvil_impersonateAccount',[ACTOR])
    local('anvil_setBalance',[ACTOR,hex(10*10**18)])
    funding=[wallet_balance(ADDRESSES['usd'],20_000_000),wallet_balance(ADDRESSES['stock'],10**18)]
    results={'chainId':4663,'surface':'LOCALHOST FORK ONLY: these are NOT public-mainnet receipts',
             'status':'running','upstreamPin':pin,'walletFundingFixtures':funding,
             'nativeGasFundingWei':10*10**18,'phases':[]}
    phase('pause','pauseBorrowing()',[],results)
    assert all(words(call(ADDRESSES['controller'],'borrowGuardianPaused(address)',ADDRESSES[m],url=URL))[0] for m in ['pUsd','pStock'])
    HISTORY.write_text(json.dumps({**history,'postPauseSnapshot':True,'surface':'local fork confirmed pauses; upstream history through source pin'},indent=2)+'\n')
    phase('migration','migrateMarkets()',[],results)
    phase('deployment','deployPaused()',[],results)
    advance(3601)
    phase('risk','applyRisk()',[],results)
    phase('funding','fundCanary()',[],results)
    phase('queue','queueActivation()',[],results)
    advance(3601)
    phase('activation','activate()',[],results)
    for side, number in [('long',1),('short',2)]:
        phase(side+'-open','openCanary(bool)',['true' if side=='short' else 'false'],results)
        phase(side+'-close','closeCanary(uint256)',[str(number)],results)
        phase(side+'-withdraw','withdrawCanary()',[],results)
    phase('keeper-open','openCanary(bool)',['false'],results)
    addresses=json.loads(RECORD.read_text())
    backend=RpcBackend(URL,addresses,ACTOR)
    journal=ROOT/'deployments/robinhood-mainnet.margin-keeper-local-journal.json'
    assert not journal.exists(), 'Keeper journal already exists'
    healthy=run_keeper(backend,3,journal)
    assert healthy['status']=='not_executable_stop'
    original=local('eth_getCode',[ADDRESSES['feed'],'latest'])
    feed=words(call(ADDRESSES['feed'],'latestRoundData()',url=URL))
    now=int(local('eth_getBlockByNumber',['latest',False])['timestamp'],16)
    try:
        local('anvil_setCode',[ADDRESSES['feed'],feed_code(feed[1],now-13*3600)])
        stale=run_keeper(backend,3,journal,True)
        assert stale['status']=='stale_or_unavailable_stop' and not journal.exists()
        local('anvil_setCode',[ADDRESSES['feed'],feed_code(feed[1]*60//100,now)])
        liquidated=run_keeper(backend,3,journal,True)
        assert liquidated['status']=='resolved', liquidated
    finally:
        local('anvil_setCode',[ADDRESSES['feed'],original])
    results['keeper']={'scenario':'Explicit local oracle-only -40% shock; real pool unchanged. Real pool shocks are tested separately in Solidity.',
                       'healthy':healthy,'stale':stale,'liquidation':liquidated,'feedCodeRestored':True}
    free=words(call(addresses['marginVault'],'freeBalance(address,address)',ACTOR,ADDRESSES['pUsd'],url=URL))[0]
    if free: phase('keeper-withdraw','withdrawCanary()',[],results)
    phase('finish','finishCanary()',[],results)
    for market in ['pUsd','pStock']:
        assert words(call(ADDRESSES[market],'totalBorrows()',url=URL))[0]==0
        assert words(call(ADDRESSES[market],'totalBorrowShares()',url=URL))[0]==0
    assert words(call(addresses['config'],'opensPaused()',url=URL))[0]==1
    assert words(call(addresses['flashVault'],'paused()',url=URL))[0]==1
    assert all(words(call(addresses['marginVault'],f'{kind}Balance(address,address)',ACTOR,ADDRESSES['pUsd'],url=URL))[0]==0 for kind in ['free','locked'])
    results.update(status='passed',finalNativeEvmBlockNumber=native_height(),zeroDebtAndBorrowShares=True,
                   zeroFreeAndLockedMargin=True,opensAndFlashPaused=True)
    RESULT.write_text(json.dumps(results,indent=2)+'\n')
    print('LOCAL REHEARSAL PASSED; no public-mainnet transactions submitted.',flush=True)


if __name__=='__main__': main()
