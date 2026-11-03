"""READ-ONLY JSON-RPC helpers. Only eth_call/eth_get*/eth_blockNumber are ever issued."""
import json, time, subprocess
URL="https://rpc.mainnet.chain.robinhood.com"
ALLOWED=("eth_call","eth_getCode","eth_getStorageAt","eth_getBlockByNumber","eth_getTransactionByHash","eth_getTransactionReceipt","eth_blockNumber","eth_getLogs","eth_getTransactionCount","eth_chainId","eth_getBalance")
class RpcErr(Exception):
    def __init__(s,e): s.err=e; super().__init__(str(e))
def _post(body):
    # curl, not urllib: urllib stalled for minutes in this environment while curl answers in <1s.
    r=subprocess.run(["curl","-s","-m","30","-A","curl/8.0","-H","content-type: application/json","-d","@-",URL],input=json.dumps(body),capture_output=True,text=True)
    if r.returncode!=0 or not r.stdout.strip(): raise IOError("curl rc=%s"%r.returncode)
    return json.loads(r.stdout)
def call(m,p,tries=6):
    """Retries transient errors and 'historical state ... not available', which public backends return intermittently for recent blocks."""
    assert m in ALLOWED, m
    last=None
    for i in range(tries):
        try:
            d=_post({"jsonrpc":"2.0","id":1,"method":m,"params":p})
        except Exception as e:
            last=e; time.sleep(1+i); continue
        if "error" in d:
            msg=str(d["error"].get("message",""))
            if d["error"].get("code") in (-32005,429) or "historical state" in msg or "not available" in msg:
                last=RpcErr(d["error"]); time.sleep(0.5+i); continue
            raise RpcErr(d["error"])
        return d["result"]
    raise last
def sel(sig):
    return subprocess.check_output(["cast","sig",sig],text=True,stdin=subprocess.DEVNULL).strip()
def calldata(sig,*a):
    return subprocess.check_output(["cast","calldata",sig,*map(str,a)],text=True,stdin=subprocess.DEVNULL).strip()
def ecall(to,data,block,frm=None,override=None):
    tx={"to":to,"data":data}
    if frm: tx["from"]=frm
    p=[tx,block]
    if override: p.append(override)
    return call("eth_call",p)
def words(h):
    h=h[2:]; return [int(h[i:i+64],16) for i in range(0,len(h),64)]
def adr(x): return "0x%040x"%x
def pad(a): return a[2:].lower().rjust(64,"0")
def keccak(h): return subprocess.check_output(["cast","keccak",h],text=True,stdin=subprocess.DEVNULL).strip()
def decode_revert(data):
    """Decode revert data to a human string. Error(string), Panic, and known custom errors."""
    if not data or data=="0x": return "empty revert data"
    s=data[:10]; body=data[10:]
    if s=="0x08c379a0":
        n=int(body[64:128],16); return 'Error("%s")'%bytes.fromhex(body[128:128+2*n]).decode()
    if s=="0x4e487b71": return "Panic(0x%x)"%int(body,16)
    known={}
    for sig in ["LiquidationError(uint8)","ExecutorError(uint8)","PriceUnavailable(address)","StaleOracle()","ReentrancyGuardReentrantCall()","OracleUnavailable()"]:
        known[sel(sig)]=sig
    if s in known: return "%s args=0x%s"%(known[s],body.lstrip("0") or "0")
    return "unknown selector %s body=%s"%(s,body)
def try_call(to,data,block,frm=None,override=None):
    try:
        r=ecall(to,data,block,frm,override); return {"ok":True,"return":r}
    except RpcErr as e:
        d=e.err.get("data") if isinstance(e.err,dict) else None
        if isinstance(d,dict): d=d.get("data") or d.get("result")
        return {"ok":False,"message":e.err.get("message") if isinstance(e.err,dict) else str(e.err),"data":d,"decoded":decode_revert(d) if d else None}
