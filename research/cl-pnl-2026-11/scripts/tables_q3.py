"""Markdown tables for Q3 from out/q3_*.json."""
import json
import sys

from lib_cl import OUT, VENUES

short = lambda v: VENUES[v].label.split(" (")[0]
DIST = {100: "1%", 300: "3%", 490: "5%"}


def table(venue, notional=1000, horizon="7", tag=""):
    p = OUT / f"q3_{venue}_{notional}_{horizon}d{tag}.json"
    if not p.exists():
        return
    o = json.loads(p.read_text())
    vp = o["venue_params"]
    print(f"\n### {short(venue)}: ${notional:,} order, {o['width_ticks']}-tick band, {horizon}-day life, {o['starts']} start times ({o['window'][0][:10]} to {o['window'][1][:10]})\n")
    print("| Side | Distance | Band filled and closed | Limit order filled | Limit filled, band not | Fees kept (mean) | Fees kept when filled | Reward if sold (mean) | Band − limit: mean | median | 5th pct | 95th pct | Better / worse than limit |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for side in ("sell", "buy"):
        for d in (100, 300, 490):
            r = o["results"][f"{side}_{d}"]
            x = r["diff_vs_limit_bps"]
            wf = r.get("when_filled", {})
            print(f"| {'Take-profit (sell)' if side == 'sell' else 'Buy'} | {DIST[d]} | {100 * r['fill_prob']:.0f}% | {100 * r['limit_fill_prob']:.0f}% | {100 * r['limit_filled_band_not']:.0f}% | "
                  f"{r['fees_bps_mean']:.1f} bp | {wf.get('fees_bps_mean', 0):.1f} bp | {r['reward_bps_if_sold_mean']:.1f} bp | {x['mean']:+.0f} bp | {x['median']:+.0f} bp | "
                  f"{x['p05']:+.0f} bp | {x['p95']:+.0f} bp | {100 * x['share_better']:.0f}% / {100 * x['share_worse']:.0f}% |")
    return o


if __name__ == "__main__":
    for v in ("ramses_nvda", "ramses_spy", "pharaoh_wavax", "v4_nvda"):
        table(v)
    print("\n## Sensitivities\n")
    for v in ("ramses_nvda", "ramses_spy", "pharaoh_wavax", "v4_nvda"):
        table(v, 10000)
        table(v, 1000, "7", "_w100")
        table(v, 1000, "2")
