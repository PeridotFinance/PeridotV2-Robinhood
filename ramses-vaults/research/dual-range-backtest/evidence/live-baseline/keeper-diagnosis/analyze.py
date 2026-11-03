"""Builds timeline.json, gaps.json, cadence-costs.json, balance-series.json from saved evidence. No RPC; reads parent raw/ read-only."""
import json,bisect,sys,time,collections,statistics
from pathlib import Path
sys.dont_write_bytecode=True
HERE=Path(__file__).parent; P=HERE.parent; sys.path.insert(0,str(P))
from lb_data import events,RCPT,BLK,iso
from lb_events import load as _l, decode
PROD="0xe2050352f4346597cc69d2776d99ae60c9440dfc28f9406ce66be0bbe3fb6b06"
KEEPER="0x978e2adf18da8b846552932c0ab2b1193da1fac4"; GOV="0x94696d767e65a75581145646960fa0ec886ce5d2"
reg=_l()
fresh=json.load(open(HERE/"raw"/"vault-logs-fresh.json")); nonces=json.load(open(HERE/"raw"/"keeper-nonces.json"))
ev=[]
for e in events("vault"): ev.append(e)
for l in fresh["logs"]:
    d=decode(l,reg); b=int(l["blockNumber"],16)
    ev.append({"name":d[0] if d else l["topics"][0],"ind":d[1] if d else [],"data":d[2] if d else l["data"],"block":b,"idx":int(l["logIndex"],16),"ts":fresh["blockTs"][str(b)],"tx":l["transactionHash"]})
ev.sort(key=lambda e:(e["block"],e["idx"]))
RC=dict(RCPT); RC.update({t:dict(r,gasUsedForL1=None) for t,r in fresh["receipts"].items()})
ad=[e for e in events("adapter") if e["ind"] and e["ind"][0]==PROD]
EA=json.load(open(P/"raw"/"eth-feed-answers.json"))["rows"]; EA=sorted([r for r in EA if r["roundId"]>1],key=lambda r:r["updatedAt"]); ET=[r["updatedAt"] for r in EA]
ethusd=lambda ts:EA[max(bisect.bisect_right(ET,ts)-1,0)]["answer"]/1e8
FA=json.load(open(P/"raw"/"feed-answers.json"))["rows"]; FA=sorted([r for r in FA if r["roundId"]>1],key=lambda r:r["updatedAt"]); FT=[r["updatedAt"] for r in FA]
nvda=lambda ts:FA[max(bisect.bisect_right(FT,ts)-1,0)]["answer"]/1e8
MAXSTALE=43200
def fresh_intervals(a,b):
    """sub-intervals of [a,b] where feed age<=12h"""
    out=[];
    i=bisect.bisect_right(FT,a)-1
    t=a
    while t<b:
        upd=FT[i] if i>=0 else 0
        nxt=FT[i+1] if i+1<len(FT) else b+1
        end=min(nxt,b)
        fe=min(upd+MAXSTALE,end)
        if fe>t: out.append((t,fe))
        t=end;i+=1
    # merge
    m=[]
    for x,y in out:
        if m and x<=m[-1][1]: m[-1]=(m[-1][0],max(y,m[-1][1]))
        else: m.append((x,y))
    return m
# ---- tx table
tx=collections.OrderedDict()
for e in ev:
    if e["ind"] and e["ind"][0]!=PROD and e["name"] in("PairCheckpoint","LiquidityRebalanced","Recentered","FeesProcessed","Deposited","Withdrawal","PairPauseUpdated"): continue
    if not e["ind"] or e["ind"][0]!=PROD: continue
    r=tx.setdefault(e["tx"],{"tx":e["tx"],"block":e["block"],"ts":e["ts"],"iso":iso(e["ts"]),"events":[]})
    r["events"].append(e["name"])
    if e["name"]=="LiquidityRebalanced": r["stockUsed"]=e["data"][0]; r["usdgUsed"]=e["data"][1]
    if e["name"]=="FeesProcessed":
        r["feeStock"]=r.get("feeStock",0)+e["data"][0]; r["feeUsdg"]=r.get("feeUsdg",0)+e["data"][1]
