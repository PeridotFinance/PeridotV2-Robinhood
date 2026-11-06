"""Print Markdown tables from results/baseline_<PAIR>[_caps].json (or evidence/). Read-only formatting.

  python3 render_baseline.py caps SPY NVDA        current caps ($1,000 pair, $50 ranged)
  python3 render_baseline.py research SPY NVDA    research scenario ($10,000, ranged cap = capital)

Returns are shown before and after ALL assumed costs. LVR is never subtracted from a return versus holding: it appears only in the
separate fees - LVR - gas - maintenance identity (a comparison against a different, delta-matched benchmark).
"""
import json
import sys

from common import HERE, RESULTS


def load(pair, profile):
    suffix = "_caps" if profile == "caps" else ""
    for d in (RESULTS, HERE / "evidence"):
        p = d / f"baseline_{pair}{suffix}.json"
        if p.exists():
            return json.loads(p.read_text())
    raise SystemExit(f"baseline_{pair}{suffix}.json not found")


def pick(doc, run, window, capital=None):
    capital = doc["capitalUsd"] if capital is None else capital
    for r in doc["results"]:
        if r["run"] == run and r["window"] == window and r["capital"] == capital:
            return r["result"]
    raise KeyError((run, window, capital))


def fmt_blocked(x):
    b = {k: v for k, v in x["blocked"].items() if v}
    d = {k: round(v, 2) for k, v in x["blocked_days"].items() if v > 0.005}
    return ("none" if not b else ", ".join(f"{k} {v}" for k, v in b.items())) + ("" if not d else " (" + ", ".join(f"{k} {v} d" for k, v in d.items()) + ")")


