"""Turn the release's JSON datasets back into the pickles the backtest scripts load, VERIFYING each against bundle/manifest-v1.json first.

  python3 unpack_datasets.py            reads datasets/dataset_<PAIR>.json.gz, writes results/dataset_<PAIR>.pkl + the small inputs

JSON is used in the release instead of pickle on purpose: unpickling a downloaded file executes code. A dataset whose canonical column hash
does not match the manifest is refused (non-zero exit), and a missing dataset file is a failure, not a skip."""
import gzip
import json
import pickle
import shutil
import sys
from pathlib import Path

import bundle as B

HERE = Path(__file__).parent
RES = HERE / "results"


def main(pairs):
    m = B.load_manifest()
    RES.mkdir(exist_ok=True)
    rc = 0
    for pair in pairs:
        f = HERE / "datasets" / f"dataset_{pair}.json.gz"
        if not f.exists():
            print(f"FAIL {pair}: {f} MISSING")
            rc |= 1
            continue
        doc = json.loads(gzip.open(f, "rt").read())
        cols, meta = doc["cols"], doc["meta"]
        if B.dataset_hash(cols) != m["pairs"][pair]["dataset"]["sha256"]:
            print(f"FAIL {pair}: dataset hash does not match the manifest")
            rc |= 2
            continue
        pickle.dump((cols, meta), open(RES / f"dataset_{pair}.pkl", "wb"))
        print(f"OK   {pair}: {len(cols['block'])} swaps verified against the manifest")
    for name, spec in m["smallInputs"].items():
        src = HERE / spec["committedAt"]
        if not src.exists() or B.file_sha(src) != spec["sha256"]:
            print(f"FAIL small input {name} missing or modified")
            rc |= 4
        else:
            shutil.copyfile(src, RES / name)
    ps = HERE / "evidence" / "pools_static.json"
    if ps.exists():
        shutil.copyfile(ps, RES / "pools_static.json")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["SPY", "GLD", "NVDA"]))
