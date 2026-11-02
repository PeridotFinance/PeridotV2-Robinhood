"""Run the dual-range grid for one pair. One JSON line per finished configuration (progress can be tailed).

  python3 run_grid.py SPY base            # full history, current fee structure (50% protocol), $10k
  python3 run_grid.py SPY last28          # same, last 28 days only (the regime with RAM emissions and 50% protocol share)
  python3 run_grid.py GLD base
"""
from __future__ import annotations
import itertools, json, sys, time
from multiprocessing import Pool
from bt_lib import *

GAS = 0.40
SINCE = {}
import datetime as _dt
SINCE["last28"] = _dt.datetime(2026, 10, 5, tzinfo=_dt.timezone.utc).timestamp()

def grid_configs(capital: float):
    cfgs = []
    for core, wide, w in itertools.product((50, 100, 200), (300, 500, 800), (0.5, 0.7, 0.85)):
        cfgs.append(dict(name=f"dual_c{core}_w{wide}_k{int(w*100)}", core_half=core, wide_half=wide, core_weight=w))
    cfgs.append(dict(name="single_pm1", core_half=100, wide_half=0, core_weight=1.0))
    cfgs.append(dict(name="single_pm5", core_half=500, wide_half=0, core_weight=1.0))
    cfgs.append(dict(name="full_range", full_range=True, core_half=0, wide_half=0, core_weight=1.0))
    for c in cfgs:
        c["capital_usd"] = capital
    return cfgs

_G = {}

def _init(pair, since):
    ds, meta, i0 = load_dataset(pair, since=since)
    _G.update(ds=ds, i0=i0, pair=pair)

def _run(cfgd):
    cfgd = dict(cfgd)
    over = cfgd.pop("_over", {})
    base = dict(gas_usd=GAS, protocol_share=0.5, ram_usd=0.0)
    base.update(cfgd); base.update(over)
    cfg = E.Config(**base)
    t = time.time()
    sim = E.Sim(_G["ds"], cfg, session_of, start_index=_G["i0"]).run()
    r = sim.result()
    r["cfg"] = {k: getattr(cfg, k) for k in ("core_half", "wide_half", "core_weight", "cooldown", "max_per_day", "gas_usd", "protocol_share", "capital_usd", "full_range", "gate_bps", "max_stale")}
    r["secs"] = time.time() - t
    return r

def main():
    """usage: run_grid.py PAIR SCENARIO   with SCENARIO = <window>_<rebal|norebal>[_hist]  window: full | last28"""
    pair, scen = sys.argv[1], sys.argv[2]
    parts = scen.split("_")
    since = SINCE["last28"] if parts[0] == "last28" else None
    over = {"rebalance": "norebal" not in parts}
    if "hist" in parts:
        over["protocol_share"] = None
    cfgs = grid_configs(10_000.0)
    for c in cfgs:
        c["_over"] = over
    out = open(RESULTS / f"bt_{pair}_{scen}.jsonl", "w")
    with Pool(5, initializer=_init, initargs=(pair, since)) as pool:
        for r in pool.imap_unordered(_run, cfgs):
            out.write(json.dumps(r) + "\n"); out.flush()
            print(r["name"], "pnl %.1f vs hold %.1f fees %.1f lvr %.1f rec %d" % (r["pnl"], r["pnl_vs_hold"], r["fees_usd"], r["lvr_usd"], r["recenters_core"] + r["recenters_wide"]), flush=True)

if __name__ == "__main__":
    main()
