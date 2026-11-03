"""Return-decomposition table (per market and combined) for headline windows, incl. protocol-paid costs. Reads results.json (lb_returns.py), raw/tx-costs.json (lb_costs.py). Writes decomposition.json"""
import json, bisect
from lb_data import *
import lb_returns as R          # (re-runs the reconciliation; prints suppressed by redirect in run_all)
res=json.load(open(HERE/"results.json")); rows=json.load(open(HERE/"raw"/"tx-costs.json"))
PIN=R.PIN_TS
def side_usd(t0,t1):
    fS=fU=rS=rU=lS=lU=0.0
    for r in R.VD:
        if not (t0<r["ts"]<=t1): continue
        p=nvda_usd(r["ts"])
        if r["kind"]=="fees":
            fS+=r["stockFees"]/1e18*p; fU+=r["usdgFees"]/1e6; rS+=r["stockRes"]/1e18*p; rU+=r["usdgRes"]/1e6
        if r["kind"]=="checkpoint": lS+=r["lossStock"]/1e18*p; lU+=r["lossUsdg"]/1e6
    return dict(gross_fees_stock_side_USD=fS,gross_fees_usdg_side_USD=fU,reserve_cut_stock_USD=rS,reserve_cut_usdg_USD=rU,net_fees_stock_USD=fS-rS,net_fees_usdg_USD=fU-rU,loss_stock_side_USD=lS,loss_usdg_side_USD=lU)
def cost(t0,t1):
    d={}
    for cls in sorted({x["cls"] for x in rows}):
        sel=[x for x in rows if x["cls"]==cls and t0<x["ts"]<=t1]
        d[cls]={"txs":len(sel),"ETH":sum(x["eth"] for x in sel),"USD":sum(x["usd"] for x in sel)}
    keeper=sum(v["USD"] for k,v in d.items() if k.startswith("keeper")); gov_ops=sum(v["USD"] for k,v in d.items() if k.startswith("governor:LP-operations"))
    one_off=sum(v["USD"] for k,v in d.items() if k.startswith("governor") and not k.startswith("governor:LP-operations")); third=d.get("third-party/test-wallet",{}).get("USD",0)
    return {"by_class":d,"ongoing_operating_USD(keeper+governor LP ops)":keeper+gov_ops,"keeper_USD":keeper,"governor_LP_ops_USD":gov_ops,"one_off_governor_admin_USD(lower bound)":one_off,"third_party_wallets_USD(NOT protocol-paid; excluded)":third,"protocol_paid_total_USD":keeper+gov_ops+one_off}
out={}
for wn,t0 in R.W.items():
    t1=PIN; wr=res["windows"][wn]
    pn=wr["markets"]["pNVDA"]; pu=wr["markets"]["pUSDG"]; px=nvda_usd(t1)
    su=side_usd(t0,t1)
    dn,du=pn["decomposition_raw_underlying"],pu["decomposition_raw_underlying"]
    blk={ "window":wn,"from":wr["from"],"to":wr["to"],"days":wr["days"],
      "pNVDA_raw_NVDA_wei":{k:dn[k] for k in("supplier_interest","reserve_factor_share","LP_net_fees_credited","LP_loss_scaledown","pnl_NAV_ex_flows","unreconciled_residual")},
      "pUSDG_raw_USDG_micro":{k:du[k] for k in("supplier_interest","reserve_factor_share","LP_net_fees_credited","LP_loss_scaledown","pnl_NAV_ex_flows","unreconciled_residual")},
      "USD":{"supplier_borrow_interest_pNVDA":dn["supplier_interest"]/1e18*px,"supplier_borrow_interest_pUSDG":du["supplier_interest"]/1e6,
             "LP_net_fees_(event-time price)":wr["pair"]["netFees_USD"],"LP_gross_fees":wr["pair"]["grossFees_USD_at_event_price"],"LP_reserve_cut":wr["pair"]["reserveCut_USD"],
             "strategy_loss_(event-time)":-wr["pair"]["strategyLoss_USD(sum of negative pnl)"],**su,
             "NAV_pnl_ex_flows_(end price)":wr["pair"]["NAV_pnl_ex_flows_USD(price at end)"]},
      "ER":{"pNVDA_stored_growth_pct":pn["er_stored"]["growth_pct"],"pNVDA_accrued_growth_pct":pn["er_accrued"]["growth_pct"],"pNVDA_accrued_ann_simple_pct":pn["er_accrued"]["annualised_simple_pct"],"pNVDA_accrued_ann_compound_pct":pn["er_accrued"]["annualised_compound_pct"],"pNVDA_USD_growth_pct":pn["USD"]["er_accrued_times_price_growth_pct"],
            "pUSDG_stored_growth_pct":pu["er_stored"]["growth_pct"],"pUSDG_accrued_growth_pct":pu["er_accrued"]["growth_pct"],"pUSDG_accrued_ann_simple_pct":pu["er_accrued"]["annualised_simple_pct"],"pUSDG_accrued_ann_compound_pct":pu["er_accrued"]["annualised_compound_pct"]},
      "utilisation_pct":{"pNVDA":pn["time_weighted_utilisation_B_over_NAV_pct"],"pUSDG":pu["time_weighted_utilisation_B_over_NAV_pct"]},
      "avg_realised_borrow_APR_pct":{"pNVDA":pn["avg_realised_borrow_APR_pct(x2628000 blocks)"],"pUSDG":pu["avg_realised_borrow_APR_pct(x2628000 blocks)"]},
      "pair_avg_principal_USD":wr["pair"]["timeWeightedPrincipal_USD"],
      "protocol_paid_costs":cost(t0,t1)}
    c=blk["protocol_paid_costs"]; 
    blk["USD"]["combined_supplier_result(interest+net fees-loss)"]=blk["USD"]["supplier_borrow_interest_pNVDA"]+blk["USD"]["supplier_borrow_interest_pUSDG"]+blk["USD"]["LP_net_fees_(event-time price)"]+blk["USD"]["strategy_loss_(event-time)"]
    blk["USD"]["protocol_paid_ongoing_operating"]=-c["ongoing_operating_USD(keeper+governor LP ops)"]
    blk["USD"]["protocol_paid_one_off_admin"]=-c["one_off_governor_admin_USD(lower bound)"]
    blk["USD"]["combined_supplier_result_less_ongoing_operating_cost"]=blk["USD"]["combined_supplier_result(interest+net fees-loss)"]-c["ongoing_operating_USD(keeper+governor LP ops)"]
    out[wn]=blk
json.dump(out,open(HERE/"decomposition.json","w"),indent=1)
for wn,b in out.items():
    print("\n##",wn,b["from"],"->",b["to"],"(%s d)"%b["days"])
    for k,v in b["USD"].items(): print("   %-52s %12.6f"%(k,v))
    pc=b["protocol_paid_costs"]; print("   costs: keeper %.4f | governor LP-ops %.4f | one-off governor admin %.4f | (excluded third-party %.4f)"%(pc["keeper_USD"],pc["governor_LP_ops_USD"],pc["one_off_governor_admin_USD(lower bound)"],pc["third_party_wallets_USD(NOT protocol-paid; excluded)"]))
