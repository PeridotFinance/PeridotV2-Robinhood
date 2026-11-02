import json, sys
pair = sys.argv[1]
rows = [json.loads(l) for l in open(f"results/fin_{pair}.jsonl")]
want = [("full|rebal"), ("last28|rebal"), ("full|norebal")]
names = []
for r in rows:
    if r["name"] not in names: names.append(r["name"])
for name in names:
    for tag in want:
        r = next((x for x in rows if x["name"] == name and x["tag"] == tag), None)
        if not r: continue
        print("%-20s %-13s days %5.1f | end equity %9.1f  P&L %8.1f (%+.2f%%)  hold %+.2f%%  excess %+7.1f (%+.2f%%) | fees %7.1f  LVR %7.1f  gas %5.1f  fees-LVR-gas %7.1f | delta-matched bench P&L %7.1f  rebalanced50/50 P&L %7.1f | worst wk excess %6.0f  worst wk equity %6.0f  maxDD %5.2f%% | rec/day %.2f  inR %.3f/%.3f  deployed %.2f  RAM %6.0f  blocked_days %s" % (
            name, tag, r["days"], r["equity_end"], r["pnl"], r["ret_pct"], r["hold_ret_pct"], r["pnl_vs_hold"], r["excess_pct"], r["fees_usd"], r["lvr_usd"], r["gas_usd"], r["fees_minus_lvr_minus_gas"], r["bench_pnl"], r.get("rebalanced_5050_pnl", float("nan")), r.get("worst_week_excess_vs_hold_usd", 0), r.get("worst_week_equity_change_usd", 0), r.get("max_drawdown_pct", 0), r["recenters_per_day"], r["in_range_core"], r["in_range_wide"], r["deployed_frac_avg"], r["ram_tokens"], {k: round(v, 2) for k, v in r["blocked_days"].items() if v}))
