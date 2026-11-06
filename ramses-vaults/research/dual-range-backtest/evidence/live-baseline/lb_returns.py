"""Realised-result reconciliation per window from LOGS only. Writes results.json. Run: python3 lb_returns.py"""
import json, time, bisect, collections
from lb_data import *
import lb_market as M
import lb_timeline as TL
NVDA_T="d0601ce157db5bdc3162bbac2a2c8af5320d9eec"
PIN_TS=TL.PIN_TS; SNAP=TL.SNAP
YEAR=365*86400
# ---------------- vault per-event deltas (production pair) -----------------
def vault_deltas():
    ps=[0,0]; out=[]
    for e in M.V:
        n=e["name"]; d=e["data"]; row=None
        if n=="FeesProcessed":
            row=dict(kind="fees",ts=e["ts"],block=e["block"],stockFees=d[0],usdgFees=d[1],stockRes=d[2],usdgRes=d[3])
            ps=[ps[0]+d[0]-d[2],ps[1]+d[1]-d[3]]
        elif n=="PairCheckpoint":
            lossS=ps[0]-d[4] if d[3]<0 else 0; lossU=ps[1]-d[5] if d[3]<0 else 0
            row=dict(kind="checkpoint",ts=e["ts"],block=e["block"],stockAssets=d[0],usdgAssets=d[1],benchmark=d[2],pnl=d[3],stockAcc=d[4],usdgAcc=d[5],lossStock=lossS,lossUsdg=lossU,beforeStock=ps[0],beforeUsdg=ps[1])
            ps=[d[4],d[5]]
        elif n=="Deposited":
            tok=e["ind"][1]; row=dict(kind="deposit",ts=e["ts"],block=e["block"],token="NVDA" if tok.endswith(NVDA_T) else "USDG",amount=d[0])
            if tok.endswith(NVDA_T): ps[0]+=d[0]
            else: ps[1]+=d[0]
        elif n=="Withdrawal":
            tok=e["ind"][1]; row=dict(kind="withdrawal",ts=e["ts"],block=e["block"],token="NVDA" if tok.endswith(NVDA_T) else "USDG",requested=d[0],returned=d[1],realizedLoss=d[2])
            if tok.endswith(NVDA_T): ps[0]-=d[1]
            else: ps[1]-=d[1]
        elif n=="LiquidityRebalanced": row=dict(kind="rebalance",ts=e["ts"],block=e["block"],stockUsed=d[0],usdgUsed=d[1],liq=d[2])
        elif n=="Recentered": row=dict(kind="recenter",ts=e["ts"],block=e["block"],old=(e["data"][0],e["data"][1]),new=(e["data"][2],e["data"][3]),liq=e["data"][4],center=e["data"][5])
        if row: out.append(row)
    return out
