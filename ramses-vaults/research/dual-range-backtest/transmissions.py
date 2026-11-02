"""Decode OCR2 NewTransmission observations per feed: spread across the node operators' observations."""
import json, statistics as st
from common import *
r = rpc(); head = r.head(); final = head - 200
SIG = "NewTransmission(uint32,int192,address,uint32,int192[],bytes,int192,bytes32,uint40)"
out = {}
for name, start in (("GLD", 67_000_000), ("SPY", 0), ("NVDA", 0)):
    agg = ADDR[name]["agg"]
    logs = r.logs(agg, [T(SIG)], start, head, final)
    rows = []
    for l in logs:
        w = words(l["data"])
        n = w[w[3] // 32]
        obs = [sN(x, 192) / 1e8 for x in w[w[3] // 32 + 1: w[3] // 32 + 1 + n]]
        rows.append({"block": l["blockNumber"], "idx": l["logIndex"], "tx": l["tx"], "answer": sN(w[0], 192) / 1e8,
                     "obsTs": w[2], "n": n, "obs": obs, "transmitter": "0x" + format(w[1], "040x")})
    out[name] = rows
    spreads = [(max(x["obs"]) / min(x["obs"]) - 1) * 100 for x in rows if x["obs"]]
    nuniq = [len(set(x["obs"])) for x in rows]
    print(name, "transmissions", len(rows), "nodes/report", st.median([x["n"] for x in rows]),
          "spread_pct median %.4f p90 %.4f max %.4f" % (st.median(spreads), sorted(spreads)[int(len(spreads)*.9)], max(spreads)),
          "distinct observation values median", st.median(nuniq))
(RESULTS / "transmissions.json").write_text(json.dumps(out))
