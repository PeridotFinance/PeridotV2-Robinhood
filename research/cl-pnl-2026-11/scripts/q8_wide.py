"""Q8. Full range and plus-or-minus 50%: return against holding per rolling 30-day window, fee-to-loss ratio, and the
loss-protection reserve that would make a $1,000 depositor whole in 95% of windows.

Same engine and ledger as Q2, with two static positions that never recentre inside the data:
  full   the adapter's full range (min to max usable tick): all of a 50/50 deposit is deployed
  pm50   a band from 0.5 x to 1.5 x the oracle price at deployment (not allowed by the vault's range library today, whose
         widest half-width is 4,800 ticks; modelled as asked). Nothing is swapped at deployment, so with a 50/50 deposit
         the band takes all the quote and about 63% of the stock; the rest of the stock stays idle.
Fees are the LP share of the fee in force (Ramses pools: 50% for the whole window, today's share), diluted by our own
liquidity, less the 20% reserve cut. Windows: every 30-day window starting one day apart.

Reserve. For each window the depositor's shortfall is max(0, holding - position) as a share of the deposit. The reserve
"makes whole in 95% of windows" if it can pay every shortfall up to the 95th-percentile one. Reported:
  standing size   the 95th-percentile shortfall of one 30-day window, % of deposits
  cost per year   the average payout per window when each shortfall is paid up to that size, x 365/30, % of deposits a year
  funded by       what the vault's own 20% cut of fees brought in over the same windows, on the same basis

Usage: q8_wide.py VENUE [--from-ts T] [--procs 4] [--protocol-share 0.5]
"""
from __future__ import annotations

import argparse
import multiprocessing as mp
import time

import numpy as np

import engine_ext as X
from lib_cl import VENUES, dump, iso

WINDOW_S, STEP_S = 30 * 86400.0, 86400.0
GEOMS = {"full": dict(full_range=True), "pm50": dict(price_range=(0.5, 1.5))}
KEEP = ("days", "excess_mark_pct", "end_mark", "hold_ret_pct", "fees_usd", "reserve_usd", "lvr_usd", "bench_pnl", "fee_over_lvr", "recenters_core",
        "deployed_frac_avg", "in_range_core", "start_ts", "end_ts")
_DS = None
_ARGS = None


def _init(venue, capital, proto, reserve_bps=2000):
    global _DS, _ARGS
    _DS = X.load_dataset(venue)
    _ARGS = (venue, capital, proto, reserve_bps)


def _run(job):
    geom, t_from, t_to, label = job
    venue, capital, proto, reserve_bps = _ARGS
    v = VENUES[venue]
    g = GEOMS[geom]
    cfg = X.single_range_config(4800, 10 ** 9, capital, v.spacing, block_sessions=("weekend",) if v.equity else (), sample_s=3600.0,
                                full_range=bool(g.get("full_range")), protocol_share=proto, reserve_bps=reserve_bps)
    try:
        r = X.run_window(_DS, cfg, t_from, t_to, equity_sessions=v.equity, price_range=g.get("price_range"))
        out = {k: r.get(k) for k in KEEP}
    except Exception as e:
        out = {"error": repr(e)}
    out.update({"geometry": geom, "window": label})
    return out


