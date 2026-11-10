"""Q3. One-sided bands as take-profit (stock only, above market) and buy (quote only, below market) orders.

For every start time on a grid, a band of WIDTH (2%) is placed DIST (1%, 3%, 5%) away from the pool price and followed
through the real swap path for HORIZON days:

  sell band  [P0(1+d), P0(1+d)(1+w)]   deposit: 1 unit of notional in stock; converts to quote as the price rises through it
  buy band   [P0(1-d)/(1+w), P0(1-d)]  deposit: 1 unit of notional in quote; converts to stock as the price falls through it

  * the keeper looks every POLL seconds and closes the band the first time it sees the pool price beyond the far edge
    (fully converted); until then the band stays in the pool and converts back if the price returns;
  * while the price is inside the band every swap pays it the LP share of the fee in force (exact per unit of liquidity
    from the price path, diluted by the pool's own active liquidity for the stated notional, less the reserve cut);
  * at the horizon an unfilled or part-filled band is withdrawn as it is.

Benchmark: a limit order for the same amount at the band's average fill price sqrt(Pa x Pb), zero fee, filled in full the
first time the pool price reaches it and then final. Both are valued in quote at the horizon's end price, so

  band - limit = fees earned  +  (band inventory - limit inventory) marked at the end.

Outputs out/q3_<venue>.json. Usage: q3_bands.py VENUE [--notional 1000] [--horizon-days 7] [--step-hours 6]
"""
from __future__ import annotations

import argparse

import numpy as np

from lib_cl import VENUES, dump, iso, load_swaps, session_of

DISTS = (0.01, 0.03, 0.05)
WIDTH = 0.02
POLL = 300.0


def one_band(side: str, p0: float, dist: float, ts: np.ndarray, p_prev: np.ndarray, p: np.ndarray, L_prev: np.ndarray, fee: np.ndarray,
             proto: np.ndarray, t_start: float, notional: float, reserve: float) -> dict:
    """Follow one band through the slice of swaps (already restricted to the horizon)."""
    if side == "sell":
        pa, pb = p0 * (1 + dist), p0 * (1 + dist) * (1 + WIDTH)
    else:
        pb = p0 * (1 - dist)
        pa = pb / (1 + WIDTH)
    sa, sb = np.sqrt(pa), np.sqrt(pb)
    limit = sa * sb                                   # average fill price of the fully crossed band
    if side == "sell":
        x0 = notional / p0                            # stock deposited
        liq = x0 / (1 / sa - 1 / sb)
    else:
        liq = notional / (sb - sa)                    # quote deposited
    s0, s1 = np.sqrt(p_prev), np.sqrt(p)
    lo, hi = np.minimum(s0, s1), np.maximum(s0, s1)
    ov_lo, ov_hi = np.maximum(lo, sa), np.minimum(hi, sb)
    inside = ov_hi > ov_lo
    f = fee / 1e6
    up = s1 > s0
    # LP fee per unit of liquidity on the part of the swap that ran inside the band (quote for up-moves, stock for down-moves)
    with np.errstate(divide="ignore", invalid="ignore"):
        fee_unit = np.where(inside, np.where(up, ov_hi - ov_lo, (1 / ov_lo - 1 / ov_hi) * p), 0.0) * f / (1 - f) * (1 - proto) * (1 - reserve)
    share = L_prev / (L_prev + liq)                   # our own liquidity dilutes the fee per unit (fixed observed flow)
    fee_usd = liq * fee_unit * share
    # keeper: first poll at which the pool price is beyond the far edge
    done_swap = (p >= pb) if side == "sell" else (p <= pa)
    closed_at = None
    if done_swap.any():
        polls = np.arange(t_start + POLL, ts[-1] + POLL, POLL)
        j = np.searchsorted(ts, polls, side="right") - 1
        ok = j >= 0
        beyond = np.zeros(len(polls), dtype=bool)
        beyond[ok] = done_swap[j[ok]]
        k = np.flatnonzero(beyond)
        if len(k):
            closed_at = float(polls[k[0]])
    touch_far = bool(done_swap.any())
    lim_hit = (p >= limit) if side == "sell" else (p <= limit)
    limit_filled = bool(lim_hit.any())
    touched = bool(inside.any())
    p_end = float(p[-1])
    if closed_at is not None:
        m = ts <= closed_at
        fees = float(fee_usd[m].sum())
        active_s = float(np.sum(np.diff(np.concatenate([ts[m], [closed_at]]))[inside[m]])) if m.any() else 0.0
        if side == "sell":
            band_val = liq * (sb - sa)                # all quote
        else:
            band_val = liq * (1 / sa - 1 / sb) * p_end  # all stock, marked at the end
    else:
        fees = float(fee_usd.sum())
        active_s = float(np.sum(np.diff(np.concatenate([ts, [ts[-1]]]))[inside]))
        sc = min(max(np.sqrt(p_end), sa), sb)
        band_val = liq * (1 / sc - 1 / sb) * p_end + liq * (sc - sa)
    if side == "sell":
        lim_val = (notional / p0) * (limit if limit_filled else p_end)
    else:
        lim_val = (notional / limit) * p_end if limit_filled else notional
    return {"filled": closed_at is not None, "touched": touched, "touched_far_edge": touch_far, "limit_filled": limit_filled,
            "fees_bps": 1e4 * fees / notional, "active_hours": active_s / 3600, "band_bps": 1e4 * (band_val + fees - notional) / notional,
            "limit_bps": 1e4 * (lim_val - notional) / notional, "diff_bps": 1e4 * (band_val + fees - lim_val) / notional,
            "inventory_diff_bps": 1e4 * (band_val - lim_val) / notional,
            "hours_to_close": None if closed_at is None else (closed_at - t_start) / 3600,
            "partial_at_end": closed_at is None and touched and (pa < p_end < pb)}


