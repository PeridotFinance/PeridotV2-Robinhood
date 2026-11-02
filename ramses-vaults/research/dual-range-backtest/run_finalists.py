"""Robustness runs for a short list of configurations: sub-periods, capital sizes (dilution), cadence and gas sensitivity.

  python3 run_finalists.py PAIR name1,name2,...      names as in the grid (dual_c<core>_w<wide>_k<weight>, single_pm1, ...)
"""
from __future__ import annotations
import datetime as _dt, json, sys
from multiprocessing import Pool
from bt_lib import *
import run_grid as G

def parse(name):
    if name == "single_pm1": return dict(core_half=100, wide_half=0, core_weight=1.0)
    if name == "single_pm5": return dict(core_half=500, wide_half=0, core_weight=1.0)
    if name == "full_range": return dict(full_range=True, core_half=0, wide_half=0, core_weight=1.0)
    _, c, w, k = name.split("_")
    return dict(core_half=int(c[1:]), wide_half=int(w[1:]), core_weight=int(k[1:]) / 100)

_S = {}
def _init(pair):
    ds, meta, _ = load_dataset(pair)
    _S.update(ds=ds)

def _run(job):
    import bisect
    name, win, over = job
    ds = _S["ds"]
    lo, hi = win
    i0 = bisect.bisect_left(ds.ts, lo) if lo else 0
    i1 = bisect.bisect_left(ds.ts, hi) if hi else len(ds.ts)
    base = dict(gas_usd=G.GAS, protocol_share=0.5, ram_usd=0.0, capital_usd=10_000.0)
    base.update(parse(name)); base.update(over)
    cfg = E.Config(name=name, **base)
    sim = E.Sim(ds, cfg, session_of, start_index=i0, end_index=i1).run()
    r = sim.result()
    r["job"] = {"name": name, "window": [lo, hi], "over": over}
    return r

def windows(pair):
    ds, _, _ = load_dataset(pair)
    # start where the oracle exists
    t0 = max(ds.ts[0], ds.oracle_t[0] + 1); t1 = ds.ts[-1] + 1
    third = (t1 - t0) / 3
    last28 = _dt.datetime(2026, 10, 5, tzinfo=_dt.timezone.utc).timestamp()
    return {"full": (t0, None), "t1": (t0, t0 + third), "t2": (t0 + third, t0 + 2 * third), "t3": (t0 + 2 * third, None), "last28": (last28, None)}

def main():
    pair = sys.argv[1]; names = sys.argv[2].split(",")
    W = windows(pair)
    jobs = []
    for n in names:
        for mode in (False, True):
            for wn, w in W.items():
                jobs.append((n, w, dict(rebalance=mode, tag=f"{wn}|{'rebal' if mode else 'norebal'}")))
    # dilution, cadence, gas on the full window with rebalance
    for n in names:
        for cap in (1_000.0, 50_000.0, 250_000.0):
            jobs.append((n, W["full"], dict(rebalance=True, capital_usd=cap, tag=f"cap{int(cap)}|rebal")))
        for cd, mpd in ((3600.0, 24), (900.0, 4), (900.0, 24)):
            jobs.append((n, W["full"], dict(rebalance=True, cooldown=cd, max_per_day=mpd, tag=f"cd{int(cd)}_max{mpd}|rebal")))
        for gas in (1.5, 5.0):
            jobs.append((n, W["full"], dict(rebalance=True, gas_usd=gas, tag=f"gas{gas}|rebal")))
        jobs.append((n, W["full"], dict(rebalance=True, protocol_share=None, tag="histshare|rebal")))
        jobs.append((n, W["full"], dict(rebalance=True, max_stale=24 * 3600.0, tag="stale24h|rebal")))
        jobs.append((n, W["full"], dict(rebalance=True, block_sessions=(), max_stale=1e12, tag="oracle_always_ok|rebal")))
    out = open(RESULTS / f"fin_{pair}.jsonl", "w")
    # `tag` is not a Config field: strip before constructing
    def strip(job):
        n, w, over = job
        over = dict(over); tag = over.pop("tag")
        return (n, w, over), tag
    prepared = [strip(j) for j in jobs]
    with Pool(5, initializer=_init, initargs=(pair,)) as pool:
        for (res, tag) in zip(pool.imap(_run, [p[0] for p in prepared]), [p[1] for p in prepared]):
            res["tag"] = tag
            out.write(json.dumps(res) + "\n"); out.flush()
            print(res["name"], tag, "pnl %.1f vsHold %.1f" % (res["pnl"], res["pnl_vs_hold"]), flush=True)

if __name__ == "__main__":
    main()
