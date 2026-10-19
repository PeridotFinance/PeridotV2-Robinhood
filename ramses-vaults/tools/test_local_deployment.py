#!/usr/bin/env python3
"""Fault-inject the deployment verifier on a disposable localhost anvil; restore each snapshot."""
import argparse, json, urllib.parse, urllib.request
from verify_deployment import ROOT, OUT, Reader, Verifier, cast

def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--rpc-url',required=True); p.add_argument('--journal',required=True); args=p.parse_args()
    url=urllib.parse.urlparse(args.rpc_url)
    assert url.scheme=='http' and url.hostname in {'localhost','127.0.0.1','::1'} and not url.username and not url.password
    reader=Reader(args.rpc_url)
    assert 'anvil' in reader.rpc('web3_clientVersion',[]).lower()
    assert int(reader.rpc('eth_chainId',[]),16)==4663
    m=json.loads((ROOT/args.journal).read_text())
    def rpc(method,params):
        req=urllib.request.Request(args.rpc_url,json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params}).encode(),{'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=30) as result: response=json.load(result)
        if 'error' in response: raise RuntimeError(response['error'])
        return response['result']
    def storage(target, artifact, label, value):
        layout=json.loads((OUT/(artifact+'.sol')/(artifact+'.json')).read_text())['storageLayout']
        x=next(x for x in layout['storage'] if x['label']==label)
        slot=hex(int(x['slot'])); old=int(reader.rpc('eth_getStorageAt',[target,slot,'latest']),16)
        size=int(layout['types'][x['type']]['numberOfBytes']); shift=8*x['offset']; mask=((1<<(8*size))-1)<<shift
        new=(old&~mask)|(value<<shift)
        rpc('anvil_setStorageAt',[target,slot,'0x'+f'{new:064x}'])
    def grant():
        rpc('anvil_impersonateAccount',[m['timelock']]); rpc('anvil_setBalance',[m['timelock'],hex(10**18)])
        tx=rpc('eth_sendTransaction',[{'from':m['timelock'],'to':m['guard'],'data':cast('calldata','grantRole(bytes32,address)',cast('keccak','CONFIG_ROLE'),m['sender']),'gas':'0x100000'}])
        receipt=rpc('eth_getTransactionReceipt',[tx]); assert int(receipt['status'],16)==1
    def borrow_cap():
        layout=json.loads((OUT/'Peridottroller.sol/Peridottroller.json').read_text())['storageLayout']['storage']
        slot=int(next(x for x in layout if x['label']=='borrowCaps')['slot'])
        key=cast('keccak','0x'+m['dollarMarket'][2:].lower().zfill(64)+f'{slot:064x}')
        rpc('anvil_setStorageAt',[m['controller'],key,'0x'+'00'*32])
    faults=[
        ('runtime byte',lambda: rpc('anvil_setCode',[m['rate'],'0xfe'+reader.code(m['rate'])[4:]])),
        ('proxy admin',lambda: storage(m['stockMarket'],'PErc20Delegator','admin',int(m['sender'],16))),
        ('proxy implementation',lambda: storage(m['stockMarket'],'PErc20Delegator','implementation',int(m['rate'],16))),
        ('supply cap',lambda: storage(m['stockMarket'],'RamsesBoostedDelegate','supplyCap',0)),
        ('borrow cap',borrow_cap),
        ('cash buffer',lambda: storage(m['stockMarket'],'RamsesBoostedDelegate','vaultBufferMantissa',10**17)),
        ('market pause',lambda: storage(m['stockMarket'],'RamsesBoostedDelegate','vaultPaused',1)),
        ('unexpected config role',grant),
        ('stale prices',lambda: (rpc('evm_increaseTime',[13*3600]),rpc('evm_mine',[]))),
    ]
    Verifier(reader,m).run()
    for name, mutate in faults:
        snapshot=rpc('evm_snapshot',[])
        try:
            mutate()
            try: Verifier(reader,m).run()
            except (AssertionError,RuntimeError): print(name+': REJECTED')
            else: raise AssertionError('Verifier accepted '+name)
        finally: assert rpc('evm_revert',[snapshot])
    Verifier(reader,m).run()
    print(f'{len(faults)} faults rejected; original local state restored and verified.')
if __name__=='__main__': main()
