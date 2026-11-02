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
    doc = json.loads((HERE / "evidence" / "pools" / "pool-quotes.json").read_text())
    bad = 0
    for name, rec in doc["pools"].items():
        inp = json.loads((HERE / "evidence" / "pools" / rec["inputsFile"]).read_text())
        q = QM.quote_all(inp, USDG, SIZES)
        same = q["rows"] == rec["quotes"] and QM.depth(inp, USDG) == rec["depth"]
        print(("OK   " if same else "FAIL ") + name)
        bad += 0 if same else 1
    return bad


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--check":
        sys.exit(check())
    show(sys.argv[1])
