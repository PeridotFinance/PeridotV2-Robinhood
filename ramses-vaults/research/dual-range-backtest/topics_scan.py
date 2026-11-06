from common import *
import collections, json
r = rpc(); head = r.head()
for name in ("SPY","GLD","NVDA"):
    a = ADDR[name]["pool"]
    # scan all logs of the pool with only an address filter, in a few windows, to enumerate event types
    cnt = collections.Counter()
    for lo in range(0, head, 9_000_000):
        try:
            logs = r.logs(a, [], lo, min(lo+8_999_999, head), None)
        except Exception as e:
            print(name, lo, "ERR", str(e)[:100]); continue
        for l in logs: cnt[l["topics"][0]] += 1
    print(name, dict(cnt))
