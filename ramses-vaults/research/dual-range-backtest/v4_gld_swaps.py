import json, collections, pickle
from common import *
r = rpc(); head = r.head(); final = head - 200
PM = "0x8366a39CC670B4001A1121B8F6A443A643e40951"
SW = T("Swap(bytes32,address,int128,int128,uint160,uint128,int24,uint24)")
ids = [x[0] for x in json.load(open(RESULTS / "v4_pools.json"))["GLD"]]
counts = {}
for i, pid in enumerate(ids):
    logs = r.logs(PM, [SW, pid], 60_000_000, head, final)
    counts[pid] = len(logs)
    if logs: print(i, pid[:12], len(logs), flush=True)
json.dump(counts, open(RESULTS / "v4_gld_swapcounts.json", "w"))
print("pools with swaps", sum(1 for v in counts.values() if v), "of", len(ids))
