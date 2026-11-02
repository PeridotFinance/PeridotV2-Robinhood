"""Label every row of the historical (pre-feasibility) backtest outputs as contract-feasible or HYPOTHETICAL, with the reasons.

The historical files are preserved unchanged; this writes evidence/historical-classification.json and prints a summary.
Their configurations used the engine's old defaults: fractional trigger 0.70 x core half-width (0.60 x wide), cooldown 900 s,
no reserve cut, an unvalidated tick spacing of 1 for every pool (NVDA's is 10) and a full range one tick narrower than the
contract's. ``rebal`` scenarios swap at recenter, which the contract does not do.

  python3 classify_historical.py [results_dir]
"""
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

import feasibility as FZ
from common import HERE, RESULTS

SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else RESULTS
OUT = HERE / "evidence" / "historical-classification.json"
PATTERNS = ["bt_*_*.jsonl", "fin_*.jsonl", "cap_*.jsonl", "stress_*.jsonl"]


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def rows_of(path):
    for line in Path(path).read_text().splitlines():
        if line.strip():
            yield json.loads(line)


def classify_row(pair, fname, row):
    cfg = dict(row.get("cfg") or {})
    job = row.get("job") or {}
    over = job.get("over") or {}
    name = row.get("name") or job.get("name")
    tag = row.get("tag", "")
    rebal = bool(over.get("rebalance")) or ("rebal" in fname and "norebal" not in fname) or (tag.endswith("|rebal"))
    if not cfg:  # finalist/capacity rows: reconstruct core/wide half-widths from the name
        import run_finalists as RF
        p = RF.parse(name)
        cfg = {"core_half": p.get("core_half", 0), "wide_half": p.get("wide_half", 0), "full_range": p.get("full_range", False),
               "cooldown": over.get("cooldown", 900.0), "max_per_day": over.get("max_per_day", 24)}
    cfg["rebalance"] = rebal
    cfg.setdefault("cooldown", 900.0)
    cfg.setdefault("max_per_day", 24)
    f = FZ.legacy_result_feasibility(name, cfg, FZ.POOL_SPACING.get(pair, 1))
    return {"name": name, "tag": tag, "rebalanceSwap": rebal, "coreHalf": cfg.get("core_half"), "wideHalf": cfg.get("wide_half"), "cooldown": cfg.get("cooldown"),
            "contractFeasible": f.contract_feasible, "mode": f.mode, "reasons": f.violations + f.hypothetical,
            "excess_apr_pct": row.get("excess_apr_pct"), "excess_pct": row.get("excess_pct")}


def main():
    out = {"schema": "ramses-historical-classification-1", "label": "HYPOTHETICAL unless contractFeasible is true",
           "note": "feasibility judged against RamsesRangeLib.validatePolicy at the pool's actual spacing; historical numbers are preserved, not corrected", "files": {}}
    summary = Counter()
    for pat in PATTERNS:
        for path in sorted(SRC.glob(pat)):
            pair = "GLD" if "GLD" in path.name else "SPY" if "SPY" in path.name else None
            if pair is None:
                continue
            rows = [classify_row(pair, path.name, r) for r in rows_of(path)]
            out["files"][path.name] = {"sha256": sha(path), "rows": len(rows), "feasible": sum(r["contractFeasible"] for r in rows), "classified": rows}
            summary[path.name] = sum(r["contractFeasible"] for r in rows)
    wider = SRC / "wider_SPY.json"
    if wider.exists():
        rows = json.loads(wider.read_text())
        cl = []
        for r in rows:
            name = r["name"]
            import run_finalists as RF
            p = RF.parse(name)
            f = FZ.legacy_result_feasibility(name, {"core_half": p["core_half"], "wide_half": p.get("wide_half", 0), "cooldown": 900.0, "max_per_day": 24, "rebalance": True}, 1)
            cl.append({"name": name, "tag": r.get("tag"), "contractFeasible": f.contract_feasible, "reasons": f.violations + f.hypothetical, "excess_apr_pct": r.get("excess_apr_pct")})
        out["files"]["wider_SPY.json"] = {"sha256": sha(wider), "rows": len(cl), "feasible": sum(c["contractFeasible"] for c in cl), "classified": cl}
    out["totals"] = {"rows": sum(v["rows"] for v in out["files"].values()), "contractFeasibleRows": sum(v["feasible"] for v in out["files"].values())}
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1))
    print(json.dumps(out["totals"]))
    for k, v in out["files"].items():
        print(f"{k:34s} rows {v['rows']:3d} feasible {v['feasible']}")


if __name__ == "__main__":
    main()