def summarise(rows: list[dict]) -> dict:
    n = len(rows)
    arr = lambda k, sel=lambda r: True: np.array([r[k] for r in rows if sel(r)], dtype=float)
    d = arr("diff_bps")
    out = {"orders": n, "fill_prob": float(np.mean([r["filled"] for r in rows])), "touch_prob": float(np.mean([r["touched"] for r in rows])),
           "limit_fill_prob": float(np.mean([r["limit_filled"] for r in rows])),
           "limit_filled_band_not": float(np.mean([r["limit_filled"] and not r["filled"] for r in rows])),
           "fees_bps_mean": float(arr("fees_bps").mean()), "fees_bps_median": float(np.median(arr("fees_bps"))),
           "diff_vs_limit_bps": {"mean": float(d.mean()), "median": float(np.median(d)), "p05": float(np.percentile(d, 5)),
                                 "p95": float(np.percentile(d, 95)), "share_better": float(np.mean(d > 0)), "share_worse": float(np.mean(d < 0))},
           "inventory_diff_bps_mean": float(arr("inventory_diff_bps").mean())}
    filled = [r for r in rows if r["filled"]]
    if filled:
        out["when_filled"] = {"orders": len(filled), "fees_bps_mean": float(np.mean([r["fees_bps"] for r in filled])),
                              "fees_bps_median": float(np.median([r["fees_bps"] for r in filled])),
                              "active_hours_median": float(np.median([r["active_hours"] for r in filled])),
                              "hours_to_close_median": float(np.median([r["hours_to_close"] for r in filled])),
                              "diff_bps_mean": float(np.mean([r["diff_bps"] for r in filled]))}
    rev = [r for r in rows if r["limit_filled"] and not r["filled"]]
    if rev:
        out["limit_filled_band_not_detail"] = {"orders": len(rev), "diff_bps_mean": float(np.mean([r["diff_bps"] for r in rev])),
                                               "fees_bps_mean": float(np.mean([r["fees_bps"] for r in rev]))}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("venue", choices=list(VENUES))
    ap.add_argument("--notional", type=float, default=1000.0)
    ap.add_argument("--horizon-days", type=float, default=7.0)
    ap.add_argument("--step-hours", type=float, default=6.0)
    ap.add_argument("--reserve", type=float, default=0.2)
    ap.add_argument("--from-ts", type=float)
    ap.add_argument("--tag", default="")
    a = ap.parse_args()
    v = VENUES[a.venue]
    s = load_swaps(a.venue, a.from_ts)
    H = a.horizon_days * 86400
    starts = np.arange(s.ts[0] + 3600, s.ts[-1] - H, a.step_hours * 3600)
    res = {"venue": a.venue, "label": v.label, "window": [iso(s.ts[0]), iso(s.ts[-1])], "notional_usd": a.notional, "horizon_days": a.horizon_days,
           "width": WIDTH, "poll_s": POLL, "reserve_cut": a.reserve, "starts": len(starts), "results": {}}
    print(f"{v.label}  {res['window']}  {len(starts)} start times, horizon {a.horizon_days:g} d, notional ${a.notional:,.0f}")
    print(f"{'side':<5}{'dist':>5}{'fill%':>7}{'touch%':>7}{'limit%':>7}{'lim-only%':>10}{'fee bp':>8}{'fee|fill':>9}{'diff bp':>9}{'median':>8}{'p05':>8}{'p95':>8}{'inv bp':>8}{'h to fill':>10}")
    for side in ("sell", "buy"):
        for dist in DISTS:
            rows = []
            for t in starts:
                i0 = int(np.searchsorted(s.ts, t, side="right"))
                i1 = int(np.searchsorted(s.ts, t + H, side="right"))
                if i1 - i0 < 10 or i0 == 0:
                    continue
                p0 = float(s.p[i0 - 1])
                r = one_band(side, p0, dist, s.ts[i0:i1], s.p_prev[i0:i1], s.p[i0:i1], s.L_prev[i0:i1], s.fee_ppm[i0:i1], s.proto[i0:i1],
                             float(t), a.notional, a.reserve)
                r["start"] = float(t)
                r["session"] = session_of(float(t), v.equity)
                rows.append(r)
            sm = summarise(rows)
            sm["by_start_session"] = {se: summarise([r for r in rows if r["session"] == se]) for se in ("regular", "extended", "overnight", "weekend")
                                      if any(r["session"] == se for r in rows)}
            res["results"][f"{side}_{int(dist * 100)}pct"] = sm
            d = sm["diff_vs_limit_bps"]
            wf = sm.get("when_filled", {})
            print(f"{side:<5}{dist * 100:>4.0f}%{100 * sm['fill_prob']:>7.1f}{100 * sm['touch_prob']:>7.1f}{100 * sm['limit_fill_prob']:>7.1f}"
                  f"{100 * sm['limit_filled_band_not']:>10.1f}{sm['fees_bps_mean']:>8.2f}{wf.get('fees_bps_mean', float('nan')):>9.2f}{d['mean']:>9.1f}"
                  f"{d['median']:>8.1f}{d['p05']:>8.1f}{d['p95']:>8.1f}{sm['inventory_diff_bps_mean']:>8.1f}{wf.get('hours_to_close_median', float('nan')):>10.1f}")
    p = dump(res, f"q3_{a.venue}_{int(a.notional)}_{a.horizon_days:g}d{a.tag}.json")
    print("wrote", p)


if __name__ == "__main__":
    main()
