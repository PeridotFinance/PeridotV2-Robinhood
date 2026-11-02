#!/usr/bin/env python3
"""Mutation check of the verification code itself: every mutation of bundle.py / replay_check.py / verify_replays.py must make its tests fail.
Source files are always restored."""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
CASES = [
    # (file, tests, name, old, new)
    ("bundle.py", "test_bundle", "required small inputs taken from the manifest", "    for f in REQUIRED_SMALL_INPUTS:\n        spec = listed.get(f)", "    for f in listed:\n        spec = listed.get(f)"),
    ("bundle.py", "test_bundle", "required event streams taken from the manifest", "    spec = m[\"pairs\"][pair]\n    for k in REQUIRED_EVENT_STREAMS:\n        if k not in raw:", "    spec = m[\"pairs\"][pair]\n    for k in (spec.get(\"events\") or {}):\n        if k not in raw:"),
    ("bundle.py", "test_bundle", "stream signature/topic not checked", "if e.get(\"signature\") != SIGS[k] or e.get(\"topic0\") != T(SIGS[k]):", "if False:"),
    ("bundle.py", "test_bundle", "pool address not checked", "if spec.get(\"pool\", \"\").lower() != ADDR[pair][\"pool\"].lower():", "if False:"),
    ("bundle.py", "test_bundle", "non-canonical small-input path accepted", "if spec.get(\"committedAt\") != f\"{SMALL_INPUT_DIR}/{f}\":", "if False:"),
    ("bundle.py", "test_bundle", "rebuild ignores the manifest structure", "    if bad:\n        print(\"MANIFEST DOES NOT DESCRIBE ALL REQUIRED EVENT STREAMS\")\n        return 5", "    pass"),
    ("bundle.py", "test_bundle", "verify ignores the manifest structure", "    if bad:\n        rc |= 5", "    if False:\n        rc |= 5"),
    ("bundle.py", "test_bundle", "raw stream missing tolerated", "            print(f\"  FAIL {k}: stream MISSING from the raw file\")\n            ok = False\n            continue", "            continue"),
    ("bundle.py", "test_bundle", "missing manifest stream tolerated in raw check", "            print(f\"  FAIL {k}: stream missing from the manifest\")\n            ok = False\n            continue", "            continue"),
    ("bundle.py", "test_bundle", "small input not listed tolerated", "            print(f\"  FAIL required input {f}: not listed in the manifest\")\n            ok = False\n            continue", "            continue"),
    ("replay_check.py", "test_replay_check", "duplicate rows tolerated", "            errors.append(f\"duplicate row {key}\")\n            continue", "            continue"),
    ("replay_check.py", "test_replay_check", "missing rows tolerated", "            errors.append(f\"missing row {key}\")", "            pass"),
    ("replay_check.py", "test_replay_check", "unexpected rows tolerated", "            errors.append(f\"unexpected row {key}\")", "            pass"),
    ("replay_check.py", "test_replay_check", "requested amount not compared", "        if r[\"requestedIn\"] != e[\"requested\"]:", "        if False:"),
    ("replay_check.py", "test_replay_check", "lower bound accepts less output", "ok = r[\"consumed\"] >= e[\"consumed\"] - max(2, TOL_REL * e[\"consumed\"]) and r[\"out\"] >= e[\"out\"] - max(2, TOL_REL * e[\"out\"])", "ok = True"),
    ("replay_check.py", "test_replay_check", "price after swap not compared", "                      and abs(r[\"sqrtAfter\"] / Q96 - e[\"sqrtAfter\"]) <= 1e-9 * e[\"sqrtAfter\"])", "                      )"),
    ("verify_replays.py", "test_replay_check", "snapshot sha not checked", "    if PQ.file_sha256(snap) != doc.get(\"inputsSha256\"):\n        errs.append(\"snapshot sha256 differs from the recorded one\")", "    pass"),
    ("verify_replays.py", "test_replay_check", "snapshot pin not checked", "    if inputs[\"pin\"][\"l2Block\"] != pin.get(\"l2Block\") or inputs[\"pin\"][\"hash\"] != pin.get(\"hash\"):\n        errs.append(\"snapshot pin differs from the proof's pin\")", "    pass"),
    ("verify_replays.py", "test_replay_check", "snapshot file name not checked", "        errs.append(\"snapshot file name does not carry the proof's block-hash\")", "        pass"),
    ("verify_replays.py", "test_replay_check", "forge log hash not checked", "        if PQ.file_sha256(logp) != doc.get(\"forgeLogSha256\"):\n            errs.append(\"forge log sha256 differs from the recorded one\")", "        pass"),
    ("verify_replays.py", "test_replay_check", "stored rows not compared with the log", "        if RP.parse_rows(logp.read_text()) != doc.get(\"forkRows\"):\n            errs.append(\"stored fork rows differ from the rows in the forge log\")", "        pass"),
    ("verify_replays.py", "test_replay_check", "row count not checked", "    if len(doc.get(\"forkRows\", [])) != doc.get(\"expectedRowCount\"):\n        errs.append(\"row count differs from the expected count\")", "    pass"),
]


def run(test):
    r = subprocess.run([sys.executable, "-m", "unittest", test], cwd=HERE, capture_output=True, text=True)
    return r.returncode


def main():
    originals = {}
    survivors = []
    try:
        for f, test, name, old, new in CASES:
            path = HERE / f
            if f not in originals:
                originals[f] = path.read_text()
            orig = originals[f]
            n = orig.count(old)
            if n != 1:
                print(f"ANCHOR {n}x: {name}")
                survivors.append(name + " (anchor)")
                continue
            path.write_text(orig.replace(old, new))
            killed = run(test) != 0
            path.write_text(orig)
            print(("KILLED   " if killed else "SURVIVED ") + name, flush=True)
            if not killed:
                survivors.append(name)
    finally:
        for f, txt in originals.items():
            (HERE / f).write_text(txt)
    print("all mutations killed" if not survivors else f"SURVIVORS: {survivors}")
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
