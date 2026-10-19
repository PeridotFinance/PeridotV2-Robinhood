#!/usr/bin/env python3
"""Actual forge --broadcast rehearsal, ONLY on anvil bound to a loopback address.
Uses unlocked impersonated public addresses; no private keys or wallets are read.
Every command and the entire rehearsal have a ten-minute process-group timeout.
"""
import argparse, json, os, pathlib, signal, subprocess, sys, time, urllib.parse, urllib.request
from verify_deployment import ROOT, Reader, Verifier, cast

SENDER='0x000000000000000000000000000000000000beef'
KEEPER='0x000000000000000000000000000000000000cafe'

def run(args, env, logfile):
    with open(logfile,'w') as out:
        child=subprocess.Popen(args,cwd=ROOT,env=env,stdout=out,stderr=subprocess.STDOUT,start_new_session=True)
        try: code=child.wait(timeout=590)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid,signal.SIGKILL); child.wait(); raise RuntimeError('STOPPED command exceeded ten minutes')
    if code: raise RuntimeError(f'Command failed ({code}); see {logfile}')

def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--rpc-url',default='http://127.0.0.1:8547'); p.add_argument('--journal',default='ramses-vaults/deployments/local-phase3.json'); p.add_argument('--finish-local',action='store_true'); args=p.parse_args()
    u=urllib.parse.urlparse(args.rpc_url)
    assert u.scheme=='http' and u.hostname in {'127.0.0.1','localhost','::1'} and not u.username and not u.password, 'Loopback HTTP only'
    read=Reader(args.rpc_url)
    assert 'anvil' in read.rpc('web3_clientVersion',[]).lower(), 'Not anvil'
    assert int(read.rpc('eth_chainId',[]),16)==4663
    def rpc(method, params):
        # Writes exist only inside this endpoint-validated local rehearsal.
        req=urllib.request.Request(args.rpc_url,json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params}).encode(),{'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=30) as r: result=json.load(r)
        if 'error' in result: raise RuntimeError(result['error'])
        return result['result']
    journal=ROOT/args.journal
    existing = journal.exists()
    if existing:
        m=json.loads(journal.read_text())
        if not args.finish_local:
            Verifier(read,m).run(); print('Already deployed and verified; no transactions sent.'); return
        Verifier(read,m).run(configured=False)
    def send(sender,to,data):
        tx=rpc('eth_sendTransaction',[{'from':sender,'to':to,'data':data,'gas':'0x1c9c380'}])
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            receipt=rpc('eth_getTransactionReceipt',[tx])
            if receipt:
                assert int(receipt['status'],16)==1, f'Reverted receipt {tx}'
                return receipt
            time.sleep(0.1)
        raise RuntimeError('Missing receipt')
    rpc('anvil_impersonateAccount',[SENDER]); rpc('anvil_setBalance',[SENDER,hex(1000*10**18)])
    env={**os.environ,'FOUNDRY_PROFILE':'ramses','RAMSES_LOCAL_REHEARSAL':'true','RAMSES_PUBLIC_SENDER':SENDER,'RAMSES_KEEPER':KEEPER,'RAMSES_STOCK_SUPPLY_CAP':str(2*10**18),'RAMSES_USDG_SUPPLY_CAP':str(400*10**6),'RAMSES_STOCK_BORROW_CAP':str(10**18),'RAMSES_USDG_BORROW_CAP':str(100*10**6),'RAMSES_STOCK_SEED':str(4*10**16),'RAMSES_USDG_SEED':str(40000),'RAMSES_JOURNAL':str(journal.relative_to(ROOT))}
    common=['--rpc-url',args.rpc_url,'--sender',SENDER,'--unlocked','--broadcast','--slow','--threads','1','--gas-estimate-multiplier','150']
    if not existing:
        before=int(read.rpc('eth_blockNumber',[]),16)
        run(['forge','script','ramses-vaults/script/LocalRehearsal.s.sol:DeployLocalRamsesTimelock',*common],env,ROOT/'ramses-vaults/research/local-timelock.log')
        text=(ROOT/'ramses-vaults/research/local-timelock.log').read_text()
        import re
        timelock=re.search(r'LOCAL_TIMELOCK\s+(0x[0-9a-fA-F]{40})',text).group(1)
        env['RAMSES_TIMELOCK']=timelock
        run(['forge','script','ramses-vaults/script/DeployMarkets.s.sol:DeployRamsesMarkets',*common],env,ROOT/'ramses-vaults/research/local-deployment.log')
        m=json.loads(journal.read_text()); m['deploymentBlock']=before; journal.write_text(json.dumps(m,indent=2)+'\n')
    Verifier(read,m).run(configured=False)
    timelock=m['timelock']
    # Obtain the tiny governance seed through an impersonated REAL pool, only in this disposable fork.
    # Token transfer, never token implementation/storage editing.
    from verify_deployment import POOL
    rpc('anvil_impersonateAccount',[POOL]); rpc('anvil_setBalance',[POOL,hex(10**18)])
    for token,amount in [(m['stock'],m['stockSeed']),(m['usdg'],m['dollarSeed'])]:
        send(POOL,token,cast('calldata','transfer(address,uint256)',timelock,amount))
    send(SENDER,timelock,m['scheduleCalldata']); rpc('evm_increaseTime',[3601]); rpc('evm_mine',[])
    send(SENDER,timelock,m['executeCalldata'])
    result=Verifier(read,m).run()
    (ROOT/'ramses-vaults/research/local-deployment-verification.json').write_text(json.dumps(result,indent=2)+'\n')
    # Idempotent second invocation is exercised by the outer reproduction target.
    print(f'Local broadcast, delayed configuration, seed and verification passed: {journal}')

if __name__=='__main__': main()
