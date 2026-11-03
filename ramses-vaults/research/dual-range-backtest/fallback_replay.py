"""Step-by-step REPLAY of the executable fallback policy (test/fallback/AnchoredFallbackOracle.sol: poke() / price()) over the saved feed answers and
pool swap history. Replaces the earlier availability tables that assumed every feed answer was anchored, used one duration for every session and
ignored basis refusal, observation history, pokes and the guard's own removal check. Offline. Changes no guard; the 12 h bound stays.

What is replayed, for every stale minute (feed older than 12 h):
  poke()   an answer is ANCHORED only if a poke happens while that answer is still the latest and fresh, the pool's observation history reaches back
           to (poke delay + W) before now, and |pool TWAP at the answer / answer - 1| <= maxBasis.  Poke models: ideal (1 min after the answer), a keeper
           polling every 15 min, hour or 2 h (a poke later than maxPokeDelay = 1 h is refused: PokeTooLate), and 10% / 30% of answers never poked (seeded).
  price()  AnchorMissing unless the latest answer is anchored; age limit D_closed while the calendar says closed, else D_open (the prototype's rule,
           'as written': a closed-session anchor is over D_open the moment the market reopens), or a variant 'since-open' clock that counts only
           the time since the later of the anchor and the reopening; then |pool TWAP now / pool TWAP at the anchor - 1| <= L.
  Two outcomes are counted separately:
    RETURNS    the oracle would return a price (anchored mode: anchor x relative move; hold mode: the anchor price held flat);
    COMPLETES  RETURNS and the vault's own removal guard would also pass: |pool spot / returned price - 1| <= R (maxRemovalDeviationBps). Exits can
               STILL be blocked by things this replay cannot see: the pair's withdrawable balance of the requested token (one-sided positions, README
               15C), the reserve and swap settings, caps and the checkpoint.
Not selected for availability: the grid is reported whole; nothing here picks D or L.

  python3 fallback_replay.py            writes evidence/oracle/fallback-replay.json
"""
import bisect
import json
import random

from common import HERE
from evidence_lib import session_of
import fallback_study as FS

W_DEFAULT = 1800
HISTORY = {"SPY": 24 * 3600, "NVDA": 12 * 3600}   # longest TWAP the pool's observation ring served at the probe pin (twap-probe.json ladder rung)
BOUND = FS.BOUND
STEP = 60
MAX_POKE_DELAY = 3600   # AnchoredFallbackOracle.Config.maxPokeDelay used by the economics and fork tests
POKES = {"ideal": ("every", 60, 0.0), "keeper15m": ("grid", 900, 0.0), "keeper1h": ("grid", 3600, 0.0), "keeper2h": ("grid", 7200, 0.0), "miss10pct": ("every", 60, 0.10), "miss30pct": ("every", 60, 0.30)}


def closed(t):
    return session_of(int(t)) in ("weekend", "holiday")


def build(pair, feed, W=W_DEFAULT, max_basis=0.01, poke="ideal", seed=7, tw=None, history=None):
    """Per answer: anchored (bool) and the reason it is not."""
    tw = tw or FS.Twap(pair)
    history = history or HISTORY[pair]
    ts_f = [x["updatedAt"] for x in feed]
    p_f = [x["answer"] / 1e8 for x in feed]
    mode, interval, miss = POKES[poke]
    rnd = random.Random(seed)
    anchored, reason, atick = {}, {}, {}
    for i, ta in enumerate(ts_f):
        if ta < tw.ts[0] + 3600 or ta > tw.ts[-1]:
            continue
        nxt = ts_f[i + 1] if i + 1 < len(ts_f) else None
        delay = interval if mode == "every" else (interval - (ta % interval) if ta % interval else interval)
        tp = ta + delay
        if rnd.random() < miss:
            anchored[i], reason[i] = False, "missed poke"
        elif nxt is not None and nxt <= tp:
            anchored[i], reason[i] = False, "superseded before the poke"
        elif delay > MAX_POKE_DELAY:
            anchored[i], reason[i] = False, "poke too late (maxPokeDelay)"
        elif delay + W > history:
            anchored[i], reason[i] = False, "pool history too short"
        else:
            tick = tw.avg_tick(ta, W)
            basis = abs(tw.price(tick) / p_f[i] - 1)
            if basis > max_basis:
                anchored[i], reason[i] = False, "basis too wide"
            else:
                anchored[i], reason[i] = True, "ok"
                atick[i] = tick
    return tw, ts_f, p_f, anchored, reason, atick


