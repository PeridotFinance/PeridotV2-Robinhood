import json
from common import *
r = rpc(); head = r.head()
PM = "0x8366a39CC670B4001A1121B8F6A443A643e40951"
SIG = T("Initialize(bytes32,address,address,uint24,int24,address,uint160,int24)")
usdg = addr_topic(USDG)
res = {}
for name in ("GLD", "SPY", "NVDA"):
    stock = addr_topic(ADDR[name]["stock"])
    # USDG as currency0 (address order) or currency1
    a = r.logs(PM, [SIG, None, usdg, stock], 0, head, head - 200)
    b = r.logs(PM, [SIG, None, stock, usdg], 0, head, head - 200)
    print(name, "v4 pools USDG/stock:", len(a), "stock/USDG:", len(b))
    for l in a + b:
        w = words(l["data"])
        print("   id", l["topics"][1], "block", l["blockNumber"], "fee", w[0], "spacing", sN(w[1], 24), "hooks", hex(w[2]))
    res[name] = [(l["topics"][1], l["blockNumber"]) for l in a + b]
json.dump(res, open(RESULTS / "v4_pools.json", "w"))
