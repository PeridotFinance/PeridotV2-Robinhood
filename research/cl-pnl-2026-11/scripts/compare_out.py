"""Compare two out/ directories number by number (used to check that run_all.sh reproduces the committed results).
Usage: compare_out.py DIR_A DIR_B [--tol 1e-6]"""
import json
import math
import sys
from pathlib import Path

IGNORE = {"seconds"}


def walk(a, b, path, diffs, tol):
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k in IGNORE:
                continue
            if k not in a or k not in b:
                diffs.append((path + "/" + k, "missing on one side")); continue
            walk(a[k], b[k], path + "/" + k, diffs, tol)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            diffs.append((path, f"length {len(a)} vs {len(b)}")); return
        for i, (x, y) in enumerate(zip(a, b)):
            walk(x, y, f"{path}[{i}]", diffs, tol)
    elif isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        if a != b and not (math.isnan(a) and math.isnan(b)) and abs(a - b) > tol * max(1.0, abs(a), abs(b)):
            diffs.append((path, f"{a} vs {b}"))
    elif a != b:
        diffs.append((path, f"{str(a)[:40]} vs {str(b)[:40]}"))


def main():
    A, B = Path(sys.argv[1]), Path(sys.argv[2])
    tol = float(sys.argv[sys.argv.index("--tol") + 1]) if "--tol" in sys.argv else 1e-6
    fa, fb = {p.name for p in A.glob("*.json")}, {p.name for p in B.glob("*.json")}
    print(f"{len(fa)} files in {A}, {len(fb)} in {B}; only in first: {sorted(fa - fb)}; only in second: {sorted(fb - fa)}")
    bad = 0
    for name in sorted(fa & fb):
        diffs = []
        walk(json.loads((A / name).read_text()), json.loads((B / name).read_text()), "", diffs, tol)
        if diffs:
            bad += 1
            print(f"DIFF {name}: {len(diffs)} values, first: {diffs[:3]}")
        else:
            print(f"same {name}")
    print(f"{len(fa & fb) - bad} of {len(fa & fb)} common files identical within {tol:g}")


if __name__ == "__main__":
    main()
