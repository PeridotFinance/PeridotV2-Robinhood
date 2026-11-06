"""Sanity checks on the swap dataset, fee in force, volume and pool-vs-feed drift by session (item 2/3)."""
import bisect, json, math, pickle, statistics as st, sys
from common import *
import engine as E
Q = E.Q

feed = json.load(open(RESULTS / "answer_updated.json"))["series"]
out = {}
def q(xs, p):
    xs = sorted(xs); return xs[min(len(xs) - 1, int(len(xs) * p))]
for name in sys.argv[1:] or ["SPY", "GLD"]:
    cols, meta = pickle.load(open(RESULTS / f"dataset_{name}.pkl", "rb"))
    stock0 = ADDR[name]["stock_is_token0"]
    n = len(cols["ts"])
    # --- depth/volume consistency: sum of L_after * path length vs actual net input
    num = den = 0.0
    ratios = []
    prev = cols["s_after"][0]
    usd_vol = []
    for i in range(1, n):
        s1 = cols["s_after"][i]
        L = cols["L_after"][i]
        fee = cols["fee_ppm"][i] / 1e6
        tok = cols["in_tok"][i]
        amt = cols["in_amt"][i] * (1 - fee)
        if s1 != prev:
            lo, hi = (prev, s1) if prev < s1 else (s1, prev)
            model = L * ((1 / lo - 1 / hi) if tok == 0 else (hi - lo))
            if amt > 0 and model > 0 and cols["tick_after"][i] == cols["tick_after"][i - 1]:
                ratios.append(model / amt)
            num += model; den += amt
        # USD volume
        price = (s1 * s1 * Q) if stock0 else Q / (s1 * s1)
        if stock0:
            usd = cols["in_amt"][i] / 1e6 if tok == 1 else cols["in_amt"][i] / 1e18 * price
        else:
            usd = cols["in_amt"][i] / 1e6 if tok == 0 else cols["in_amt"][i] / 1e18 * price
        usd_vol.append(usd)
        prev = s1
    days = (cols["ts"][-1] - cols["ts"][0]) / 86400
    tot = sum(usd_vol)
    fees = [cols["fee_ppm"][i] for i in range(n)]
    vw_fee = sum(f * u for f, u in zip(fees, usd_vol)) / tot
    res = {"swaps": n, "days": days, "usd_volume_total": tot, "usd_volume_per_day": tot / days, "median_swap_usd": st.median(usd_vol),
           "volume_weighted_fee_ppm": vw_fee, "time_in_force_fee_changes": len(meta["fee_changes"]),
           "L_model_over_actual_input_agg": num / den if den else None, "L_model_over_actual_input_median_same_tick": st.median(ratios) if ratios else None,
           "L_model_p10_p90": [q(ratios, .1), q(ratios, .9)] if ratios else None}
    # --- pool vs feed drift sampled every 300 s
    ft = [x["updatedAt"] for x in feed[name]]; fp = [x["answer"] / 1e8 for x in feed[name]]
    j = 0; ts = cols["ts"]
    drift = {"regular": [], "overnight": [], "weekend": [], "holiday": []}
    t = max(ts[0], ft[0] + 1)
    k = 0
    series_age = []
    while t < ts[-1]:
        k = bisect.bisect_right(ts, t) - 1
        s = cols["s_after"][k]
        price = (s * s * Q) if stock0 else Q / (s * s)
        fi = bisect.bisect_right(ft, t) - 1
        if fi >= 0:
            d = (price / fp[fi] - 1) * 100
            drift[session_of(int(t))].append(d)
            series_age.append((session_of(int(t)), t - ft[fi]))
        t += 300
    res["drift_pool_vs_feed_pct"] = {sess: ({"n": len(v), "mean": st.mean(v), "mean_abs": st.mean(abs(x) for x in v), "p95_abs": q([abs(x) for x in v], .95), "max_abs": max(abs(x) for x in v)} if v else None) for sess, v in drift.items()}
    # feed age distribution by session
    ages = {}
    for sess, a in series_age:
        ages.setdefault(sess, []).append(a)
    res["feed_age_h_by_session"] = {k2: {"mean": st.mean(v) / 3600, "p50": q(v, .5) / 3600, "p95": q(v, .95) / 3600, "frac_gt_12h": sum(1 for x in v if x > 43200) / len(v), "frac_gt_6h": sum(1 for x in v if x > 21600) / len(v), "frac_gt_2h": sum(1 for x in v if x > 7200) / len(v)} for k2, v in ages.items()}
    allv = [a for _, a in series_age]
    nonwk = [a for s_, a in series_age if s_ in ("regular", "overnight")]
    res["feed_age_all_frac_gt_12h"] = sum(1 for x in allv if x > 43200) / len(allv)
    res["feed_age_nonweekend_frac_gt_12h"] = sum(1 for x in nonwk if x > 43200) / len(nonwk)
    out[name] = res
    print(name, json.dumps(res, indent=1))
(RESULTS / "dataset_checks.json").write_text(json.dumps(out, indent=1))
