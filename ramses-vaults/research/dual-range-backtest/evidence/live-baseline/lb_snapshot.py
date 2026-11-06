"""READ-ONLY fresh-state snapshot of the live Peridot Robinhood NVDA/USDG vault + lending markets at ONE pinned block.
usage: python3 lb_snapshot.py [l2block]   -> raw/snapshot.json (pin defaults to head-2)
All reads are eth_call / eth_getCode / eth_getStorageAt / eth_getBlockByNumber at the pinned block."""
import json, sys, subprocess, math
from lb_rpc import *
V="0x280825b2d856706Ff7E0d6351CcB2e935E1a9A2f"; A="0xadA73211711e4790bc83B5d6B39f47fE04D276f3"; G="0xaE4D4DdB8dD646951d54fE9B13BE23DcB61C6741"
R="0x806b182B050f7EcF908758dD6bBF91DB8B2212aF"; PN="0xa155cccb986774ae818b3f10f07d01d1b7a47b26"; PU="0x55aed0569c8f0d166d71face57b57c2f2624a563"
C="0x6148183676e304dbe63a85c350c208da3ceac39c"; NVDA="0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec"; USDG="0x5fc5360d0400a0fd4f2af552add042d716f1d168"
ORACLE_OLD="0x266f014d1325774f1190f963df4369e07dda1d33"; ADAPT_OR="0xe4e03c2fdaef915ace705d106b2660b1e342a2e4"
TL="0x6797FB8Ce049B42C5BC2b42Bf76c6d15C7B12498"; SLIB="0x813AbFeC0DE50f8674798CbaB72Ed7b5D8CcB9cB"
PID="e2050352f4346597cc69d2776d99ae60c9440dfc28f9406ce66be0bbe3fb6b06"
FEED="0x379EC4f7C378F34a1B47E4F3cbeBCbAC3E8E9F15"
IMPL_SLOT="0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"
ADMIN_SLOT="0xb53127684a568b3173ae13b9f8a6016e243e63b6e8ee1178d6a717850b5d6103"
def keccak_hex(h): return subprocess.run(["cast","keccak",h],capture_output=True,text=True).stdout.strip()
def main():
    head = int(call("eth_blockNumber",[]),16)
    P = int(sys.argv[1]) if len(sys.argv)>1 else head-2
    B = hex(P)
    blk = call("eth_getBlockByNumber",[B,False])
    snap = {"pin":{"l2":P,"hash":blk["hash"],"timestamp":int(blk["timestamp"],16),"timestampUtc":subprocess.run(["date","-u","-r",str(int(blk["timestamp"],16)),"+%FT%TZ"],capture_output=True,text=True).stdout.strip(),"l1BlockNumber_native":int(blk["l1BlockNumber"],16),"headAtStart":head}}
    ts = int(blk["timestamp"],16)
    def c(to,sig,args=""):
        try: return {"ok":True,"w":words(eth_call(to,sig,args,B))}
        except RpcError as e: return {"ok":False,"err":str(e.err)[:300]}
        except Exception as e: return {"ok":False,"err":"EXC "+str(e)[:300]}
    S = snap
    # ---- code
    S["code"]={}
    names={"vault":V,"adapter":A,"guard":G,"reserve":R,"pNVDA":PN,"pUSDG":PU,"comptroller":C,"oracleOld":ORACLE_OLD,"lendingPriceAdapter":ADAPT_OR,"timelock":TL,"settlementLib":SLIB,"NVDA":NVDA,"USDG":USDG,"feedProxy":FEED}
    for n,a in names.items():
        code = call("eth_getCode",[a,B])
        S["code"][n]={"address":a,"bytes":(len(code)-2)//2,"keccak":keccak_hex(code) if len(code)>2 else None}
    S["proxySlots"]={}
    for n,a in {"vault":V,"adapter":A,"guard":G,"reserve":R}.items():
        S["proxySlots"][n]={"impl":addr(int(call("eth_getStorageAt",[a,IMPL_SLOT,B]),16)),"admin":addr(int(call("eth_getStorageAt",[a,ADMIN_SLOT,B]),16))}
    for n,a in {"pNVDA":PN,"pUSDG":PU}.items():
        r=c(a,"implementation()"); S["proxySlots"][n]={"implementation":addr(r["w"][0]) if r["ok"] else r}
    r=c(C,"peridottrollerImplementation()"); S["proxySlots"]["comptroller"]={"impl":addr(r["w"][0]) if r["ok"] else r}
    # ---- vault
    v=S["vault"]={}
    r=c(V,"pairConfig(bytes32)",PID); names_pc=["stockToken","usdg","stockAccount","usdgAccount","maxPairValueUSDG","maxSettlementSwapUSDG","maxCheckpointAge","deprecatedMinDeadlineDelay","maxDeadlineDelay","reserveFeeBps","maxSwapSlippageBps","withdrawOverUnwindBps","stockDecimals","usdgDecimals","allocationPaused","swapsPaused","emergencyMode","exists"]
    v["pairConfig"]={n:(addr(x) if n in("stockToken","usdg","stockAccount","usdgAccount") else (bool(x) if n in("allocationPaused","swapsPaused","emergencyMode","exists") else x)) for n,x in zip(names_pc,r["w"])}
    r=c(V,"ledger(bytes32)",PID); v["ledger"]=dict(zip(["stockPrincipal","usdgPrincipal","stockIdle","usdgIdle","cumulativeLossUSDG","lastCheckpoint"],r["w"]))
    v["ledger"]["lastCheckpointAgeSec"]=ts-v["ledger"]["lastCheckpoint"]
    r=c(V,"rangePolicy(bytes32)",PID); v["rangePolicy"]=dict(zip(["enabled","halfWidthTicks","triggerTicks","minIntervalSeconds","maxPerDay","maxLossBps","maxRangedValueUsdE18"],r["w"]))
    r=c(V,"rangeState(bytes32)",PID); v["rangeState"]={"raw":r.get("w"),"centerTick":sword(r["w"][0]) if r["ok"] else None,"initialized":r["w"][1] if r["ok"] else None,"lastRecenter":r["w"][2] if r["ok"] else None,"head":r["w"][3] if r["ok"] else None}
    for n,sg,ar in [("aggregateUsdgPrincipal","aggregateUsdgPrincipal(address)",pad_addr(USDG)),("aggregateUsdgDepositCap","aggregateUsdgDepositCap(address)",pad_addr(USDG))]:
        r=c(V,sg,ar); v[n]=r["w"][0] if r["ok"] else r
    for tn,t in [("stock",NVDA),("usdg",USDG)]:
        for fn in ["accountedAssets","liquidAssets","withdrawableAssets"]:
            r=c(V,f"{fn}(bytes32,address)",PID+pad_addr(t)); v[f"{fn}_{tn}"]=r["w"][0] if r["ok"] else r
    r=c(V,"totalPairAssets(bytes32)",PID); v["totalPairAssets_poolPriced"]=dict(zip(["stock","usdg"],r["w"])) if r["ok"] else r
    for n,sg in [("oracleGuard","oracleGuard()"),("lossReserve","lossReserve()"),("liquidityAdapter","liquidityAdapter()")]:
        r=c(V,sg); v[n]=addr(r["w"][0])
    # ---- balances
    S["balances"]={}
    for holder,ha in {"vault":V,"adapter":A,"reserve":R,"pNVDA":PN,"pUSDG":PU,"timelock":TL}.items():
        S["balances"][holder]={}
        for tn,t in [("NVDA",NVDA),("USDG",USDG)]:
            r=c(t,"balanceOf(address)",pad_addr(ha)); S["balances"][holder][tn]=r["w"][0] if r["ok"] else r
    # ---- adapter / pool / position
    a=S["adapter"]={}
    r=c(A,"positionState(bytes32)",PID); a["positionState"]=dict(zip(["tokenId","liquidity","stockAmount","usdgAmount"],r["w"]))
    r=c(A,"positionTicks(bytes32)",PID); a["positionTicks"]={"tickLower":sword(r["w"][0]),"tickUpper":sword(r["w"][1]),"ranged":bool(r["w"][2])}
    r=c(A,"poolKey(bytes32)",PID); pk=r["w"]; a["poolKey"]={"currency0":addr(pk[0]),"currency1":addr(pk[1]),"fee":pk[2],"tickSpacing":sword(pk[3]),"hooks":addr(pk[4])}
    pool_id = keccak_hex("0x"+"".join(pad_int(x) if i not in(0,1,4) else pad_addr(addr(x)) for i,x in enumerate(pk)))
    a["poolId"]=pool_id
    r=c(A,"removalTolerance(bytes32)",PID); a["removalToleranceBps"]=r["w"][0] if r["ok"] else r
    PM="0x8366a39cc670b4001a1121b8f6a443a643e40951"
    st=keccak_hex("0x"+pool_id[2:]+pad_int(6))
    s0=int(call("eth_call",[{"to":PM,"data":sel("extsload(bytes32)")+st[2:]},B]),16)
    sqrtp=s0&((1<<160)-1); tick=(s0>>160)&0xffffff; tick = tick-(1<<24) if tick>=(1<<23) else tick
    lp_fee=(s0>>208)&0xffffff
    liq_slot=hex(int(st,16)+3)
    pliq=int(call("eth_call",[{"to":PM,"data":sel("extsload(bytes32)")+liq_slot[2:].rjust(64,"0")},B]),16)
    a["pool"]={"poolManager":PM,"sqrtPriceX96":sqrtp,"tick":tick,"lpFee":lp_fee,"poolActiveLiquidity":pliq,"slot0raw":hex(s0)}
    # ---- guard / feed / token
    g=S["guard"]={}
    r=c(G,"feedConfig(bytes32)",PID)
    fn=["stockToken","usdg","stockFeed","usdgFeed","sequencerFeed","poolId","maxStaleness","sequencerGracePeriod","maxPriceDeviationBps","stockDecimals","usdgDecimals","stockFeedDecimals","usdgFeedDecimals","usdgFixedOne","enabled","maxRemovalDeviationBps"]
    g["feedConfig"]={n:(addr(x) if n in("stockToken","usdg","stockFeed","usdgFeed","sequencerFeed") else (hex(x) if n=="poolId" else x)) for n,x in zip(fn,r["w"])}
    for n,sg in [("maxPriceDeviationBps","maxPriceDeviationBps(bytes32)"),("maxRemovalDeviationBps","maxRemovalDeviationBps(bytes32)")]:
        r=c(G,sg,PID); g[n]=r["w"][0] if r["ok"] else r
    r=c(G,"pricesUSD18(bytes32)",PID); g["pricesUSD18"]=dict(zip(["stock","usdg"],r["w"])) if r["ok"] else r
    sf=g["feedConfig"]["stockFeed"]
    r=c(sf,"latestRoundData()"); 
    if r["ok"]:
        g["feed"]={"roundId":r["w"][0],"answer":sword(r["w"][1]),"startedAt":r["w"][2],"updatedAt":r["w"][3],"answeredInRound":r["w"][4],"ageSec":ts-r["w"][3]}
    else: g["feed"]=r
    r=c(sf,"decimals()"); g["feed"]["decimals"]=r["w"][0]
    r=c(sf,"description()"); g["feed"]["descriptionRaw"]=r.get("w")
    for fnm in ["oraclePaused()","paused()"]:
        r=c(NVDA,fnm); g["NVDA_"+fnm]=bool(r["w"][0]) if r["ok"] else r
    r=c(NVDA,"uiMultiplier()"); g["NVDA_uiMultiplier"]=r["w"][0] if r["ok"] else r
    # validatePoolPrice / validateRemovalPrice need the poolKey tuple encoded
    pkenc="".join(pad_int(x) if i in(2,3) else pad_addr(addr(x)) for i,x in enumerate(pk))
    if pk[3]>=(1<<255): pass
    pkenc = pad_addr(addr(pk[0]))+pad_addr(addr(pk[1]))+pad_int(pk[2])+pad_int(pk[3])+pad_addr(addr(pk[4]))
    for n,sg in [("validatePoolPrice","validatePoolPrice(bytes32,(address,address,uint24,int24,address))"),("validateRemovalPrice","validateRemovalPrice(bytes32,(address,address,uint24,int24,address))")]:
        r=c(G,sg,PID+pkenc); g[n]=dict(zip(["oracleStockInUsdgWad","poolStockInUsdgWad","referenceSqrtPriceX96"],r["w"])) if r["ok"] else r
    # reference sqrt price (own computation from feed answer if guard OK) -> positionStateAt
    ref=None
    if g["validateRemovalPrice"].get("referenceSqrtPriceX96"): ref=g["validateRemovalPrice"]["referenceSqrtPriceX96"]
    if ref:
        r=c(A,"positionStateAt(bytes32,uint160)",PID+pad_int(ref)); a["positionStateAtOracleRef"]=dict(zip(["tokenId","liquidity","stockAmount","usdgAmount"],r["w"])) if r["ok"] else r
    else:
        a["positionStateAtOracleRef"]="guard reverted: no oracle-reference computed on-chain"
    # ---- reserve
    rs=S["reserve"]={}
    r=c(R,"reserveConfig(bytes32)",PID); rs["reserveConfig"]=dict(zip(["stockToken","usdg","maxUsePerTxUSDG","dailyCapUSDG","maxCoverageBps","paused","exists"],[addr(r["w"][0]),addr(r["w"][1])]+r["w"][2:]))
    for tn,t in [("NVDA",NVDA),("USDG",USDG)]:
        r=c(R,"available(bytes32,address)",PID+pad_addr(t)); rs["available_"+tn]=r["w"][0] if r["ok"] else r
        r=c(R,"accountedBalance(address)",pad_addr(t)); rs["accountedBalance_"+tn]=r["w"][0] if r["ok"] else r
    r=c(R,"dailyUsage(bytes32)",PID); rs["dailyUsage"]=dict(zip(["day","usedUSDG"],r["w"])) if r["ok"] else r
    # ---- comptroller / oracle
    cp=S["comptroller"]={}
    r=c(C,"oracle()"); cp["oracle"]=addr(r["w"][0])
    for n in ["closeFactorMantissa()","liquidationIncentiveMantissa()"]:
        r=c(C,n); cp[n]=r["w"][0] if r["ok"] else r
    for n in ["seizeGuardianPaused()","transferGuardianPaused()"]:
        r=c(C,n); cp[n]=bool(r["w"][0]) if r["ok"] else r
    for mn,m in [("pNVDA",PN),("pUSDG",PU)]:
        d={}
        r=c(C,"markets(address)",pad_addr(m)); d["markets"]=dict(zip(["isListed","collateralFactorMantissa","isPeridoted"],r["w"])) if r["ok"] else r
        for n in ["mintGuardianPaused(address)","borrowGuardianPaused(address)","borrowCaps(address)","supplyCaps(address)"]:
            r=c(C,n,pad_addr(m)); d[n]=r["w"][0] if r["ok"] else r
        r=c(cp["oracle"],"getUnderlyingPrice(address)",pad_addr(m)); d["oracle_getUnderlyingPrice"]=r["w"][0] if r["ok"] else r
        cp[mn]=d
    # lending oracle details (adapter wraps old oracle)
    o=S["oracle"]={"controllerOracle":cp["oracle"]}
    r=c(cp["oracle"],"source()"); o["adapterSource"]=addr(r["w"][0]) if r["ok"] else r
    src = o["adapterSource"] if isinstance(o["adapterSource"],str) else ORACLE_OLD
    r=c(src,"stockChainlinkPriceStaleThreshold()"); o["stockStaleThresholdSec"]=r["w"][0] if r["ok"] else r
    r=c(src,"chainlinkPriceStaleThreshold()"); o["defaultStaleThresholdSec"]=r["w"][0] if r["ok"] else r
    for tn,t in [("NVDA",NVDA),("USDG",USDG)]:
        d={}
        r=c(src,"assetToAggregator(address)",pad_addr(t)); d["aggregator"]=addr(r["w"][0]) if r["ok"] else r
        r=c(src,"lastValidChainlinkPrice(address)",pad_addr(t)); d["lastValidChainlinkPrice"]=r["w"][0] if r["ok"] else r
        r=c(src,"isStockAsset(address)",pad_addr(t)); d["isStockAsset"]=bool(r["w"][0]) if r["ok"] else r
        r=c(src,"isPriceStale(address)",pad_addr(t)); d["isPriceStale"]=bool(r["w"][0]) if r["ok"] else r
        r=c(src,"assetPrices(address)",pad_addr(t)); d["assetPrices"]=r["w"][0] if r["ok"] else r
        # manual prices mapping slot 0
        slot=keccak_hex("0x"+pad_addr(t)+pad_int(0))
        d["manualPrice_slot0map"]=int(call("eth_getStorageAt",[src,slot,B]),16)
        o[tn]=d
    # ---- pTokens
    S["pTokens"]={}
    for mn,m in [("pNVDA",PN),("pUSDG",PU)]:
        d={}
        for n in ["totalSupply()","exchangeRateStored()","totalBorrows()","totalReserves()","getCash()","supplyRatePerBlock()","borrowRatePerBlock()","reserveFactorMantissa()","accrualBlockNumber()","borrowIndex()","decimals()","totalBorrowShares()","initialExchangeRateMantissa()"]:
            r=c(m,n); d[n[:-2]]=r["w"][0] if r["ok"] else r
        for n in ["underlying()","interestRateModel()","peridottroller()","admin()"]:
            r=c(m,n); d[n[:-2]]=addr(r["w"][0]) if r["ok"] else r
        for n in ["vaultAccountedAssets()","vaultLiquidAssets()","vaultWithdrawableAssets()","totalManagedAssets()","vaultBufferMantissa()","vaultPaused()","vaultOperator()"]:
            r=c(m,n); d[n[:-2]]=r["w"][0] if r["ok"] else r
        S["pTokens"][mn]=d
    irm=S["pTokens"]["pNVDA"]["interestRateModel"]
    S["irm"]={"address":irm}
    for n in ["baseRatePerBlock()","multiplierPerBlock()","jumpMultiplierPerBlock()","kink()","blocksPerYear()"]:
        r=c(irm,n); S["irm"][n[:-2]]=r["w"][0] if r["ok"] else r
    json.dump(S,open(HERE/"raw"/"snapshot.json","w"),indent=1)
    print(json.dumps(S["pin"]))
main()
