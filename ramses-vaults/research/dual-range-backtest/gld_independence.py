"""Is the GLD feed derived from the Ramses GLD pool? Compare each OCR report's node observations and answer with the
pool price (from the swap log) around the observation time, and compare with SPY as a control."""
import bisect, json, pickle, statistics as st
from common import *
import engine as E
Q = E.Q
tr = json.load(open(RESULTS / "transmissions.json"))
out = {}
def q(xs, p):
    xs = sorted(xs); return xs[min(len(xs) - 1, int(len(xs) * p))]
for name in ("GLD", "SPY"):
    cols, meta = pickle.load(open(RESULTS / f"dataset_{name}.pkl", "rb"))
    stock0 = ADDR[name]["stock_is_token0"]
    ts = cols["ts"]; sa = cols["s_after"]
    def pool_price(t):
        k = bisect.bisect_right(ts, t) - 1
        if k < 0: return None
        s = sa[k]
        return s * s * Q if stock0 else Q / (s * s)
    rows = [x for x in tr[name] if x["obsTs"] >= ts[0] + 3600 and x["answer"] > 1]
    res = {"n_reports": len(rows)}
    # best lag: smallest median |answer / pool_price(obsTs - lag) - 1|
    lags = [-60, -30, -10, -5, 0, 5, 10, 30, 60, 120, 300, 900]
    table = {}
    for lag in lags:
        errs = []
        for x in rows:
            p = pool_price(x["obsTs"] - lag)
            if p: errs.append(abs(x["answer"] / p - 1) * 100)
        table[lag] = {"median_abs_err_pct": st.median(errs), "p90": q(errs, .9), "max": max(errs)}
    res["answer_vs_pool_by_lag_s"] = table
    # each node's observation vs the pool price at the same moment: how many nodes see the same number?
    ob_err = []
    for x in rows:
        p = pool_price(x["obsTs"])
        if p:
            ob_err += [abs(o / p - 1) * 100 for o in x["obs"]]
    res["node_obs_vs_pool_at_obsTs_pct"] = {"median": st.median(ob_err), "p90": q(ob_err, .9), "max": max(ob_err)}
    spreads = [(max(x["obs"]) / min(x["obs"]) - 1) * 100 for x in rows]
    res["node_spread_pct_median"] = st.median(spreads)
    # spread of the pool price itself over the 60 s before obsTs (how much would nodes differ if they read the pool at different moments)
    mv = []
    for x in rows:
        a = [pool_price(x["obsTs"] - d) for d in range(0, 60, 5)]
        a = [v for v in a if v]
        if a: mv.append((max(a) / min(a) - 1) * 100)
    res["pool_price_range_over_60s_pct_median"] = st.median(mv)
    # weekend/off-session reports
    wk = [x for x in rows if session_of(x["obsTs"]) == "weekend"]
    e = []
    for x in wk:
        p = pool_price(x["obsTs"])
        if p: e.append((x["answer"] / p - 1) * 100)
    res["weekend_reports"] = {"n": len(wk), "answer_vs_pool_pct_mean": st.mean(e) if e else None, "mean_abs": st.mean(abs(v) for v in e) if e else None}
    # how often is the answer exactly (to 4 bp) at the pool price within +-120 s
    near = 0
    for x in rows:
        ps = [pool_price(x["obsTs"] + d) for d in range(-120, 121, 10)]
        ps = [v for v in ps if v]
        if ps and min(abs(x["answer"] / v - 1) for v in ps) < 0.0004: near += 1
    res["frac_answer_within_4bp_of_pool_price_in_pm120s"] = near / len(rows)
    out[name] = res
    print(name, json.dumps(res, indent=1))
(RESULTS / "gld_independence.json").write_text(json.dumps(out, indent=1))
