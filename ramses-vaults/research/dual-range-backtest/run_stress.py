"""Volatility stress: replay the real swap sequence (timing, volumes, liquidity) with the price path amplified by k around its start.

s' = s0 * (s/s0)^k  and  oracle p' = p0 * (p/p0)^k  =>  every move is k times larger in log terms, so realised volatility is k times
the sample's (SPY sample about 10 % annualised). Volumes are NOT increased, which makes this a pessimistic stress for fee income
(real stress days also bring more volume). It is a sensitivity, not a forecast.

  python3 run_stress.py PAIR name1,name2,...
"""
from __future__ import annotations
import copy, json, math, sys
from multiprocessing import Pool
from bt_lib import *
import run_finalists as F

KS = (1.0, 2.0, 3.0)
_D = {}

def _init(pair):
    ds, meta, _ = load_dataset(pair)
    _D["ds"] = ds

def _amplified(ds, k):
    if k == 1.0:
        return ds
    d = copy.copy(ds)
    s0 = ds.s_after[0]
    # the pool's own price is amplified on the USD scale: sqrt price moves with the same exponent (inverse for token1 stock)
    exp_s = k
    d.s_after = [s0 * math.exp(exp_s * math.log(s / s0)) for s in ds.s_after]
    d.tick_after = [int(math.floor(E.sqrt_tick(s))) for s in d.s_after]
    p0 = ds.market.usd_per_stock(s0)   # same origin for the pool and the oracle
    d.oracle_p = [p0 * math.exp(k * math.log(p / p0)) for p in ds.oracle_p]
    return d

def _run(job):
    name, k, over = job
    ds = _amplified(_D["ds"], k)
    base = dict(gas_usd=0.40, protocol_share=0.5, ram_usd=0.0, capital_usd=10_000.0)
    base.update(F.parse(name)); base.update(over)
    cfg = E.Config(name=name, **base)
    sim = E.Sim(ds, cfg, session_of, start_index=max(0, 0)).run()
    r = sim.result(); r["k"] = k
    return r

def main():
    pair = sys.argv[1]; names = sys.argv[2].split(",")
    jobs = [(n, k, dict(rebalance=True)) for n in names for k in KS]
    out = open(RESULTS / f"stress_{pair}.jsonl", "w")
    with Pool(5, initializer=_init, initargs=(pair,)) as pool:
        for r in pool.imap(_run, jobs):
            out.write(json.dumps(r) + "\n"); out.flush()
            print(r["name"], "k=%.0f" % r["k"], "vsHold %.1f (%.2f%%) fees %.1f lvr %.1f rec %d/%d worstWk %.0f" % (r["pnl_vs_hold"], r["excess_pct"], r["fees_usd"], r["lvr_usd"], r["recenters_core"], r["recenters_wide"], r.get("worst_week_excess_vs_hold_usd", 0)), flush=True)

if __name__ == "__main__":
    main()
