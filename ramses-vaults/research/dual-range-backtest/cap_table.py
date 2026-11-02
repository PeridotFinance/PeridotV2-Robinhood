import json, sys, collections
pair = sys.argv[1]
rows = [json.loads(l) for l in open(f"results/cap_{pair}.jsonl")]
by = collections.defaultdict(lambda: collections.defaultdict(dict))
for r in rows:
    cap, win = r["tag"].split("|"); by[r["name"]][int(cap[3:])][win] = r
for name, d in by.items():
    print("==", name, "(excess vs hold, %/yr simple; windows: full | thirds t1 t2 t3 | last28)")
    for cap in sorted(d):
        w = d[cap]
        print("  $%-8d " % cap + "  ".join("%s %6.1f" % (k, w[k]["excess_apr_pct"]) for k in ("full", "t1", "t2", "t3", "last28") if k in w), "| fees/yr on full %.1f%% lvr/yr %.1f%%" % (w["full"]["fees_usd"]/cap*100*365/w["full"]["days"], w["full"]["lvr_usd"]/cap*100*365/w["full"]["days"]))
