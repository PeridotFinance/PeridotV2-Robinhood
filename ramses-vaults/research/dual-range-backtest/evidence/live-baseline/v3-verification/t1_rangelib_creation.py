"""READ-ONLY: find the RangeLib creation tx, then compare input with compiled creation code."""
import json,sys,pathlib,re
HERE=pathlib.Path(__file__).parent; sys.path.insert(0,str(HERE))
from rpc_util import *
GOV="0x94696d767e65a75581145646960fa0ec886ce5d2"; A="0x7c585bab533229ace7d53ad093561001d320b7d9"
out={"txsFromGovernor":[]}; F=None
for n in range(83608445,83608300,-1):
    b=call("eth_getBlockByNumber",[hex(n),True])
    for tx in b["transactions"]:
        if tx["from"].lower()==GOV:
            rc=call("eth_getTransactionReceipt",[tx["hash"]])
            out["txsFromGovernor"].append(dict(block=n,tx=tx["hash"],nonce=int(tx["nonce"],16),to=tx.get("to"),contractAddress=rc.get("contractAddress"),status=rc["status"],inputBytes=(len(tx["input"])-2)//2,timestamp=int(b["timestamp"],16)))
            if (rc.get("contractAddress") or "").lower()==A or A[2:] in str(rc).lower(): F=(tx,rc)
if F:
    tx,rc=F
    art=json.load(open(HERE/"_build/c07b67d5/remediation/out-vault-v3/RangeLib.sol/RangeLib.json"))
    c=art["bytecode"]["object"][2:]; inp=tx["input"][2:]
    out["creation"]=dict(tx=tx["hash"],to=tx.get("to"),status=rc["status"],inputBytes=len(inp)//2,compiledBytes=len(c)//2,inputContainsCompiled=c in inp,exact=c==inp,prefixHexLen=inp.find(c))
json.dump(out,open(HERE/"t1-rangelib-creation.json","w"),indent=1); print(json.dumps(out,indent=0)[:2000])
