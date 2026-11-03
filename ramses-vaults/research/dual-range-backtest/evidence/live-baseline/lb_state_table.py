"""Derived USD/composition table from raw/snapshot.json (pin) + raw/borrowers.json. Pure arithmetic."""
import json
from lb_rpc import HERE
S=json.load(open(HERE/"raw"/"snapshot.json")); Bw=json.load(open(HERE/"raw"/"borrowers.json"))
px=S["guard"]["pricesUSD18"]["stock"]/1e18          # guard price (fresh, 12h-gated)
feed=S["guard"]["feed"]; 
L=S["vault"]["ledger"]; pos=S["adapter"]["positionState"]; ref=S["adapter"]["positionStateAtOracleRef"]
n=lambda x:x/1e18; u=lambda x:x/1e6
out={"pin":S["pin"],"nvda_usd_oracle":px,"feed":{"answer_usd":feed["answer"]/10**feed["decimals"],"updatedAt":feed["updatedAt"],"age_sec_at_pin":feed["ageSec"],"age_h":feed["ageSec"]/3600}}
out["position"]={"tokenId":pos["tokenId"],"tickLower":S["adapter"]["positionTicks"]["tickLower"],"tickUpper":S["adapter"]["positionTicks"]["tickUpper"],"poolTick":S["adapter"]["pool"]["tick"],"inRange":S["adapter"]["positionTicks"]["tickLower"]<=S["adapter"]["pool"]["tick"]<S["adapter"]["positionTicks"]["tickUpper"],
  "liquidity":pos["liquidity"],"poolActiveLiquidity":S["adapter"]["pool"]["poolActiveLiquidity"],"shareOfActivePoolLiquidity_pct":100*pos["liquidity"]/S["adapter"]["pool"]["poolActiveLiquidity"],
  "atPoolSpot":{"NVDA":n(pos["stockAmount"]),"USDG":u(pos["usdgAmount"]),"USD":n(pos["stockAmount"])*px+u(pos["usdgAmount"])},
  "atOracleRef":{"NVDA":n(ref["stockAmount"]),"USDG":u(ref["usdgAmount"]),"USD":n(ref["stockAmount"])*px+u(ref["usdgAmount"])},
  "poolPriceNVDAinUSDG":S["guard"]["validatePoolPrice"]["poolStockInUsdgWad"]/1e18,"oraclePriceNVDAinUSDG":S["guard"]["validatePoolPrice"]["oracleStockInUsdgWad"]/1e18,
  "poolVsOracle_bps":10000*(S["guard"]["validatePoolPrice"]["poolStockInUsdgWad"]/S["guard"]["validatePoolPrice"]["oracleStockInUsdgWad"]-1)}
idleS,idleU=L["stockIdle"],L["usdgIdle"]
tot_s=idleS+ref["stockAmount"]; tot_u=idleU+ref["usdgAmount"]
out["vault_ledger"]={"stockPrincipal":L["stockPrincipal"],"usdgPrincipal":L["usdgPrincipal"],"stockIdle":idleS,"usdgIdle":idleU,"cumulativeLossUSDG":L["cumulativeLossUSDG"]/1e18,"lastCheckpoint":L["lastCheckpoint"],"lastCheckpointAgeSec":L["lastCheckpointAgeSec"],
  "principal_USD(benchmark at oracle)":n(L["stockPrincipal"])*px+u(L["usdgPrincipal"]),
  "assets_at_oracleRef":{"NVDA":n(tot_s),"USDG":u(tot_u),"USD":n(tot_s)*px+u(tot_u)},
  "gross_minus_benchmark_USD":(n(tot_s)*px+u(tot_u))-(n(L["stockPrincipal"])*px+u(L["usdgPrincipal"])),
  "native_surplus_NVDA(assets-principal)":n(tot_s-L["stockPrincipal"]),"native_shortfall_USDG(principal-assets)":u(L["usdgPrincipal"]-tot_u),
  "idle_NVDA_above_principal":n(idleS-L["stockPrincipal"]),"vault_token_balances":S["balances"]["vault"]}
