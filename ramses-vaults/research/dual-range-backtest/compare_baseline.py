"""Compare a freshly computed results/baseline_<PAIR>[_caps].json with the committed evidence copy: every numeric result must agree.

  python3 compare_baseline.py SPY caps"""
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).parent


def walk(a, b, path, bad):
    if isinstance(a, dict) and isinstance(b, dict):
        for k in a:
            if k in b:
                walk(a[k], b[k], f"{path}/{k}", bad)
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for i, (x, y) in enumerate(zip(a, b)):
            walk(x, y, f"{path}[{i}]", bad)
    elif isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        if not math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9):
            bad.append((path, a, b))


def main(pair, profile):
    suffix = "_caps" if profile == "caps" else ""
    new = json.loads((HERE / "results" / f"baseline_{pair}{suffix}.json").read_text())
    old = json.loads((HERE / "evidence" / f"baseline_{pair}{suffix}.json").read_text())
    key = lambda r: (r["run"], r["window"], r["capital"])
    om = {key(r): r["result"] for r in old["results"]}
    nm = {key(r): r["result"] for r in new["results"]}
    if set(om) != set(nm):
        print("FAIL: different set of runs", set(om) ^ set(nm))
        return 1
    bad = []
    for k in om:
        walk(nm[k], om[k], str(k), bad)
    print(f"{pair} {profile}: {len(om)} runs compared, {len(bad)} numeric differences")
    for b in bad[:10]:
        print("  ", b)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
