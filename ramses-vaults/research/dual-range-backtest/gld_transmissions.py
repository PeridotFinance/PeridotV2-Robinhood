import json
from common import *
r = rpc(); head = r.head()
cands = ["NewTransmission(uint32,int192,address,uint32,int192[],bytes,int192,bytes32,uint40)",
         "NewTransmission(uint32,int192,address,int192[],bytes,int192,bytes32,uint40)",
         "NewTransmission(uint32,int192,address,uint32,int192[],bytes,int192,bytes32,uint40,uint32)"]
for name in ("GLD","SPY"):
    agg = ADDR[name]["agg"]
    for c in cands:
        logs = r.logs(agg, [T(c)], 67_000_000 if name=="GLD" else 90_000_000, head, head-200)
        print(name, c[:60], len(logs))
        if logs:
            l = logs[-1]; w = words(l["data"]); print(" last data words", len(w), [hex(x)[:20] for x in w[:12]]); break
