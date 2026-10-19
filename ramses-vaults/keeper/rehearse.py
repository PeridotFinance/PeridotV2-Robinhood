"""Run real Worker cycles on an existing disposable loopback anvil. No keystore or keys.
Use tools/check_phase2.py to cap this process group to ten minutes. Initial deployment uses
real feed/pool/gauge; ONLY subsequent feed updates are a local double, with real pool swaps.
"""
import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
import worker as w
from build_manifest import build
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from verify_deployment import ROOT, OUT, POOL, FEED
from rehearse_markets import run, SENDER

DRIVER='0x000000000000000000000000000000000000f00d'


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--rpc-url',required=True); p.add_argument('--deployment',required=True)
    p.add_argument('--state',required=True); p.add_argument('--deploy',action='store_true')
    a=p.parse_args(); w.endpoint(a.rpc_url,True)
    c=w.Chain(a.rpc_url,SENDER,unlocked=True)
    if a.deploy:
        run([sys.executable,'ramses-vaults/tools/rehearse_markets.py','--rpc-url',a.rpc_url,'--journal',a.deployment],dict(os.environ),ROOT/'ramses-vaults/research/keeper-deploy.log')
    m=json.loads((ROOT/a.deployment).read_text())
    # Independent verifier before modifying any real feed code.
    from verify_deployment import Reader, Verifier
    Verifier(Reader(a.rpc_url),m).run()
    c.rpc('anvil_impersonateAccount',[SENDER]); c.rpc('anvil_setBalance',[SENDER,hex(10**20)])
    def send(sender,to,sig,*args):
        h=c.rpc('eth_sendTransaction',[{'from':sender,'to':to,'data':w.offline('calldata',sig,*args),'gas':hex(15_000_000)}])
        end=time.monotonic()+30
        while time.monotonic()<end:
            receipt=c.rpc('eth_getTransactionReceipt',[h])
            if receipt:
                w.require(int(receipt['status'],16)==1,'local setup transaction reverted: '+sig)
                return receipt
            time.sleep(.05)
        raise RuntimeError('local setup receipt timeout')
    initial=c.read(FEED,'latestRoundData()')[1]
    # Existing initial feed has already passed the production verifier above.
    for target,contract in [(FEED,'KeeperFeed'),(DRIVER,'KeeperForkDriver')]:
        code=json.loads((OUT/'KeeperForkDriver.sol'/(contract+'.json')).read_text())['deployedBytecode']['object']
        c.rpc('anvil_setCode',[target,code])
    send(SENDER,FEED,'setPrice(int256)',initial)
    # Fund only the local swap driver by editing the ERC20 balance slot, as Foundry deal does.
    # Do not withdraw a large test balance from the pool: that would break its real swap backing.
    for token,amount in [(m['stock'],100000*10**18),(m['usdg'],10000000*10**6)]:
        data=w.offline('calldata','balanceOf(address)',DRIVER)
        trace=c.rpc('debug_traceCall',[{'to':token,'data':data},'latest',{}])
        reads=[step['stack'][-1] for step in trace['structLogs'] if step['op']=='SLOAD']
        w.require(bool(reads),'cannot discover local token balance slot')
        slot='0x'+reads[-1].removeprefix('0x').zfill(64)
        c.rpc('anvil_setStorageAt',[token,slot,'0x'+format(amount,'064x')])
        w.require(c.uint(token,'balanceOf(address)',DRIVER)==amount,'local token deal failed')
    send(SENDER,DRIVER,'align(uint256)',initial*10**10)
    manifest=build(a.rpc_url,m)
    state=Path(a.state); state.mkdir(parents=True,exist_ok=True)
    (state/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    keeper=m['keeper']; c.rpc('anvil_impersonateAccount',[keeper]); c.rpc('anvil_setBalance',[keeper,hex(10**18)])
    k=w.Chain(a.rpc_url,keeper,unlocked=True)
    identity=hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest()
    journal=w.Journal(state/'journal.json',identity,k,manifest['bootstrapNonce'])
    worker=w.Worker(k,manifest,journal,True,max_gas_price=2_000_000_000)
    results=[]
    def check(name, condition):
        w.require(condition,'rehearsal assertion: '+name); results.append(name); print('PASS '+name,flush=True)
    def advance(seconds): c.rpc('evm_increaseTime',[seconds]); c.rpc('evm_mine',[])
    with w.signer_lock(keeper):
        check('first real harvest/checkpoint/delegates/rebalance/recenter cycle',worker.cycle()=='complete')
        position=k.read(m['adapter'],'positionState(bytes32)',m['pair'])
        check('real NFT with positive liquidity',position[0]>0 and position[1]>0)
        old_token=position[0]
        nonce=k.nonce()
        check('no-op cycle sends nothing',worker.cycle()=='noop' and k.nonce()==nonce)
        # Let the new NFT age before testing rewarded maintenance (real validator retained).
        advance(3601); send(SENDER,DRIVER,'shift(int24)',0)
        check('aged NFT periodic maintenance',worker.cycle()=='complete')
        advance(300)
        send(SENDER,DRIVER,'shift(int24)',60) # approximately 60 bps, less than recenter trigger
        before=journal.data['checkpointPrice']
        check('50bps price-move cycle before periodic deadline',worker.cycle()=='complete')
        check('checkpoint baseline follows moved oracle',journal.data['checkpointPrice']!=before)
        check('sub-trigger move retains NFT',k.read(m['adapter'],'positionState(bytes32)',m['pair'])[0]==old_token)
        advance(3601); send(SENDER,DRIVER,'shift(int24)',700)
        check('positive-direction recenter cycle',worker.cycle()=='complete')
        token=k.read(m['adapter'],'positionState(bytes32)',m['pair'])[0]
        check('positive-direction NFT replaced',token!=old_token); old_token=token
        advance(3601); send(SENDER,DRIVER,'shift(int24)',-1400)
        check('negative-direction recenter cycle',worker.cycle()=='complete')
        check('negative-direction NFT replaced',k.read(m['adapter'],'positionState(bytes32)',m['pair'])[0]!=old_token)
        ram='0x5173d45a1191ee33cbb7d8c7e65f21b04ed54802'
        check('real gauge rewards harvested into zero-NAV RAM bucket',k.uint(m['vault'],'externalRewards(bytes32,address)',m['pair'],ram)>0)
        # Pending recovery uses a real anvil pending transaction, mined only after restart.
        c.rpc('evm_setAutomine',[False])
        tx=worker.transaction('checkpoint',m['vault']); gas=int(c.rpc('eth_gasPrice',[]),16)
        tx.update(nonce=hex(k.nonce()),gasPrice=hex(gas))
        journal.data['pending']={'action':'checkpoint','nonce':k.nonce(),'target':m['vault'],'hash':None}; journal.save()
        h=k.submit(tx); journal.data['pending']['hash']=h; journal.save()
        restarted=w.Journal(journal.path,identity,k,manifest['bootstrapNonce']); recovered=w.Worker(k,manifest,restarted,True,max_gas_price=2_000_000_000)
        check('pending transaction stops real loop',recovered.cycle()=='pending')
        c.rpc('evm_mine',[]); c.rpc('evm_setAutomine',[True])
        check('restart recovers mined pending without resubmission',recovered.recover() and k.nonce()==int(tx['nonce'],16)+1)
        # Every refusal restores its snapshot; never leaves privileged role or changed code behind.
        for label,mutate in [
            ('runtime-code change',lambda: c.rpc('anvil_setCode',[m['adapter'],'0x6000'])),
            ('privileged keeper',lambda: send(m['timelock'],m['vault'],'grantRole(bytes32,address)',w.ZERO_ROLE,keeper)),
            ('timelock keeper',lambda: send(m['timelock'],m['timelock'],'grantRole(bytes32,address)',w.offline('keccak','PROPOSER_ROLE'),keeper)),
        ]:
            snap=c.rpc('evm_snapshot',[])
            try:
                c.rpc('anvil_impersonateAccount',[m['timelock']]); c.rpc('anvil_setBalance',[m['timelock'],hex(10**18)])
                mutate()
                try: recovered.cycle()
                except w.SafetyError: check('refuse '+label,True)
                else: check('refuse '+label,False)
            finally: c.rpc('evm_revert',[snap])
        advance(13*3600)
        check('closed-market stale-feed wait',recovered.cycle()=='wait')
    (state/'results.json').write_text(json.dumps({'checks':results,'count':len(results),'pinBlock':manifest['pinBlock'],
        'forkBlock':os.environ.get('REMEDIATION_FORK_BLOCK'),'nativeBlock':os.environ.get('REMEDIATION_NATIVE_BLOCK'),
        'keeperTransactions':k.nonce()-manifest['bootstrapNonce'],'simulatedSubsequentFeed':True},indent=2)+'\n')
    print('Keeper local fork rehearsal passed:',len(results),'checks')

if __name__=='__main__': main()
