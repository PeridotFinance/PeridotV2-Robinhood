"""Offline replay of a saved pinned pool-inputs file. No network access.

  python3 quote_offline.py evidence/pools/inputs/<slug>.json            print the quotes
  python3 quote_offline.py --check                                       recompute every saved inputs file and compare with evidence/pools/pool-quotes.json
"""
import json
import sys
from pathlib import Path

import quote_math as QM

HERE = Path(__file__).parent
USDG = "0x5fc5360d0400a0fd4f2af552add042d716f1d168"
SIZES = [50, 250, 1_000, 5_000, 10_000, 50_000]


def show(path):
    inp = json.loads(Path(path).read_text())
    q = QM.quote_all(inp, USDG, SIZES)
    print(f"{inp['pool']} pin {inp['pin']['l2Block']} fee {inp['fee_ppm']} ppm spacing {inp['tickSpacing']} scan {inp['scan']['mode']}")
    for row in q["rows"]:
        f = lambda x: x["status"] if x["costBps"] is None else f"{x['costBps']:.1f} bp"
        print(f"  ${row['sizeUsd']:>6}  buy {f(row['buy']):<24} sell {f(row['sell']):<24} sequential round trip {f(row['sequentialRoundTrip'])}")
    return q


def check():
    """Recompute every published quote from its saved snapshot; verify the snapshot's sha256, pin and (where present) its fork-replay binding."""
    import hashlib
    pools_dir = HERE / "evidence" / "pools"
    doc = json.loads((pools_dir / "pool-quotes.json").read_text())
    bad = 0
    for name, rec in doc["pools"].items():
        f = pools_dir / rec["inputsFile"]
        problems = []
        if not f.exists():
            problems.append("snapshot MISSING")
        else:
            raw = f.read_bytes()
            if hashlib.sha256(raw).hexdigest() != rec["inputsSha256"]:
                problems.append("snapshot sha256 mismatch")
            inp = json.loads(raw)
            if inp["pin"]["l2Block"] != rec["pinL2Block"] or inp["pin"]["hash"] != rec["pinHash"]:
                problems.append("snapshot pin differs from the quote's pin")
            q = QM.quote_all(inp, USDG, SIZES)
            if q["rows"] != rec["quotes"] or QM.depth(inp, USDG) != rec["depth"]:
                problems.append("recomputed quotes differ")
        if rec.get("forkReplayFile"):
            rp = pools_dir / rec["forkReplayFile"]
            rd = json.loads(rp.read_text()) if rp.exists() else {}
            if rd.get("inputsSha256") != rec["inputsSha256"] or rd.get("inputsFile") != rec["inputsFile"]:
                problems.append("fork replay is not bound to this snapshot")
        print(("OK   " if not problems else "FAIL ") + name + ("" if not problems else ": " + "; ".join(problems)))
        bad += 1 if problems else 0
    return bad


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--check":
        sys.exit(check())
    show(sys.argv[1])
