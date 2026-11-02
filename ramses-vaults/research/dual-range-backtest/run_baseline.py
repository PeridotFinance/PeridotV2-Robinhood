"""Contract-feasible baseline: ONE ranged position, 600-tick half-width, no swap at recenter, one-hour cooldown, an explicit
valid trigger, against holding and the separately validated full-range mode. Refuses to run any configuration that
``feasibility.require_feasible`` rejects for the pool's ACTUAL tick spacing.

  python3 run_baseline.py PAIR            PAIR in SPY, GLD, NVDA (writes results/baseline_PAIR.json)

Assumptions are written into the output, not buried here: gas per recenter and the maintenance (checkpoint) cost are the
Uniswap vault keeper's measured costs used as a proxy (PR #13 evidence), RAM is counted at zero, the reserve cut is the
20% fixture value in ramses-vaults/README.md, and the pool keeps 50% of every swap fee (fork-measured).
"""
from __future__ import annotations

import bisect
import datetime as _dt
import hashlib
import json
import platform
import sys
import time
from multiprocessing import Pool

from bt_lib import *  # noqa: F401,F403  (load_dataset, E, RESULTS, session_of)
import feasibility as FZ

CAPITAL = 10_000.0
RECENTER_GAS_USD = 0.0663        # measured per recenter, Uniswap vault keeper (proxy), PR #13 evidence r3
RECENTER_GAS_CONSERVATIVE = 0.40  # the engine's earlier assumption
MAINTENANCE_USD_PER_DAY = 0.5737  # hourly checkpoint cadence the vault requires, same evidence; NOT modelled inside the engine
RESERVE_BPS = 2000                # README fixture value (configuration, not an operator decision)
LAST28_START = _dt.datetime(2026, 10, 5, tzinfo=_dt.timezone.utc).timestamp()


def base_kwargs(pair: str, capital: float = CAPITAL) -> dict:
    return dict(capital_usd=capital, spacing=FZ.POOL_SPACING[pair], wide_half=0, core_weight=1.0, cooldown=3600.0, max_per_day=24,
                loss_bps=10, rebalance=False, protocol_share=0.5, reserve_bps=RESERVE_BPS, ram_usd=0.0, gas_usd=RECENTER_GAS_USD,
                max_ranged_usd=capital, max_stale=12 * 3600.0, block_sessions=("weekend", "holiday"))


# name -> (overrides, description). Every entry is validated against the contract before it runs.
RUNS = {
    "single600_trig300": (dict(core_half=600, trigger_ticks=300), "BASELINE: +/-600 ticks (~6.2%), trigger 300 (the contract maximum for 600), 1 h cooldown, <=24/day"),
    "single600_trig100": (dict(core_half=600, trigger_ticks=100), "sensitivity: trigger 100 (more frequent recentering)"),
    "single1200_trig600": (dict(core_half=1200, trigger_ticks=600), "sensitivity: wider +/-1200 ticks (~12.7%), trigger 600"),
    "single600_trig300_noreserve": (dict(core_half=600, trigger_ticks=300, reserve_bps=0), "sensitivity: reserve cut 0 (shows what the 20% reserve costs holders)"),
    "single600_trig300_gas040": (dict(core_half=600, trigger_ticks=300, gas_usd=RECENTER_GAS_CONSERVATIVE), "sensitivity: engine's conservative $0.40 per recenter"),
    "single600_trig300_cap5k": (dict(core_half=600, trigger_ticks=300, max_ranged_usd=CAPITAL / 2), "sensitivity: maxRangedValueUsd = half of capital (rest stays idle)"),
    "single600_trig300_histshare": (dict(core_half=600, trigger_ticks=300, protocol_share=None), "sensitivity: historical protocol share (5% before 2026-09-30, then 50%)"),
    "full_range": (dict(full_range=True, core_half=0, wide_half=0), "full range (adapter default minUsableTick..maxUsableTick); no recentering"),
}

_S: dict = {}


def _init(pair):
    ds, meta, _ = load_dataset(pair)
    _S.update(ds=ds, pair=pair, meta=meta)


def windows(ds):
    t0 = max(ds.ts[0], ds.oracle_t[0] + 1)
    t1 = ds.ts[-1] + 1
    third = (t1 - t0) / 3
    return {"full": (t0, None), "t1": (t0, t0 + third), "t2": (t0 + third, t0 + 2 * third), "t3": (t0 + 2 * third, None), "last28": (LAST28_START, None)}


