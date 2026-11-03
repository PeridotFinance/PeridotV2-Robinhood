"""READ-ONLY: locate creation txs of V3 implementations + RangeLib by scanning blocks before the queue tx; compare tx input with compiled creation code; read upgrade receipts."""
import json,sys,pathlib,re,time
HERE=pathlib.Path(__file__).parent; sys.path.insert(0,str(HERE))
from rpc_util import *
from rpc_util import _post
BUILD=HERE/"_build/c07b67d5/remediation/out-vault-v3"
T={"vault":("0x53a23ef7a5639f237625ff7c89ff9e9ecf1dd18a","RobinhoodBoostedVaultV3"),"adapter":("0xb24ba4dd6d1436e2d2eb2cd054b07931c8bdd456","UniswapV4PairedAdapterV3"),"RangeLib":("0x7c585bab533229ace7d53ad093561001d320b7d9","RangeLib")}
GOV="0x94696d767e65a75581145646960fa0ec886ce5d2"
out={"creation":{},"upgrades":{}}
START=83608423; WANT={a:(k,n) for k,(a,n) in T.items()}
found={}
def getblock(n): return call("eth_getBlockByNumber",[hex(n),True])
def check_block(blk):
    for tx in blk["transactions"]:
        if tx["from"].lower()==GOV and tx.get("to") is None:
            rc=call("eth_getTransactionReceipt",[tx["hash"]]); ca=(rc.get("contractAddress") or "").lower()
            out.setdefault("governorCreatesSeen",[]).append(dict(block=int(tx["blockNumber"],16),tx=tx["hash"],nonce=int(tx["nonce"],16),contract=ca))
            if ca in WANT: found[ca]=(tx,rc,blk)
# README says the roll-out txs sit at blocks 83,608,423..83,608,493 (the schedule tx is at ..493).
scanned=[]
for n in list(range(83608500,83608300,-1)):
    check_block(getblock(n)); scanned.append(n)
    if len(found)==len(WANT) and n<83608423: break
blk_no,START_=min(scanned),max(scanned)
out["scannedBlocks"]=[blk_no,START_]
for a,(k,n) in WANT.items():
    r={"address":a}
    if a not in found:
        r["note"]="not found in scan"; out["creation"][k]=r; continue
    tx,rc,blk=found[a]
    r.update(tx=tx["hash"],block=int(tx["blockNumber"],16),timestamp=int(blk["timestamp"],16),frm=tx["from"],nonce=int(tx["nonce"],16),status=rc["status"],gasUsed=int(rc["gasUsed"],16),inputBytes=(len(tx["input"])-2)//2)
    art=json.load(open(BUILD/(n+".sol")/(n+".json")))
    obj=art["bytecode"]["object"]; obj=obj[2:] if obj.startswith("0x") else obj
    inp=tx["input"][2:]
    refs=[]
    for f,l in art["bytecode"].get("linkReferences",{}).items():
        for lib,sp in l.items():
            for s in sp: refs.append((lib,s["start"],s["length"]))
    c=bytearray.fromhex(re.sub(r"__\$[0-9a-f]{34}\$__","0"*40,obj)); oc=bytearray.fromhex(inp)
    r["compiledCreationBytes"]=len(c); r["creationLenEqual"]=len(c)==len(oc)
    r["creationLinks"]=sorted({(l,"0x"+oc[s:s+m].hex()) for l,s,m in refs})
    for l,s,m in refs:
        c[s:s+m]=bytes(m); oc[s:s+m]=bytes(m)
    r["creationExactAfterMaskingLinks"]=bytes(c)==bytes(oc)
    r["keccakCreationInput"]=keccak(tx["input"])
    out["creation"][k]=r
EV="0xbc7cd75a20ee27fd9adebab32041f755214dbc6bffa90cc0225b39da2e5c2d3b"
for name,h in (("2026-09-26_vaultV2","0x24eb2c2545f073041c37850046f2a4cf2ff561be7a17322bc865d03f7d6d0e41"),("2026-10-09_V3batch","0xa86c862a254a82f1a2dd56f10abbe3d4194fa1a60a0ef5945f0e92991565bdbe")):
    rc=call("eth_getTransactionReceipt",[h]); tx=call("eth_getTransactionByHash",[h]); blk=call("eth_getBlockByNumber",[rc["blockNumber"],False])
    out["upgrades"][name]=dict(tx=h,block=int(rc["blockNumber"],16),timestamp=int(blk["timestamp"],16),status=rc["status"],frm=tx["from"],to=tx["to"],Upgraded=[(l["address"],adr(int(l["topics"][1],16))) for l in rc["logs"] if l["topics"][0]==EV],topic0s=sorted({l["topics"][0][:10] for l in rc["logs"]}),inputSelector=tx["input"][:10],input=tx["input"])
json.dump(out,open(HERE/"t1-creation-and-upgrades.json","w"),indent=1)
print(json.dumps({"creation":out["creation"],"upgrades":{k:{x:y for x,y in v.items() if x!="input"} for k,v in out["upgrades"].items()}},indent=1))
