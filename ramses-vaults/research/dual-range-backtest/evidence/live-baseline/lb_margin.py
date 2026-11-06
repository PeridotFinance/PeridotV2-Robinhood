"""READ-ONLY: isolated-margin stack reads at one pinned block (own pin): accounts' metrics from the risk engine, margin price source wiring, marketPriceable, opensPaused."""
import json, subprocess
from lb_rpc import *
AD=json.load(open("/Users/joshua/Peridot/RobinhoodVaults/deployments/margin-mainnet-live/addresses.json"))
RE=AD["riskEngine"]; ORC=AD["oracle"]; CFG=AD["config"]; MV=AD["marginVault"]; GS=AD["guardedSource"]
PN="0xa155cccb986774ae818b3f10f07d01d1b7a47b26"; PU="0x55aed0569c8f0d166d71face57b57c2f2624a563"
ISO=json.load(open(HERE/"raw"/"borrowers.json"))["isolatedMarginAccountsRegistered"]
head=int(call("eth_blockNumber",[]),16); P=head-2; B=hex(P); blk=call("eth_getBlockByNumber",[B,False])
out={"pin":{"l2":P,"hash":blk["hash"],"timestamp":int(blk["timestamp"],16),"l1":int(blk["l1BlockNumber"],16)},"addresses":AD}
def c(to,sig,args=""):
    try: return eth_call(to,sig,args,B)
    except RpcError as e: return {"err":str(e.err)[:200]}
code=lambda a:(len(call("eth_getCode",[a,B]))-2)//2
out["code_bytes"]={k:code(AD[k]) for k in("riskEngine","oracle","config","marginVault","guardedSource","liquidator","accountFactory","executor","quoter","swapModule","insuranceFund","flashVault")}
r=c(ORC,"assetSource()"); out["marginOracle.assetSource"]=addr(words(r)[0]) if isinstance(r,str) else r
out["assetSource_is_GuardedMarginPriceSource(addresses.json guardedSource)"]=(out["marginOracle.assetSource"].lower()==GS.lower()) if isinstance(out["marginOracle.assetSource"],str) else None
r=c(GS,"guard()"); out["guardedSource.guard"]=addr(words(r)[0]) if isinstance(r,str) else r
r=c(GS,"lendingSource()"); out["guardedSource.lendingSource"]=addr(words(r)[0]) if isinstance(r,str) else r
r=c(GS,"pairId()"); out["guardedSource.pairId"]=r
for nm,m in (("pNVDA",PN),("pUSDG",PU)):
    r=c(ORC,"marketPriceable(address)",pad_addr(m)); out["marketPriceable_"+nm]=bool(words(r)[0]) if isinstance(r,str) else r
    r=c(ORC,"marketAsset(address)",pad_addr(m)); out["marginOracle.marketAsset_"+nm]=addr(words(r)[0]) if isinstance(r,str) else r
    r=c(ORC,"getPrice(address)",pad_addr(out["marginOracle.marketAsset_"+nm])); out["marginOracle.getPrice(underlying)_"+nm]=words(r)[0] if isinstance(r,str) else r
r=c(CFG,"opensPaused()"); out["opensPaused"]=bool(words(r)[0]) if isinstance(r,str) else r
out["pairRisk"]={}
for lbl,(a,b_,d) in {"long(margin=pUSDG,pos=pNVDA,debt=pUSDG)":(PU,PN,PU),"short(margin=pUSDG,pos=pUSDG,debt=pNVDA)":(PU,PU,PN)}.items():
    r=c(CFG,"getPairRisk(address,address,address)",pad_addr(a)+pad_addr(b_)+pad_addr(d))
    if isinstance(r,str):
        w=words(r); out["pairRisk"][lbl]=dict(zip(["enabled","maxLeverageX100","initialMarginBps","maintenanceMarginBps","liquidationTargetBps","fullLiquidationHealthBps","maxLiquidationBps","liquidationBonusBps","maxSlippageBps","oracleDeviationBps","maxPositionValueUsd","maxDebtValueUsd"],w))
    else: out["pairRisk"][lbl]=r
out["accounts"]=[]
for a in ISO:
    row={"account":a}
    r=c(RE,"accounts(address)",pad_addr(a))
    if isinstance(r,str):
        w=words(r); row["record"]=dict(zip(["positionId","owner","marginPToken","positionPToken","debtPToken","side(0=long?)","status"],[w[0],addr(w[1]),addr(w[2]),addr(w[3]),addr(w[4]),w[5],w[6]]))
    else: row["record"]=r
    r=c(RE,"getMetrics(address)",pad_addr(a))
    if isinstance(r,str):
        w=words(r); row["metrics"]=dict(zip(["grossAssetValueUsd","debtValueUsd","equityUsd","initialRequirementUsd","maintenanceRequirementUsd","healthFactorBps","leverageX100"],[w[0],w[1],sword(w[2]),w[3],w[4],w[5],w[6]]))
    else: row["metrics"]=r
    r=c(RE,"isLiquidatable(address)",pad_addr(a)); row["isLiquidatable"]=bool(words(r)[0]) if isinstance(r,str) else r
    out["accounts"].append(row)
r=c(MV,"totalFreeBalance(address)",pad_addr(PU)); out["marginVault.totalFreeBalance_pUSDG"]=words(r)[0] if isinstance(r,str) else r
r=c(MV,"totalLockedBalance(address)",pad_addr(PU)); out["marginVault.totalLockedBalance_pUSDG"]=words(r)[0] if isinstance(r,str) else r
r=c(PU,"balanceOf(address)",pad_addr(MV)); out["marginVault.pUSDG_balance"]=words(r)[0] if isinstance(r,str) else r
json.dump(out,open(HERE/"margin-state.json","w"),indent=1)
print(json.dumps({k:v for k,v in out.items() if k!="addresses"},indent=1))
