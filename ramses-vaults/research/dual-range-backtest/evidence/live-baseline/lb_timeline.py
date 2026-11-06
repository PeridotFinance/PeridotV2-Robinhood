"""State timeline for each market: after every market event, every transfer of underlying, and every production-pair vault event."""
import json, bisect
from lb_data import *
import lb_market as M
SNAP=json.load(open(HERE/"raw"/"snapshot.json"))
PIN_TS=SNAP["pin"]["timestamp"]; PIN_L1=SNAP["pin"]["l1BlockNumber_native"]
BORR=json.load(open(HERE/"raw"/"borrowers.json"))
# block->(ts,l1) arrays for interpolation (all scanned blocks + the two pins)
_pts=sorted({(v["ts"],v["l1"]) for v in BLK.values()}|{(PIN_TS,PIN_L1),(BORR["pin"]["timestamp"],BORR["pin"]["l1"])})
_ts=[p[0] for p in _pts]
def native_at(ts):
    i=bisect.bisect_right(_ts,ts)
    if i==0: return _pts[0][1]
    if i>=len(_pts):
        # extrapolate 12s per native block beyond last known
        t,l=_pts[-1]; return l+(ts-t)//12
    (t0,l0),(t1,l1)=_pts[i-1],_pts[i]
    if t1==t0: return l0
    return l0+(l1-l0)*(ts-t0)//(t1-t0)
def accrue(B,R,cash_for_rate,dblocks):
    if B==0 or dblocks<=0: return B,R,0
    rate=M.borrow_rate(cash_for_rate,B,R)
    interest=rate*dblocks*B//10**18
    return B+interest, R+interest*M.RF//10**18, interest
def timeline(mkt):
    pt=M.PT[mkt]; idx=0 if mkt=="pNVDA" else 1
    ev=[("M",e) for e in events(mkt)]+[("V",e) for e in M.V]+[("T",t) for t in M.transfers("NVDA" if mkt=="pNVDA" else "USDG",pt)]
    ev.sort(key=lambda x:(x[1]["block"],x[1]["idx"]))
    st=dict(L=0,P=0,S=0,B=0,R=0,acc=None,cashrate=0,idx=None)
    ps=[0,0]; out=[]
    for kind,e in ev:
        ts=BLK[str(e["block"])]["ts"] if kind=="T" else e["ts"]; l1=BLK[str(e["block"])]["l1"]
        tag=None
        if kind=="T": st["L"]+=e["delta"]; tag="T"
        elif kind=="V":
            n=e["name"]; d=e["data"]; tag="V:"+n
            if n=="PairCheckpoint": ps=[d[4],d[5]]
            elif n=="FeesProcessed": ps=[ps[0]+d[0]-d[2],ps[1]+d[1]-d[3]]
            elif n=="Deposited":
                if e["ind"][1].endswith("d0601ce157db5bdc3162bbac2a2c8af5320d9eec"): ps[0]+=d[0]
                else: ps[1]+=d[0]
            elif n=="Withdrawal":
                if e["ind"][1].endswith("d0601ce157db5bdc3162bbac2a2c8af5320d9eec"): ps[0]-=d[1]
                else: ps[1]-=d[1]
            st["P"]=ps[idx]
        else:
            n=e["name"]; d=e["data"]; tag="M:"+n
            if n=="AccrueInterest":
                st["R"]+=d[1]*M.RF//10**18; st["B"]=d[3]; st["acc"]=l1; st["cashrate"]=d[0]
            elif n=="Mint": st["S"]+=d[2]
            elif n=="Redeem": st["S"]-=d[2]
            elif n=="Borrow": st["B"]=d[3]
            elif n=="RepayBorrow": st["B"]=d[4]
        out.append({"key":(e["block"],e["idx"]),"ts":ts,"l1":l1,"tag":tag,**{k:st[k] for k in("L","P","S","B","R","acc","cashrate")},"ps":list(ps),"ev":e if kind=="M" else None})
    return out
def state_at(tl,ts):
    keys=[x["ts"] for x in tl]
    i=bisect.bisect_right(keys,ts)-1
    return tl[i] if i>=0 else None
def er_at(tl,ts,withdrawable_hint=None):
    s=state_at(tl,ts)
    if s is None or s["S"]==0: return None
    nat=native_at(ts) if ts!=PIN_TS else PIN_L1
    Bs,Rs=s["B"],s["R"]
    stored=(s["L"]+s["P"]+Bs-Rs)*10**18//s["S"]
    # accrue B,R from the last accrual block to `nat`; cash for the rate model = last event cashPrior (approx: borrows tiny)
    cash=withdrawable_hint if withdrawable_hint is not None else (s["cashrate"] or s["L"])
    Ba,Ra,intr=accrue(Bs,Rs,cash,(nat-s["acc"]) if s["acc"] else 0)
    acc=(s["L"]+s["P"]+Ba-Ra)*10**18//s["S"]
    return {"ts":ts,"native":nat,"L":s["L"],"P":s["P"],"S":s["S"],"B_stored":Bs,"B_accrued":Ba,"R_stored":Rs,"R_accrued":Ra,"er_stored":stored,"er_accrued":acc,"nav_accrued":s["L"]+s["P"]+Ba-Ra}
if __name__=="__main__":
    for m in ("pNVDA","pUSDG"):
        tl=timeline(m)
        e=er_at(tl,PIN_TS)
        print(m,e)
        print("  snapshot stored ER:",SNAP["pTokens"][m]["exchangeRateStored"])
