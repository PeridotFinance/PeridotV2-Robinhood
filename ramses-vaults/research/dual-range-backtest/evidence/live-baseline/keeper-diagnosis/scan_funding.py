"""Full-block scan of a time window for native transfers INTO the keeper wallet (read-only). args: label from_ts to_ts"""
import json,sys
from kd_rpc import *
KEEPER="0x978e2adf18da8b846552932c0ab2b1193da1fac4"
label,a,b=sys.argv[1],int(sys.argv[2]),int(sys.argv[3])
lo,hi=block_by_ts(a),block_by_ts(b); print(label,lo,hi,hi-lo+1)
found=[];n=0;odd=[]
for i in range(lo,hi+1,200):
    bl=batch([("eth_getBlockByNumber",[hex(k),True]) for k in range(i,min(i+200,hi+1))],size=25)
    for x in bl:
        n+=1
        for t in x["transactions"]:
            if (t.get("to") or "").lower()==KEEPER or t["from"].lower()==KEEPER:
                found.append({"tx":t["hash"],"block":int(x["number"],16),"ts":int(x["timestamp"],16),"from":t["from"],"to":t["to"],"valueWei":int(t["value"],16),"type":t.get("type"),"nonce":int(t["nonce"],16)})
print(n,json.dumps(found,indent=0))
rc=[]
if found:
    rs=batch([("eth_getTransactionReceipt",[f["tx"]]) for f in found])
    for f,r in zip(found,rs): f["status"]=r["status"]
json.dump({"label":label,"fromTs":a,"toTs":b,"fromBlock":lo,"toBlock":hi,"blocksScanned":n,"found":found},open(HERE/"raw"/f"funding-scan-{label}.json","w"),indent=1)
