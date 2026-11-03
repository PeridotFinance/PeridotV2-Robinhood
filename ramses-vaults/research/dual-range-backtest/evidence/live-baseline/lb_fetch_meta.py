"""Fetch block headers (timestamp, l1BlockNumber) and tx receipts/transactions for every log-bearing block/tx. Caches raw/blocks.json, raw/receipts.json."""
import json, sys
from lb_rpc import *
def load(p): 
    return json.load(open(p)) if p.exists() else {}
NAMES=["vault","adapter","guard","reserve","pNVDA","pUSDG","comptroller","timelock","oracle","feed"]+sys.argv[1:]
blocks=set();txs=set()
for n in NAMES:
    p=HERE/"raw"/f"logs-{n}.json"
    if not p.exists(): continue
    for l in json.load(open(p))["logs"]:
        blocks.add(int(l["blockNumber"],16)); txs.add(l["transactionHash"])
bp=HERE/"raw"/"blocks.json"; rp=HERE/"raw"/"receipts.json"
B=load(bp); R=load(rp)
for i,b in enumerate(sorted(blocks)):
    if str(b) in B: continue
    h=call("eth_getBlockByNumber",[hex(b),False],log=False)
    B[str(b)]={"ts":int(h["timestamp"],16),"l1":int(h["l1BlockNumber"],16),"hash":h["hash"]}
    if i%50==0: bp.write_text(json.dumps(B))
bp.write_text(json.dumps(B))
for i,t in enumerate(sorted(txs)):
    if t in R: continue
    r=call("eth_getTransactionReceipt",[t],log=False)
    R[t]={k:r.get(k) for k in ["from","to","status","gasUsed","effectiveGasPrice","gasUsedForL1","l1Fee","blockNumber","cumulativeGasUsed","type"]}
    if i%50==0: rp.write_text(json.dumps(R))
rp.write_text(json.dumps(R))
print(len(B),len(R))
