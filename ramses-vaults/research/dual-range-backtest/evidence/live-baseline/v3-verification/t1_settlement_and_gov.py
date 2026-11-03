"""READ-ONLY: SettlementLib vs pinned archived artifact; live range policy and governance reads."""
import json,sys,pathlib,re
HERE=pathlib.Path(__file__).parent; sys.path.insert(0,str(HERE))
from rpc_util import *
ROOT=pathlib.Path("/Users/joshua/Peridot/ramses-group-pr")
head=int(call("eth_blockNumber",[]),16); P=head-5; B=hex(P); blk=call("eth_getBlockByNumber",[B,False])
out={"pin":{"l2":P,"hash":blk["hash"],"timestamp":int(blk["timestamp"],16)}}
SET="0x813AbFeC0DE50f8674798CbaB72Ed7b5D8CcB9cB"
a=json.load(open(ROOT/"snapshot/artifacts/vault/SettlementLib.json"))
on=bytes.fromhex(call("eth_getCode",[SET,B])[2:]); c=bytes.fromhex(a["deployedBytecode"]["object"][2:])
m=[]; x=bytearray(on); y=bytearray(c)
for k,sp in a["deployedBytecode"].get("immutableReferences",{}).items():
    for s in sp:
        m.append(dict(kind="immutable",what="library self address astId "+k,start=s["start"],length=s["length"],onchain="0x"+on[s["start"]:s["start"]+s["length"]].hex(),compiled="0x"+c[s["start"]:s["start"]+s["length"]].hex()))
        x[s["start"]:s["start"]+s["length"]]=bytes(s["length"]); y[s["start"]:s["start"]+s["length"]]=bytes(s["length"])
ml=int.from_bytes(on[-2:],"big")+2
out["SettlementLib"]=dict(address=SET,onchainBytes=len(on),compiledBytes=len(c),onchainCodehash=keccak("0x"+on.hex()),masked=m,metadataLen=ml,exactAfterMaskingImmutable=bytes(x)==bytes(y),bodyEqualExclMetadata=bytes(x[:-ml])==bytes(y[:-ml]),metadataEqual=on[-ml:]==c[-ml:],artifact="snapshot/artifacts/vault/SettlementLib.json",rawMetadataSettings=json.loads(a["rawMetadata"])["settings"]["optimizer"])
# also the fresh c07b67d5 build differs in optimizer_runs (1 vs deployed setting): record
fresh=HERE/"_build/c07b67d5/remediation/out-vault-v3/SettlementLib.sol/SettlementLib.json"
if fresh.exists():
    f=json.load(open(fresh)); out["SettlementLib"]["vault_v3_rebuild_bytes"]=(len(f["deployedBytecode"]["object"])-2)//2
V="0x280825b2d856706Ff7E0d6351CcB2e935E1a9A2f"; AD="0xadA73211711e4790bc83B5d6B39f47fE04D276f3"; TL="0x6797FB8Ce049B42C5BC2b42Bf76c6d15C7B12498"
PAIR=keccak("0x"+b"NVDA/USDG".hex())
w=words(ecall(V,calldata("rangePolicy(bytes32)",PAIR),B))
out["rangePolicy"]=dict(zip(["enabled","halfWidthTicks","triggerTicks","minInterval","maxPerDay","maxLossBps","maxRangedValueUsd"],w))
r=try_call(V,calldata("pairConfig(bytes32)",PAIR),B); out["pairConfigRawWords"]=words(r["return"]) if r["ok"] else r
tl={}
tl["minDelay"]=words(ecall(TL,sel("getMinDelay()"),B))[0]
GOV="0x94696d767e65a75581145646960FA0eC886cE5d2"; RK="0x16aEC17597E5224998e2043C9c83C4a35dD95A86"
roles={}
for nm in ("PROPOSER_ROLE","EXECUTOR_ROLE","CANCELLER_ROLE","DEFAULT_ADMIN_ROLE"):
    rid=ecall(TL,sel(nm+"()"),B)
    roles[nm]={"id":rid,"holders":{}}
    for who,addr in (("governor",GOV),("timelockSelf",TL),("marginKeeperWallet",RK),("rangeKeeperWallet","0x978e2ADF18da8B846552932c0aB2B1193da1fAc4"),("zeroAddress(openRole)","0x0000000000000000000000000000000000000000")):
        roles[nm]["holders"][who]=bool(int(ecall(TL,calldata("hasRole(bytes32,address)",rid,addr),B),16))
tl["roles"]=roles; out["timelock"]=dict(address=TL,**tl)
vr={}
for nm in ("DEFAULT_ADMIN_ROLE","CONFIG_ROLE","KEEPER_ROLE","GUARDIAN_ROLE"):
    q=try_call(V,sel(nm+"()"),B)
    if not q["ok"]: vr[nm]=q; continue
    rid=q["return"]; vr[nm]={"id":rid,"governor":bool(int(ecall(V,calldata("hasRole(bytes32,address)",rid,GOV),B),16)),"timelock":bool(int(ecall(V,calldata("hasRole(bytes32,address)",rid,TL),B),16)),"rangeKeeperWallet":bool(int(ecall(V,calldata("hasRole(bytes32,address)",rid,"0x978e2ADF18da8B846552932c0aB2B1193da1fAc4"),B),16))}
out["vaultRoles"]=vr
out["roleEventsFromRawLogs"]=[]
LB=HERE.parent/"raw"
for f in ("logs-vault.json","logs-timelock.json"):
    d=json.load(open(LB/f))
    for l in d["logs"]:
        if l["topics"][0]=="0x2f8788117e7eff1d82e926ec794901d17c78024a50270940304540a733656f0d":
            out["roleEventsFromRawLogs"].append(dict(file=f,block=int(l["blockNumber"],16),role=l["topics"][1],account=adr(int(l["topics"][2],16)),sender=adr(int(l["topics"][3],16)),tx=l["transactionHash"]))
        if l["topics"][0]=="0xf6391f5c32d9c69d2a47ea670b442974b53935d1edc7fd64eb21e047a839171b":
            out["roleEventsFromRawLogs"].append(dict(file=f,revoked=True,block=int(l["blockNumber"],16),role=l["topics"][1],account=adr(int(l["topics"][2],16)),tx=l["transactionHash"]))
json.dump(out,open(HERE/"t1-settlement-and-governance.json","w"),indent=1)
print(json.dumps(out,indent=0)[:6000])
