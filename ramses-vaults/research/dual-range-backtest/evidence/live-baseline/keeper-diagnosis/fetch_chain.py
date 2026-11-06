"""Read-only fresh chain pull: new vault logs after the earlier scan, receipts, keeper nonces, balances, native-funding block scans."""
import json,sys,time
from kd_rpc import *
P=HERE.parent
KEEPER="0x978e2adf18da8b846552932c0ab2b1193da1fac4"; GOV="0x94696d767e65a75581145646960fa0ec886ce5d2"; VAULT="0x280825b2d856706Ff7E0d6351CcB2e935E1a9A2f"
old=json.load(open(P/"raw"/"logs-vault.json")); start=old["scannedTo"]+1
head=int(call("eth_blockNumber",[]),16)
logs=[];b=start
while b<=head:
    e=min(b+200000,head); logs+=call("eth_getLogs",[{"address":VAULT,"fromBlock":hex(b),"toBlock":hex(e)}]); b=e+1
txs=sorted({l["transactionHash"] for l in logs})
rc=batch([("eth_getTransactionReceipt",[t]) for t in txs]) if txs else []
blocks=sorted({int(l["blockNumber"],16) for l in logs})
bl=batch([("eth_getBlockByNumber",[hex(n),False]) for n in blocks]) if blocks else []
json.dump({"scannedFrom":start,"scannedTo":head,"logs":logs,"receipts":{t:{"from":r["from"],"to":r["to"],"status":r["status"],"gasUsed":r["gasUsed"],"effectiveGasPrice":r["effectiveGasPrice"],"blockNumber":r["blockNumber"]} for t,r in zip(txs,rc)},"blockTs":{str(n):int(x["timestamp"],16) for n,x in zip(blocks,bl)}},open(HERE/"raw"/"vault-logs-fresh.json","w"))
print("fresh",start,head,len(logs),len(txs))
R=json.load(open(P/"raw"/"receipts.json"))
ktx=[t for t,r in R.items() if r["from"]==KEEPER]+[t for t,r in zip(txs,rc) if r["from"]==KEEPER]
ktx=sorted(set(ktx))
info=batch([("eth_getTransactionByHash",[t]) for t in ktx])
n={t:{"nonce":int(x["nonce"],16),"block":int(x["blockNumber"],16),"gasLimit":int(x["gas"],16),"maxFee":x.get("maxFeePerGas")} for t,x in zip(ktx,info)}
cur={"head":head,"ts":int(block(head)["timestamp"],16)}
for name,a in (("keeper",KEEPER),("governor",GOV)):
    cur[name]={"nonce":int(call("eth_getTransactionCount",[a,"latest"]),16),"balanceWei":int(call("eth_getBalance",[a,"latest"]),16)}
json.dump({"current":cur,"keeperTxNonces":n},open(HERE/"raw"/"keeper-nonces.json","w"),indent=0)
print(json.dumps(cur)); ns=sorted(v["nonce"] for v in n.values()); print(len(ns),ns[0],ns[-1],[i for i in range(ns[-1]+1) if i not in ns])
