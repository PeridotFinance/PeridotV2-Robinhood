"""READ-ONLY simulation (eth_call with a state override of the lending oracle's STOCK staleness threshold) of what the LIVE lending oracle/controller do when the
NVDA feed is older than the oracle's bound. No transaction is sent; the override exists only inside the eth_call.
slot layout of StockSimplePriceOracle: 0 prices, 1 admin, 2 assetToAggregator, 3 lastValidChainlinkPrice, 4 owner, 5 chainlinkPriceStaleThreshold, 6 stockChainlinkPriceStaleThreshold, 7 isStockAsset."""
import json
from lb_rpc import *
ORACLE="0x266f014d1325774f1190f963df4369e07dda1d33"; ADAPTER="0xe4e03c2fdaef915ace705d106b2660b1e342a2e4"; C="0x6148183676e304dbe63a85c350c208da3ceac39c"
PN="0xa155cccb986774ae818b3f10f07d01d1b7a47b26"; PU="0x55aed0569c8f0d166d71face57b57c2f2624a563"
NVDA="0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec"
head=int(call("eth_blockNumber",[]),16); P=head-2; B=hex(P)
out={"pin":P}
slots={str(i):call("eth_getStorageAt",[ORACLE,hex(i),B]) for i in range(0,9)}
out["oracleSlots0to8"]=slots
assert int(slots["6"],16)==259200, slots["6"]
def cc(to,sig,args,override=None,frm=None):
    tx={"to":to,"data":sel(sig)+args}
    if frm: tx["from"]=frm
    params=[tx,B]+([override] if override else [])
    try: return call("eth_call",params)
    except RpcError as e: return "REVERT "+json.dumps(e.err)[:300]
ov=lambda thr:{ORACLE:{"stateDiff":{"0x"+"0"*63+"6":"0x"+hex(thr)[2:].rjust(64,"0")}}}
borrower="0x12c1e2c33f63f897e0e4ac1969c29493136f2481"   # non-isolated? checked below via liquidity err
res={}
for label,thr in [("as_live_72h",259200),("feed_older_than_bound(thr=1s)",1)]:
    o=None if thr==259200 else ov(thr)
    r={}
    r["adapter.getUnderlyingPrice(pNVDA)"]=cc(ADAPTER,"getUnderlyingPrice(address)",pad_addr(PN),o)
    r["adapter.getUnderlyingPrice(pUSDG)"]=cc(ADAPTER,"getUnderlyingPrice(address)",pad_addr(PU),o)
    r["controller.getAccountLiquidity(0x12c1..)"]=cc(C,"getAccountLiquidity(address)",pad_addr(borrower),o)
    r["controller.getAccountLiquidity(deployer)"]=cc(C,"getAccountLiquidity(address)",pad_addr("0x94696d767e65a75581145646960fa0ec886ce5d2"),o)
    # borrowAllowed must be called by the pToken (msg.sender==pToken) for a not-yet-member; use from=pUSDG
    r["controller.borrowAllowed(pNVDA, deployer, 1)  [from pNVDA]"]=cc(C,"borrowAllowed(address,address,uint256)",pad_addr(PN)+pad_addr("0x94696d767e65a75581145646960fa0ec886ce5d2")+pad_int(1),o,frm=PN)
    r["controller.borrowAllowed(pUSDG, deployer, 1)  [from pUSDG]"]=cc(C,"borrowAllowed(address,address,uint256)",pad_addr(PU)+pad_addr("0x94696d767e65a75581145646960fa0ec886ce5d2")+pad_int(1),o,frm=PU)
    r["controller.redeemAllowed(pUSDG, 0x12c1.., 1)  [member w/ debt]"]=cc(C,"redeemAllowed(address,address,uint256)",pad_addr(PU)+pad_addr(borrower)+pad_int(1),o,frm=PU)
    r["controller.liquidateBorrowAllowed(pUSDG,pNVDA,liq,0x12c1..,1)"]=cc(C,"liquidateBorrowAllowed(address,address,address,address,uint256)",pad_addr(PU)+pad_addr(PN)+pad_addr("0x00000000000000000000000000000000000000aa")+pad_addr(borrower)+pad_int(1),o,frm=PU)
    r["controller.liquidateCalculateSeizeTokens(pUSDG,pNVDA,1000000)"]=cc(C,"liquidateCalculateSeizeTokens(address,address,uint256)",pad_addr(PU)+pad_addr(PN)+pad_int(1000000),o)
    res[label]=r
out["sim"]=res
json.dump(out,open(HERE/"stale-oracle-simulation.json","w"),indent=1)
for k,v in res.items():
    print("==",k)
    for a,b in v.items(): print("  ",a,"->",b if len(str(b))<200 else str(b)[:200])
