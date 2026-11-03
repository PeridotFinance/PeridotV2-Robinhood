"""Read-only: nonces of captured governor txs since 09-15 and which nonces are unaccounted (txs with no scanned logs: transfers, reverts, other targets)."""
import json,sys
from kd_rpc import *
P=HERE.parent
R=json.load(open(P/"raw"/"receipts.json")); B=json.load(open(P/"raw"/"blocks.json"))
G=[t for t,r in R.items() if r["from"]=="0x94696d767e65a75581145646960fa0ec886ce5d2"]
info=batch([("eth_getTransactionByHash",[t]) for t in G])
rows=sorted([{"tx":t,"nonce":int(x["nonce"],16),"ts":B[str(int(x["blockNumber"],16))]["ts"],"to":x["to"],"status":R[t]["status"]} for t,x in zip(G,info)],key=lambda r:r["nonce"])
miss=[]
for a,b in zip(rows,rows[1:]):
    if b["nonce"]-a["nonce"]>1: miss.append({"missingFrom":a["nonce"]+1,"missingTo":b["nonce"]-1,"afterTs":a["ts"],"beforeTs":b["ts"]})
json.dump({"captured":len(rows),"rows":rows,"missing":miss},open(HERE/"raw"/"governor-nonces.json","w"),indent=0)
import time
f=lambda t:time.strftime("%FT%TZ",time.gmtime(t))
print(len(rows),rows[-1]["nonce"],sum(r["status"]!="0x1" for r in rows))
for m in miss:
    if m["afterTs"]>1789000000: print(m["missingFrom"],m["missingTo"],f(m["afterTs"]),f(m["beforeTs"]))
for r in rows[-14:]: print(r["nonce"],f(r["ts"]),r["to"][:10],r["status"])