def _run(job):
    name, win_name, win, over, capital = job
    ds, pair = _S["ds"], _S["pair"]
    lo, hi = win
    i0 = bisect.bisect_left(ds.ts, lo) if lo else 0
    i1 = bisect.bisect_left(ds.ts, hi) if hi else len(ds.ts)
    kw = base_kwargs(pair, capital)
    kw.update(over)
    if "max_ranged_usd" not in over:
        kw["max_ranged_usd"] = capital
    cfg = E.Config(name=name, **kw)
    feas = FZ.require_feasible(cfg, FZ.POOL_SPACING[pair])
    t = time.time()
    sim = E.Sim(ds, cfg, session_of, start_index=i0, end_index=i1).run()
    r = sim.result()
    days = r["days"]
    maint = MAINTENANCE_USD_PER_DAY * days
    eq0 = sim.equity0
    r.update({
        "maintenance_gas_usd": maint,
        "window_return_pct": r["ret_pct"], "window_hold_return_pct": r["hold_ret_pct"], "window_excess_pct": r["excess_pct"],
        "window_excess_after_maintenance_pct": r["excess_pct"] - maint / eq0 * 100.0,
        "annualised_excess_pct": r["excess_apr_pct"],
        "annualised_excess_after_maintenance_pct": (r["excess_pct"] - maint / eq0 * 100.0) * 365.0 / days,
        "idle_frac_avg": 1.0 - r["deployed_frac_avg"],
        "fees_net_of_reserve_pct_of_capital": r["fees_usd"] / eq0 * 100.0,
        "start_iso": _dt.datetime.fromtimestamp(sim.start_ts, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end_iso": _dt.datetime.fromtimestamp(sim.end_ts, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ram_tokens_valued_at_zero": r["ram_tokens"],
        # excess vs holding = fees credited to holders + inventory/price effect - recenter gas. The inventory effect is NOT fee income:
        # with no swap at recenter the unmatched side stays idle, so the position can drift into one token and win or lose on direction.
        "inventory_effect_usd": r["pnl_vs_hold"] - r["fees_usd"] + r["gas_usd"],
        "final_idle_stock_usd": ((sim.idle1 if not sim.m.stock_is_token0 else sim.idle0) / 1e18) * sim.m.usd_per_stock(sim.s_end),
        "final_idle_usdg": (sim.idle0 / 1e6 if not sim.m.stock_is_token0 else sim.idle1 / 1e6),
    })
    for k in ("twr_pct_series",):
        r.pop(k, None)
    return {"run": name, "window": win_name, "capital": capital, "feasibility": feas.asdict(), "config": {k: (list(v) if isinstance(v, tuple) else v) for k, v in cfg.__dict__.items()},
            "result": r, "seconds": time.time() - t}


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    pair = sys.argv[1]
    if pair not in FZ.POOL_SPACING:
        raise SystemExit("PAIR must be one of " + ", ".join(FZ.POOL_SPACING))
    ds, meta, _ = load_dataset(pair)
    W = windows(ds)
    jobs = []
    for name, (over, _desc) in RUNS.items():
        for wn, w in W.items():
            jobs.append((name, wn, w, over, CAPITAL))
    for cap in (1_000.0, 50_000.0):                      # dilution: our liquidity against the observed volume
        jobs.append(("single600_trig300", "full", W["full"], RUNS["single600_trig300"][0], cap))
        jobs.append(("full_range", "full", W["full"], RUNS["full_range"][0], cap))
    out = []
    with Pool(4, initializer=_init, initargs=(pair,)) as pool:
        for res in pool.imap(_run, jobs):
            out.append(res)
            r = res["result"]
            print("%-30s %-7s cap %-7d ret %6.2f%% hold %6.2f%% excess %6.2f%% (%6.1f%%/yr) fees %6.1f reserve %6.1f gas %5.2f rec %3d blocked %s" % (
                res["run"], res["window"], res["capital"], r["ret_pct"], r["hold_ret_pct"], r["excess_pct"], r["excess_apr_pct"], r["fees_usd"],
                r["reserve_usd"], r["gas_usd"], r["recenters_core"], r["blocked"]), flush=True)
    doc = {
        "schema": "ramses-baseline-1", "pair": pair, "spacing": FZ.POOL_SPACING[pair], "poolAddress": ADDR[pair]["pool"], "stockIsToken0": ADDR[pair]["stock_is_token0"],
        "command": "python3 " + " ".join(sys.argv), "python": platform.python_version(), "generated": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "dataset": {"file": f"results/dataset_{pair}.pkl", "sha256": sha256_file(RESULTS / f"dataset_{pair}.pkl"), "swaps": meta["n_swaps"], "poolHeadBlock": meta["head"],
                    "glitchSwapsDropped": len(meta.get("glitch_swaps_dropped", [])), "timeIndexError": meta.get("time_index_error"),
                    "gridLimitBlock": meta.get("grid_limit_block"), "swapsDroppedAfterGrid": meta.get("swaps_dropped_after_grid")},
        "inputs": {f: sha256_file(RESULTS / f) for f in ("answer_updated.json", "gauge_notify.json")},
        "code": {f: sha256_file(HERE / f) for f in ("engine.py", "feasibility.py", "run_baseline.py", "bt_lib.py")},
        "assumptions": {
            "capitalUsd": CAPITAL, "recenterGasUsd": RECENTER_GAS_USD, "maintenanceUsdPerDay": MAINTENANCE_USD_PER_DAY,
            "gasSource": "Uniswap vault keeper measured costs used as a PROXY (PR #13 evidence r3: $0.0663 per recenter, $0.5737/day hourly checkpoint cadence); not Ramses vault measurements",
            "reserveBps": RESERVE_BPS, "reserveSource": "ramses-vaults/README.md fixture value (20%); configuration, not decided", "ramValuedAtZero": True,
            "protocolShare": "0.5 (fork-measured, fork-final.log) unless a run says histshare",
            "guard": "max_stale 12 h; weekend and holiday sessions block recentering; 150 bp pool-vs-oracle allocation gate; per-recenter loss bound 10 bp",
            "unmodelled": "dividends/uiMultiplier, _requireBandCoversGate, integer oracle tick, settlement swaps, borrow demand, RAM holder entitlement",
        },
        "windows": {k: [v[0], v[1]] for k, v in W.items()},
        "runs": {k: v[1] for k, v in RUNS.items()},
        "results": out,
    }
    (RESULTS / f"baseline_{pair}.json").write_text(json.dumps(doc, indent=1))
    print("wrote", RESULTS / f"baseline_{pair}.json")


if __name__ == "__main__":
    main()
