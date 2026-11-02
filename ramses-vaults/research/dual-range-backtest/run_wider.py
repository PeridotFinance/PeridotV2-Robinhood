"""Edge-of-grid check: the best core half-width in the grid was the widest one tested (200 ticks); try 300 and 400."""
import json, sys
from multiprocessing import Pool
import run_finalists as F
from bt_lib import *
def main():
    pair = sys.argv[1]
    W = F.windows(pair)
    names = ["dual_c200_w500_k85", "dual_c300_w500_k85", "dual_c300_w800_k85", "dual_c400_w800_k85", "dual_c300_w800_k70"]
    jobs, tags = [], []
    for n in names:
        for cap in (10_000.0, 50_000.0):
            for wn in ("full", "last28"):
                jobs.append((n, W[wn], dict(rebalance=True, capital_usd=cap))); tags.append((cap, wn))
    with Pool(5, initializer=F._init, initargs=(pair,)) as pool:
        res = pool.map(F._run, jobs)
    out = []
    for r, (cap, wn) in zip(res, tags):
        out.append(dict(r, tag=f"cap{int(cap)}|{wn}"))
        print("%-22s $%-6d %-7s excess %5.1f%%/yr  fees %7.1f lvr %7.1f rec %d/%d" % (r["name"], cap, wn, r["excess_apr_pct"], r["fees_usd"], r["lvr_usd"], r["recenters_core"], r["recenters_wide"]))
    json.dump(out, open(RESULTS / f"wider_{pair}.json", "w"))
if __name__ == "__main__":
    main()
