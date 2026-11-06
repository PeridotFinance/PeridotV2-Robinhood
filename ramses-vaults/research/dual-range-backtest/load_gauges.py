import json, collections
from common import *
r = rpc(); head = r.head(); final = head - 200
out = {}
for name in ("SPY", "GLD", "NVDA"):
    g = ADDR[name]["gauge"]
    logs = r.logs(g, [T("NotifyReward(address,address,uint256,uint256)")], 0, head, final)
    rows = []
    for l in logs:
        w = words(l["data"])
        rows.append({"block": l["blockNumber"], "idx": l["logIndex"], "from": topic_addr(l["topics"][1]), "token": topic_addr(l["topics"][2]), "amount": w[0], "period": w[1], "tx": l["tx"]})
    out[name] = rows
    by = collections.defaultdict(lambda: [0, 0])
    for x in rows: by[x["token"]][0] += 1; by[x["token"]][1] += x["amount"]
    print(name, len(rows), {k: (v[0], v[1] / 1e18) for k, v in by.items()}, "senders", collections.Counter(x["from"] for x in rows))
(RESULTS / "gauge_notify.json").write_text(json.dumps(out))
