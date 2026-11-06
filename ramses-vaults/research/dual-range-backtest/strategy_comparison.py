"""Side-by-side comparison of the simplest supported Ramses configurations, from the EXISTING baseline evidence (no new simulation).

  full range                       adapter default, no recentring
  +/-600 ticks (~6.2%)             the nearest contract-feasible single range
  +/-1200 ticks (~12.7%)           wider sensitivity
  +/-2400 ticks (~27%)             the policy read back from the LIVE Uniswap vault (trigger 600, <= 4 recentres/day)

Two profiles, never mixed: CURRENT CAPS ($1,000 pair, $50 ranged cap; operator-supplied, the Ramses vault is not deployed) and, labelled COUNTERFACTUAL,
the research profile ($10,000 with the ranged cap equal to capital) scaled by 1/10 to $1,000. The counterfactual shows what a 75% to 90% allocation
would require (a higher ranged cap) and is NOT approved. Returns are the window returns of the baselines (not annualised: the windows are 74 and 90
days and the LP income is tiny, so an annualised figure would overstate how well measured it is).

  python3 strategy_comparison.py        prints the tables; render functions are used by the README assembly
"""
import json

from common import HERE

RUNS = [("full_range", "full range"), ("single600_trig300", "±600 ticks"), ("single1200_trig600", "±1200 ticks"), ("live2400_trig600_perday4", "±2400 ticks (live policy)")]
PAIRS = ("SPY", "NVDA")


def load(pair, profile):
    d = json.load(open(HERE / "evidence" / f"baseline_{pair}{'_caps' if profile == 'caps' else ''}.json"))
    out = {}
    for run, _ in RUNS:
        out[run] = next(r["result"] for r in d["results"] if r["run"] == run and r["window"] == "full" and r["capital"] == d["capitalUsd"])
    return d, out


def rows(profile):
    res = []
    for pair in PAIRS:
        d, runs = load(pair, profile)
        scale = 1000.0 / d["capitalUsd"]       # to a $1,000 pair
        for run, label in RUNS:
            x = runs[run]
            res.append({"profile": profile, "pair": pair, "run": run, "label": label, "days": x["days"], "capitalUsd": d["capitalUsd"], "rangedCapUsd": d["rangedCapUsd"], "scaleTo1000": scale,
                        "supplierReturnPct": x["supplier_return_pct"], "holdReturnPct": x["hold_ret_pct"], "supplierExcessVsHoldPct": x["supplier_excess_vs_hold_pct"],
                        "feesToHoldersUsd": x["fees_usd"] * scale, "reserveInflowUsd": x["protocol_reserve_inflow_usd"] * scale,
                        "lvrUsd": x["lvr_usd"] * scale, "inventoryEffectUsd": x["inventory_effect_usd"] * scale, "worstWeekVsHoldUsd": x["worst_week_excess_vs_hold_usd"] * scale,
                        "maxDrawdownPct": x["max_drawdown_pct"], "idleAvgPct": 100 * x["idle_frac_avg"], "deployedAvgPct": 100 * x["deployed_frac_avg"],
                        "recenterGasUsd": x["gas_usd"] * scale, "maintenanceUsd": x["maintenance_gas_usd"], "protocolOperatingCostUsd": x["protocol_operating_cost_usd"],
                        "protocolNetUsd": x["protocol_net_usd"], "recenters": x["recenters_core"]})
    return res


def table(profile):
    head = ("| pair | configuration | window (days) | supplier return | hold | supplier excess vs hold | fees to holders | reserve inflow | LVR (cost of being the counterparty) | "
            "worst week vs hold | idle capital (avg) | deployed (avg) | recentres | recentre gas | maintenance (protocol-paid, assumed) | protocol net |")
    out = [head, "|" + " --- |" * 16]
    for r in rows(profile):
        out.append(f"| {r['pair']} | {r['label']} | {r['days']:.0f} | {r['supplierReturnPct']:+.2f}% | {r['holdReturnPct']:+.2f}% | {r['supplierExcessVsHoldPct']:+.2f}% | ${r['feesToHoldersUsd']:.2f} | "
                   f"${r['reserveInflowUsd']:.2f} | ${r['lvrUsd']:.2f} | ${r['worstWeekVsHoldUsd']:.2f} | {r['idleAvgPct']:.0f}% | {r['deployedAvgPct']:.0f}% | {r['recenters']} | ${r['recenterGasUsd']:.2f} | "
                   f"${r['maintenanceUsd']:.2f} | ${r['protocolNetUsd']:.2f} |")
    return "\n".join(out)


def main():
    print("CURRENT CAPS ($1,000 pair, $50 ranged cap)\n" + table("caps"))
    print("\nCOUNTERFACTUAL (ranged cap = capital, $10,000 results scaled to $1,000; NOT approved)\n" + table("research"))
    (HERE / "evidence" / "strategy-comparison.json").write_text(json.dumps({"schema": "ramses-strategy-comparison-1", "doc": __doc__, "caps": rows("caps"), "counterfactual": rows("research")}, indent=1))


if __name__ == "__main__":
    main()
