"""Whole-chain eth_getLogs scan for one address (adaptive range splitting), cached to raw/logs-<name>.json."""
import json, sys
from pathlib import Path
from lb_rpc import call, RpcError, HERE
def scan(name, address, topics=None, start=0, end=None, step=10_000_000, force=False):
    p = HERE/"raw"/f"logs-{name}.json"
    if p.exists() and not force:
        return json.loads(p.read_text())
    if end is None: end = int(call("eth_blockNumber",[]),16)
    out = []
    def rng(a,b):
        flt = {"fromBlock":hex(a),"toBlock":hex(b)}
        if address: flt["address"]=address
        if topics: flt["topics"]=topics
        try:
            r = call("eth_getLogs",[flt],log=False)
        except RpcError as e:
            m = str(e)
            if b>a and ("timed out" in m.lower() or "too many" in m.lower() or "limit" in m.lower() or "range" in m.lower() or "exceed" in m.lower() or "more than" in m.lower()):
                mid=(a+b)//2; rng(a,mid); rng(mid+1,b); return
            raise
        out.extend(r)
    a = start
    while a <= end:
        b = min(a+step-1, end); rng(a,b); a=b+1
    out.sort(key=lambda l:(int(l["blockNumber"],16), int(l["logIndex"],16)))
    p.write_text(json.dumps({"address":address,"scannedTo":end,"topics":topics,"logs":out}))
    return {"address":address,"scannedTo":end,"topics":topics,"logs":out}
if __name__=="__main__":
    name, address = sys.argv[1], sys.argv[2]
    r = scan(name, address, force=True)
    print(name, len(r["logs"]), r["scannedTo"])
