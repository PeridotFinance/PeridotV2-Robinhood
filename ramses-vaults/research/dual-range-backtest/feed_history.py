"""AnswerUpdated + NewTransmission history per feed aggregator (read-only, cached)."""
import json
from common import *
r = rpc(); head = r.head(); final = head - 200
T_ANS = T("AnswerUpdated(int256,uint256,uint256)")
out = {}
for name, aggr in [("SPY", ADDR["SPY"]["agg"]), ("GLD", ADDR["GLD"]["agg"]), ("NVDA", ADDR["NVDA"]["agg"]), ("USDG", USDG_AGG)]:
    logs = r.logs(aggr, [T_ANS], 0, head, final)
    rows = []
    for l in logs:
        w = words(l["data"])
        rows.append({"block": l["blockNumber"], "idx": l["logIndex"], "answer": topic_int(l["topics"][1]), "round": int(l["topics"][2], 16), "updatedAt": w[0], "tx": l["tx"]})
    out[name] = rows
    print(name, len(rows), "first", rows[0]["block"] if rows else None, iso(rows[0]["updatedAt"]) if rows else None, "last", iso(rows[-1]["updatedAt"]) if rows else None)
(RESULTS / "answer_updated.json").write_text(json.dumps({"head": head, "series": out}))
