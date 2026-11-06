"""Read-only JSON-RPC helper for the live-baseline reconciliation. stdlib only; shells out to curl (IPv4, curl UA).
Every request/response is appended to raw/rpc-log.jsonl. Only read methods are allowed."""
import json, subprocess, time, os, hashlib
from pathlib import Path
HERE = Path(__file__).parent
URL = "https://rpc.mainnet.chain.robinhood.com"
ALLOWED = {"eth_call","eth_getCode","eth_getLogs","eth_getBlockByNumber","eth_getBlockByHash","eth_getStorageAt","eth_blockNumber","eth_getTransactionReceipt","eth_getTransactionByHash","eth_chainId","eth_getBalance","eth_getTransactionCount"}
LOG = HERE / "raw" / "rpc-log.jsonl"
_last = [0.0]
MIN_INTERVAL = float(os.environ.get("LB_MIN_INTERVAL", "0.15"))
_id = [0]
def call(method, params, retries=4, log=True):
    assert method in ALLOWED, method
    last_err = None
    for attempt in range(retries):
        dt = time.time() - _last[0]
        if dt < MIN_INTERVAL: time.sleep(MIN_INTERVAL - dt)
        _id[0] += 1
        body = json.dumps({"jsonrpc":"2.0","id":_id[0],"method":method,"params":params})
        p = subprocess.run(["curl","-s","-4","-m","40","-A","curl/8.0","-H","content-type: application/json","--data",body,URL],capture_output=True,text=True)
        _last[0] = time.time()
        resp = p.stdout
        try:
            j = json.loads(resp)
        except Exception as e:
            last_err = f"bad json: {resp[:200]!r} rc={p.returncode}"
            time.sleep(1.5*(attempt+1)); continue
        if log:
            with open(LOG,"a") as f:
                f.write(json.dumps({"t":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),"req":{"method":method,"params":params},"resp":(j if len(resp)<4000 else {"truncated_len":len(resp),"sha256":hashlib.sha256(resp.encode()).hexdigest(),"error":j.get("error"),"result_prefix":str(j.get("result"))[:200]})})+"\n")
        if "error" in j:
            msg = str(j["error"])
            if "rate" in msg.lower() or "429" in msg or "timeout" in msg.lower():
                last_err = msg; time.sleep(2*(attempt+1)); continue
            raise RpcError(j["error"])
        return j["result"]
    raise RuntimeError(f"rpc failed {method}: {last_err}")
class RpcError(Exception):
    def __init__(self, e): self.err = e; super().__init__(str(e))
def sel(sig):
    return subprocess.run(["cast","sig",sig],capture_output=True,text=True).stdout.strip()
_selcache = {}
def eth_call(to, sig, args="", block="latest"):
    """sig like 'foo(uint256)', args = hex of already-encoded args (no 0x)."""
    if sig not in _selcache: _selcache[sig] = sel(sig)
    data = _selcache[sig] + args
    return call("eth_call",[{"to":to,"data":data}, block if isinstance(block,str) else hex(block)])
def words(h):
    h = h[2:] if h.startswith("0x") else h
    return [int(h[i:i+64],16) for i in range(0,len(h),64)]
def sword(v): return v - (1<<256) if v >= (1<<255) else v
def addr(v): return "0x"+v.to_bytes(32,"big")[12:].hex()
def pad_addr(a): return a.lower().replace("0x","").rjust(64,"0")
def pad_int(i): return hex(i)[2:].rjust(64,"0")
def topic(sig): return subprocess.run(["cast","keccak",sig],capture_output=True,text=True).stdout.strip()
