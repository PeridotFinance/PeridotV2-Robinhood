"""Gate 1. Reproduce the live NVDA/USDG pair (Uniswap v4, 0.30%) over 2026-10-19 .. 2026-11-06 from the swap table alone.

Target (inputs/fee-apr-2026-11-06, read from the vault's own events): fees collected $0.1888, pair value $19.3648 -> $17.6900,
holding the deposited tokens $18.2254, i.e. -2.94% against holding.

What the chain shows about that window (data/robinhood/live_pair_checkpoints.json, Recentered events): the +/-2400 policy was
stored at block 92,657,111 (19 Oct) but the range in the pool stayed the earlier one, ticks 221340..223800 (+/-1200 around
222570), until the first recentre under the new policy at block 104,502,519 (2 Nov), after the 10.8-day keeper outage.
The replay therefore starts from that range, applies the 2400 / 600 policy to every later recentre, and sends nothing
during the outage. A second run shows what a +/-2400 range placed on 19 Oct would have done.
"""
import json

import engine_ext as X
from lib_cl import ROOT, dump

fa = json.load(open(ROOT / "inputs/fee-apr-2026-11-06/fee-apr.json"))
cf = json.load(open(ROOT / "inputs/fee-apr-2026-11-06/counterfactual.json"))
live = json.load(open(ROOT / "data/robinhood/live_pair_checkpoints.json"))
cps = live["checkpoints"]
t0, t1 = cps[0]["time"], cps[-1]["time"]
gap, gi = max((cps[i + 1]["time"] - cps[i]["time"], i) for i in range(len(cps) - 1))
outage = (cps[gi]["time"], cps[gi + 1]["time"])
start = cps[0]["nvda"] * cps[0]["price"] + cps[0]["usdg"]
frac = cps[0]["nvda"] * cps[0]["price"] / start
center_price = 1e12 / 1.0001 ** 222570        # centre of ticks 221340..223800 (token0 = USDG)
ds = X.load_dataset("v4_nvda")
target = {"fees_collected_usd": fa["feesUSD"], "start_usd": start, "end_usd": cf["actualUsd"], "hold_usd": cf["holdUsd"],
          "vs_hold_pct": cf["actualVsHoldPct"], "nvda_start": cps[0]["nvda"], "nvda_end": cps[-1]["nvda"], "usdg_end": cps[-1]["usdg"],
          "recentres_in_window": sum(1 for r in live["recentered"] if r["block"] > 92657111)}
out = {"target_live": target, "outage_utc": outage, "runs": {}}
print("live      :", {k: round(v, 4) for k, v in target.items()})
for name, kw in (("replay_as_it_ran", dict(initial_half=1200, initial_center_price=center_price, outages=(outage,))),
                 ("replay_no_outage", dict(initial_half=1200, initial_center_price=center_price)),
                 ("if_2400_from_19_oct", dict(outages=(outage,))),
                 ("if_2400_no_outage", dict())):
    cfg = X.single_range_config(2400, 600, start, 60, max_per_day=4, max_ranged_usd=50.0, gate_bps=300.0, gas_usd=0.0)
    r = X.run_window(ds, cfg, t0, t1, start_stock_frac=frac, keep_series=True, **kw)
    gross = r["fees_usd"] + r["reserve_usd"]
    q_end = r["q_series"][-1]
    row = {"fees_gross_usd": gross, "fees_to_holders_usd": r["fees_usd"], "end_usd": r["equity_end_mark"], "hold_usd": r["hold_end_mark"],
           "vs_hold_pct": 100 * (r["equity_end_mark"] - r["hold_end_mark"]) / r["hold_end_mark"], "recentres": r["recenters_core"],
           "nvda_end": q_end, "lvr_usd": r["lvr_usd"], "bench_minus_hold_usd": r["bench_pnl"] - (r["hold_end"] - r["equity_end"] + r["pnl"]),
           "deployed_frac_avg": r["deployed_frac_avg"], "in_range": r["in_range_core"], "end_mark": r["end_mark"],
           "recentre_times": [x[0] for x in r["recenter_log"]]}
    out["runs"][name] = row
    print(f"{name:<20}:", {k: (round(float(v), 4) if isinstance(v, (int, float)) else v) for k, v in row.items() if k != "recentre_times"})
a = out["runs"]["replay_as_it_ran"]
out["reconciliation"] = {"fees_ratio_replay_over_live": a["fees_gross_usd"] / target["fees_collected_usd"],
                         "vs_hold_gap_points": a["vs_hold_pct"] - target["vs_hold_pct"], "end_value_gap_usd": a["end_usd"] - target["end_usd"]}
print("reconciliation:", out["reconciliation"])
dump(out, "gate1_reproduce_live.json")
