"""Q6. Session-aware width: does changing the range by time of week beat a fixed range?

Same engine and contract rules as Q2 (oracle-centred, no swap at a recentre, 1 h cooldown, max per day, 10 bp loss bound,
no recentre while the primary market is shut). A plan switch is an ordinary recentre: it costs gas, obeys every gate and,
because nothing is swapped, redeploys only matched value.

Plans tested against the two fixed ranges they are built from:
  weekend-wide    narrow in the week, wide from 30 min before Friday's close to 30 min after Monday's open
  weekend-narrow  the reverse (wide in the week, narrow over the weekend), because Q1 finds the weekend is where
                  fees exceed arbitrage loss
  hours-wide      wide during regular hours, narrow otherwise (regular hours are where arbitrage loss exceeds fees)

Usage: q6_session.py VENUE [--capital 1000] [--max-per-day 4]
"""
from __future__ import annotations

import argparse
import multiprocessing as mp
import time

import engine_ext as X
from lib_cl import VENUES, dump, iso
from q2_geometry import KEEP, SPACING, STEP_S, WINDOW_S

PAIRS = (((600, 300), (2400, 600)), ((1200, 300), (2400, 600)), ((1200, 300), (4800, 1200)), ((2400, 600), (4800, 1200)))

_DS = None
_ARGS = None
_RW = (None, 0.0)


def _init(venue, capital, max_per_day):
    global _DS, _ARGS, _RW
    from rewards import load_rewards
    rate, price, cfg = load_rewards(venue)
    _RW = (price, (cfg or {}).get("sale_cost", 0.0))
    _DS = X.load_dataset(venue, ram_rate=rate)
    _ARGS = (venue, capital, max_per_day)


def make_plan(kind: str, narrow, wide, equity: bool):
    if kind == "fixed_narrow":
        return None, narrow
    if kind == "fixed_wide":
        return None, wide
    if kind == "weekend_wide":
        return X.weekend_plan(narrow, wide), narrow
    if kind == "weekend_narrow":
        return X.weekend_plan(wide, narrow), wide
    if kind == "hours_wide":
        return X.session_plan({"regular": wide, "extended": narrow, "overnight": narrow, "weekend": narrow}, equity), narrow
    raise ValueError(kind)


def _run(job):
    kind, narrow, wide, t_from, t_to, label = job
    venue, capital, max_per_day = _ARGS
    v = VENUES[venue]
    plan, base = make_plan(kind, narrow, wide, v.equity)
    cfg = X.single_range_config(base[0], base[1], capital, SPACING[venue], max_per_day=max_per_day,
                                block_sessions=("weekend",) if v.equity else ())
    try:
        r = X.run_window(_DS, cfg, t_from, t_to, equity_sessions=v.equity, plan=plan, reward_price=_RW[0], reward_sale_cost=_RW[1])
        out = {k: r.get(k) for k in KEEP}
    except Exception as e:
        out = {"error": repr(e)}
    out.update({"plan": kind, "narrow": narrow, "wide": wide, "window": label})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("venue", choices=list(VENUES))
    ap.add_argument("--capital", type=float, default=1000.0)
    ap.add_argument("--max-per-day", type=int, default=4)
    ap.add_argument("--procs", type=int, default=8)
    ap.add_argument("--from-ts", type=float)
    ap.add_argument("--tag", default="")
    a = ap.parse_args()
    ds = X.load_dataset(a.venue)
    t0 = max(ds.ts[0], a.from_ts or 0.0, ds.oracle_t[0])
    t1 = ds.ts[-1]
    windows = [(t0, t1, "full")]
    s = t0
    while s + WINDOW_S <= t1 + 1:
        windows.append((s, s + WINDOW_S, "30d@" + iso(s)[:10]))
        s += STEP_S
    kinds = ("fixed_narrow", "fixed_wide", "weekend_wide", "weekend_narrow", "hours_wide")
    sp = SPACING[a.venue]
    pairs = [(n, w) for n, w in PAIRS if n[0] % sp == 0 and w[0] % sp == 0]
    jobs = [(k, n, w, w0, w1, lab) for (n, w) in pairs for k in kinds for (w0, w1, lab) in windows]
    tic = time.time()
    with mp.Pool(a.procs, initializer=_init, initargs=(a.venue, a.capital, a.max_per_day)) as pool:
        rows = pool.map(_run, jobs, chunksize=4)
    res = {"venue": a.venue, "label": VENUES[a.venue].label, "capital_usd": a.capital, "max_per_day": a.max_per_day,
           "data_window": [iso(t0), iso(t1)], "windows": [w[2] for w in windows], "rows": rows, "seconds": time.time() - tic}
    p = dump(res, f"q6_{a.venue}_{int(a.capital)}{a.tag}.json")
    print(f"{res['label']}  {res['data_window']}  capital ${a.capital:,.0f}  ({len(jobs)} runs, {res['seconds']:.0f} s)")
    print(f"{'narrow':>10}{'wide':>11}  {'plan':<15}{'full %':>8}{'fee/LVR':>8}{'fees $':>8}{'LVR $':>8}{'rec':>5}{'sw':>4}{'depl':>6}{'30d min':>9}{'30d med':>9}{'30d max':>9}{'beats both':>11}")
    for n, w in pairs:
        cell = {}
        for k in kinds:
            full = next(r for r in rows if r["plan"] == k and r["narrow"] == n and r["wide"] == w and r["window"] == "full")
            ws = {r["window"]: r["excess_mark_pct"] for r in rows if r["plan"] == k and r["narrow"] == n and r["wide"] == w
                  and r["window"] != "full" and "error" not in r}
            cell[k] = (full, ws)
        for k in kinds:
            full, ws = cell[k]
            if "error" in full:
                print(n, w, k, full["error"]); continue
            vals = sorted(ws.values())
            beats = ""
            if k not in ("fixed_narrow", "fixed_wide"):
                fn, fw = cell["fixed_narrow"][1], cell["fixed_wide"][1]
                b = sum(1 for lab, x in ws.items() if lab in fn and lab in fw and x > max(fn[lab], fw[lab]))
                beats = f"{b}/{len(ws)}"
            med = vals[len(vals) // 2] if vals else float("nan")
            print(f"{str(n):>10}{str(w):>11}  {k:<15}{full['excess_mark_pct']:>8.2f}{(full['fee_over_lvr'] or float('nan')):>8.2f}{full['fees_usd']:>8.2f}"
                  f"{full['lvr_usd']:>8.2f}{full['recenters_core']:>5}{full.get('plan_switches', 0):>4}{full['deployed_frac_avg']:>6.2f}"
                  f"{(vals[0] if vals else float('nan')):>9.2f}{med:>9.2f}{(vals[-1] if vals else float('nan')):>9.2f}{beats:>11}")
    print("wrote", p)


if __name__ == "__main__":
    main()
