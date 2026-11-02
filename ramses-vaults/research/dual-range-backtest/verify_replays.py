"""Re-verify every committed fork-replay proof WITHOUT the network.

  python3 verify_replays.py            exit 0 only if every proof checks out

For each evidence/pools/fork-replay/<pool>@<block>.json: the bound snapshot exists and its sha256 equals the recorded one; the snapshot's pin
(block and hash) equals the proof's pin; the raw forge log exists, its sha256 matches, and the REPLAY rows parsed from it equal the stored fork rows;
and the strict row check (replay_check.check_rows: none missing, duplicated or unexpected; every number within rounding of the offline walk recomputed
from the snapshot) passes. Also: every pool in pool-quotes.json that claims a proof is bound to a proof for the SAME snapshot."""
import json
import sys
from pathlib import Path

import pool_quote as PQ
import replay_check as RC
import replay_pool as RP

POOLS_DIR = PQ.POOLS_DIR


def verify_doc(path):
    errs = []
    doc = json.loads(Path(path).read_text())
    snap = POOLS_DIR / doc.get("inputsFile", "MISSING")
    if not snap.exists():
        return [f"snapshot missing: {doc.get('inputsFile')}"]
    if PQ.file_sha256(snap) != doc.get("inputsSha256"):
        errs.append("snapshot sha256 differs from the recorded one")
    inputs = json.loads(snap.read_text())
    pin = doc.get("pin", {})
    if inputs["pin"]["l2Block"] != pin.get("l2Block") or inputs["pin"]["hash"] != pin.get("hash"):
        errs.append("snapshot pin differs from the proof's pin")
    if not snap.name.startswith(f"{pin.get('l2Block')}-{str(pin.get('hash', ''))[2:10]}"):
        errs.append("snapshot file name does not carry the proof's block-hash")
    logp = POOLS_DIR / doc.get("forgeLogFile", "MISSING")
    if not logp.exists():
        errs.append("forge log missing")
    else:
        if PQ.file_sha256(logp) != doc.get("forgeLogSha256"):
            errs.append("forge log sha256 differs from the recorded one")
        if RP.parse_rows(logp.read_text()) != doc.get("forkRows"):
            errs.append("stored fork rows differ from the rows in the forge log")
    _report, row_errs = RC.check_rows(inputs, doc["usdg"], doc["sizesUsd"], doc["limitPct"], doc.get("forkRows", []))
    errs += row_errs
    if len(doc.get("forkRows", [])) != doc.get("expectedRowCount"):
        errs.append("row count differs from the expected count")
    return errs


def main():
    bad = 0
    docs = sorted((POOLS_DIR / "fork-replay").glob("*@*.json"))
    if not docs:
        print("FAIL no fork-replay proofs found")
        return 1
    proofs = {}
    for d in docs:
        errs = verify_doc(d)
        print(("OK   " if not errs else "FAIL ") + d.name + ("" if not errs else ": " + "; ".join(errs[:4])))
        bad += 1 if errs else 0
        proofs[str(d.relative_to(POOLS_DIR))] = json.loads(d.read_text())
    q = json.loads((POOLS_DIR / "pool-quotes.json").read_text())
    for name, rec in q["pools"].items():
        rf = rec.get("forkReplayFile")
        if not rf or rf not in proofs:
            print(f"FAIL {name}: no fork-replay proof bound to the published quote")
            bad += 1
        elif proofs[rf]["inputsSha256"] != rec["inputsSha256"] or proofs[rf]["inputsFile"] != rec["inputsFile"]:
            print(f"FAIL {name}: published quote and its proof use different snapshots")
            bad += 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