VD=vault_deltas()
# ---------------- windows -----------------
def ts_of_block(b): return BLK[str(b)]["ts"]
LISTING_TS=ts_of_block(54976766) if "54976766" in BLK else None
W={
 "since_market_open (pToken seed mint 2026-09-05)":ts_of_block(54976766),
 "since_pair_first_LP (2026-09-15)":ts_of_block(63771095),
 "since_current_scale (first >$19 deposits 2026-10-08)":ts_of_block(83370663),
 "since_ranged_v3 (first recenter 2026-10-09)":ts_of_block(84108135),
 "last_30d":PIN_TS-30*86400,
 "last_7d":PIN_TS-7*86400,
 "last_24h":PIN_TS-86400,
}
def nv(ts): return nvda_usd(ts)
res={"pin":SNAP["pin"],"windows":{}}
TLs={m:TL.timeline(m) for m in ("pNVDA","pUSDG")}
HINT={"pNVDA":SNAP["pTokens"]["pNVDA"]["getCash"],"pUSDG":SNAP["pTokens"]["pUSDG"]["getCash"]}
for wname,t0 in W.items():
    t1=PIN_TS; days=(t1-t0)/86400
    wr={"from":iso(t0),"to":iso(t1),"days":round(days,3),"markets":{},"pair":{}}
    # ---- vault flows in window
    fees=[r for r in VD if r["kind"]=="fees" and t0<r["ts"]<=t1]
    cps=[r for r in VD if r["kind"]=="checkpoint" and t0<r["ts"]<=t1]
    gS=sum(r["stockFees"] for r in fees); gU=sum(r["usdgFees"] for r in fees); rS=sum(r["stockRes"] for r in fees); rU=sum(r["usdgRes"] for r in fees)
    feeUSD_gross=sum(r["stockFees"]/1e18*nv(r["ts"])+r["usdgFees"]/1e6 for r in fees)
    feeUSD_res=sum(r["stockRes"]/1e18*nv(r["ts"])+r["usdgRes"]/1e6 for r in fees)
    lossUSD=sum(-r["pnl"]/1e18 for r in cps if r["pnl"]<0)
    lossS=sum(r["lossStock"] for r in cps); lossU=sum(r["lossUsdg"] for r in cps)
    wr["pair"]={"feesProcessed_events":len(fees),"grossFees_NVDA_raw":gS,"grossFees_USDG_raw":gU,"reserveCut_NVDA_raw":rS,"reserveCut_USDG_raw":rU,
        "netFeesToPrincipal_NVDA_raw":gS-rS,"netFeesToPrincipal_USDG_raw":gU-rU,"grossFees_USD_at_event_price":feeUSD_gross,"reserveCut_USD":feeUSD_res,"netFees_USD":feeUSD_gross-feeUSD_res,
        "checkpoints":len(cps),"lossEvents":sum(1 for r in cps if r["pnl"]<0),"strategyLoss_USD(sum of negative pnl)":lossUSD,"principalScaleDown_NVDA_raw":lossS,"principalScaleDown_USDG_raw":lossU,
        "recenters":sum(1 for r in VD if r["kind"]=="recenter" and t0<r["ts"]<=t1),"rebalances":sum(1 for r in VD if r["kind"]=="rebalance" and t0<r["ts"]<=t1),
        "deposits":[(iso(r["ts"]),r["token"],r["amount"]) for r in VD if r["kind"]=="deposit" and t0<r["ts"]<=t1],"withdrawals":[(iso(r["ts"]),r["token"],r["requested"],r["returned"],r["realizedLoss"]) for r in VD if r["kind"]=="withdrawal" and t0<r["ts"]<=t1]}
    # time-weighted principal value (USD) over window: piecewise-constant principals, feed price
    bps=sorted({t0,t1}|{r["ts"] for r in VD if t0<r["ts"]<t1 and r["kind"] in("fees","checkpoint","deposit","withdrawal")}|{u for u in TL._ts if False})
    tlN=TLs["pNVDA"]; tlU=TLs["pUSDG"]
    # principals from pNVDA timeline entries (P) and pUSDG timeline (P)
    tw=0.0; 
    for a,b in zip(bps[:-1],bps[1:]):
        sN=TL.state_at(tlN,a); sU=TL.state_at(tlU,a)
        if not sN or not sU: continue
        val=sN["P"]/1e18*(nv(a) or 0)+sU["P"]/1e6
        tw+=val*(b-a)
    wr["pair"]["timeWeightedPrincipal_USD"]=tw/(t1-t0) if t1>t0 else None
    avgv=wr["pair"]["timeWeightedPrincipal_USD"]
    if avgv:
        wr["pair"]["netFees_APR_on_avg_principal_pct"]=100*(feeUSD_gross-feeUSD_res)/avgv*(YEAR/(t1-t0))
        wr["pair"]["grossFees_APR_on_avg_principal_pct"]=100*feeUSD_gross/avgv*(YEAR/(t1-t0))
        wr["pair"]["netFees_minus_loss_APR_pct"]=100*((feeUSD_gross-feeUSD_res)-lossUSD)/avgv*(YEAR/(t1-t0))
        wr["pair"]["loss_APR_pct"]=100*lossUSD/avgv*(YEAR/(t1-t0))
    # ---- markets
    for m in ("pNVDA","pUSDG"):
        tl=TLs[m]; s0=TL.er_at(tl,t0); s1=TL.er_at(tl,t1,withdrawable_hint=HINT[m] if True else None)
        mr={}
        if s0 is None or s1 is None: wr["markets"][m]={"note":"no supply at window start"}; continue
        dec=1e18 if m=="pNVDA" else 1e6
        for k in("er_stored","er_accrued"):
            g=s1[k]/s0[k]-1
            mr[k]={"start":s0[k],"end":s1[k],"growth_pct":100*g,"annualised_simple_pct":100*g*YEAR/(t1-t0),"annualised_compound_pct":100*((1+g)**(YEAR/(t1-t0))-1) if g>-1 else None}
        # flows & interest in window
        mints=sum(x["ev"]["data"][1] for x in tl if x["tag"]=="M:Mint" and t0<x["ts"]<=t1)
        reds=sum(x["ev"]["data"][1] for x in tl if x["tag"]=="M:Redeem" and t0<x["ts"]<=t1)
        borrows=sum(x["ev"]["data"][1] for x in tl if x["tag"]=="M:Borrow" and t0<x["ts"]<=t1)
        repays=sum(x["ev"]["data"][2] for x in tl if x["tag"]=="M:RepayBorrow" and t0<x["ts"]<=t1)
        interest=sum(x["ev"]["data"][1] for x in tl if x["tag"]=="M:AccrueInterest" and t0<x["ts"]<=t1)
        # interest generated (incl. unaccrued tail) from the borrow balance identity
        int_total=(s1["B_accrued"]-s0["B_accrued"])-borrows+repays
        dR=s1["R_accrued"]-s0["R_accrued"]
        suppl_int=int_total-dR
        dNAV=s1["nav_accrued"]-s0["nav_accrued"]
        side=0 if m=="pNVDA" else 1
        net_fee=(gS-rS) if m=="pNVDA" else (gU-rU)
        loss=lossS if m=="pNVDA" else lossU
        explained=suppl_int+net_fee-loss
        pnl_nav=dNAV-(mints-reds)
        mr["decomposition_raw_underlying"]={"deltaNAV_accrued":dNAV,"externalSupplyFlows(mint-redeem)":mints-reds,"pnl_NAV_ex_flows":pnl_nav,"interest_generated_total(incl unaccrued)":int_total,"AccrueInterest_events_interest_sum":interest,
              "reserve_factor_share":dR,"supplier_interest":suppl_int,"LP_net_fees_credited":net_fee,"LP_loss_scaledown":-loss,"sum_explained":explained,"unreconciled_residual":pnl_nav-explained}
        mr["avg_NAV_underlying"]=None
        mr["underlying_decimals"]=18 if m=="pNVDA" else 6
        mr["supply_start_end"]=(s0["S"],s1["S"]); mr["NAV_start_end_raw"]=(s0["nav_accrued"],s1["nav_accrued"])
        # borrow stats in window: time-weighted B and NAV utilisation, average realised borrow APR from accrual events
        ev=[x for x in tl if x["ts"]>t0 and x["ts"]<=t1 and x["tag"]=="M:AccrueInterest"]
        num=0;den=0
        prev=None
        # realised borrow rate per accrual interval: interest/(Bprev*dblocks)
        allacc=[x for x in tl if x["tag"]=="M:AccrueInterest"]
        for i,x in enumerate(allacc):
            if i==0 or not (t0<x["ts"]<=t1): continue
            p=allacc[i-1]; dbl=x["l1"]-p["l1"]; Bprev=None
            # B just before this accrual = state before event: previous entries
            j=tl.index(x)-1; Bp=tl[j]["B"]
            if dbl>0 and Bp>0:
                num+=x["ev"]["data"][1]; den+=Bp*dbl
        mr["avg_realised_borrow_rate_per_block_x1e18"]=(num*10**18/den) if den else None
        mr["avg_realised_borrow_APR_pct(x2628000 blocks)"]=(num*10**18/den*2628000/1e18*100) if den else None
        # time-weighted NAV utilisation
        bp=sorted({t0,t1}|{x["ts"] for x in tl if t0<x["ts"]<t1})
        tu=0;tn=0
        for a,b in zip(bp[:-1],bp[1:]):
            st=TL.state_at(tl,a)
            if not st or st["S"]==0: continue
            nav=st["L"]+st["P"]+st["B"]-st["R"]
            if nav>0: tu+=st["B"]/nav*(b-a); tn+=nav*(b-a)
        mr["time_weighted_utilisation_B_over_NAV_pct"]=100*tu/(t1-t0) if t1>t0 else None
        mr["time_weighted_avg_NAV_raw"]=tn/(t1-t0) if t1>t0 else None
        # USD terms
        if m=="pNVDA":
            p0,p1=nv(t0),nv(t1)
            mr["USD"]={"nvda_price_start":p0,"nvda_price_end":p1,"er_accrued_times_price_growth_pct":100*(s1["er_accrued"]*p1/(s0["er_accrued"]*p0)-1),"price_effect_pct":100*(p1/p0-1)}
        navavg=mr["time_weighted_avg_NAV_raw"]; dtw=(t1-t0)
        if navavg:
            mr["APR_components_pct_of_avg_NAV"]={"supplier_borrow_interest":100*suppl_int/navavg*YEAR/dtw,"LP_net_fees":100*net_fee/navavg*YEAR/dtw,"LP_loss_scaledown":-100*loss/navavg*YEAR/dtw,"total_ex_flows":100*pnl_nav/navavg*YEAR/dtw,
              "note":"pnl / time-weighted average NAV, linearly annualised; NAV = cash+borrows-reserves (full vault principal counted)"}
        wr["markets"][m]=mr
    # pair-level USD roll-up of supplier interest
    try:
        pn=wr["markets"]["pNVDA"]["decomposition_raw_underlying"]; pu=wr["markets"]["pUSDG"]["decomposition_raw_underlying"]
        px=nv(t1)
        wr["pair"]["supplier_borrow_interest_USD(price at window end)"]=pn["supplier_interest"]/1e18*px+pu["supplier_interest"]/1e6
        wr["pair"]["reserve_factor_share_USD"]=pn["reserve_factor_share"]/1e18*px+pu["reserve_factor_share"]/1e6
        wr["pair"]["NAV_pnl_ex_flows_USD(price at end)"]=pn["pnl_NAV_ex_flows"]/1e18*px+pu["pnl_NAV_ex_flows"]/1e6
    except Exception as ex: wr["pair"]["usd_rollup_error"]=str(ex)
    res["windows"][wname]=wr
