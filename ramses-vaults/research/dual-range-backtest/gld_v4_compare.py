"""Compare the GLD feed with the Ramses pool and with the busiest Uniswap v4 GLD/USDG pools."""
import bisect, json, pickle, statistics as st, sys
from common import *
import engine as E
Q = E.Q
r = rpc(); head = r.head(); final = head - 200
PM = "0x8366a39CC670B4001A1121B8F6A443A643e40951"
SW = T("Swap(bytes32,address,int128,int128,uint160,uint128,int24,uint24)")
counts = json.load(open(RESULTS / "v4_gld_swapcounts.json"))
top = [k for k, v in sorted(counts.items(), key=lambda kv: -kv[1]) if v >= 200][:4]
print("v4 pools with >=200 swaps:", [(k[:12], counts[k]) for k in top])
INIT = T("Initialize(bytes32,address,address,uint24,int24,address,uint160,int24)")
series = {}
for pid in top:
    ini = r.logs(PM, [INIT, pid], 0, head, final)
    w = words(ini[0]["data"]); meta = {"fee": w[0], "spacing": sN(w[1], 24), "hooks": hex(w[2])}
    logs = r.logs(PM, [SW, pid], 60_000_000, head, final)
    ti = TimeIndex(r, path=CACHE / "headers.json")
    ts, px = [], []
    for l in logs:
        ww = words(l["data"]); s = ww[2] / 2 ** 96
        ts.append(ti.ts(l["blockNumber"])); px.append(Q / (s * s))  # USDG = currency0, GLD = currency1
    series[pid] = (ts, px, meta)
    print(pid[:12], meta, len(ts), "first", iso(int(ts[0])), "last", iso(int(ts[-1])))
tr = json.load(open(RESULTS / "transmissions.json"))["GLD"]
cols, _ = pickle.load(open(RESULTS / "dataset_GLD.pkl", "rb"))
rts, rs = cols["ts"], cols["s_after"]
def ramses(t):
    k = bisect.bisect_right(rts, t) - 1
    return Q / (rs[k] ** 2) if k >= 0 else None
def at(pid, t):
    ts, px, _ = series[pid]
    k = bisect.bisect_right(ts, t) - 1
    return px[k] if k >= 0 and t - ts[k] < 6 * 3600 else None
rows = [x for x in tr if x["obsTs"] > rts[0] + 3600 and x["answer"] > 1]
res = {"n": len(rows)}
def stats(name, f, subset):
    e = []
    for x in subset:
        p = f(x["obsTs"])
        if p: e.append(abs(x["answer"] / p - 1) * 100)
    return {"n": len(e), "median_abs_err_pct": st.median(e) if e else None, "mean_abs_err_pct": st.mean(e) if e else None, "max": max(e) if e else None}
wk = [x for x in rows if session_of(x["obsTs"]) == "weekend"]
for label, subset in (("all", rows), ("weekend", wk)):
    res[label] = {"ramses": stats("ramses", ramses, subset)}
    for pid in top:
        res[label]["v4_" + pid[:10]] = stats(pid, lambda t, pid=pid: at(pid, t), subset)
    # best-of: the closer one of the sources
    def best(t):
        c = [p for p in [ramses(t)] + [at(pid, t) for pid in top] if p]
        return c
    errs = []
    for x in subset:
        c = best(x["obsTs"])
        if c: errs.append(min(abs(x["answer"] / p - 1) for p in c) * 100)
    res[label]["closest_of_all_median_pct"] = st.median(errs) if errs else None
    # volume weighted blend of Ramses + main v4 pool
print(json.dumps(res, indent=1))
json.dump(res, open(RESULTS / "gld_v4_compare.json", "w"), indent=1)
