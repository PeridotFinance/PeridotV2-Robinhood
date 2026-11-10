"""Q3. One-sided bands as take-profit (base token only, above market) and buy (quote only, below market) orders.

The band is the range take-profit prototype's preset (data/facts/MECHANICS.md section 5): DIST ticks (100 / 300 / 490,
about 1% / 3% / 5%) from the pool price, 200 ticks (2%) wide. Every start time on a grid places one band and follows it
through the real swap path for HORIZON days:

  sell band  starts all base token, converts to quote as the price rises through it
  buy band   starts all quote, converts to base token as the price falls through it (not built; modelled as the mirror)

  * finalize: the keeper polls every POLL seconds and closes the band the first time it sees the pool price beyond the far
    edge AND the contract's own conditions hold: the feed is no older than MAX_ORACLE_AGE and the pool is within
    ORACLE_GATE of the feed. Until then the band stays in the pool and converts back if the price returns;
  * fees: while the price is inside the band every swap pays it the LP share of the fee in force (exact per unit of
    liquidity from the price path, diluted by the pool's active liquidity for the stated notional); the user keeps
    USER_FEE_SHARE of what the band collects;
  * rewards: gauge emissions accrue to the band while it is in range, in proportion to its share of in-range liquidity;
    reported in tokens and in USD "if sold", on a separate line, never inside the comparison;
  * at the horizon an unfilled or part-filled band is withdrawn as it is.

Benchmark: a limit order for the same amount at the band's average fill price sqrt(Pa x Pb), zero fee, filled in full the
first time the pool price reaches it and then final. A second benchmark is a limit at the band's far edge (the price the
band needs to see before it is final). Both sides of every comparison are valued in quote at the horizon's end price:

  band - limit = fees kept  +  (band inventory - limit inventory) at the end price.

Outputs out/q3_<venue>_<notional>_<horizon>d.json.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from lib_cl import VENUES, dump, iso, load_feed, load_swaps, session_of

PAR = json.loads((Path(__file__).parent / "params.json").read_text())


def one_band(side: str, p0: float, dist_ticks: int, width_ticks: int, sl: dict, t_start: float, notional: float, vp: dict) -> dict:
    ts, p_prev, p, L_prev, L, fee, proto = (sl[k] for k in ("ts", "p_prev", "p", "L_prev", "L", "fee", "proto"))
    if side == "sell":
        pa = p0 * 1.0001 ** dist_ticks
        pb = pa * 1.0001 ** width_ticks
    else:
        pb = p0 * 1.0001 ** -dist_ticks
        pa = pb * 1.0001 ** -width_ticks
    sa, sb = np.sqrt(pa), np.sqrt(pb)
    limit = sa * sb
    liq = (notional / p0) / (1 / sa - 1 / sb) if side == "sell" else notional / (sb - sa)
    s0, s1 = np.sqrt(p_prev), np.sqrt(p)
    lo, hi = np.minimum(s0, s1), np.maximum(s0, s1)
    ov_lo, ov_hi = np.maximum(lo, sa), np.minimum(hi, sb)
    inside = ov_hi > ov_lo
    f = fee / 1e6
    up = s1 > s0
    with np.errstate(divide="ignore", invalid="ignore"):
        fee_unit = np.where(inside, np.where(up, ov_hi - ov_lo, (1 / ov_lo - 1 / ov_hi) * p), 0.0) * f / (1 - f) * (1 - proto)
    fee_usd = liq * fee_unit * (L_prev / (L_prev + liq)) * vp["user_fee_share"]
    # --- finalize: beyond the far edge at a poll, feed fresh, pool within the gate of the feed
    nxt = np.concatenate([ts[1:], [ts[-1]]])
    beyond = (p >= pb) if side == "sell" else (p <= pa)
    poll = vp["poll_s"]
    first_poll = t_start + np.ceil(np.maximum(ts - t_start, 0.0) / poll) * poll      # first poll at or after the swap
    seen = beyond & (first_poll < nxt)
    ok = seen
    if sl.get("oracle") is not None:
        o = sl["oracle"]
        op = o.at(first_poll)
        age = o.age(first_poll)
        ok = seen & (age <= vp["max_oracle_age_s"]) & (np.abs(p / op - 1) * 1e4 <= vp["oracle_gate_bps"])
    k = np.flatnonzero(ok)
    closed_i = int(k[0]) if len(k) else None
    closed_at = float(first_poll[closed_i]) if closed_i is not None else None
    blocked = bool(seen.any()) and (closed_i is None or int(np.flatnonzero(seen)[0]) < closed_i)
    lim_hit = (p >= limit) if side == "sell" else (p <= limit)
    limit_filled = bool(lim_hit.any())
    edge_filled = bool(beyond.any())
    touched = bool(inside.any())
    p_end = float(p[-1])
    m = np.ones(len(ts), dtype=bool) if closed_i is None else (np.arange(len(ts)) <= closed_i)
    fees = float(fee_usd[m].sum())
    # in-range time and reward accrual (state after swap i holds until swap i+1)
    in_band = (p >= pa) & (p < pb)
    dt_ = np.where(m, nxt - ts, 0.0)
    if closed_at is not None:
        dt_[closed_i] = max(closed_at - ts[closed_i], 0.0)
    active_s = float(dt_[in_band].sum())
    rew_tokens = rew_usd = 0.0
    if sl.get("rate") is not None:
        acc = dt_ * in_band * sl["rate"] * liq / (L + liq)
        rew_tokens = float(acc.sum())
        if sl.get("rprice") is not None:
            rew_usd = float(np.nansum(acc * sl["rprice"]))
    if closed_at is not None:
        band_val = liq * (sb - sa) if side == "sell" else liq * (1 / sa - 1 / sb) * p_end
    else:
        sc = min(max(np.sqrt(p_end), sa), sb)
        band_val = liq * (1 / sc - 1 / sb) * p_end + liq * (sc - sa)
    if side == "sell":
        base = notional / p0
        lim_val = base * (limit if limit_filled else p_end)
        edge_val = base * (pb if edge_filled else p_end)
    else:
        lim_val = (notional / limit) * p_end if limit_filled else notional
        edge_val = (notional / pa) * p_end if edge_filled else notional
    bps = lambda x: 1e4 * x / notional
    return {"filled": closed_at is not None, "touched": touched, "limit_filled": limit_filled, "edge_touched": edge_filled, "finalize_blocked": blocked,
            "fees_bps": bps(fees), "active_hours": active_s / 3600, "reward_tokens": rew_tokens, "reward_bps_if_sold": bps(rew_usd),
            "diff_bps": bps(band_val + fees - lim_val), "inventory_diff_bps": bps(band_val - lim_val), "diff_vs_edge_limit_bps": bps(band_val + fees - edge_val),
            "hours_to_close": None if closed_at is None else (closed_at - t_start) / 3600}


def dist(x) -> dict:
    x = np.asarray(x, dtype=float)
    return {"mean": float(x.mean()), "median": float(np.median(x)), "p05": float(np.percentile(x, 5)), "p95": float(np.percentile(x, 95)),
            "share_better": float(np.mean(x > 1e-9)), "share_worse": float(np.mean(x < -1e-9))}


def summarise(rows: list[dict]) -> dict:
    if not rows:
        return {}
    g = lambda k, rs=None: [r[k] for r in (rows if rs is None else rs)]
    out = {"orders": len(rows), "fill_prob": float(np.mean(g("filled"))), "touch_prob": float(np.mean(g("touched"))),
           "limit_fill_prob": float(np.mean(g("limit_filled"))), "edge_touch_prob": float(np.mean(g("edge_touched"))),
           "finalize_blocked_prob": float(np.mean(g("finalize_blocked"))),
           "limit_filled_band_not": float(np.mean([r["limit_filled"] and not r["filled"] for r in rows])),
           "fees_bps_mean": float(np.mean(g("fees_bps"))), "reward_bps_if_sold_mean": float(np.mean(g("reward_bps_if_sold"))),
           "reward_tokens_mean": float(np.mean(g("reward_tokens"))),
           "diff_vs_limit_bps": dist(g("diff_bps")), "diff_vs_edge_limit_bps": dist(g("diff_vs_edge_limit_bps")),
           "inventory_diff_bps_mean": float(np.mean(g("inventory_diff_bps")))}
    fl = [r for r in rows if r["filled"]]
    if fl:
        out["when_filled"] = {"orders": len(fl), "fees_bps_mean": float(np.mean(g("fees_bps", fl))), "fees_bps_median": float(np.median(g("fees_bps", fl))),
                              "reward_bps_if_sold_mean": float(np.mean(g("reward_bps_if_sold", fl))),
                              "active_hours_median": float(np.median(g("active_hours", fl))), "hours_to_close_median": float(np.median(g("hours_to_close", fl))),
                              "diff_bps_mean": float(np.mean(g("diff_bps", fl)))}
    rev = [r for r in rows if r["limit_filled"] and not r["filled"]]
    if rev:
        out["limit_filled_band_not_detail"] = {"orders": len(rev), "diff_bps_mean": float(np.mean(g("diff_bps", rev))), "diff_bps_median": float(np.median(g("diff_bps", rev))),
                                               "fees_bps_mean": float(np.mean(g("fees_bps", rev)))}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("venue", choices=list(VENUES))
    ap.add_argument("--notional", type=float, default=1000.0)
    ap.add_argument("--horizon-days", type=float, default=7.0)
    ap.add_argument("--step-hours", type=float, default=6.0)
    ap.add_argument("--width-ticks", type=int, default=PAR["bands"]["width_ticks"])
    ap.add_argument("--from-ts", type=float)
    ap.add_argument("--no-gates", action="store_true", help="ignore the feed-age and pool-versus-feed conditions on finalize")
    ap.add_argument("--tag", default="")
    a = ap.parse_args()
    v = VENUES[a.venue]
    vp = PAR["bands"][a.venue]
    s = load_swaps(a.venue, a.from_ts)
    oracle = None
    if not a.no_gates:
        oracle = load_feed(v.feed, v.feed_decimals)
    rate = rprice = None
    rw = PAR.get("rewards", {}).get(a.venue)
    if rw:
        from rewards import emission_rate, reward_price
        er = emission_rate(rw)
        rate = np.array([er(float(t)) for t in s.ts])
        ps = reward_price(rw)
        rprice = ps.at(s.ts) * (1 - rw.get("sale_cost", 0.0)) if ps is not None else None
    H = a.horizon_days * 86400
    t_first = s.ts[0] + 3600 if oracle is None else max(s.ts[0] + 3600, oracle.t[0] + 60)
    starts = np.arange(t_first, s.ts[-1] - H, a.step_hours * 3600)
    res = {"venue": a.venue, "label": v.label, "window": [iso(s.ts[0]), iso(s.ts[-1])], "notional_usd": a.notional, "horizon_days": a.horizon_days,
           "width_ticks": a.width_ticks, "venue_params": vp, "gates": not a.no_gates, "starts": len(starts), "results": {}}
    print(f"{v.label}  {res['window']}  {len(starts)} start times, horizon {a.horizon_days:g} d, notional ${a.notional:,.0f}, gates {'on' if oracle is not None else 'off'}")
    print(f"{'side':<5}{'dist':>5}{'fill%':>7}{'touch%':>7}{'limit%':>7}{'lim-only%':>10}{'blocked%':>9}{'fee bp':>8}{'fee|fill':>9}{'rew bp':>8}{'diff bp':>9}{'median':>8}"
          f"{'p05':>8}{'p95':>8}{'vsEdge':>8}{'h to fill':>10}")
    for side in ("sell", "buy"):
        for dt in PAR["bands"]["dist_ticks"]:
            rows = []
            for t in starts:
                i0 = int(np.searchsorted(s.ts, t, side="right"))
                i1 = int(np.searchsorted(s.ts, t + H, side="right"))
                if i1 - i0 < 10 or i0 == 0:
                    continue
                sl = {"ts": s.ts[i0:i1], "p_prev": s.p_prev[i0:i1], "p": s.p[i0:i1], "L_prev": s.L_prev[i0:i1], "L": s.L[i0:i1], "fee": s.fee_ppm[i0:i1],
                      "proto": s.proto[i0:i1], "oracle": oracle, "rate": None if rate is None else rate[i0:i1],
                      "rprice": None if rprice is None else rprice[i0:i1]}
                r = one_band(side, float(s.p[i0 - 1]), dt, a.width_ticks, sl, float(t), a.notional, vp)
                r["start"] = float(t)
                r["session"] = session_of(float(t), v.equity)
                rows.append(r)
            sm = summarise(rows)
            sm["by_start_session"] = {se: summarise([r for r in rows if r["session"] == se]) for se in ("regular", "extended", "overnight", "weekend")}
            res["results"][f"{side}_{dt}"] = sm
            d = sm["diff_vs_limit_bps"]
            wf = sm.get("when_filled", {})
            print(f"{side:<5}{dt:>5}{100 * sm['fill_prob']:>7.1f}{100 * sm['touch_prob']:>7.1f}{100 * sm['limit_fill_prob']:>7.1f}{100 * sm['limit_filled_band_not']:>10.1f}"
                  f"{100 * sm['finalize_blocked_prob']:>9.1f}{sm['fees_bps_mean']:>8.2f}{wf.get('fees_bps_mean', float('nan')):>9.2f}{sm['reward_bps_if_sold_mean']:>8.2f}"
                  f"{d['mean']:>9.1f}{d['median']:>8.1f}{d['p05']:>8.1f}{d['p95']:>8.1f}{sm['diff_vs_edge_limit_bps']['mean']:>8.1f}{wf.get('hours_to_close_median', float('nan')):>10.1f}")
    p = dump(res, f"q3_{a.venue}_{int(a.notional)}_{a.horizon_days:g}d{a.tag}.json")
    print("wrote", p)


if __name__ == "__main__":
    main()
