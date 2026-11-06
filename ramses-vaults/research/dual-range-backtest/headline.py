"""Headline numbers: excess return versus holding the deposited pair (simple, annualised), RAM at zero and at 0.0355 USD."""
import collections, json, sys
RAM = 0.0355
pair = sys.argv[1]
rows = [json.loads(l) for l in open(f"results/cap_{pair}.jsonl")]
by = collections.defaultdict(lambda: collections.defaultdict(dict))
for r in rows:
    cap, win = r["tag"].split("|"); by[r["name"]][int(cap[3:])][win] = r
def apr(r, ram):
    return (r["pnl_vs_hold"] + ram * r["ram_tokens"] * RAM) / r["capital"] * 100 * 365 / r["days"]
for name, d in by.items():
    print("==", name, "excess vs hold %/yr  [RAM=0 | RAM=$0.0355]   windows: full(74d/42d) t1 t2 t3 last28")
    for cap in sorted(d):
        w = d[cap]
        print("  $%-8d " % cap + "  ".join("%s %5.1f|%5.1f" % (k, apr(w[k], 0), apr(w[k], 1)) for k in ("full", "t1", "t2", "t3", "last28") if k in w))
