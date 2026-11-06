"""READ-ONLY eth_call: can a CLOSED account be re-opened or re-borrowed; margin implementations vs archived snapshot artifacts; SettlementLib references in the vault."""
import json,sys,pathlib,re
HERE=pathlib.Path(__file__).parent; sys.path.insert(0,str(HERE))
from rpc_util import *
ROOT=pathlib.Path("/Users/joshua/Peridot/ramses-group-pr")
AD=json.load(open("/Users/joshua/Peridot/RobinhoodVaults/deployments/margin-mainnet-live/addresses.json"))
RE,EX,LQ,CT=AD["riskEngine"],AD["executor"],AD["liquidator"],AD["controller"]; PU,PN=AD["pUsd"],AD["pStock"]
head=int(call("eth_blockNumber",[]),16); P=head-3; B=hex(P); blk=call("eth_getBlockByNumber",[B,False])
out={"pin":{"l2":P,"hash":blk["hash"],"timestamp":int(blk["timestamp"],16)}}
sim=json.load(open(HERE/"t2-closed-liquidation-sim.json"))
out["closedAccountReopenAttempts"]=[]
ANY="0x00000000000000000000000000000000000dEaD1"
for r in sim["positions"]:
    if r["executorStatus"]!="CLOSED": continue
    a=r["account"]; d=r["debtPToken"]; pid=r["positionId"]
    o={"positionId":pid,"account":a}
    o["engine.borrowAllowed_from_controller"]=try_call(RE,calldata("borrowAllowed(address,address,uint256)",a,d,1),B,CT)
    o["engine.activateAccount_from_executor"]=try_call(RE,calldata("activateAccount(address)",a),B,EX)
    o["engine.beginClose_from_executor"]=try_call(RE,calldata("beginClose(address)",a),B,EX)
    o["engine.registerAccount_again_from_executor"]=try_call(RE,calldata("registerAccount(uint256,address,address,address,address,address,uint8)",pid,a,r["owner"],r["marginPToken"],r["positionPToken"],r["debtPToken"],r["side"]),B,EX)
    o["executor.addCollateral_from_owner"]=try_call(EX,calldata("addCollateral(uint256,uint256)",pid,1),B,r["owner"])
    o["executor.repayWithUnderlying_from_owner"]=try_call(EX,calldata("repayWithUnderlying(uint256,uint256)",pid,1),B,r["owner"])
    out["closedAccountReopenAttempts"].append(o)
# stale-free current status of oracle
out["marketPriceable"]={n:bool(int(ecall(AD["oracle"],calldata("marketPriceable(address)",m),B),16)) for n,m in (("pUSDG",PU),("pNVDA",PN))}
out["marginImplVsSnapshot"]={}
for k,art in (("liquidator","IsolatedMarginLiquidatorUpgradeable"),("riskEngine","IsolatedMarginRiskEngineUpgradeable"),("executor","IsolatedMarginExecutorUpgradeable")):
    impl=sim["proxies"][k]["impl"]; on=bytes.fromhex(call("eth_getCode",[impl,B])[2:])
    a=json.load(open(ROOT/"snapshot/artifacts/margin"/(art+".json")))
    t=a["deployedBytecode"]["object"]; t=t[2:] if t.startswith("0x") else t
    c=bytes.fromhex(re.sub(r"__\$[0-9a-f]{34}\$__","0"*40,t))
    x=bytearray(on); y=bytearray(c); masks=[]
    for f,l in a["deployedBytecode"].get("linkReferences",{}).items():
        for lib,sp in l.items():
            for s in sp:
                masks.append(dict(kind="link",lib=lib,start=s["start"],onchain="0x"+on[s["start"]:s["start"]+s["length"]].hex()))
                x[s["start"]:s["start"]+s["length"]]=bytes(s["length"]); y[s["start"]:s["start"]+s["length"]]=bytes(s["length"])
    for kk,sp in a["deployedBytecode"].get("immutableReferences",{}).items():
        for s in sp:
            masks.append(dict(kind="immutable",ast=kk,start=s["start"],onchain="0x"+on[s["start"]:s["start"]+s["length"]].hex()))
            x[s["start"]:s["start"]+s["length"]]=bytes(s["length"]); y[s["start"]:s["start"]+s["length"]]=bytes(s["length"])
    out["marginImplVsSnapshot"][k]=dict(impl=impl,artifact="snapshot/artifacts/margin/%s.json"%art,onchainBytes=len(on),artifactBytes=len(c),exactEqualIncludingMetadataAfterMasks=bytes(x)==bytes(y),masks=masks)
V=sim and "0x53a23ef7a5639f237625ff7c89ff9e9ecf1dd18a"
code=call("eth_getCode",[V,B])[2:].lower(); art=json.load(open(HERE/"_build/c07b67d5/remediation/out-vault-v3/RobinhoodBoostedVaultV3.sol/RobinhoodBoostedVaultV3.json"))
obj=art["deployedBytecode"]["object"].lower()
set_=("813AbFeC0DE50f8674798CbaB72Ed7b5D8CcB9cB").lower()
pos=[m.start()//2 for m in re.finditer(set_,code) if m.start()%2==0]
out["vaultSettlementLibEmbedded"]=dict(address="0x"+set_,onchainOffsets=pos,compiledObjectContainsSameAddressAtSameOffsets=[m.start()//2-1 for m in re.finditer(set_,obj) if m.start()%2==0]==pos or [m.start()//2 for m in re.finditer(set_,obj[2:] if obj.startswith("0x") else obj) if m.start()%2==0]==pos,note="pre-linked at compile time by the vault_v3 profile's libraries setting, so not a link reference; verified as compiled bytes")
json.dump(out,open(HERE/"t2-supplement.json","w"),indent=1)
for o in out["closedAccountReopenAttempts"]: print(o["positionId"],{k:(v.get("decoded") or v.get("return") or v.get("message")) for k,v in o.items() if isinstance(v,dict)})
print(out["marketPriceable"]); print(json.dumps(out["marginImplVsSnapshot"],indent=0)[:1800]); print(out["vaultSettlementLibEmbedded"])
