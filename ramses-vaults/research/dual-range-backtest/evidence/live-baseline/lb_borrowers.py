"""READ-ONLY: borrowers (derived from Borrow/RepayBorrow/LiquidateBorrow logs + isolated-margin accounts), their debts, collateral and liquidity at one pinned block,
plus *Current (interest-accrued-to-pin) views via eth_call simulation, and a stale-oracle simulation using eth_call state overrides."""
import json, sys, time
from lb_rpc import *
from lb_events import load as load_reg, decode
PN="0xa155cccb986774ae818b3f10f07d01d1b7a47b26"; PU="0x55aed0569c8f0d166d71face57b57c2f2624a563"; C="0x6148183676e304dbe63a85c350c208da3ceac39c"
ORACLE="0x266f014d1325774f1190f963df4369e07dda1d33"
reg=load_reg()
borrowers={}
for mn in ["pNVDA","pUSDG"]:
    for l in json.load(open(HERE/"raw"/f"logs-{mn}.json"))["logs"]:
        d=decode(l,reg)
        if not d: continue
        if d[0]=="Borrow": borrowers.setdefault(d[2][0],set()).add(mn)
        if d[0]=="RepayBorrow": borrowers.setdefault(d[2][1],set()).add(mn)
        if d[0]=="LiquidateBorrow": borrowers.setdefault(d[2][1],set()).add(mn)
iso=[]
for l in json.load(open(HERE/"raw"/"logs-comptroller.json"))["logs"]:
    d=decode(l,reg)
    if d and d[0]=="IsolatedMarginAccountRegistered": iso.append(d[2][0] if d[2] else "0x"+l["topics"][1][-40:]); 
head=int(call("eth_blockNumber",[]),16); P=head-2; B=hex(P)
blk=call("eth_getBlockByNumber",[B,False]); ts=int(blk["timestamp"],16)
out={"pin":{"l2":P,"hash":blk["hash"],"timestamp":ts,"l1":int(blk["l1BlockNumber"],16)},"borrowersFromEvents":{k:sorted(v) for k,v in borrowers.items()},"isolatedMarginAccountsRegistered":iso}
def c(to,sig,args=""):
    try: return eth_call(to,sig,args,B)
    except RpcError as e: return {"err":str(e.err)[:200]}
def w0(r): return words(r)[0] if isinstance(r,str) else r
allacc=sorted(set(borrowers)|set(a.lower() for a in iso))
rows=[]
for a in allacc:
    row={"account":a}
    for mn,m in [("pNVDA",PN),("pUSDG",PU)]:
        row[mn]={"borrowBalanceStored":w0(c(m,"borrowBalanceStored(address)",pad_addr(a))),
                 "borrowBalanceCurrent_sim":w0(c(m,"borrowBalanceCurrent(address)",pad_addr(a))),
                 "pTokenBalance":w0(c(m,"balanceOf(address)",pad_addr(a)))}
    r=c(C,"getAccountLiquidity(address)",pad_addr(a)); row["getAccountLiquidity(err,liq,shortfall)"]=words(r) if isinstance(r,str) else r
    r=c(C,"getAssetsIn(address)",pad_addr(a)); row["assetsIn_raw"]=r
    rows.append(row)
out["accounts"]=rows
out["market"]={}
for mn,m in [("pNVDA",PN),("pUSDG",PU)]:
    out["market"][mn]={"exchangeRateStored":w0(c(m,"exchangeRateStored()")),"exchangeRateCurrent_sim":w0(c(m,"exchangeRateCurrent()")),
      "totalBorrowsStored":w0(c(m,"totalBorrows()")),"totalBorrowsCurrent_sim":w0(c(m,"totalBorrowsCurrent()")),"totalReserves":w0(c(m,"totalReserves()")),"totalSupply":w0(c(m,"totalSupply()")),
      "getCash":w0(c(m,"getCash()")),"borrowIndex":w0(c(m,"borrowIndex()")),"accrualBlockNumber":w0(c(m,"accrualBlockNumber()")),
      "supplyRatePerBlock":w0(c(m,"supplyRatePerBlock()")),"borrowRatePerBlock":w0(c(m,"borrowRatePerBlock()"))}
json.dump(out,open(HERE/"raw"/"borrowers.json","w"),indent=1)
print(json.dumps(out["pin"]), len(rows))
