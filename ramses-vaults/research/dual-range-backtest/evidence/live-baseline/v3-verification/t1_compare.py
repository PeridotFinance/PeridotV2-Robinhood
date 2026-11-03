"""READ-ONLY: compare deployed runtime and creation code with a compiled artifact. No signing, no broadcast."""
import json,sys,pathlib,re
HERE=pathlib.Path(__file__).parent; sys.path.insert(0,str(HERE))
from rpc_util import *
BUILD=HERE/"_build/c07b67d5/remediation/out-vault-v3"
head=int(call("eth_blockNumber",[]),16); P=head-5; B=hex(P); blk=call("eth_getBlockByNumber",[B,False])
IMPL="0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"
ADM="0xb53127684a568b3173ae13b9f8a6016e243e63b6e8ee1178d6a717850b5d6103"
VAULT="0x280825b2d856706Ff7E0d6351CcB2e935E1a9A2f"; ADAPTER="0xadA73211711e4790bc83B5d6B39f47fE04D276f3"
SET="0x813AbFeC0DE50f8674798CbaB72Ed7b5D8CcB9cB"
out={"pin":{"l2":P,"hash":blk["hash"],"timestamp":int(blk["timestamp"],16)},"source":{"commit":"07b67d5cf8deaafe15d2ec2365350696d8adc58a","profile":"vault_v3","solc":"0.8.26","via_ir":True,"optimizer_runs":1,"evm":"cancun"}}
def impl(p): return adr(int(call("eth_getStorageAt",[p,IMPL,B]),16))
def art(n,s=None): return json.load(open(BUILD/((s or n)+".sol")/(n+".json")))
def metalen(code): return int.from_bytes(code[-2:],"big")+2
def link_refs(a,key="deployedBytecode"):
    r=[]
    for f,l in a[key].get("linkReferences",{}).items():
        for lib,sp in l.items():
            for s in sp: r.append(dict(lib=lib,file=f,start=s["start"],length=s["length"]))
    return r
def imm_refs(a):
    r=[]
    for k,sp in a["deployedBytecode"].get("immutableReferences",{}).items():
        for s in sp: r.append(dict(ast=k,start=s["start"],length=s["length"]))
    return r
def hex2b(obj):
    t=obj[2:] if obj.startswith("0x") else obj
    return t
def compare(name,address,a):
    onchain=bytes.fromhex(call("eth_getCode",[address,B])[2:])
    t=hex2b(a["deployedBytecode"]["object"])
    # placeholders -> zeros, then mask same ranges on chain
    t=re.sub(r"__\$[0-9a-f]{34}\$__","0"*40,t)
    comp=bytes.fromhex(t)
    r={"address":address,"onchainBytes":len(onchain),"compiledBytes":len(comp),"onchainCodehash":keccak("0x"+onchain.hex()),"compiledCodehashWithZeroedMasks":keccak("0x"+comp.hex()),"lengthEqual":len(onchain)==len(comp)}
    M=[]
    a1=bytearray(onchain); b1=bytearray(comp)
    for x in link_refs(a):
        v=onchain[x["start"]:x["start"]+x["length"]].hex()
        M.append(dict(kind="library link",what=x["lib"],file=x["file"],start=x["start"],length=x["length"],onchainValue="0x"+v))
        a1[x["start"]:x["start"]+x["length"]]=bytes(x["length"]); b1[x["start"]:x["start"]+x["length"]]=bytes(x["length"])
    for x in imm_refs(a):
        v=onchain[x["start"]:x["start"]+x["length"]].hex()
        M.append(dict(kind="immutable",what="astId "+x["ast"],start=x["start"],length=x["length"],onchainValue="0x"+v,compiledValue="0x"+comp[x["start"]:x["start"]+x["length"]].hex()))
        a1[x["start"]:x["start"]+x["length"]]=bytes(x["length"]); b1[x["start"]:x["start"]+x["length"]]=bytes(x["length"])
    ml=metalen(onchain); mlc=metalen(comp)
    r["metadata"]={"onchainLen":ml,"compiledLen":mlc,"onchain":onchain[-ml:].hex(),"compiled":comp[-mlc:].hex(),"metadataIdentical":onchain[-ml:]==comp[-mlc:]}
    M.append(dict(kind="CBOR metadata",what="ipfs hash+solc version",start=len(onchain)-ml,length=ml))
    body_a=bytes(a1[:-ml]); body_b=bytes(b1[:-mlc])
    r["maskedBodyEqual"]=body_a==body_b
    r["exactEqualAfterFillingLinkReferences"]=None
    if body_a!=body_b:
        n=min(len(body_a),len(body_b)); diffs=[i for i in range(n) if body_a[i]!=body_b[i]]
        r["diffCount"]=len(diffs)+abs(len(body_a)-len(body_b)); r["firstDiffs"]=diffs[:40]
    # exact check with link values and compiled metadata
    filled=bytearray(comp)
    for x in link_refs(a):
        filled[x["start"]:x["start"]+x["length"]]=onchain[x["start"]:x["start"]+x["length"]]
    r["exactEqualAfterFillingLinkReferences"]=bytes(filled)==onchain
    # all linkage locations consistent
    r["masked"]=M
    return r,onchain
out["proxies"]={}
for n,p in (("vault",VAULT),("adapter",ADAPTER)):
    i=impl(p); ad=adr(int(call("eth_getStorageAt",[p,ADM,B]),16))
    owner=adr(words(ecall(ad,sel("owner()"),B))[0])
    out["proxies"][n]=dict(proxy=p,implementation=i,proxyAdmin=ad,proxyAdminOwner=owner,proxyCode=keccak(call("eth_getCode",[p,B])))
va=art("RobinhoodBoostedVaultV3"); aa=art("UniswapV4PairedAdapterV3"); ra=art("RangeLib")
res={}
res["vault"],vc=compare("vault",out["proxies"]["vault"]["implementation"],va)
res["adapter"],_=compare("adapter",out["proxies"]["adapter"]["implementation"],aa)
libs={x["what"]:x["onchainValue"] for x in res["vault"]["masked"] if x["kind"]=="library link"}
out["linkedLibraries"]=libs
locs={}
for x in res["vault"]["masked"]:
    if x["kind"]=="library link": locs.setdefault(x["what"],set()).add(x["onchainValue"])
out["libraryLinkConsistency"]={k:sorted(v) for k,v in locs.items()}
rl=libs["RangeLib"]
res["RangeLib"],_=compare("RangeLib",rl,ra)
out["results"]=res
json.dump(out,open(HERE/"t1-runtime-compare.json","w"),indent=1)
print(json.dumps(out,indent=1)[:7000])
