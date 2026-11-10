"""Q2. Range geometry: half-width x recentre trigger, under the contract's rules, on real swaps.

One oracle-centred range, recentred when the oracle has moved ``trigger`` ticks from the centre or left the range; at most
``max_per_day`` recentres per rolling 24 h with a 1 h cooldown; no swap at a recentre (matched value only, the surplus
token stays idle); a recentre that would lose more than 10 bp of pair equity at the oracle is refused; no recentre while
the feed is older than 12 h or the primary market is shut; the keeper looks every 300 s. Fees are the LP share of the fee
in force on every swap, diluted by our own liquidity, less the 20% reserve cut. Rewards are zero here (Q4/Q5).

Cells the contract rejects (trigger > half-width / 2, or not a multiple of the tick spacing) are not run.

Outputs out/q2_<venue>.json: for every cell the full-window result and every rolling 30-day window (step 7 days).
Usage: q2_geometry.py VENUE [--capital 1000] [--max-per-day 4] [--procs 8]
"""
from __future__ import annotations

import argparse
import itertools
import multiprocessing as mp
import sys
import time

import engine_ext as X
from lib_cl import VENUES, dump, iso

HALVES = (600, 900, 1200, 1800, 2400, 3600, 4800)
TRIGGERS = (100, 200, 300, 450, 600, 900, 1200)
SPACING = {k: v.spacing for k, v in VENUES.items()}
WINDOW_S, STEP_S = 30 * 86400.0, 7 * 86400.0
KEEP = ("days", "excess_mark_pct", "excess_pct", "end_mark", "hold_ret_pct", "ret_pct", "fees_usd", "reserve_usd", "lvr_usd", "bench_pnl",
        "fee_over_lvr", "fees_minus_lvr_minus_gas", "gas_usd", "recenters_core", "recenters_per_day", "deployed_frac_avg", "in_range_core",
        "blocked", "max_drawdown_pct", "start_ts", "end_ts", "plan_switches", "reward_tokens", "reward_usd_if_sold", "reward_pct_if_sold")

_DS = None
_ARGS = None
_RW = (None, 0.0)


def feasible(half: int, trig: int, spacing: int) -> bool:
    return 600 <= half <= 4800 and half % spacing == 0 and spacing <= trig <= half // 2


def _init(venue, capital, max_per_day, extra):
    global _DS, _ARGS, _RW
    from rewards import load_rewards
    rate, price, cfg = load_rewards(venue)
    _RW = (price, (cfg or {}).get("sale_cost", 0.0))
    _DS = X.load_dataset(venue, ram_rate=rate)
    _ARGS = (venue, capital, max_per_day, extra)


def _run(job):
    half, trig, t_from, t_to, label = job
    venue, capital, max_per_day, extra = _ARGS
    v = VENUES[venue]
    cfg = X.single_range_config(half, trig, capital, SPACING[venue], max_per_day=max_per_day,
                                block_sessions=("weekend",) if v.equity else (), **extra)
    try:
        r = X.run_window(_DS, cfg, t_from, t_to, equity_sessions=v.equity, reward_price=_RW[0], reward_sale_cost=_RW[1])
        out = {k: r.get(k) for k in KEEP}
    except Exception as e:  # a window with no fresh oracle, etc.: recorded, not hidden
        out = {"error": repr(e)}
    out.update({"half": half, "trigger": trig, "window": label})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("venue", choices=list(VENUES))
    ap.add_argument("--capital", type=float, default=1000.0)
    ap.add_argument("--max-per-day", type=int, default=4)
    ap.add_argument("--procs", type=int, default=8)
    ap.add_argument("--from-ts", type=float)
    ap.add_argument("--tag", default="")
    ap.add_argument("--no-reserve", action="store_true")
    a = ap.parse_args()
    extra = {"reserve_bps": 0} if a.no_reserve else {}
    ds = X.load_dataset(a.venue)
    t0 = max(ds.ts[0], a.from_ts or 0.0, ds.oracle_t[0])
    t1 = ds.ts[-1]
    windows = [(t0, t1, "full")]
    s = t0
    while s + WINDOW_S <= t1 + 1:
        windows.append((s, s + WINDOW_S, "30d@" + iso(s)[:10]))
        s += STEP_S
    cells = [(h, t) for h, t in itertools.product(HALVES, TRIGGERS) if feasible(h, t, SPACING[a.venue])]
    jobs = [(h, t, w0, w1, lab) for (h, t) in cells for (w0, w1, lab) in windows]
    tic = time.time()
    with mp.Pool(a.procs, initializer=_init, initargs=(a.venue, a.capital, a.max_per_day, extra)) as pool:
        rows = pool.map(_run, jobs, chunksize=4)
    res = {"venue": a.venue, "label": VENUES[a.venue].label, "capital_usd": a.capital, "max_per_day": a.max_per_day, "spacing": SPACING[a.venue],
           "data_window": [iso(t0), iso(t1)], "days": (t1 - t0) / 86400, "windows": [w[2] for w in windows],
           "infeasible_cells": [(h, t) for h, t in itertools.product(HALVES, TRIGGERS) if not feasible(h, t, SPACING[a.venue])],
           "reserve_cut": not a.no_reserve, "rows": rows, "seconds": time.time() - tic}
    p = dump(res, f"q2_{a.venue}_{int(a.capital)}_mpd{a.max_per_day}{a.tag}.json")
    # console summary: full window, and worst / best / share-positive over the rolling windows
    print(f"{res['label']}  {res['data_window']}  capital ${a.capital:,.0f}  max/day {a.max_per_day}  ({len(jobs)} runs, {res['seconds']:.0f} s)")
    print(f"{'half':>5}{'trig':>6}{'full %':>8}{'fee/LVR':>8}{'fees $':>8}{'LVR $':>8}{'rec':>5}{'depl':>6}{'30d min':>9}{'30d med':>9}{'30d max':>9}{'pos/n':>7}{'rew %':>8}{'+rew min':>9}{'pos/n':>7}")
    for h, t in cells:
        full = next(r for r in rows if r["half"] == h and r["trigger"] == t and r["window"] == "full")
        w = sorted(r["excess_mark_pct"] for r in rows if r["half"] == h and r["trigger"] == t and r["window"] != "full" and "error" not in r)
        if "error" in full:
            print(h, t, full["error"]); continue
        med = w[len(w) // 2] if w else float("nan")
        print(f"{h:>5}{t:>6}{full['excess_mark_pct']:>8.2f}{(full['fee_over_lvr'] or float('nan')):>8.2f}{full['fees_usd']:>8.2f}{full['lvr_usd']:>8.2f}"
              f"{full['recenters_core']:>5}{full['deployed_frac_avg']:>6.2f}{(w[0] if w else float('nan')):>9.2f}{med:>9.2f}{(w[-1] if w else float('nan')):>9.2f}"
              f"{sum(x > 0 for x in w):>4}/{len(w)}", end="")
        if full.get("reward_pct_if_sold") is not None:
            wr = sorted(r["excess_mark_pct"] + r["reward_pct_if_sold"] for r in rows if r["half"] == h and r["trigger"] == t and r["window"] != "full"
                        and "error" not in r and r.get("reward_pct_if_sold") is not None)
            print(f"{full['reward_pct_if_sold']:>8.2f}{(wr[0] if wr else float('nan')):>9.2f}{sum(x > 0 for x in wr):>4}/{len(wr)}")
        else:
            print()
    print("wrote", p)


if __name__ == "__main__":
    main()