rows=[]
for r in tx.values():
    c=RC.get(r["tx"],{}); s=set(r["events"])
    r["from"]=c.get("from"); r["status"]=c.get("status")
    g=int(c.get("gasUsed","0x0"),16); p=int(c.get("effectiveGasPrice","0x0"),16)
    r["gasUsed"]=g; r["feeWei"]=g*p; r["usd"]=g*p/1e18*ethusd(r["ts"])
    r["type"]="recenter" if "Recentered" in s else "rebalance" if "LiquidityRebalanced" in s else "checkpoint" if "PairCheckpoint" in s else "/".join(sorted(s))[:40]
    r["hasCheckpoint"]="PairCheckpoint" in s
    r["who"]="keeper" if r["from"]==KEEPER else "governor" if r["from"]==GOV else (r["from"] or "?")
    r["nonce"]=nonces["keeperTxNonces"].get(r["tx"],{}).get("nonce")
    del r["events"]; r["events"]=sorted(s)
    rows.append(r)
# keeper txs without any prod vault log?
kall=[t for t,c in RC.items() if c["from"]==KEEPER]
missing=[t for t in kall if t not in tx]
assert len(nonces["keeperTxNonces"])==nonces["current"]["keeper"]["nonce"]
st=collections.Counter(c["status"] for t,c in RC.items() if c["from"]==KEEPER)
# ---- balance reconciliation vs worker heartbeat
R=[json.loads(l) for l in open(HERE/"raw"/"run-logs-redacted.jsonl")]
HB=[(r["unix"],int(r["gasBalanceWei"])) for r in R if r.get("status")=="heartbeat"]
fund=json.load(open(HERE/"raw"/"funding-scan-20261102.json"))["found"][0]
kt=sorted([r for r in rows if r["who"]=="keeper"],key=lambda r:r["nonce"])
bal=2_000_000_000_000_000; series=[]
for r in kt:
    if r["ts"]>fund["ts"] and not any(s.get("funded") for s in series):
        bal+=fund["valueWei"]; series.append({"t":fund["ts"],"iso":iso(fund["ts"]),"funded":fund["valueWei"],"tx":fund["tx"],"balanceWei":bal})
    bal-=r["feeWei"]; r["balanceAfterWei"]=bal
    series.append({"t":r["ts"],"iso":r["iso"],"type":r["type"],"nonce":r["nonce"],"feeWei":r["feeWei"],"balanceWei":bal})
    i=bisect.bisect_right([h[0] for h in HB],r["ts"])
    r["nextHeartbeatBal"]=HB[i][1] if i<len(HB) else None
mism=[(s["iso"]) for s in series if False]
ck={"computedFinal":bal,"latestChain":nonces["current"]["keeper"]["balanceWei"],"diffWei":bal-nonces["current"]["keeper"]["balanceWei"],
    "balanceAfterTxBeforeGasOut":None}
gasout=[r for r in kt if r["ts"]<fund["ts"]][-1]
ck["balanceAfterLastTxBeforeGasOut"]=gasout["balanceAfterWei"]; ck["heartbeatAwaitingBal"]=294912258829028; ck["gasOutDiff"]=gasout["balanceAfterWei"]-294912258829028
# checkpoint gap table
cps=sorted([r for r in rows if r["hasCheckpoint"]],key=lambda r:r["ts"])
gaps=[]
for a,b in zip(cps,cps[1:]):
    d=b["ts"]-a["ts"]
    if d>7200:
        fi=fresh_intervals(a["ts"],b["ts"]); fs=sum(y-x for x,y in fi)
        firstfresh=fi[0][0] if fi else None
        gaps.append({"from":a["iso"],"to":b["iso"],"fromTs":a["ts"],"toTs":b["ts"],"hours":round(d/3600,2),"days":round(d/86400,2),"fromWho":a["who"],"toWho":b["who"],"toType":b["type"],
          "feedFreshSeconds":fs,"feedFreshPct":round(100*fs/d,1),"feedFreshIntervals":[[iso(x),iso(y)] for x,y in fi],
          "feedAgeAtEnd_h":round((b["ts"]-FT[bisect.bisect_right(FT,b["ts"])-1])/3600,2),
          "weekdayFrom":time.strftime("%a",time.gmtime(a["ts"])),"weekdayTo":time.strftime("%a",time.gmtime(b["ts"])),
          "nvdaFrom":nvda(a["ts"]),"nvdaTo":nvda(b["ts"])})
