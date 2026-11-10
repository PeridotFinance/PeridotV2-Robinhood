"""Q7 check. Is "liquidity in force between swaps = the last swap's liquidity" good enough, in particular on Blackhole,
whose main LP re-mints its position continually? Rebuild the active liquidity exactly from the pool's Mint and Burn
events between swaps and compare the two time-weighted averages of L x sqrt(P), week by week, for both pools.
Usage: q7_check_inrange.py [blackhole|pharaoh]"""
import sys
import csv
import datetime as dt
import gzip
import json

import numpy as np

from lib_cl import DATA, dump

WEEK = 604800
WHICH = sys.argv[1] if len(sys.argv) > 1 else "blackhole"
SWAPS, LIQ = {"blackhole": ("blackhole/blackhole_wavax_usdc_swaps.csv.gz", "blackhole/blackhole_wavax_usdc_liquidity.csv.gz"),
              "pharaoh": ("avalanche/pharaoh_wavax_usdc_swaps.csv.gz", "avalanche/pharaoh_wavax_usdc_liquidity.csv.gz")}[WHICH]
sw = {k: [] for k in ("block", "idx", "ts", "L", "tick", "sq")}
with gzip.open(DATA / SWAPS, "rt") as f:
    for r in csv.DictReader(f):
        sw["block"].append(int(r["block"])); sw["idx"].append(int(r["log_index"])); sw["ts"].append(float(r["ts"]))
        sw["L"].append(int(r["liquidity"]) / 1e12); sw["tick"].append(int(r["tick"])); sw["sq"].append(int(r["sqrt_price_x96"]) / 2 ** 96)
sw = {k: np.array(v) for k, v in sw.items()}
sqrtp = sw["sq"] * 1e6            # sqrt(USDC per WAVAX): raw price x 1e12, so sqrt x 1e6
b0, b1 = int(sw["block"][0]), int(sw["block"][-1])
ev = []
with gzip.open(DATA / LIQ, "rt") as f:
    for r in csv.DictReader(f):
        b = int(r["block"])
        if b0 <= b <= b1:
            ev.append((b, int(r["log_index"]), int(r["tick_lower"]), int(r["tick_upper"]), int(r["liquidity_delta"]) / 1e12))
print("swaps", len(sw["ts"]), "mint/burn events in the window", len(ev))
# merge: 0 = swap, 1 = liquidity event
keys = [(int(b), int(i), 0, k) for k, (b, i) in enumerate(zip(sw["block"], sw["idx"]))] + [(e[0], e[1], 1, k) for k, e in enumerate(ev)]
keys.sort()
ts_of_block = lambda b: float(np.interp(b, sw["block"], sw["ts"]))
rows = {}
L = sw["L"][0]; tick = int(sw["tick"][0]); sp = sqrtp[0]; t_prev = sw["ts"][0]
L_swap = L
mism = 0
for b, i, kind, k in keys:
    t = sw["ts"][k] if kind == 0 else ts_of_block(b)
    wk = int(t_prev // WEEK)
    r = rows.setdefault(wk, [0.0, 0.0, 0.0])
    d = max(t - t_prev, 0.0)
    r[0] += L * sp * d; r[1] += L_swap * sp * d; r[2] += d
    t_prev = max(t, t_prev)
    if kind == 0:
        if abs(L - sw["L"][k]) > 1e-6 * max(1.0, sw["L"][k]) and False:
            mism += 1
        L = sw["L"][k]; L_swap = L; tick = int(sw["tick"][k]); sp = sqrtp[k]
    else:
        _, _, lo, hi, dl = ev[k]
        if lo <= tick < hi:
            L += dl
out = []
print(f"{'week':<12}{'exact in-range $':>18}{'swap-only $':>14}{'ratio':>8}")
for wk in sorted(rows):
    a, c, d = rows[wk]
    if d < 0.99 * WEEK:
        continue
    ex, so = 0.009976 * a / d, 0.009976 * c / d
    out.append({"week_start": dt.datetime.fromtimestamp(wk * WEEK, tz=dt.timezone.utc).strftime("%Y-%m-%d"), "exact_usd": ex, "swap_only_usd": so, "ratio": ex / so})
    print(f"{out[-1]['week_start']:<12}{ex:>18,.0f}{so:>14,.0f}{ex / so:>8.3f}")
tot_e, tot_s = sum(x["exact_usd"] for x in out), sum(x["swap_only_usd"] for x in out)
print("all full weeks: exact / swap-only =", round(tot_e / tot_s, 4))
dump({"weeks": out, "ratio_all_full_weeks": tot_e / tot_s, "method": "active liquidity rebuilt from Mint/Burn between swaps versus the last swap's liquidity held constant"}, f"q7_check_inrange_{WHICH}.json")
