import json, sys
def load(p): return {r["name"]: r for r in map(json.loads, open(p))}
def pivot(pair, scen, key="excess_apr_pct"):
    d = load(f"results/bt_{pair}_{scen}.jsonl")
    print(f"-- {pair} {scen}: {key}  (rows core half-width ticks; columns wide half-width / core weight)")
    cols = [(w, k) for w in (300, 500, 800) for k in (50, 70, 85)]
    print("core\\wide/k " + " ".join(f"{w}/{k:>2}" .rjust(8) for w, k in cols))
    for c in (50, 100, 200):
        print(f"{c:>10}  " + " ".join(("%8.1f" % d[f"dual_c{c}_w{w}_k{k}"][key]) for w, k in cols))
    for n in ("single_pm1", "single_pm5", "full_range"):
        print(f"{n:>12} {d[n][key]:8.1f}")
for pair in ("SPY", "GLD"):
    for scen in ("full_rebal", "last28_rebal", "full_norebal"):
        pivot(pair, scen)
