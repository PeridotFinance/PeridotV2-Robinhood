"""NVDA control pool: fee history, SetFeeProtocol and protocol-fee recipients (cheap events only; swaps are not needed)."""
import collections, json
from common import *
r = rpc(); head = r.head(); final = head - 200
a = ADDR["NVDA"]["pool"]
out = {}
for k, s in (("init", "Initialize(uint160,int24)"), ("feeadj", "FeeAdjustment(uint24,uint24)"), ("proto_b", "SetFeeProtocol(uint24,uint24)"),
             ("collectproto", "CollectProtocol(address,address,uint128,uint128)")):
    out[k] = r.logs(a, [T(s)], 0, head, final)
    print(k, len(out[k]), flush=True)
ti = TimeIndex(r, path=CACHE / "headers.json")
fa = sorted(out["feeadj"], key=lambda l: (l["blockNumber"], l["logIndex"]))
summ = {"pool_init": iso(int(ti.ts(out["init"][0]["blockNumber"]))), "fee_changes": len(fa),
        "fee_values": dict(collections.Counter(words(l["data"])[1] for l in fa).most_common(8)),
        "setfeeprotocol": [(iso(int(ti.ts(l["blockNumber"]))), words(l["data"])[0] / 1e6, words(l["data"])[1] / 1e6) for l in out["proto_b"]],
        "collect_recipients": dict(collections.Counter(topic_addr(l["topics"][2]) for l in out["collectproto"]))}
if fa:
    last_h = [ (iso(int(ti.ts(l["blockNumber"]))), words(l["data"])[1]) for l in fa[-5:] ]
    summ["last_fee_changes"] = last_h
print(json.dumps(summ, indent=1))
json.dump(summ, open(RESULTS / "nvda_control_events.json", "w"), indent=1)