def stale_rows(tw, ts_f, W):
    """Per stale minute: answer index, age, closed flag, time since the market reopened (None while closed), pool move inputs."""
    rows = []
    t0 = max(ts_f[0], tw.ts[0]) + 3600
    t_hi = min(ts_f[-1], tw.ts[-1])
    start = next(t for t in ts_f if t >= t0) + BOUND
    t = start
    last_closed, open_start = closed(t), None
    while t <= t_hi:
        i = bisect.bisect_right(ts_f, t) - 1
        c = closed(t)
        if last_closed and not c:
            open_start = t
        last_closed = c
        if t - ts_f[i] > BOUND and i + 1 < len(ts_f):
            rows.append((i, t - ts_f[i], c, open_start, t))
        t += STEP
    return rows, start, t_hi


def run_policy(tw, ts_f, p_f, anchored, reason, atick, rows, W, d_open, d_closed, L, R, clock, mode):
    out = {"stale": 0, "returns": 0, "completes": 0, "fail": {}, "errs": [], "tr_stale": 0, "tr_returns": 0, "tr_completes": 0, "cl_stale": 0, "cl_returns": 0, "cl_completes": 0,
           "recovered_idx": []}
    for k, (i, age, c, open_start, t) in enumerate(rows):
        s = "cl" if c else "tr"
        out["stale"] += 1
        out[s + "_stale"] += 1
        fail = None
        if i not in anchored:
            fail = "no anchor (outside data)"
        elif not anchored[i]:
            fail = "AnchorMissing: " + reason[i]
        else:
            eff = age
            if clock == "since-open" and not c and open_start is not None and open_start > ts_f[i]:
                eff = t - open_start
            if eff > (d_closed if c else d_open):
                fail = "FallbackExpired"
            else:
                ratio = tw.price(tw.avg_tick(t, W)) / tw.price(atick[i])
                if abs(ratio - 1) > L:
                    fail = "MoveExceedsBound"
        if fail:
            out["fail"][fail] = out["fail"].get(fail, 0) + 1
            continue
        out["returns"] += 1
        out[s + "_returns"] += 1
        price = p_f[i] * ratio if mode == "anchored" else p_f[i]
        spot = tw.price(tw.spot_tick(t))
        if abs(spot / price - 1) <= R:
            out["completes"] += 1
            out[s + "_completes"] += 1
        else:
            out["fail"]["removal guard rejects (pool spot vs returned price)"] = out["fail"].get("removal guard rejects (pool spot vs returned price)", 0) + 1
        out["recovered_idx"].append(k)
        if t % 1800 < STEP:
            out["errs"].append(price / p_f[i + 1] - 1)
    return out


def summarize(o, weeks):
    ts, cs = max(o["tr_stale"], 1), max(o["cl_stale"], 1)
    return {"staleMinutes": o["stale"], "tradingStale": o["tr_stale"], "closureStale": o["cl_stale"],
            "returnsShareTrading": o["tr_returns"] / ts, "returnsShareClosure": o["cl_returns"] / cs,
            "completesShareTrading": o["tr_completes"] / ts, "completesShareClosure": o["cl_completes"] / cs,
            "failures": dict(sorted(o["fail"].items(), key=lambda kv: -kv[1])),
            "errorVsNextAnswer": FS.summarize(o["errs"]) if o["errs"] else None}


def step_loss(rows, o, ts_f, n=400, seed=3):
    """A uiMultiplier step at a uniformly random time x halts every returned price whose anchor is older than x (until the next answer is anchored).
    Mean share of the recovered minutes lost per step."""
    rec = [rows[k] for k in o["recovered_idx"]]
    if not rec:
        return None
    t_lo, t_hi = rows[0][4], rows[-1][4]
    rnd = random.Random(seed)
    tot = 0.0
    for _ in range(n):
        x = rnd.uniform(t_lo, t_hi)
        tot += sum(1 for (i, age, c, os_, t) in rec if ts_f[i] < x <= t) / len(rec)
    return tot / n


