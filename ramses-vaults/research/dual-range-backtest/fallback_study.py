"""Evidence for a bounded oracle fallback (SPY, NVDA): how far a Ramses-TWAP-anchored estimate is from the next Chainlink answer, and what
availability each (session rule, maximum duration D, deviation limit L) policy would recover. Offline; uses the committed feed answers and the pool
swap history. NOTHING here changes a guard; the 12 h bound stays; this only measures a candidate.

Definitions
  feed          Chainlink answers (updatedAt, answer); the answer is already USD per RAW TOKEN (= equity price x uiMultiplier per Chainlink's docs), so the
                multiplier is NOT applied again anywhere below.
  pool TWAP     time-weighted mean TICK of the Ramses pool over window W (the Uniswap-v3 oracle definition), converted to USD per token.
  anchor        the last Chainlink answer (t_a, P_a) that was fresh when recorded. NEVER re-anchored from a fallback value.
  fallback F(t) P_a x TWAP_W(t) / TWAP_W(t_a): the pool's RELATIVE move since the anchor applied to the anchor price.
  error         F(t_n-) / P_n - 1 where P_n is the next Chainlink answer (the best later observation of the price); compared with the naive error of
                simply keeping P_a, |P_n / P_a - 1|.
  session       evidence_lib.session_of (regular / overnight / weekend / holiday); closures = weekend + holiday.
  false liquidation risk  a borrower at utilisation u of its limit is wrongly liquidated if the estimate understates by more than 1 - u.

  python3 fallback_study.py            writes evidence/oracle/fallback-study.json
"""
import bisect
import json
import math
import pickle
import statistics

from common import HERE, RESULTS, ADDR
import engine as E
from evidence_lib import session_of

BOUND = 12 * 3600
WINDOWS = {"5m": 300, "15m": 900, "1h": 3600}
GAP_BUCKETS = [(0, 3600, "<1h"), (3600, 6 * 3600, "1-6h"), (6 * 3600, 12 * 3600, "6-12h"), (12 * 3600, 24 * 3600, "12-24h"), (24 * 3600, 48 * 3600, "24-48h"), (48 * 3600, 10 ** 9, ">48h")]


class Twap:
    def __init__(self, pair, cols=None, stock_is_token0=None):
        if cols is None:
            cols, _ = pickle.load(open(RESULTS / f"dataset_{pair}.pkl", "rb"))
        self.ts = cols["ts"]
        self.tick = cols["tick_after"]
        self.market = E.Market(ADDR[pair]["stock_is_token0"] if stock_is_token0 is None else stock_is_token0)
        self.cum = [0.0]
        for i in range(1, len(self.ts)):
            self.cum.append(self.cum[-1] + self.tick[i - 1] * (self.ts[i] - self.ts[i - 1]))

    def _integral(self, t):
        i = bisect.bisect_right(self.ts, t) - 1
        if i < 0:
            return 0.0
        return self.cum[i] + self.tick[i] * (t - self.ts[i])

    def avg_tick(self, t, w):
        a = max(t - w, self.ts[0])
        return (self._integral(t) - self._integral(a)) / (t - a) if t > a else self.tick[0]

    def price(self, tick):
        return self.market.usd_per_stock(E.tick_sqrt(tick))

    def spot_tick(self, t):
        i = bisect.bisect_right(self.ts, t) - 1
        return self.tick[max(i, 0)]


