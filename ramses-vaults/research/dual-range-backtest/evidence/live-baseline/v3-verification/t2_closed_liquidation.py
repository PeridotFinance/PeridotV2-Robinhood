"""READ-ONLY eth_call simulation only (never sends): liquidation on CLOSED margin positions at one pinned block."""
import json,sys,pathlib
HERE=pathlib.Path(__file__).parent; sys.path.insert(0,str(HERE))
from rpc_util import *
AD=json.load(open("/Users/joshua/Peridot/RobinhoodVaults/deployments/margin-mainnet-live/addresses.json"))
EX,LQ,RE,ORC=AD["executor"],AD["liquidator"],AD["riskEngine"],AD["oracle"]
PU,PN=AD["pUsd"],AD["pStock"]
ANY="0x00000000000000000000000000000000000dEaD1"
KEEPER="0x16aEC17597E5224998e2043C9c83C4a35dD95A86"
head=int(call("eth_blockNumber",[]),16); P=head-5; B=hex(P); blk=call("eth_getBlockByNumber",[B,False])
out={"pin":{"l2":P,"hash":blk["hash"],"timestamp":int(blk["timestamp"],16),"l1":int(blk.get("l1BlockNumber","0x0"),16)},"caller":ANY}
IMPL="0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"; ADM="0xb53127684a568b3173ae13b9f8a6016e243e63b6e8ee1178d6a717850b5d6103"
out["proxies"]={}
for k in ("executor","liquidator","riskEngine","config","marginVault"):
    a=AD[k]; i=adr(int(call("eth_getStorageAt",[a,IMPL,B]),16)); c=call("eth_getCode",[i,B])
    out["proxies"][k]={"proxy":a,"impl":i,"admin":adr(int(call("eth_getStorageAt",[a,ADM,B]),16)),"implCodeBytes":(len(c)-2)//2,"implCodehash":keccak(c)}
n=words(ecall(EX,sel("nextPositionId()"),B))[0]; out["nextPositionId"]=n
S={0:"NONE",1:"OPENING",2:"ACTIVE",3:"CLOSING",4:"LIQUIDATING",5:"CLOSED",6:"LIQUIDATED"}
def liq(pid,frm=ANY,over=None,recipient=None):
    d=calldata("liquidate((uint256,address,uint256,uint256,bytes,bytes))",f"({pid},{recipient or frm},0,0,0x,0x)")
    return try_call(LQ,d,B,frm,over)
# locate mapping base slots by probing (engine.accounts, executor.positions) for counterfactual overrides
def find_slot(contract,key_hex,expect,next_word=None,rng=range(0,120)):
    # next_word disambiguates from e.g. a bool mapping holding 1 when pid==1
    for s in rng:
        k=keccak("0x"+key_hex+hex(s)[2:].rjust(64,"0"))
        if int(call("eth_getStorageAt",[contract,k,B]),16)==expect:
            if next_word is None: return s,k
            if int(call("eth_getStorageAt",[contract,hex(int(k,16)+1),B]),16)==next_word: return s,k
    return None,None
out["positions"]=[]; posSlot=None; engSlot=None
for pid in range(1,n):
    w=words(ecall(EX,calldata("positions(uint256)",pid),B)); acct=adr(w[2])
    row={"positionId":pid,"owner":adr(w[1]),"account":acct,"marginPToken":adr(w[3]),"positionPToken":adr(w[4]),"debtPToken":adr(w[5]),"lockedMarginPTokens":w[6],"borrowedPrincipal":w[8],"requestedLeverageX100":w[9],"side":w[10],"executorStatus":S[w[11]]}
    e=words(ecall(RE,calldata("accounts(address)",acct),B)); row["engineStatus"]=S[e[6]]
    row["pUSDG_balance"]=words(ecall(PU,calldata("balanceOf(address)",acct),B))[0]
    row["pNVDA_balance"]=words(ecall(PN,calldata("balanceOf(address)",acct),B))[0]
    row["debt_pUSDG_stored"]=words(ecall(PU,calldata("borrowBalanceStored(address)",acct),B))[0]
    row["debt_pNVDA_stored"]=words(ecall(PN,calldata("borrowBalanceStored(address)",acct),B))[0]
    row["debt_current_debtMarket(eth_call)"]=try_call(adr(w[5]),calldata("borrowBalanceCurrent(address)",acct),B)
    if row["debt_current_debtMarket(eth_call)"]["ok"]: row["debt_current_debtMarket(eth_call)"]=int(row["debt_current_debtMarket(eth_call)"]["return"],16)
    m=try_call(RE,calldata("getMetrics(address)",acct),B)
    if m["ok"]:
        x=words(m["return"]); row["metrics"]=dict(gross=x[0],debt=x[1],equity=x[2]-(1<<256) if x[2]>>255 else x[2],initReq=x[3],maintReq=x[4],healthBps=("MAX" if x[5]==(1<<256)-1 else x[5]),levX100=("MAX" if x[6]==(1<<256)-1 else x[6]))
    else: row["metrics"]=m
    r=try_call(RE,calldata("isLiquidatable(address)",acct),B); row["isLiquidatable"]=(bool(int(r["return"],16)) if r["ok"] else r)
    row["liquidate_from_arbitrary"]=liq(pid)
    row["liquidate_from_keeper"]=liq(pid,KEEPER)
    row["beginLiquidation_from_liquidator"]=try_call(RE,calldata("beginLiquidation(address)",acct),B,LQ)
    row["beginLiquidation_from_arbitrary"]=try_call(RE,calldata("beginLiquidation(address)",acct),B,ANY)
    row["authorizeMovement_from_liquidator"]=try_call(RE,calldata("authorizeMovement(address,address,uint256)",acct,adr(w[4]),1),B,LQ)
    # account-level seizure entry is liquidator-only
    row["liquidationRedeem_from_arbitrary"]=try_call(acct,calldata("liquidationRedeem(address,uint256,address)",adr(w[4]),1,ANY),B,ANY)
    # ordinary lending liquidation path against the same account, small repay, both market orderings
    row["ordinary_liquidateBorrow"]={}
    for rp,col,nm in ((PU,PN,"repay_pUSDG_seize_pNVDA"),(PU,PU,"repay_pUSDG_seize_pUSDG"),(PN,PU,"repay_pNVDA_seize_pUSDG"),(PN,PN,"repay_pNVDA_seize_pNVDA")):
        row["ordinary_liquidateBorrow"][nm]=try_call(rp,calldata("liquidateBorrow(address,uint256,address)",acct,1000,col),B,ANY)
    if w[11]==5 and posSlot is None:
        posSlot,_=find_slot(EX,hex(pid)[2:].rjust(64,"0"),pid,w[1])
        engSlot,_=find_slot(RE,pad(acct),pid,w[1])
    out["positions"].append(row)
out["nonexistent_position_liquidate"]=liq(n+1000)
out["zero_recipient_on_closed"]=liq(1,ANY,None,"0x0000000000000000000000000000000000000000")
# Counterfactual state overrides on eth_call (local to the call, nothing written to chain)
out["override_slots"]={"executor.positions":posSlot,"engine.accounts":engSlot}
def status_word(contract,base,key_hex,idx,val):
    k=keccak("0x"+key_hex+hex(base)[2:].rjust(64,"0")); slot=hex(int(k,16)+idx)
    slot="0x"+slot[2:].rjust(64,"0")
    cur=call("eth_getStorageAt",[contract,slot,B]); return slot,int(cur,16)
out["counterfactuals"]=[]
if posSlot is not None and engSlot is not None:
    for r in out["positions"]:
        if r["executorStatus"]!="CLOSED": continue
        pid=r["positionId"]; acct=r["account"]
        s,cur=status_word(EX,posSlot,hex(pid)[2:].rjust(64,"0"),9,0)
        # packed word: requestedLeverageX100 (uint16) | side (uint8) | status (uint8)
        lev=cur&0xffff; side=(cur>>16)&0xff; st=(cur>>24)&0xff
        assert st==5 and lev==r["requestedLeverageX100"], (hex(cur))
        new=cur-(st<<24)+(2<<24)
        ov={EX:{"stateDiff":{s:"0x"+hex(new)[2:].rjust(64,"0")}}}
        c={"positionId":pid,"executorPackedSlot":s,"before":hex(cur),"after":hex(new)}
        c["executor_forced_ACTIVE_engine_unchanged"]=liq(pid,ANY,ov)
        # view consistency with override: the status field as read back
        v=words(ecall(EX,calldata("positions(uint256)",pid),B,None,ov))[11]; c["readback_status"]=S[v]
        # AccountConfig packs debtPToken(20)|side(1)|status(1) into word 4
        s2,cur2=status_word(RE,engSlot,pad(acct),4,0)
        st2=(cur2>>168)&0xff; assert st2==5,hex(cur2)
        new2=cur2-(st2<<168)+(2<<168)
        ov2={EX:ov[EX],RE:{"stateDiff":{s2:"0x"+hex(new2)[2:].rjust(64,"0")}}}
        c["engineSlot"]=s2
        c["both_forced_ACTIVE"]=liq(pid,ANY,ov2)
        c["engine_only_forced_ACTIVE_beginLiquidation_from_liquidator"]=try_call(RE,calldata("beginLiquidation(address)",acct),B,LQ,{RE:ov2[RE]})
        out["counterfactuals"].append(c)
json.dump(out,open(HERE/"t2-closed-liquidation-sim.json","w"),indent=1)
for r in out["positions"]:
    print(r["positionId"],r["executorStatus"],r["engineStatus"],"isLiq",r["isLiquidatable"],"bal",r["pUSDG_balance"],r["pNVDA_balance"],"debt",r["debt_pUSDG_stored"],r["debt_pNVDA_stored"],r["metrics"])
    print("  arb:",r["liquidate_from_arbitrary"].get("decoded") or r["liquidate_from_arbitrary"]," begin:",r["beginLiquidation_from_liquidator"].get("decoded"),"|",r["beginLiquidation_from_arbitrary"].get("decoded")," auth:",r["authorizeMovement_from_liquidator"].get("decoded"),"redeem:",r["liquidationRedeem_from_arbitrary"].get("decoded"))
    print("  ordinary:",{k:(v.get("decoded") or v.get("return") or v.get("message")) for k,v in r["ordinary_liquidateBorrow"].items()})
print(out["proxies"]); print(out["override_slots"]); print(json.dumps(out["counterfactuals"],indent=0)[:2500]); print(out["nonexistent_position_liquidate"],out["zero_recipient_on_closed"])