def main():
    answers = json.loads((HERE / "evidence" / "bundle" / "inputs" / "answer_updated.json").read_text())["series"]
    res = {"schema": "ramses-fallback-replay-1", "doc": __doc__.split("Not selected")[0].strip(), "history": HISTORY, "pairs": {}}
    for pair in ("SPY", "NVDA"):
        feed = answers[pair]
        base = {}
        rec = {"pair": pair}
        # reference configuration = the prototype test configuration (NOT a recommendation): W 30 min, D_open 24 h, D_closed 72 h, L 2%, basis 1%, R 6%
        ref = dict(W=W_DEFAULT, d_open=24 * 3600, d_closed=72 * 3600, L=0.02, R=0.06, clock="as-written", mode="anchored")
        tw, ts_f, p_f, anch, rsn, atick = build(pair, feed, W_DEFAULT, 0.01, "ideal")
        rows, start, t_hi = stale_rows(tw, ts_f, W_DEFAULT)
        weeks = (t_hi - start) / 86400 / 7
        rec["weeksObserved"], rec["windowStart"], rec["windowEnd"] = weeks, start, t_hi
        # 1. grid: D_open x D_closed x L x clock (anchored mode, ideal pokes, basis 1%, R 6%)
        grid = []
        for clock in ("as-written", "since-open"):
            for d_o in (18, 24, 36):   # the constructor requires maxOpenDuration >= maxStaleness (12 h); at 12 h the fallback can never apply
                for d_c in (48, 72, 96):
                    for L in (0.01, 0.02, 0.03):
                        o = run_policy(tw, ts_f, p_f, anch, rsn, atick, rows, W_DEFAULT, d_o * 3600, d_c * 3600, L, 0.06, clock, "anchored")
                        grid.append({"clock": clock, "dOpenH": d_o, "dClosedH": d_c, "L": L, **summarize(o, weeks)})
        rec["grid"] = grid
        # 2. reference policy under each poke model, basis limit, W, removal bound, mode
        sens = []
        for poke in POKES:
            for mb in (0.005, 0.01, 0.02, 0.04):
                tw2, ts2, p2, an2, rs2, at2 = build(pair, feed, W_DEFAULT, mb, poke)
                o = run_policy(tw2, ts2, p2, an2, rs2, at2, rows, W_DEFAULT, ref["d_open"], ref["d_closed"], ref["L"], ref["R"], ref["clock"], "anchored")
                sens.append({"poke": poke, "maxBasis": mb, **summarize(o, weeks), "anchoredAnswerShare": sum(1 for v in an2.values() if v) / max(len(an2), 1),
                             "notAnchoredReasons": {r: list(rs2.values()).count(r) for r in set(rs2.values()) if r != "ok"}})
        rec["sensitivityPokeAndBasis"] = sens
        extra = []
        for R in (0.01, 0.03, 0.06):
            for mode in ("anchored", "hold"):
                o = run_policy(tw, ts_f, p_f, anch, rsn, atick, rows, W_DEFAULT, ref["d_open"], ref["d_closed"], ref["L"], R, ref["clock"], mode)
                extra.append({"removalBound": R, "mode": mode, **summarize(o, weeks)})
        tw3, ts3, p3, an3, rs3, at3 = build(pair, feed, 3600, 0.01, "ideal")
        o = run_policy(tw3, ts3, p3, an3, rs3, at3, rows, 3600, ref["d_open"], ref["d_closed"], ref["L"], ref["R"], ref["clock"], "anchored")
        extra.append({"window": 3600, **summarize(o, weeks)})
        rec["sensitivityRemovalBoundModeWindow"] = extra
        o_ref = run_policy(tw, ts_f, p_f, anch, rsn, atick, rows, W_DEFAULT, ref["d_open"], ref["d_closed"], ref["L"], ref["R"], ref["clock"], "anchored")
        rec["reference"] = {"config": ref, **summarize(o_ref, weeks), "multiplierStepLossShare": step_loss(rows, o_ref, ts_f)}
        res["pairs"][pair] = rec
        r = rec["reference"]
        print(pair, "ref: returns trading %.3f closure %.3f; completes trading %.3f closure %.3f; step loss %s; fails %s" % (
            r["returnsShareTrading"], r["returnsShareClosure"], r["completesShareTrading"], r["completesShareClosure"], r["multiplierStepLossShare"], r["failures"]))
    p = HERE / "evidence" / "oracle" / "fallback-replay.json"
    p.write_text(json.dumps(res, indent=1))
    print("wrote", p)


if __name__ == "__main__":
    main()
