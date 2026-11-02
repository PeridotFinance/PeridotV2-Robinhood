"""Print Markdown tables from results/baseline_<PAIR>.json (or evidence/baseline_<PAIR>.json). Read-only formatting.

  python3 render_baseline.py SPY NVDA
"""
import json
import sys

from common import HERE, RESULTS


def load(pair):
    for d in (RESULTS, HERE / "evidence"):
        p = d / f"baseline_{pair}.json"
        if p.exists():
            return json.loads(p.read_text())
    raise SystemExit(f"baseline_{pair}.json not found")


def pick(doc, run, window, capital=10_000.0):
    for r in doc["results"]:
        if r["run"] == run and r["window"] == window and r["capital"] == capital:
            return r["result"]
    raise KeyError((run, window, capital))


def fmt_blocked(x):
    b = {k: v for k, v in x["blocked"].items() if v}
    d = {k: round(v, 2) for k, v in x["blocked_days"].items() if v > 0.005}
    return ("none" if not b else ", ".join(f"{k} {v}" for k, v in b.items())) + ("" if not d else " (" + ", ".join(f"{k} {v} d" for k, v in d.items()) + ")")


def main():
    for pair in sys.argv[1:] or ["SPY", "NVDA"]:
        doc = load(pair)
        print(f"### {pair}  (tick spacing {doc['spacing']}, {doc['dataset']['swaps']:,} swaps, pool head block {doc['dataset']['poolHeadBlock']:,})\n")
        for win, title in (("full", "Full window"), ("last28", "Last 28 days"), ("t1", "First third"), ("t2", "Middle third"), ("t3", "Last third")):
            x0 = pick(doc, "single600_trig300", win)
            print(f"**{title}**: {x0['start_iso']} to {x0['end_iso']}, {x0['days']:.1f} days, $10,000 start\n")
            print("| run | window return | hold | excess (window) | excess (annualised) | fees to holders | reserve cut | recenter gas | idle capital (avg) | recenters | worst week vs hold | blocked maintenance |")
            print("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
            for run in ("single600_trig300", "single600_trig100", "single1200_trig600", "full_range"):
                x = pick(doc, run, win)
                print(f"| {run} | {x['ret_pct']:+.2f}% | {x['hold_ret_pct']:+.2f}% | {x['excess_pct']:+.2f}% | {x['excess_apr_pct']:+.1f}%/yr | ${x['fees_usd']:.1f} | ${x['reserve_usd']:.1f} | ${x['gas_usd']:.2f} | "
                      f"{100 * x['idle_frac_avg']:.0f}% | {x['recenters_core']} | ${x['worst_week_excess_vs_hold_usd']:.0f} | {fmt_blocked(x)} |")
            print()
        print("**Where the excess comes from (full window, baseline)**\n")
        print("| run | excess vs hold | = fees to holders | + inventory effect | - recenter gas | LVR vs delta-matched benchmark | fees - LVR - gas | maintenance gas (assumed) | excess after maintenance (annualised) |")
        print("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        for run in ("single600_trig300", "full_range"):
            x = pick(doc, run, "full")
            print(f"| {run} | ${x['pnl_vs_hold']:.1f} | ${x['fees_usd']:.1f} | ${x['inventory_effect_usd']:.1f} | ${x['gas_usd']:.2f} | ${x['lvr_usd']:.1f} | ${x['fees_minus_lvr_minus_gas']:.1f} | "
                  f"${x['maintenance_gas_usd']:.1f} | {x['annualised_excess_after_maintenance_pct']:+.1f}%/yr |")
        x = pick(doc, "single600_trig300", "full")
        print(f"\nEnd state of the baseline: idle USDG ${x['final_idle_usdg']:.0f}, idle stock ${x['final_idle_stock_usd']:.0f}, max drawdown {x['max_drawdown_pct']:.2f}%, "
              f"in range {100 * x['in_range_core']:.0f}% of the time it held a position, RAM tokens accrued {x['ram_tokens']:.1f} (valued at zero).\n")
        print("**Sensitivities (full window, annualised excess vs hold / window excess)**\n")
        print("| run | what changes | excess (annualised) | excess (window) | recenters |")
        print("| --- | --- | --- | --- | --- |")
        for run, desc in doc["runs"].items():
            if run in ("single600_trig300", "full_range"):
                continue
            x = pick(doc, run, "full")
            print(f"| {run} | {desc} | {x['excess_apr_pct']:+.1f}%/yr | {x['excess_pct']:+.2f}% | {x['recenters_core']} |")
        for cap in (1_000.0, 50_000.0):
            for run in ("single600_trig300", "full_range"):
                x = pick(doc, run, "full", cap)
                print(f"| {run} @ ${cap:,.0f} | dilution at a different size | {x['excess_apr_pct']:+.1f}%/yr | {x['excess_pct']:+.2f}% | {x['recenters_core']} |")
        print()


if __name__ == "__main__":
    main()
