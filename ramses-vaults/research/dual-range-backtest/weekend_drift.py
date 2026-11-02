"""Per closed-session (weekend) window: how far the pool drifts from the last feed value, and the gap at reopening."""
import bisect, datetime as dt, json, pickle, statistics as st
from common import *
import engine as E
Q = E.Q
feed = json.load(open(RESULTS / "answer_updated.json"))["series"]
out = {}
for name in ("SPY", "GLD"):
    cols, _ = pickle.load(open(RESULTS / f"dataset_{name}.pkl", "rb"))
    ts, sa = cols["ts"], cols["s_after"]; stock0 = ADDR[name]["stock_is_token0"]
    ft = [x["updatedAt"] for x in feed[name]]; fp = [x["answer"] / 1e8 for x in feed[name]]
    def pool(t):
        k = bisect.bisect_right(ts, t) - 1
        s = sa[k]; return s * s * Q if stock0 else Q / (s * s)
    def f(t):
        i = bisect.bisect_right(ft, t) - 1
        return fp[i]
    # walk 5-minute grid and group consecutive non-open samples into closed windows
    wins = []; cur = None
    t = max(ts[0], ft[0]) + 600   # the feed does not exist before its first update
    while t < ts[-1]:
        sess = session_of(int(t))
        closed = sess in ("weekend", "holiday")
        if closed:
            d = (pool(t) / f(t) - 1) * 100
            if cur is None: cur = {"start": t, "max_abs": 0.0, "last": 0.0, "n": 0, "start_pool_vs_feed": d}
            cur["max_abs"] = max(cur["max_abs"], abs(d)); cur["last"] = d; cur["n"] += 1; cur["end"] = t
        elif cur is not None:
            # first sample after reopening: gap between pool and feed, and the feed's update right after
            cur["reopen_pool_vs_feed"] = (pool(t) / f(t) - 1) * 100
            wins.append(cur); cur = None
        t += 300
    res = []
    for w in wins:
        res.append({"from": iso(int(w["start"])), "hours": (w["end"] - w["start"]) / 3600, "max_abs_drift_pct": w["max_abs"], "drift_at_end_pct": w["last"], "reopen_gap_pct": w.get("reopen_pool_vs_feed")})
    out[name] = res
    print(name, "closed windows:", len(res), "max |drift| median %.3f max %.3f; drift at end max %.3f" % (st.median(r["max_abs_drift_pct"] for r in res), max(r["max_abs_drift_pct"] for r in res), max(abs(r["drift_at_end_pct"]) for r in res)))
    for r in res: print("  ", r)
json.dump(out, open(RESULTS / "weekend_drift.json", "w"), indent=1)
