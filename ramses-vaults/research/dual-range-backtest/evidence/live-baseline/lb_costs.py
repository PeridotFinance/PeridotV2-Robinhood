"""Gas cost (gasUsed x effectiveGasPrice, from receipts) for keeper + governor transactions identified in the scanned logs; USD via the on-chain ETH/USD feed answer at tx time."""
import json, bisect, collections
from lb_data import *
KEEPER="0x978e2adf18da8b846552932c0ab2b1193da1fac4"; GOV="0x94696d767e65a75581145646960fa0ec886ce5d2"
E=json.load(open(HERE/"raw"/"eth-feed-answers.json"))["rows"]; E=sorted([r for r in E if r["roundId"]>1],key=lambda r:r["updatedAt"]); ET=[r["updatedAt"] for r in E]
def eth_usd(ts):
    i=bisect.bisect_right(ET,ts)-1; return E[i]["answer"]/1e8
SNAP=json.load(open(HERE/"raw"/"snapshot.json")); PIN=SNAP["pin"]["timestamp"]
names=["vault","adapter","guard","reserve","pNVDA","pUSDG","comptroller","timelock","oracle","feed"]
txev=collections.defaultdict(list)
for n in names:
    for e in events(n): txev[e["tx"]].append((n,e["name"]))
rows=[]
for t,r in RCPT.items():
    g=int(r["gasUsed"],16); p=int(r["effectiveGasPrice"],16); l1=int(r["gasUsedForL1"] or "0x0",16); b=int(r["blockNumber"],16); ts=BLK[str(b)]["ts"]
    evs=[x[1] for x in txev[t]]
    s=set(evs)
    if r["from"]==KEEPER:
        cls="keeper:recenter" if "Recentered" in s else "keeper:rebalance" if "LiquidityRebalanced" in s else "keeper:checkpoint(+collect fees)" if "PairCheckpoint" in s else "keeper:other"
    elif r["from"]==GOV:
        if s & {"PairCheckpoint","LiquidityRebalanced","Recentered","FeesProcessed"}: cls="governor:LP-operations(ongoing, pre-keeper)"
        elif r["to"] is None: cls="governor:contract-creation(one-off)"
        elif r["to"]=="0x6797fb8ce049b42c5bc2b42bf76c6d15c7b12498": cls="governor:timelock schedule/execute(one-off admin/upgrade)"
        else: cls="governor:other admin/config/own-supply-borrow-tests(one-off)"
    else: cls="third-party/test-wallet"
    rows.append(dict(tx=t,block=b,ts=ts,frm=r["from"],to=r["to"],cls=cls,gas=g,price=p,l1gas=l1,eth=g*p/1e18,usd=g*p/1e18*eth_usd(ts),events=sorted(s)))
def summ(sel,label):
    n=len(sel); eth=sum(x["eth"] for x in sel); usd=sum(x["usd"] for x in sel)
    return {"label":label,"txs":n,"gas":sum(x["gas"] for x in sel),"l1GasPart":sum(x["l1gas"] for x in sel),"ETH":eth,"USD":usd,"avgUSD_per_tx":usd/n if n else None}
out={"ethUsd_at_pin":eth_usd(PIN),"note":"txs are only those emitting a log from the scanned contracts (vault, adapter, guard, reserve, pTokens, controller, timelock, price oracle, feed). keeper nonce 176 == 176 captured => keeper list complete; governor nonce 322 vs captured below => governor list is a partial lower bound.","byClass":{}, "windows":{}}
for cls in sorted({x["cls"] for x in rows}):
    out["byClass"][cls]=summ([x for x in rows if x["cls"]==cls],cls)
W={"all_time":0,"since_2026-10-09_ranged":1791500000,"last_30d":PIN-30*86400,"last_7d":PIN-7*86400,"last_24h":PIN-86400}
for w,t0 in W.items():
    d={}
    for cls in sorted({x["cls"] for x in rows}):
        sel=[x for x in rows if x["cls"]==cls and x["ts"]>t0]
        if sel: d[cls]=summ(sel,cls)
    out["windows"][w]={"from_ts":t0,"classes":d}
# governor txs by 'to' contract and by year-month for one-off vs ongoing
gov=[x for x in rows if x["cls"].startswith("governor")]
by=collections.defaultdict(list)
for x in gov: by[x["to"]].append(x)
out["governorByTarget"]={k:summ(v,k) for k,v in by.items()}
# keeper cadence
kp=sorted([x for x in rows if x["frm"]==KEEPER],key=lambda x:x["ts"])
out["keeper"]={"first":iso(kp[0]["ts"]),"last":iso(kp[-1]["ts"]),"txs":len(kp)}
json.dump(out,open(HERE/"costs.json","w"),indent=1)
json.dump(rows,open(HERE/"raw"/"tx-costs.json","w"))
print(json.dumps({k:out[k] for k in("ethUsd_at_pin","byClass","keeper")},indent=1))
for w,v in out["windows"].items():
    print(w); 
    for c,s in v["classes"].items(): print("  ",c,s["txs"],"txs",round(s["ETH"],8),"ETH",round(s["USD"],4),"USD")
print("gov by target"); 
for k,v in out["governorByTarget"].items(): print(" ",k,v["txs"],round(v["USD"],3))
