"""READ-ONLY: run the unmodified margin keeper cycle() with execute=False over all positions using the plain RpcBackend (no signer; submit is impossible on a non-loopback URL).
Only transport is swapped to curl because urllib stalls in this environment. Journal dir is a temp under this folder."""
import sys,json,pathlib,subprocess,time
HERE=pathlib.Path(__file__).parent; ROOT=pathlib.Path("/Users/joshua/Peridot/RobinhoodVaults")
sys.dont_write_bytecode=True
sys.path.insert(0,str(ROOT/"margin-mainnet/tools")); sys.path.insert(0,str(ROOT/"margin-mainnet/keeper-service"))
import rpc as R
ALLOWED={"eth_chainId","eth_getCode","eth_call","eth_blockNumber","eth_estimateGas","eth_getTransactionCount"}
LOG=[]
def curl_rpc(method,params,url=R.RPC,*a,**k):
    assert method in ALLOWED, method
    for i in range(8):
        r=subprocess.run(["curl","-s","-m","30","-A","curl/8.0","-H","content-type: application/json","-d","@-",url],input=json.dumps({"jsonrpc":"2.0","id":1,"method":method,"params":params}),capture_output=True,text=True)
        try: d=json.loads(r.stdout)
        except Exception: time.sleep(1+i); continue
        if "error" in d:
            if d["error"].get("code") in (429,-32005) or "not available" in str(d["error"].get("message")): time.sleep(1+i); continue
            raise RuntimeError(d["error"])
        LOG.append(method); return d["result"]
    raise RuntimeError("rpc retries exhausted "+method)
R.rpc=curl_rpc
import keeper, service
addresses=json.load(open(ROOT/"deployments/margin-mainnet-live/addresses.json"))
backend=keeper.RpcBackend(R.RPC,addresses,"0x16aEC17597E5224998e2043C9c83C4a35dD95A86")
res=service.cycle(backend,HERE/"_keeper_tmp",execute=False,max_positions=100)
def clean(p): return {k:(v if k!="journal" else {"attempts":len(v.get("attempts",[]))}) for k,v in p.items()}
out={"cycleStatus":res["status"],"positions":[clean(p) for p in res["positions"]],"methodCounts":{m:LOG.count(m) for m in set(LOG)},"note":"execute=False; service.py cycle + tools/keeper.py run unmodified"}
json.dump(out,open(HERE/"t2-keeper-dryrun.json","w"),indent=1)
print(json.dumps(out,indent=0)[:3000])