def main():
    profile = sys.argv[1]
    pairs = sys.argv[2:] or ["SPY", "NVDA"]
    for pair in pairs:
        doc = load(pair, profile)
        runs = [r for r in doc["runs"] if r in {x["run"] for x in doc["results"]}]
        x0 = pick(doc, "single600_trig300", "full")
        print(f"### {pair}: {doc['profileLabel']}\n")
        print(f"Tick spacing {doc['spacing']}, {doc['dataset']['swaps']:,} swaps, pool head block {doc['dataset']['poolHeadBlock']:,}, starting capital ${doc['capitalUsd']:,.0f}, ranged cap ${doc['rangedCapUsd']:,.0f}.\n")
        for win, title in (("full", "Full window"), ("last28", "Last 28 days")):
            x0 = pick(doc, "single600_trig300", win)
            print(f"**{title}**: {x0['start_iso']} to {x0['end_iso']} ({x0['days']:.1f} days)\n")
            print("| run | return | hold | excess before maintenance | return after all costs | excess after all costs (window / annualised) | fees to holders | reserve cut | recenter gas | maintenance (assumed) | idle capital (avg) | recenters | worst week vs hold | blocked |")
            print("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
            for run in runs:
                if run in ("single600_trig300_noreserve", "single600_trig300_gas040", "single600_trig300_cap5k", "single600_trig300_histshare") and win != "full":
                    continue
                x = pick(doc, run, win)
                print(f"| {run} | {x['ret_pct']:+.2f}% | {x['hold_ret_pct']:+.2f}% | {x['excess_pct']:+.2f}% | {x['window_return_after_all_costs_pct']:+.2f}% | "
                      f"{x['window_excess_after_all_costs_pct']:+.2f}% / {x['annualised_excess_after_all_costs_pct']:+.1f}%/yr | ${x['fees_usd']:.2f} | ${x['reserve_usd']:.2f} | ${x['gas_usd']:.3f} | "
                      f"${x['maintenance_gas_usd']:.2f} | {100 * x['idle_frac_avg']:.0f}% | {x['recenters_core']} | ${x['worst_week_excess_vs_hold_usd']:.2f} | {fmt_blocked(x)} |")
            print()
        print("**Fees minus LVR minus costs (a different benchmark from holding; not subtracted from any return above)**\n")
        print("| run (full window) | fees to holders | LVR vs delta-matched benchmark | recenter gas | maintenance (assumed) | fees - LVR - gas | fees - LVR - gas - maintenance | maintenance if the observed average $0.1428/day: excess after all costs |")
        print("| --- | --- | --- | --- | --- | --- | --- | --- |")
        for run in ("single600_trig300", "single600_trig300_poll300", "full_range"):
            if run not in runs:
                continue
            x = pick(doc, run, "full")
            print(f"| {run} | ${x['fees_usd']:.2f} | ${x['lvr_usd']:.2f} | ${x['gas_usd']:.3f} | ${x['maintenance_gas_usd']:.2f} | ${x['fees_minus_lvr_minus_gas']:.2f} | "
                  f"${x['fees_minus_lvr_minus_gas_minus_maintenance_usd']:.2f} | {x['window_excess_after_all_costs_observed_maintenance_pct']:+.2f}% / {x['annualised_excess_after_all_costs_observed_maintenance_pct']:+.1f}%/yr |")
        print("\n**Supplier returns versus protocol-paid operating costs (full window)**. In the contract the keeper wallet pays gas and the vault owes suppliers only their claims, so recenter gas and maintenance are operator/protocol costs, not deductions from supplier principal. Supplier view = the run with recenter gas added back; protocol view = reserve-cut inflow minus gas minus maintenance.\n")
        print("| run | supplier return | hold | supplier excess vs hold (window / annualised) | reserve-cut inflow to the loss reserve | recenter gas (operator-paid) | maintenance (operator-paid, assumed) | protocol net (inflow - costs) |")
        print("| --- | --- | --- | --- | --- | --- | --- | --- |")
        for run in ("single600_trig300", "single600_trig300_poll300", "full_range"):
            if run not in runs:
                continue
            x = pick(doc, run, "full")
            print(f"| {run} | {x['supplier_return_pct']:+.2f}% | {x['hold_ret_pct']:+.2f}% | {x['supplier_excess_vs_hold_pct']:+.2f}% / {x['supplier_excess_vs_hold_apr_pct']:+.1f}%/yr | "
                  f"${x['protocol_reserve_inflow_usd']:.2f} | ${x['gas_usd']:.3f} | ${x['maintenance_gas_usd']:.2f} | ${x['protocol_net_usd']:.2f} |")
        if "fee_x2" in runs:
            print("\n**Fee history and fee stress (full window; time-varying fee in force on every swap is the baseline; the stresses scale or replace it with volume unchanged, so they are upper-bound fee scenarios, not forecasts)**\n")
            print("| run | fees to holders | supplier excess vs hold (window / annualised) | after all assumed costs incl. maintenance (window / annualised) |")
            print("| --- | --- | --- | --- |")
            for run, label in (("single600_trig300", "actual fee in force (baseline)"), ("fee_fixed150", "constant 150 ppm (listed / instantaneous)"), ("fee_x15", "fee x1.5"), ("fee_x2", "fee x2"), ("fee_x3", "fee x3")):
                if run in runs:
                    x = pick(doc, run, "full")
                    print(f"| {label} | ${x['fees_usd']:.2f} | {x['supplier_excess_vs_hold_pct']:+.2f}% / {x['supplier_excess_vs_hold_apr_pct']:+.1f}%/yr | {x['window_excess_after_all_costs_pct']:+.2f}% / {x['annualised_excess_after_all_costs_pct']:+.1f}%/yr |")
        x = pick(doc, "single600_trig300", "full")
        print(f"\nExcess versus holding = fees to holders + inventory effect - recenter gas (baseline: ${x['fees_usd']:.2f} + ${x['inventory_effect_usd']:.2f} - ${x['gas_usd']:.3f}); "
              f"end state idle USDG ${x['final_idle_usdg']:.0f}, idle stock ${x['final_idle_stock_usd']:.0f}; RAM accrued {x['ram_tokens']:.1f} tokens, valued at zero.\n")
        if profile == "research":
            print("**Windows (baseline, before maintenance) and dilution**\n")
            print("| window | excess (window) | annualised |")
            print("| --- | --- | --- |")
            for win in ("t1", "t2", "t3"):
                x = pick(doc, "single600_trig300", win)
                print(f"| {win} {x['start_iso'][:10]} to {x['end_iso'][:10]} | {x['excess_pct']:+.2f}% | {x['excess_apr_pct']:+.1f}%/yr |")
            for cap in (1_000.0, 50_000.0):
                for run in ("single600_trig300", "full_range"):
                    x = pick(doc, run, "full", cap)
                    print(f"| full, {run} at ${cap:,.0f} | {x['excess_pct']:+.2f}% | {x['excess_apr_pct']:+.1f}%/yr |")
            print()


if __name__ == "__main__":
    main()