ser={}
for m in ("pNVDA","pUSDG"):
    pts=[]
    for x in TLs[m]:
        if x["tag"] in("V:PairCheckpoint","M:AccrueInterest","M:Mint","M:Redeem") and x["S"]>0:
            e=TL.er_at(TLs[m],x["ts"])
            if e: pts.append({"ts":iso(x["ts"]),"tag":x["tag"],"er_stored":e["er_stored"],"er_accrued":e["er_accrued"],"S":e["S"],"nav_raw":e["nav_accrued"],"nvda_usd":nv(x["ts"])})
    ser[m]=pts
json.dump(ser,open(HERE/"er-series.json","w"))
json.dump(res,open(HERE/"results.json","w"),indent=1,default=str)
for wn,wr in res["windows"].items():
    print("\n=====",wn,wr["from"],"->",wr["to"],"days",wr["days"])
    p=wr["pair"]
    print(" pair: fees gross USD %.6f reserve %.6f net %.6f | loss USD %.6f (events %d) | avgPrincipal %s | netFeeAPR %s lossAPR %s net-loss APR %s"%(p["grossFees_USD_at_event_price"],p["reserveCut_USD"],p["netFees_USD"],p["strategyLoss_USD(sum of negative pnl)"],p["lossEvents"],p["timeWeightedPrincipal_USD"],p.get("netFees_APR_on_avg_principal_pct"),p.get("loss_APR_pct"),p.get("netFees_minus_loss_APR_pct")))
    for m,mr in wr["markets"].items():
        if "er_stored" not in mr: print(" ",m,mr); continue
        print(" ",m,"ER stored growth %.5f%% (ann %.4f%%) | accrued growth %.5f%% (ann simple %.4f%% comp %s%%) util %.3f%% avgBorrowAPR %s"%(mr["er_stored"]["growth_pct"],mr["er_stored"]["annualised_simple_pct"],mr["er_accrued"]["growth_pct"],mr["er_accrued"]["annualised_simple_pct"],mr["er_accrued"]["annualised_compound_pct"],mr["time_weighted_utilisation_B_over_NAV_pct"],mr["avg_realised_borrow_APR_pct(x2628000 blocks)"]))
        print("     decomposition",mr["decomposition_raw_underlying"])
