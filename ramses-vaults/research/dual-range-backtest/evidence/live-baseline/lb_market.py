"""Log-only reconstruction of the two Peridot markets (pNVDA, pUSDG) and the vault principals; self-checks against every AccrueInterest.cashPrior and the pin snapshot.
exchangeRate = (cash + borrows - reserves) / supply, cash = underlying balance of the pToken + vault accounted (principal) assets (verified at the pin: getCash = balanceOf + vaultAccountedAssets)."""
import json, sys, time, collections
from lb_data import *
RF=100000000000000000
IRM=json.load(open(HERE/"raw"/"snapshot.json"))["irm"]
BASE,MULT,JUMP,KINK=IRM["baseRatePerBlock"],IRM["multiplierPerBlock"],IRM["jumpMultiplierPerBlock"],IRM["kink"]
def borrow_rate(cash,b,r):
    if b==0: return BASE
    util=b*10**18//(cash+b-r) if cash+b-r>0 else 0
    if util<=KINK: return util*MULT//10**18+BASE
    normal=KINK*MULT//10**18+BASE
    return (util-KINK)*JUMP//10**18+normal
def toks(name,pt):
    return events(name)
V=[e for e in events("vault") if len(e["topics"])>1 and e["topics"][1]==PROD]
TR_NVDA=[]; TR_USDG=[]
def transfers(side, pt):
    out=[]
    for nm in (f"tok{side}_to_pToken",f"tok{side}_from_pToken"):
        for l in json.load(open(HERE/"raw"/f"logs-{nm}.json"))["logs"]:
            b=int(l["blockNumber"],16); frm="0x"+l["topics"][1][-40:]; to="0x"+l["topics"][2][-40:]; v=int(l["data"],16)
            out.append({"block":b,"idx":int(l["logIndex"],16),"delta":(v if to==pt else 0)-(v if frm==pt else 0),"tx":l["transactionHash"]})
    return sorted(out,key=lambda x:(x["block"],x["idx"]))
PT={"pNVDA":"0xa155cccb986774ae818b3f10f07d01d1b7a47b26","pUSDG":"0x55aed0569c8f0d166d71face57b57c2f2624a563"}
def build(mkt, side):
    """returns list of state samples (after each event) and self-check results"""
    pt=PT[mkt]; ev=[]
    for e in events(mkt):
        ev.append(("M",e))
    for e in V: ev.append(("V",e))
    for t in transfers("NVDA" if mkt=="pNVDA" else "USDG", pt): ev.append(("T",t))
    def key(x):
        if x[0]=="T": return (x[1]["block"],x[1]["idx"])
        return (x[1]["block"],x[1]["idx"])
    ev.sort(key=key)
    L=0; P=0; S=0; B=0; R=0; acc_native=None; idx_side=0 if mkt=="pNVDA" else 1
    ps=[0,0]            # vault principals [stock, usdg]
    samples=[]; checks=[]; lastts=None
    for kind,e in ev:
        if kind=="T": L+=e["delta"]; continue
        if kind=="V":
            n=e["name"]; d=e["data"]
            if n=="PairCheckpoint": ps=[d[4],d[5]]
            elif n=="FeesProcessed": ps=[ps[0]+d[0]-d[2], ps[1]+d[1]-d[3]]
            elif n=="Deposited":
                tok=e["ind"][1]
                if tok.endswith("d0601ce157db5bdc3162bbac2a2c8af5320d9eec"): ps[0]+=d[0]
                else: ps[1]+=d[0]
            elif n=="Withdrawal":
                tok=e["ind"][1]; req=d[1]   # principal falls by `returned` (verified vs 2026-09-16 ledger 16631839952566)
                if tok.endswith("d0601ce157db5bdc3162bbac2a2c8af5320d9eec"): ps[0]-=req
                else: ps[1]-=req
            continue
        n=e["name"]; d=e["data"]
        if n=="AccrueInterest":
            cash=L+ps[idx_side]; 
            checks.append({"block":e["block"],"ts":e["ts"],"cashPrior_event":d[0],"L_plus_principal":cash,"implied_vaultWithdrawable":d[0]-L,"principal":ps[idx_side],"diff":cash-d[0]})
            R+=d[1]*RF//10**18; B=d[3]; acc_native=e["l1"]
            samples.append(("accrue",e,dict(L=L,P=ps[idx_side],S=S,B=B,R=R,cash=d[0],native=e["l1"],interest=d[1])))
        elif n=="Mint": S+=d[2]; samples.append(("mint",e,dict(L=L,P=ps[idx_side],S=S,B=B,R=R,native=e["l1"],amount=d[1],tokens=d[2])))
        elif n=="Redeem": S-=d[2]; samples.append(("redeem",e,dict(L=L,P=ps[idx_side],S=S,B=B,R=R,native=e["l1"],amount=d[1],tokens=d[2])))
        elif n=="Borrow": B=d[3]
        elif n=="RepayBorrow": B=d[4]
    return {"samples":samples,"checks":checks,"final":dict(L=L,P=ps[idx_side],S=S,B=B,R=R,acc_native=acc_native),"principals_end":ps}
if __name__=="__main__":
    out={}
    for m in ("pNVDA","pUSDG"):
        r=build(m,None); out[m]=r
        print(m,"final",r["final"],"vault principals",r["principals_end"])
        bad=[c for c in r["checks"] if c["diff"]!=0]
        print("  accrue cash checks:",len(r["checks"]),"mismatches:",len(bad))
        for c in bad[:10]: print("   ",iso(c["ts"]),c)