for m,dec,tok in (("pNVDA",18,"NVDA"),("pUSDG",6,"USDG")):
    t=S["pTokens"][m]; sc=10**dec
    nav=(t["totalManagedAssets"]+t["totalBorrows"]-t["totalReserves"])
    out[m]={"totalSupply_pTokens":t["totalSupply"],"exchangeRateStored":t["exchangeRateStored"],"exchangeRate_vs_initial_pct":100*(t["exchangeRateStored"]/(2e26 if m=="pNVDA" else 2e14)-1),
      "NAV_underlying":nav/sc,"NAV_USD":nav/sc*(px if m=="pNVDA" else 1),"getCash":t["getCash"]/sc,"localBalance":S["balances"][m][tok]/sc,"vaultAccounted":t["vaultAccountedAssets"]/sc,"vaultWithdrawable_now":t["vaultWithdrawableAssets"]/sc,
      "totalBorrows":t["totalBorrows"]/sc,"totalBorrows_accrued_to_pin2(eth_call sim)":Bw["market"][m]["totalBorrowsCurrent_sim"]/sc,"totalReserves":t["totalReserves"]/sc,
      "utilisation_cashModel_pct":100*t["totalBorrows"]/(t["getCash"]+t["totalBorrows"]-t["totalReserves"]),
      "supplyAPR_pct(rate*2628000)":t["supplyRatePerBlock"]*2628000/1e16,"borrowAPR_pct(rate*2628000)":t["borrowRatePerBlock"]*2628000/1e16,
      "supplyAPY_pct_compound_per_blockModel":100*((1+t["supplyRatePerBlock"]/1e18)**2628000-1),
      "reserveFactor":t["reserveFactorMantissa"]/1e18,"accrualBlockNumber":t["accrualBlockNumber"],"nativeBlocksSinceLastAccrual":S["pin"]["l1BlockNumber_native"]-t["accrualBlockNumber"],
      "instantlyRedeemable_underlying(local+withdrawable)":(S["balances"][m][tok]+t["vaultWithdrawableAssets"])/sc}
out["TVL_USD"]=out["pNVDA"]["NAV_USD"]+out["pUSDG"]["NAV_USD"]
out["vault_principal_share_of_TVL_pct"]=100*out["vault_ledger"]["principal_USD(benchmark at oracle)"]/out["TVL_USD"]
out["reserve"]={"NVDA":S["reserve"]["available_NVDA"]/1e18,"USDG":S["reserve"]["available_USDG"]/1e6,"USD":S["reserve"]["available_NVDA"]/1e18*px+S["reserve"]["available_USDG"]/1e6,"config":S["reserve"]["reserveConfig"]}
accs=[]
for a in Bw["accounts"]:
    d_n=a["pNVDA"]["borrowBalanceCurrent_sim"]/1e18*px; d_u=a["pUSDG"]["borrowBalanceCurrent_sim"]/1e6
    c_n=a["pNVDA"]["pTokenBalance"]*S["pTokens"]["pNVDA"]["exchangeRateStored"]/1e18/1e18*px
    c_u=a["pUSDG"]["pTokenBalance"]*S["pTokens"]["pUSDG"]["exchangeRateStored"]/1e18/1e6
    accs.append({"account":a["account"],"isolatedMargin":a["account"] in Bw["isolatedMarginAccountsRegistered"],"debt_NVDA_raw_current":a["pNVDA"]["borrowBalanceCurrent_sim"],"debt_USDG_raw_current":a["pUSDG"]["borrowBalanceCurrent_sim"],"debt_USD":d_n+d_u,"collateral_pNVDA_USD":c_n,"collateral_pUSDG_USD":c_u,"controller_getAccountLiquidity":a["getAccountLiquidity(err,liq,shortfall)"],"controller_liquidity_USD":a["getAccountLiquidity(err,liq,shortfall)"][1]/1e18,"controller_shortfall_USD":a["getAccountLiquidity(err,liq,shortfall)"][2]/1e18})
out["accounts"]=accs
out["total_debt_USD"]=sum(a["debt_USD"] for a in accs)
json.dump(out,open(HERE/"state-table.json","w"),indent=1)
print(json.dumps(out,indent=1))
