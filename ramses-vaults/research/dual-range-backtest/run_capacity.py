"""Capacity curve: net result versus capital for chosen configurations, per window (dilution of fees, LVR does not dilute).

  python3 run_capacity.py PAIR name1,name2,...
"""
from __future__ import annotations
import json, sys
from multiprocessing import Pool
import run_finalists as F
from bt_lib import *

CAPS = (1_000.0, 5_000.0, 10_000.0, 25_000.0, 50_000.0, 100_000.0, 250_000.0, 500_000.0)

def main():
    pair = sys.argv[1]; names = sys.argv[2].split(",")
    W = F.windows(pair)
    jobs, tags = [], []
    for n in names:
        for cap in CAPS:
            for wn, w in W.items():
                jobs.append((n, w, dict(rebalance=True, capital_usd=cap))); tags.append(f"cap{int(cap)}|{wn}")
    out = open(RESULTS / f"cap_{pair}.jsonl", "w")
    with Pool(5, initializer=F._init, initargs=(pair,)) as pool:
        for res, tag in zip(pool.imap(F._run, jobs), tags):
            res["tag"] = tag
            out.write(json.dumps(res) + "\n"); out.flush()
            print(res["name"], tag, "vsHold %.1f (%.1f%%/yr)" % (res["pnl_vs_hold"], res["excess_apr_pct"]), flush=True)

if __name__ == "__main__":
    main()
