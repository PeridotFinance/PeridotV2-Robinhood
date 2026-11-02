import json, sys
pair = sys.argv[1]
def load(s): return [json.loads(l) for l in open(f"results/bt_{pair}_{s}.jsonl")]
names = []
for scen in ("full_rebal", "full_norebal"):
    rows = load(scen)
    dual = [r for r in rows if r["name"].startswith("dual")]
    dual.sort(key=lambda r: -r["pnl_vs_hold"])
    names += [r["name"] for r in dual[:2]]
names += ["dual_c100_w500_k70", "single_pm1", "single_pm5", "full_range"]
seen = []
for n in names:
    if n not in seen: seen.append(n)
print(",".join(seen))