# state events
state=[{"iso":iso(e["ts"]),"name":e["name"],"who":RC.get(e["tx"],{}).get("from","?")[:10],"data":e["data"]} for e in ev if e["ind"] and e["ind"][0]==PROD and e["name"] in("PairPauseUpdated","Deposited","Withdrawal","Recentered","RangePolicyUpdated","PairRegistered")]
adstate=[{"iso":iso(e["ts"]),"name":e["name"],"data":e["data"],"who":RC.get(e["tx"],{}).get("from","?")[:10]} for e in ad if e["name"] in("LiquidityAdded","LiquidityDecreased","PositionBurned","RangeSet")]
json.dump(rows,open(HERE/"timeline.json","w"),indent=0)
json.dump({"gapThresholdSec":7200,"checkpoints":len(cps),"gaps":gaps},open(HERE/"gaps-raw.json","w"),indent=1)
json.dump({"reconciliation":ck,"statusCount":dict(st),"keeperTxWithoutProdVaultLog":missing,"series":series,"funding":[{"note":"10-15 funding: 2e15 observed at first heartbeat, see funding-scan-20261015 if found"},fund]},open(HERE/"balance-series.json","w"),indent=0)
# prints
print(ck,st,len(missing))
print("counts by who/type",collections.Counter((r["who"],r["type"]) for r in rows))
print(len(gaps))
for g in gaps: print(g["from"],g["to"],g["hours"],g["fromWho"][:3],g["toWho"][:3],g["toType"],"fresh%",g["feedFreshPct"],"ageEnd",g["feedAgeAtEnd_h"],g["weekdayFrom"],g["weekdayTo"])
print('---state'); 
for s in state: print(s)
print('---adapter');
for s in adstate:
    if s['iso']<'2026-10-19T05': print(s)
# keeper stats
K=[r for r in rows if r["who"]=="keeper"]
for ty in ("checkpoint","rebalance","recenter"):
    S=[r for r in K if r["type"]==ty]
    print(ty,len(S),"gas med",statistics.median(r["gasUsed"] for r in S),"min",min(r["gasUsed"] for r in S),"max",max(r["gasUsed"] for r in S),"ETH",sum(r["feeWei"] for r in S)/1e18,"USD",round(sum(r["usd"] for r in S),4),"avgUSD",round(statistics.mean(r["usd"] for r in S),4))
reb=[r for r in K if r["type"]=="rebalance"]
for r in reb: r["deployUsd"]=r["stockUsed"]/1e18*nvda(r["ts"])+r["usdgUsed"]/1e6
print("rebalance deployed USD total",sum(r["deployUsd"] for r in reb),"median",statistics.median(r["deployUsd"] for r in reb),"gas usd",sum(r["usd"] for r in reb))
print(sorted(round(r["deployUsd"],6) for r in reb)[-12:], sum(r["deployUsd"]<r["usd"] for r in reb))
fees=sum((r.get("feeStock",0)/1e18*nvda(r["ts"])+r.get("feeUsdg",0)/1e6) for r in K)
allkgas=sum(r["usd"] for r in K)
print("fees collected in keeper txs USD",fees,"keeper gas USD",allkgas)
