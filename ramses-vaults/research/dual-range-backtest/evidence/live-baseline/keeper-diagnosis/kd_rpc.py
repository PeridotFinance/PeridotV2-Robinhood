"""Read-only JSON-RPC helper (curl, -4, UA). Only read methods. Logs go to keeper-diagnosis/raw/rpc-log.jsonl."""
import json,subprocess,time
from pathlib import Path
HERE=Path(__file__).parent
URL="https://rpc.mainnet.chain.robinhood.com"
ALLOWED={"eth_blockNumber","eth_getBlockByNumber","eth_getLogs","eth_getTransactionReceipt","eth_getTransactionByHash","eth_getBalance","eth_getTransactionCount","eth_call","eth_chainId"}
LOG=HERE/"raw"/"rpc-log.jsonl"
def _post(body):
    for a in range(5):
        p=subprocess.run(["curl","-s","-4","-m","60","-A","curl/8.0","-H","content-type: application/json","--data-binary","@-",URL],input=body,capture_output=True,text=True)
        try: return json.loads(p.stdout)
        except Exception: time.sleep(2*(a+1))
    raise RuntimeError("rpc failed: "+p.stdout[:200])
def call(m,params):
    assert m in ALLOWED,m
    for a in range(5):
        j=_post(json.dumps({"jsonrpc":"2.0","id":1,"method":m,"params":params}))
        with open(LOG,"a") as f: f.write(json.dumps({"t":time.strftime("%FT%TZ",time.gmtime()),"m":m,"p":str(params)[:200],"err":j.get("error")})+"\n")
        if "error" in j:
            if any(x in str(j["error"]).lower() for x in("rate","429","timeout")): time.sleep(2*(a+1)); continue
            raise RuntimeError(str(j["error"]))
        return j["result"]
    raise RuntimeError("rate limited: "+str(j.get("error"))[:200])
def batch(reqs,size=50):
    out=[]
    for i in range(0,len(reqs),size):
        chunk=reqs[i:i+size]
        for m,_ in chunk: assert m in ALLOWED,m
        body=json.dumps([{"jsonrpc":"2.0","id":k,"method":m,"params":p} for k,(m,p) in enumerate(chunk)])
        for a in range(12):
            j=_post(body)
            if isinstance(j,list) and all("result" in x for x in j): break
            time.sleep(min(15*(a+1),90))  # shared public RPC: back off hard on 429
        else: raise RuntimeError("batch failed "+str(j)[:300])
        j.sort(key=lambda x:x["id"]); out+=[x["result"] for x in j]
        with open(LOG,"a") as f: f.write(json.dumps({"t":time.strftime("%FT%TZ",time.gmtime()),"m":"batch:"+chunk[0][0],"n":len(chunk),"first":str(chunk[0][1])[:100]})+"\n")
        time.sleep(0.6)
    return out
def block(n,full=False): return call("eth_getBlockByNumber",[hex(n),full])
def block_by_ts(ts):
    hi=int(call("eth_blockNumber",[]),16); lo=hi-40_000_000
    while hi-lo>1:
        mid=(lo+hi)//2
        if int(block(mid)["timestamp"],16)<ts: lo=mid
        else: hi=mid
    return hi
