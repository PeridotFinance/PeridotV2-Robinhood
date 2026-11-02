import json, sys
def load(p): return [json.loads(l) for l in open(p)]
def show(path, top=None, keys=None):
    rows = load(path)
    rows.sort(key=lambda r: -r["pnl_vs_hold"])
    print("==", path.split("/")[-1], "days %.1f" % rows[0]["days"], "capital", rows[0]["capital"])
    print("%-22s %8s %8s %7s %7s %6s %5s %6s %6s %5s %7s %7s" % ("config","pnl","vsHold","fees","lvr","gas","rec","depl","swapd","inR_c","wkWorst","dd%"))
    for r in rows[:top]:
        print("%-22s %8.1f %8.1f %7.1f %7.1f %6.1f %2d/%-2d %6.3f %6.0f %5.3f %7.0f %7.2f" % (r["name"], r["pnl"], r["pnl_vs_hold"], r["fees_usd"], r["lvr_usd"], r["gas_usd"], r["recenters_core"], r["recenters_wide"], r["deployed_frac_avg"], r["swapped_usd"], r["in_range_core"], r.get("worst_week_excess_vs_hold_usd", 0), r.get("max_drawdown_pct", 0)))
if __name__ == "__main__":
    for p in sys.argv[1:]:
        show(p, top=int(__import__("os").environ.get("TOP", "40")))
