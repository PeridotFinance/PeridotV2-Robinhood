"""Read-only manifest builder. Review the output before provisioning; never auto-repin in worker."""
import argparse
import json
import re
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from verify_deployment import Reader, Verifier, POOL, GAUGE, MANAGER, FEED
from worker import Chain, offline, preflight, address

SLOTS = [hex(int(offline('keccak', 'eip1967.proxy.'+key),16)-1) for key in ('implementation','beacon','admin')]


def build(url, deployment):
    r=Reader(url); r.block=r.rpc('eth_blockNumber',[])
    checked=Verifier(r,deployment).run()
    c=Chain(url,deployment['keeper'])
    m={**deployment,'pool':POOL,'gauge':GAUGE,'manager':MANAGER,'feed':FEED,
       'codeHashes':{a:v['runtimeHash'] for a,v in checked.items()},'storagePins':[],'callPins':[],
       'bootstrapNonce':c.nonce('latest')}
    # Refuse a pending signer at enrollment. Missing journal after any later nonce advance is fatal.
    if c.nonce()!=m['bootstrapNonce']: raise RuntimeError('Signer has pending transactions')
    pending=[m[k].lower() for k in ('timelock','pool','gauge','manager','feed','stock','usdg')]
    visited=set()
    getters=('implementation()','aggregator()','factory()','poolDeployer()','voter()',
             'nfpManager()','pool()','rewardValidator()','rewardClaimValidator()','claimValidator()','validator()','owner()','beacon()')
    while pending:
        target=pending.pop()
        if target in visited: continue
        visited.add(target)
        code=c.rpc('eth_getCode',[target,r.block])
        if code=='0x': continue
        m['codeHashes'][target]=offline('keccak',code)
        # Linked upstream libraries and immutable beacon addresses are embedded as PUSH20.
        # Resolve only candidates with code; fixed masks/token constants are harmless misses.
        for candidate in re.findall(r'PUSH20 (0x[0-9a-fA-F]{40})', offline('disassemble',code)):
            if candidate.lower() not in visited:
                pending.append(candidate.lower())
        for slot in SLOTS:
            value=c.rpc('eth_getStorageAt',[target,slot,r.block])
            m['storagePins'].append(dict(address=target,slot=slot,value=value))
            if int(value,16): pending.append('0x'+value[-40:])
        for sig in getters:
            try: result=c.read(target,sig,block=r.block)
            except Exception: continue # unsupported discovery getter, not a worker/preflight bypass
            if len(result)==1 and 0<result[0]<2**160:
                m['callPins'].append(dict(address=target,signature=sig,words=result))
                pending.append('0x'+format(result[0],'040x'))
        if len(visited)>100: raise RuntimeError('Dependency graph too large; review manually')
    rewards=c.read(GAUGE,'getRewardTokens()',block=r.block)
    m['callPins'].append(dict(address=GAUGE,signature='getRewardTokens()',words=rewards))
    for word in rewards[2:]:
        token='0x'+format(word,'040x')
        code=c.rpc('eth_getCode',[token,r.block])
        if code=='0x': raise RuntimeError('Reward token has no code')
        m['codeHashes'][token]=offline('keccak',code)
        for slot in SLOTS:
            value=c.rpc('eth_getStorageAt',[token,slot,r.block])
            m['storagePins'].append(dict(address=token,slot=slot,value=value))
            if int(value,16):
                impl='0x'+value[-40:]
                m['codeHashes'][impl]=offline('keccak',c.rpc('eth_getCode',[impl,r.block]))
    # Only public data; discard bulky timelock transaction payloads from runtime config.
    for key in ('targets','payloads','scheduleCalldata','executeCalldata'): m.pop(key,None)
    m['pinBlock']=int(r.block,16)
    preflight(c,m,True)
    return m


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--rpc-url',required=True); p.add_argument('--deployment',required=True); p.add_argument('--out',required=True)
    a=p.parse_args()
    m=build(a.rpc_url,json.loads(Path(a.deployment).read_text()))
    Path(a.out).write_text(json.dumps(m,indent=2)+'\n')
    print('Reviewed-build verification and keeper preflight passed; inspect manifest:',a.out)

if __name__=='__main__': main()
