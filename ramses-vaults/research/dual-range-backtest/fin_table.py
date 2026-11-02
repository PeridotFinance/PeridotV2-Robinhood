import json, sys, collections
pair = sys.argv[1]
rows = [json.loads(l) for l in open(f"results/fin_{pair}.jsonl")]
by = collections.defaultdict(dict)
for r in rows: by[r["name"]][r["tag"]] = r
order = ["full|rebal","full|norebal","t1|rebal","t2|rebal","t3|rebal","last28|rebal","cap1000|rebal","cap50000|rebal","cap250000|rebal","cd3600_max24|rebal","cd900_max4|rebal","cd900_max24|rebal","gas1.5|rebal","gas5.0|rebal","histshare|rebal","stale24h|rebal","oracle_always_ok|rebal"]
for name, d in by.items():
    print("==", name)
    for tag in order:
        r = d.get(tag)
        if not r: continue
        cap = r["capital"]
        print("  %-22s days %5.1f  pnl %9.1f  vsHold %8.1f (%.2f%% = %.1f%%/yr)  fees %8.1f lvr %8.1f gas %5.1f  rec %2d/%-2d depl %.2f  ram %.0f  inR %.3f/%.3f  wkWorst %.0f" % (tag, r["days"], r["pnl"], r["pnl_vs_hold"], r["pnl_vs_hold"]/cap*100, r["excess_apr_pct"], r["fees_usd"], r["lvr_usd"], r["gas_usd"], r["recenters_core"], r["recenters_wide"], r["deployed_frac_avg"], r["ram_tokens"], r["in_range_core"], r["in_range_wide"], r.get("worst_week_excess_vs_hold_usd",0)))