def reserve(rows: list[dict], capital: float) -> dict:
    x = np.array([r["excess_mark_pct"] for r in rows])
    short = np.maximum(0.0, -x)
    q95 = float(np.percentile(short, 95))
    per_year = 365.0 / 30.0
    income = np.array([100.0 * r["reserve_usd"] / capital for r in rows])
    return {"windows": len(rows), "return_min": float(x.min()), "return_p05": float(np.percentile(x, 5)), "return_median": float(np.median(x)),
            "return_max": float(x.max()), "share_at_or_above_holding": float(np.mean(x >= 0)),
            "shortfall_p95_pct": q95, "shortfall_max_pct": float(short.max()),
            "cost_pct_per_year_capped_at_p95": float(np.mean(np.minimum(short, q95)) * per_year),
            "cost_pct_per_year_uncapped": float(np.mean(short) * per_year),
            "reserve_income_pct_per_year_from_20pct_fee_cut": float(np.mean(income) * per_year),   # key name kept; the cut is reserve_bps
            "holder_fee_income_pct_per_year": float(np.mean([100.0 * r["fees_usd"] / capital for r in rows]) * per_year)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("venue", choices=list(VENUES))
    ap.add_argument("--capital", type=float, default=1000.0)
    ap.add_argument("--from-ts", type=float)
    ap.add_argument("--procs", type=int, default=4)
    ap.add_argument("--protocol-share", type=float, default=None, help="apply one protocol share to the whole window (Ramses today: 0.5)")
    ap.add_argument("--step-days", type=float, default=1.0)
    ap.add_argument("--tag", default="")
    ap.add_argument("--reserve-bps", type=int, default=2000, help="the vault's cut of collected fees that goes to the reserve (live: 2000; contract cap 5000)")
    a = ap.parse_args()
    v = VENUES[a.venue]
    ds = X.load_dataset(a.venue)
    t0 = max(ds.ts[0], a.from_ts or 0.0, ds.oracle_t[0])
    t1 = ds.ts[-1]
    del ds
    windows = [(t0, t1, "full")]
    s = t0
    while s + WINDOW_S <= t1 + 1:
        windows.append((s, s + WINDOW_S, "30d@" + iso(s)[:10]))
        s += a.step_days * STEP_S
    jobs = [(g, w0, w1, lab) for g in GEOMS for (w0, w1, lab) in windows]
    tic = time.time()
    with mp.Pool(a.procs, initializer=_init, initargs=(a.venue, a.capital, a.protocol_share, a.reserve_bps)) as pool:
        rows = pool.map(_run, jobs, chunksize=4)
    res = {"venue": a.venue, "label": v.label, "capital_usd": a.capital, "protocol_share_override": a.protocol_share, "reserve_bps": a.reserve_bps, "data_window": [iso(t0), iso(t1)],
           "days": (t1 - t0) / 86400, "step_days": a.step_days, "rows": rows, "summary": {}, "seconds": time.time() - tic}
    print(f"{v.label}  {res['data_window']}  ${a.capital:,.0f}" + (f"  protocol share {a.protocol_share}" if a.protocol_share is not None else ""))
    print(f"{'geometry':<9}{'full %':>8}{'fee/LVR':>8}{'fees $':>8}{'LVR $':>8}{'depl':>6}{'n':>5}{'min':>8}{'p05':>8}{'median':>8}{'max':>8}{'>=0':>6}{'p95 short':>10}{'cost %/y':>9}"
          f"{'uncapped':>9}{'cut %/y':>8}")
    for g in GEOMS:
        full = next(r for r in rows if r["geometry"] == g and r["window"] == "full")
        wins = [r for r in rows if r["geometry"] == g and r["window"] != "full" and "error" not in r]
        if "error" in full or not wins:
            print(g, full.get("error")); continue
        sm = reserve(wins, a.capital)
        sm["full_window"] = full
        res["summary"][g] = sm
        print(f"{g:<9}{full['excess_mark_pct']:>8.2f}{(full['fee_over_lvr'] or float('nan')):>8.2f}{full['fees_usd']:>8.2f}{full['lvr_usd']:>8.2f}{full['deployed_frac_avg']:>6.2f}"
              f"{sm['windows']:>5}{sm['return_min']:>8.2f}{sm['return_p05']:>8.2f}{sm['return_median']:>8.2f}{sm['return_max']:>8.2f}{100 * sm['share_at_or_above_holding']:>5.0f}%"
              f"{sm['shortfall_p95_pct']:>10.2f}{sm['cost_pct_per_year_capped_at_p95']:>9.2f}{sm['cost_pct_per_year_uncapped']:>9.2f}"
              f"{sm['reserve_income_pct_per_year_from_20pct_fee_cut']:>8.2f}")
    tag = "" if a.protocol_share is None else f"_share{int(round(100 * (1 - a.protocol_share)))}"
    p = dump(res, f"q8_{a.venue}{tag}{a.tag}.json")
    print("wrote", p)


if __name__ == "__main__":
    main()