def q(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else None


def summarize(errs):
    a = [abs(e) for e in errs]
    return {"n": len(errs), "p50": q(a, .5), "p90": q(a, .9), "p99": q(a, .99), "max": max(a) if a else None,
            "understateP1": q(errs, .01), "overstateP99": q(errs, .99)}


def study(pair, feed):
    tw = Twap(pair)
    ts_f = [x["updatedAt"] for x in feed]
    p_f = [x["answer"] / 1e8 for x in feed]
    out = {"pair": pair, "feedUpdates": len(feed), "poolSwaps": len(tw.ts)}
    t_lo, t_hi = max(ts_f[0], tw.ts[0]) + 3600, min(ts_f[-1], tw.ts[-1])
    idx = [i for i in range(len(ts_f)) if t_lo <= ts_f[i] <= t_hi]

    # 1. pool-versus-feed basis at the moment of each Chainlink update (feed fresh by definition)
    basis = {w: {"regular": [], "overnight": [], "weekend": [], "holiday": []} for w in WINDOWS}
    for i in idx:
        s = session_of(ts_f[i])
        for w, sec in WINDOWS.items():
            basis[w][s].append(tw.price(tw.avg_tick(ts_f[i], sec)) / p_f[i] - 1)
    out["basisPoolTwapVsFeedAtFeedUpdate"] = {w: {s: summarize(v) for s, v in d.items() if v} for w, d in basis.items()}

    # 2. error of the anchored fallback vs naive stale price, at the moment before each next update, by gap and anchor session
    pairs = []
    for k in idx:
        if k + 1 >= len(ts_f) or ts_f[k + 1] > t_hi:
            continue
        ta, tn = ts_f[k], ts_f[k + 1]
        gap = tn - ta
        pa, pn = p_f[k], p_f[k + 1]
        row = {"gap": gap, "sa": session_of(ta), "sn": session_of(tn), "naive": pa / pn - 1}
        for w, sec in WINDOWS.items():
            ratio = tw.price(tw.avg_tick(tn - 1, sec)) / tw.price(tw.avg_tick(ta, sec))
            row[w] = pa * ratio / pn - 1
            row["move_" + w] = ratio - 1
        pairs.append(row)
    out["anchoredErrorByGap"] = {}
    for lo, hi, name in GAP_BUCKETS:
        sel = [r for r in pairs if lo <= r["gap"] < hi]
        if sel:
            out["anchoredErrorByGap"][name] = {"pairs": len(sel), "naiveKeepLastPrice": summarize([r["naive"] for r in sel]),
                                               **{f"anchoredTwap_{w}": summarize([r[w] for r in sel]) for w in WINDOWS}}
    out["anchoredErrorByGapSpanningClosure"] = {}
    for label, pred in (("gap starts and ends in open sessions", lambda r: r["sa"] in ("regular", "overnight") and r["sn"] in ("regular", "overnight") and r["gap"] <= 12 * 3600),
                        ("gap spans a weekend or holiday", lambda r: (r["sa"] in ("weekend", "holiday") or r["sn"] in ("weekend", "holiday") or r["gap"] > 36 * 3600))):
        sel = [r for r in pairs if pred(r)]
        if sel:
            out["anchoredErrorByGapSpanningClosure"][label] = {"pairs": len(sel), "naiveKeepLastPrice": summarize([r["naive"] for r in sel]),
                                                               **{f"anchoredTwap_{w}": summarize([r[w] for r in sel]) for w in WINDOWS}}

    # 3. policies over every stale minute: share recovered, and error of the fallback against the next answer at that moment
    policies = {}
    d_grid = [24 * 3600, 48 * 3600, 72 * 3600, 96 * 3600]
    l_grid = [0.01, 0.02, 0.03]
    rules = {"open sessions only": ("regular", "overnight"), "all sessions incl. weekend/holiday": ("regular", "overnight", "weekend", "holiday")}
    stale = {"regular": 0, "overnight": 0, "weekend": 0, "holiday": 0}
    cnt = {(rn, d, l): {s: 0 for s in stale} for rn in rules for d in d_grid for l in l_grid}
    errs_by_policy = {(rn, d, l): [] for rn in rules for d in d_grid for l in l_grid}
    t = ts_f[idx[0]] + BOUND
    W = 3600
    while t <= t_hi:
        i = bisect.bisect_right(ts_f, t) - 1
        age = t - ts_f[i]
        if age > BOUND:
            s = session_of(int(t))
            stale[s] += 1
            if i + 1 < len(ts_f):
                move = tw.price(tw.avg_tick(t, W)) / tw.price(tw.avg_tick(ts_f[i], W)) - 1
                est_err = None
                for rn, sessions in rules.items():
                    for d in d_grid:
                        for l in l_grid:
                            if s in sessions and age <= d and abs(move) <= l:
                                cnt[(rn, d, l)][s] += 1
                                if int(t) % 1800 < 60:      # sample the error every 30 minutes against the NEXT answer
                                    if est_err is None:
                                        est_err = p_f[i] * (1 + move) / p_f[i + 1] - 1
                                    errs_by_policy[(rn, d, l)].append(est_err)
        t += 60
    tot_stale = sum(stale.values())
    out["staleMinutes"] = {**stale, "total": tot_stale}
    out["weeksObserved"] = (t_hi - ts_f[idx[0]] - BOUND) / 86400 / 7
    out["windowStart"], out["windowEnd"] = ts_f[idx[0]] + BOUND, t_hi
    out["policies"] = []
    for (rn, d, l), c in cnt.items():
        rec_tr = c["regular"] + c["overnight"]
        rec_cl = c["weekend"] + c["holiday"]
        e = errs_by_policy[(rn, d, l)]
        out["policies"].append({"rule": rn, "maxDurationHours": d / 3600, "deviationLimit": l,
                                "recoveredShareOfStaleTradingSessionTime": rec_tr / max(stale["regular"] + stale["overnight"], 1),
                                "recoveredShareOfStaleClosureTime": rec_cl / max(stale["weekend"] + stale["holiday"], 1),
                                "recoveredHoursPerWeek": (rec_tr + rec_cl) / 60 / (out["weeksObserved"]),
                                "errorSamples": len(e), "errorVsNextAnswer": summarize(e) if e else None,
                                "falseLiquidationExposure": {f"u={u}": (sum(1 for x in e if x < -(1 - u)) / len(e)) if e else None for u in (0.95, 0.98, 0.99)}})
    return out


def multiplier_check(pair, feed, mult):
    """Is the feed already in token units (equity x uiMultiplier)? Compare the SIGNED pool-TWAP-over-feed basis at feed updates with the three
    hypotheses: feed already includes the multiplier (basis ~ 0), feed is the bare equity price (basis ~ +mult-1), multiplier applied twice
    (basis ~ -(mult-1))."""
    tw = Twap(pair)
    xs = []
    for x in feed:
        t = x["updatedAt"]
        if tw.ts[0] + 3600 <= t <= tw.ts[-1] and session_of(t) in ("regular", "overnight"):
            xs.append(tw.price(tw.avg_tick(t, 900)) / (x["answer"] / 1e8) - 1)
    xs.sort()
    n = len(xs)
    se = statistics.stdev(xs) / math.sqrt(n)
    m = statistics.mean(xs)
    d = mult - 1
    return {"n": n, "multiplier": mult, "meanBasis": m, "medianBasis": xs[n // 2], "standardError": se,
            "hypotheses": {"feedIncludesMultiplier (expect ~0)": {"expected": 0.0, "sigmas": abs(m - 0) / se},
                           "feedIsBareEquityPrice (expect +mult-1)": {"expected": d, "sigmas": abs(m - d) / se},
                           "multiplierAppliedTwice (expect -(mult-1))": {"expected": -d, "sigmas": abs(m + d) / se}},
            "caveat": "uiMultiplier read once at the fork pin (1.001718e18) and assumed constant over the window; a distribution inside the window would blur this"}


def main():
    answers = json.loads((HERE / "evidence" / "bundle" / "inputs" / "answer_updated.json").read_text())["series"]
    res = {"schema": "ramses-fallback-study-1", "method": __doc__.split("Definitions")[0].strip(), "bound": BOUND, "pairs": {}}
    for pair in ("SPY", "NVDA"):
        res["pairs"][pair] = study(pair, answers[pair])
        r = res["pairs"][pair]
        print(pair, "stale minutes", r["staleMinutes"])
        for w in ("regular", "overnight", "weekend"):
            b = r["basisPoolTwapVsFeedAtFeedUpdate"]["1h"].get(w)
            if b:
                print(f"   basis 1h TWAP vs feed at update, {w}: n={b['n']} p50 {100 * b['p50']:.3f}% p90 {100 * b['p90']:.3f}% p99 {100 * b['p99']:.3f}% max {100 * b['max']:.3f}%")
    res["multiplierCheckSPY"] = multiplier_check("SPY", answers["SPY"], 1.001717991187472003)
    mc = res["multiplierCheckSPY"]
    print("SPY multiplier check: mean basis %.3f%% (se %.3f%%); sigmas away: " % (100 * mc["meanBasis"], 100 * mc["standardError"]),
          {k: round(v["sigmas"], 1) for k, v in mc["hypotheses"].items()})
    p = HERE / "evidence" / "oracle" / "fallback-study.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(res, indent=1))
    print("wrote", p)


if __name__ == "__main__":
    main()
