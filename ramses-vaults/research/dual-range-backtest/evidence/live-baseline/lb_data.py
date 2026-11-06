"""Decoded event table for all scanned contracts (from raw/logs-*.json, raw/blocks.json, raw/receipts.json, raw/feed-answers.json)."""
import json, bisect, time
from lb_rpc import HERE
from lb_events import load as load_reg, decode
PROD="0xe2050352f4346597cc69d2776d99ae60c9440dfc28f9406ce66be0bbe3fb6b06"
CAN="0x536e330d7e6d12c73d1ae0547dfec4ea4d47ad94f4244a096ea5fad4f87f28ee"
reg=load_reg()
BLK=json.load(open(HERE/"raw"/"blocks.json")); RCPT=json.load(open(HERE/"raw"/"receipts.json"))
def events(name):
    out=[]
    for l in json.load(open(HERE/"raw"/f"logs-{name}.json"))["logs"]:
        d=decode(l,reg); b=int(l["blockNumber"],16); bl=BLK[str(b)]
        out.append({"name":d[0] if d else l["topics"][0],"ind":d[1] if d else [],"data":d[2] if d else l["data"],"block":b,"idx":int(l["logIndex"],16),"ts":bl["ts"],"l1":bl["l1"],"tx":l["transactionHash"],"topics":l["topics"],"decoded":bool(d)})
    return out
_F=json.load(open(HERE/"raw"/"feed-answers.json"))["rows"]
_F=sorted([r for r in _F if r["roundId"]>1],key=lambda r:r["updatedAt"])
_FT=[r["updatedAt"] for r in _F]
def nvda_usd(ts):
    """latest NVDA/USD answer (8 dec -> float USD) with updatedAt <= ts"""
    i=bisect.bisect_right(_FT,ts)-1
    return _F[i]["answer"]/1e8 if i>=0 else None
def iso(ts): return time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime(ts))
