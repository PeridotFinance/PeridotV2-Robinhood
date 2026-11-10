"""Q1. Fee income versus realised arbitrage loss, by session and by realised-volatility bucket.

Everything is measured on real swaps of the pool itself (no position of ours is assumed):

  fee            what the swappers paid: input x fee in force; the LP part is fee x (1 - protocol share).
  arbitrage loss the liquidity's own trade (fee removed) valued at a later price: for swap i with pool-side flows
                 (base_i, usd_i), markout_h = base_i x mark(t_i + h) + usd_i. Loss_h = -sum(markout_h).
                 mark = the pool's own mid price h seconds later (h = 0: the price right after the swap, which is the
                 loss against a delta-matched rebalancing portfolio at swap resolution; larger h lets noise revert).
                 With --ref, also the external reference price at the swap's own time.
  reference $1   the same two numbers for $1 of liquidity kept in range in a +/-W tick band (exact from the price path:
                 per unit of liquidity a swap from sqrt price s0 to s1 trades base (1/s1 - 1/s0), quote (s1 - s0) and
                 pays fee/(1-fee) on the input). The fee/loss ratio does not depend on W; the APRs scale with 1/W.
  forecast       LVR = sigma^2/4 x L x sqrt(P) (per unit time; = sigma^2/8 of value for full range) and the fee-adjusted
                 arbitrage loss LVR x P_trade, P_trade = 1/(1 + gamma/(sigma sqrt(dt/2))) (Milionis-Moallemi-Roughgarden 2023).
                 It is shown beside the measurement, never fitted to it.

Cells are maximal runs of one session (a regular session, a pre-market, an overnight, a weekend ...); each gets its own
realised volatility (5-minute sampling of the pool price) and is assigned to a volatility quartile of its venue.

Usage: q1_fee_vs_lvr.py VENUE [--from ISO] [--to ISO] [--width 2400] [--ref external/nvda_1m.csv.gz]
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
import sys

import numpy as np

from lib_cl import (SESSIONS, VENUES, YEAR, Series, dump, iso, load_bars, load_swaps, markout, p_trade, pool_price_at, realised_vol,
                    session_codes)

HORIZONS = (0, 60, 300, 3600, 4 * 3600, 24 * 3600)
MAIN_H = (60, 3600)


def parse_ts(s: str | None) -> float | None:
    return None if s is None else dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc).timestamp()


def cells_of(ts0: float, ts1: float, equity: bool) -> list[tuple[float, float, int]]:
    """(start, end, session code) for each maximal run of one session on a 1-minute grid."""
    grid = np.arange(math.floor(ts0 / 60) * 60, ts1 + 60, 60.0)
    codes = session_codes(grid, equity)
    cut = np.flatnonzero(np.diff(codes)) + 1
    starts = np.concatenate([[0], cut])
    ends = np.concatenate([cut, [len(grid) - 1]])
    return [(float(grid[a]), float(grid[b]), int(codes[a])) for a, b in zip(starts, ends) if grid[b] > grid[a]]


def analyse(key: str, t_from: float | None, t_to: float | None, width: int, ref_rel: str | None, protocol_share: float | None = None) -> dict:
    v = VENUES[key]
    s = load_swaps(key, t_from, t_to)
    if protocol_share is not None:      # one protocol share for the whole window (e.g. Ramses at today's 50%)
        s.proto = np.full(len(s), protocol_share)
    n = len(s)
    t0, t1 = float(s.ts[0]), float(s.ts[-1])
    usable = s.ts <= t1 - max(HORIZONS)            # every horizon observed for every scored swap
    lp_fee = s.lp_fee_usd
    fee = s.fee_usd
    b_net, u_net = s.net_flows()
    marks = {h: (s.p if h == 0 else pool_price_at(s, s.ts + h)) for h in HORIZONS}
    mk = {h: b_net * marks[h] + u_net for h in HORIZONS}
    ref = load_bars(ref_rel) if ref_rel else None
    if ref is not None:
        rp = ref.at(s.ts)
        fresh = ref.age(s.ts) <= 180.0             # a bar closed within three minutes: the primary market is trading
        mk_ref = np.where(fresh, b_net * rp + u_net, np.nan)
        gap_bps = np.where(fresh, (s.p / rp - 1) * 1e4, np.nan)

    # --- reference $1 position, +/-width ticks, always in range (exact per unit of liquidity from the price path)
    s0, s1 = np.sqrt(s.p_prev), np.sqrt(s.p)
    r = 1.0001 ** width
    l_usd = 1.0 / (2.0 * s0 * (1.0 - r ** -0.5))    # liquidity of $1 centred at the pre-swap price
    f = s.fee_ppm / 1e6
    up = s1 > s0
    unit_fee = np.where(up, (s1 - s0), (1 / s1 - 1 / s0) * s.p_prev) * f / (1 - f) * (1 - s.proto)   # USD per unit liquidity
    unit_base, unit_usd = (1 / s1 - 1 / s0), (s1 - s0)
    ref_fee = l_usd * unit_fee
    ref_mk = {h: l_usd * (unit_base * marks[h] + unit_usd) for h in HORIZONS}

    cells = cells_of(t0, t1, v.equity)
    rows = []
    ci = np.searchsorted(s.ts, [c[0] for c in cells] + [cells[-1][1] + 1])
    for k, (a, b, code) in enumerate(cells):
        i0, i1 = ci[k], ci[k + 1]
        dur = b - a
        sl = slice(i0, i1)
        m = usable[sl]
        sig = realised_vol(s.ts, s.p, a, b) if dur >= 1800 and i1 > 0 else float("nan")
        # time-weighted L*sqrt(P) over the cell from the swap stream (state in force between swaps)
        j0 = max(i0 - 1, 0)
        tt = np.concatenate([[a], s.ts[i0:i1], [b]])
        lsp = np.concatenate([[s.L[j0] * math.sqrt(s.p[j0])], s.L[i0:i1] * np.sqrt(s.p[i0:i1])])
        lsqrtp_dt = float(np.sum(lsp * np.diff(tt)))
        vol_usd = float(s.notional[sl].sum())
        fee_w = float((s.fee_ppm[sl] * s.notional[sl]).sum() / vol_usd) if vol_usd > 0 else float(s.fee_ppm[j0])
        row = {"start": a, "end": b, "session": SESSIONS[code], "seconds": dur, "swaps": int(i1 - i0), "sigma": sig,
               "volume_usd": vol_usd, "fee_ppm_vw": fee_w, "fee_usd": float(fee[sl].sum()), "lp_fee_usd": float(lp_fee[sl].sum()),
               "lp_fee_usd_scored": float(lp_fee[sl][m].sum()), "L_sqrtP_dt": lsqrtp_dt,
               "ref_fee": float(ref_fee[sl].sum()), "ref_fee_scored": float(ref_fee[sl][m].sum()),
               "proto": float(np.mean(s.proto[sl])) if i1 > i0 else float(s.proto[j0])}
        for h in HORIZONS:
            row[f"loss_{h}"] = float(-mk[h][sl][m].sum())
            row[f"ref_loss_{h}"] = float(-ref_mk[h][sl][m].sum())
        if ref is not None:
            ok = ~np.isnan(mk_ref[sl])
            row["loss_ext"] = float(-np.nansum(mk_ref[sl]))
            row["lp_fee_usd_ext_scored"] = float(lp_fee[sl][ok].sum())
            row["ext_scored_swaps"] = int(ok.sum())
        # forecast for the cell: sigma^2/4 x integral(L sqrtP dt), and the fee-adjusted version
        if sig == sig and sig > 0:
            lvr = sig ** 2 / 4.0 * lsqrtp_dt / YEAR
            pt = p_trade(sig, fee_w / 1e6, v.block_time_s)
            row.update({"lvr_naive_usd": lvr, "p_trade": pt, "arb_forecast_usd": lvr * pt,
                        "ref_lvr_naive": sig ** 2 / (8.0 * (1 - r ** -0.5)) * dur / YEAR})
        rows.append(row)

    # volatility quartiles over cells that have a volatility (time-weighted cut points)
    sig_cells = [x for x in rows if x["sigma"] == x["sigma"]]
    order = sorted(sig_cells, key=lambda x: x["sigma"])
    tot = sum(x["seconds"] for x in order)
    acc, cuts = 0.0, []
    for x in order:
        acc += x["seconds"]
        while len(cuts) < 3 and acc >= tot * (len(cuts) + 1) / 4:
            cuts.append(x["sigma"])
    for x in rows:
        x["vol_bucket"] = None if x["sigma"] != x["sigma"] else "Q" + str(1 + sum(x["sigma"] > c for c in cuts))

    def agg(sel) -> dict:
        xs = [x for x in rows if sel(x)]
        if not xs:
            return {}
        sec = sum(x["seconds"] for x in xs)
        o = {"cells": len(xs), "days": sec / 86400, "swaps": sum(x["swaps"] for x in xs), "volume_usd": sum(x["volume_usd"] for x in xs),
             "fee_usd": sum(x["fee_usd"] for x in xs), "lp_fee_usd": sum(x["lp_fee_usd"] for x in xs),
             "lp_fee_usd_scored": sum(x["lp_fee_usd_scored"] for x in xs)}
        o["volume_usd_per_day"] = o["volume_usd"] / o["days"]
        o["lp_fee_bps_of_volume"] = 1e4 * o["lp_fee_usd"] / o["volume_usd"] if o["volume_usd"] else None
        sw = [x for x in xs if x["sigma"] == x["sigma"]]
        o["sigma_time_weighted"] = (sum(x["sigma"] ** 2 * x["seconds"] for x in sw) / sum(x["seconds"] for x in sw)) ** 0.5 if sw else None
        for h in HORIZONS:
            loss = sum(x[f"loss_{h}"] for x in xs)
            o[f"loss_{h}_usd"] = loss
            o[f"fee_over_loss_{h}"] = o["lp_fee_usd_scored"] / loss if loss > 0 else None
            o[f"net_{h}_usd"] = o["lp_fee_usd_scored"] - loss
        # reference $1 position: APR on in-range capital, time basis = the cells' own time
        rf = sum(x["ref_fee_scored"] for x in xs)
        o["ref_fee_apr_pct"] = 100 * sum(x["ref_fee"] for x in xs) / sec * YEAR
        for h in HORIZONS:
            rl = sum(x[f"ref_loss_{h}"] for x in xs)
            o[f"ref_loss_{h}_apr_pct"] = 100 * rl / sec * YEAR
            o[f"ref_net_{h}_apr_pct"] = 100 * (rf - rl) / sec * YEAR
        fc = [x for x in xs if "lvr_naive_usd" in x]
        if fc:
            fsec = sum(x["seconds"] for x in fc)
            o["forecast"] = {"lvr_naive_usd": sum(x["lvr_naive_usd"] for x in fc), "arb_fee_adjusted_usd": sum(x["arb_forecast_usd"] for x in fc),
                             "p_trade_time_weighted": sum(x["p_trade"] * x["seconds"] for x in fc) / fsec,
                             "ref_lvr_naive_apr_pct": 100 * sum(x["ref_lvr_naive"] for x in fc) / fsec * YEAR,
                             "ref_arb_fee_adjusted_apr_pct": 100 * sum(x["ref_lvr_naive"] * x["p_trade"] for x in fc) / fsec * YEAR,
                             "seconds_covered": fsec}
        if ref is not None:
            le = sum(x.get("loss_ext", 0.0) for x in xs)
            fe = sum(x.get("lp_fee_usd_ext_scored", 0.0) for x in xs)
            o["external"] = {"loss_usd": le, "lp_fee_usd_on_scored": fe, "fee_over_loss": fe / le if le > 0 else None,
                             "scored_swaps": sum(x.get("ext_scored_swaps", 0) for x in xs)}
        return o

    out = {"venue": key, "label": v.label, "window": [iso(t0), iso(t1)], "days": (t1 - t0) / 86400, "swaps": n, "reference_half_width_ticks": width,
           "dropped_glitch_swaps": getattr(s, "dropped_glitches", None),
           "price_start": float(s.p[0]), "price_end": float(s.p[-1]), "sigma_whole_window_5min": realised_vol(s.ts, s.p, t0, t1),
           "vol_quartile_cuts_annualised": cuts, "block_time_s": v.block_time_s,
           "total": agg(lambda x: True),
           "by_session": {se: agg(lambda x, se=se: x["session"] == se) for se in SESSIONS},
           "by_vol_bucket": {q: agg(lambda x, q=q: x["vol_bucket"] == q) for q in ("Q1", "Q2", "Q3", "Q4")},
           "by_session_and_vol": {se: {q: agg(lambda x, se=se, q=q: x["session"] == se and x["vol_bucket"] == q) for q in ("Q1", "Q2", "Q3", "Q4")}
                                  for se in SESSIONS}}
    # per UTC day (each swap in its own day)
    day_idx = (s.ts // 86400).astype(np.int64)
    d0 = int(day_idx[0])
    nd = int(day_idx[-1]) - d0 + 1
    um = usable.astype(float)

    def per_day(x):
        return np.bincount(day_idx - d0, weights=x, minlength=nd)
    cols = {"lp_fee_usd": per_day(lp_fee), "lp_fee_usd_scored": per_day(lp_fee * um), "fee_usd_scored": per_day(fee * um), "loss_0": per_day(-mk[0] * um),
            "loss_60": per_day(-mk[60] * um), "loss_3600": per_day(-mk[3600] * um), "volume_usd": per_day(s.notional), "ref_fee": per_day(ref_fee * um),
            "ref_loss_60": per_day(-ref_mk[60] * um), "ref_loss_3600": per_day(-ref_mk[3600] * um)}
    days = {}
    for k in range(nd):
        a = (d0 + k) * 86400.0
        e = {c: float(v[k]) for c, v in cols.items()}
        e["sigma"] = realised_vol(s.ts, s.p, max(a, t0), min(a + 86400, t1))
        days[dt.datetime.fromtimestamp(a, tz=dt.timezone.utc).strftime("%Y-%m-%d")] = e
    out["by_day"] = days
    # rolling 30-day windows of the pool-level net (fee - loss) at the two main horizons, stepping one day
    dkeys = sorted(days)
    roll = []
    for i in range(0, len(dkeys) - 29):
        w = [days[k_] for k_ in dkeys[i:i + 30]]
        fee30 = sum(x["lp_fee_usd_scored"] for x in w)
        roll.append({"start": dkeys[i], "fee_over_loss_60": fee30 / max(sum(x["loss_60"] for x in w), 1e-12),
                     "fee_over_loss_3600": fee30 / max(sum(x["loss_3600"] for x in w), 1e-12),
                     "ref_net_60_pct": 100 * sum(x["ref_fee"] - x["ref_loss_60"] for x in w),
                     "ref_net_3600_pct": 100 * sum(x["ref_fee"] - x["ref_loss_3600"] for x in w)})
    out["rolling_30d"] = roll
    if ref is not None:
        g = gap_bps[~np.isnan(gap_bps)]
        out["pool_vs_external_gap_bps"] = {"swaps": int(len(g)), "median_abs": float(np.median(np.abs(g))), "p95_abs": float(np.percentile(np.abs(g), 95)),
                                           "mean": float(np.mean(g))} if len(g) else None
    out["cells"] = rows
    return out


def fmt(x, nd=1):
    return "   n/a" if x is None or x != x else f"{x:,.{nd}f}"


def show(o: dict):
    print(f"\n=== {o['label']}  {o['window'][0]} .. {o['window'][1]}  ({o['days']:.1f} d, {o['swaps']:,} swaps, sigma {100 * o['sigma_whole_window_5min']:.0f}%)")
    print(f"{'bucket':<18}{'days':>6}{'vol $/d':>12}{'LPfee bp':>9}{'LP fee $':>11}{'loss0 $':>11}{'loss1m $':>11}{'loss1h $':>11}{'fee/L1m':>8}{'fee/L1h':>8}"
          f"{'refFee%':>8}{'refL1h%':>8}{'fcLVR%':>8}{'Ptrade':>8}")
    def line(name, a):
        if not a:
            return
        fc = a.get("forecast", {})
        print(f"{name:<18}{a['days']:>6.1f}{a['volume_usd_per_day']:>12,.0f}{fmt(a['lp_fee_bps_of_volume'], 2):>9}{a['lp_fee_usd_scored']:>11,.0f}"
              f"{a['loss_0_usd']:>11,.0f}{a['loss_60_usd']:>11,.0f}{a['loss_3600_usd']:>11,.0f}{fmt(a['fee_over_loss_60'], 2):>8}{fmt(a['fee_over_loss_3600'], 2):>8}"
              f"{a['ref_fee_apr_pct']:>8.1f}{a['ref_loss_3600_apr_pct']:>8.1f}{fmt(fc.get('ref_lvr_naive_apr_pct')):>8}{fmt(fc.get('p_trade_time_weighted'), 4):>8}")
    line("TOTAL", o["total"])
    for se, a in o["by_session"].items():
        line(se, a)
    for q, a in o["by_vol_bucket"].items():
        line("vol " + q, a)
    if "external" in o["total"]:
        print("external-reference loss:", {k: o["total"]["external"] for k in ("total",)}, "gap", o.get("pool_vs_external_gap_bps"))
        for se, a in o["by_session"].items():
            if a and "external" in a:
                print("  ", se, a["external"])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("venue", choices=list(VENUES))
    ap.add_argument("--from", dest="frm")
    ap.add_argument("--to")
    ap.add_argument("--width", type=int, default=2400)
    ap.add_argument("--ref")
    ap.add_argument("--tag", default="")
    ap.add_argument("--protocol-share", type=float, default=None)
    a = ap.parse_args()
    res = analyse(a.venue, parse_ts(a.frm), parse_ts(a.to), a.width, a.ref, a.protocol_share)
    res["protocol_share_override"] = a.protocol_share
    show(res)
    p = dump(res, f"q1_{a.venue}{a.tag}.json")
    print("wrote", p)
