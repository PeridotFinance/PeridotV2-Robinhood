"""Versioned, hash-verified recovery of the backtest inputs from the public chain (no hosted copy is needed or assumed).

The raw pool event logs (~810 MB for SPY, GLD, NVDA) and the pickled datasets (~60 MB) are too large for Git and are NOT committed.
They are immutable chain data, so the bundle is a MANIFEST that pins, per pool, the block range, the event signatures and a SHA-256
over the canonical log set (and over the built dataset columns). Anyone can rebuild the inputs from the RPC and verify them:

  python3 bundle.py make PAIR            hash local raw logs + dataset into evidence/bundle/manifest-v1.json (maintainer step)
  python3 bundle.py rebuild PAIR [--cache DIR] [--out DIR]
                                         fetch the logs cold from the RPC into DIR, verify every hash, build the dataset, verify it
  python3 bundle.py verify PAIR [--raw FILE]   verify an already downloaded raw file / the local dataset against the manifest

Canonical hash: for each event type, logs sorted by (blockNumber, logIndex) and serialised as compact JSON of
[blockNumber, logIndex, topics, data]. Logs at blocks above the manifest's ``toBlock`` are ignored, so later chain growth does not
matter. Small inputs that are committed (feed answers, gauge notifications) are hashed in the manifest as well.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pickle
import sys
import time
from pathlib import Path

from common import *  # noqa: F401,F403  (Rpc, T, ADDR, HERE, RESULTS, CACHE)

MANIFEST = HERE / "evidence" / "bundle" / "manifest-v1.json"
VERSION = 1
SIGS = {
    "init": "Initialize(uint160,int24)",
    "swap": "Swap(address,address,int256,int256,uint160,uint128,int24)",
    "mint": "Mint(address,address,uint256,int24,int24,uint128,uint256,uint256)",
    "burn": "Burn(address,int24,int24,uint128,uint256,uint256)",
    "collect": "Collect(address,address,int24,int24,uint128,uint128)",
    "feeadj": "FeeAdjustment(uint24,uint24)",
    "setfee": "SetFee(uint24,uint24)",
    "proto_a": "SetFeeProtocol(uint8,uint8,uint8,uint8)",
    "proto_b": "SetFeeProtocol(uint24,uint24)",
    "proto_c": "SetFeeProtocol(uint24)",
    "collectproto": "CollectProtocol(address,address,uint128,uint128)",
    "flash": "Flash(address,address,uint256,uint256,uint256,uint256)",
}
DATASET_COLS = ("block", "idx", "ts", "s_after", "tick_after", "L_after", "in_tok", "in_amt", "fee_ppm", "proto", "a0", "a1")
SMALL_INPUTS = ("answer_updated.json", "gauge_notify.json")
# The REQUIRED sets are fixed here, in code. They are deliberately NOT read from the manifest: a manifest that drops an input or an event
# stream (and the file with it) must fail verification, not pass with a smaller set.
REQUIRED_SMALL_INPUTS = SMALL_INPUTS
REQUIRED_EVENT_STREAMS = tuple(SIGS)
SMALL_INPUT_DIR = "evidence/bundle/inputs"


def canon_hash(logs, to_block):
    keep = sorted((l for l in logs if l["blockNumber"] <= to_block), key=lambda l: (l["blockNumber"], l["logIndex"]))
    h = hashlib.sha256()
    for l in keep:
        h.update(json.dumps([l["blockNumber"], l["logIndex"], list(l["topics"]), l["data"]], separators=(",", ":")).encode())
        h.update(b"\n")
    return len(keep), h.hexdigest()


def dataset_hash(cols):
    h = hashlib.sha256()
    for k in DATASET_COLS:
        h.update(k.encode())
        h.update(json.dumps(cols[k], separators=(",", ":")).encode())
    return h.hexdigest()


def file_sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest():
    return json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {"schema": "ramses-input-bundle", "version": VERSION, "pairs": {}, "smallInputs": {}}


def save_manifest(m):
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(m, indent=1, sort_keys=True))


def make(pair):
    raw = json.loads((CACHE.parent / f"ramses-group-pool-{pair}.json").read_text())
    cols, meta = pickle.load(open(RESULTS / f"dataset_{pair}.pkl", "rb"))
    head = raw["head"]
    ev = {}
    for k, sig in SIGS.items():
        n, hx = canon_hash(raw[k], head)
        ev[k] = {"signature": sig, "topic0": T(sig), "count": n, "sha256": hx}
    m = load_manifest()
    m["pairs"][pair] = {"pool": ADDR[pair]["pool"], "fromBlock": 0, "toBlock": head, "events": ev,
                        "dataset": {"swaps": len(cols["block"]), "columns": list(DATASET_COLS), "sha256": dataset_hash(cols),
                                    "gridLimitBlock": meta.get("grid_limit_block"), "swapsDroppedAfterGrid": meta.get("swaps_dropped_after_grid")},
                        "rawFileBytes": (CACHE.parent / f"ramses-group-pool-{pair}.json").stat().st_size}
    for f in REQUIRED_SMALL_INPUTS:
        m["smallInputs"][f] = {"sha256": file_sha(RESULTS / f), "committedAt": f"{SMALL_INPUT_DIR}/{f}"}
    m["created"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    m["note"] = ("Raw logs and pickled datasets are not committed (too large). Rebuild with `python3 bundle.py rebuild PAIR`; every hash below is verified. "
                 "The chain is the source of truth; the public RPC serves all logs but only ~9 minutes of state, so state reads are NOT recoverable and are not part of this bundle.")
    save_manifest(m)
    print("manifest updated for", pair, "toBlock", head)


def fetch_raw(pair, to_block, cache_dir):
    r = Rpc(cache_dir=Path(cache_dir), min_interval=0.3)
    a = ADDR[pair]["pool"]
    out = {"head": to_block}
    for k, sig in SIGS.items():
        t = time.time()
        out[k] = r.logs(a, [T(sig)], 0, to_block, to_block)
        print(f"  {pair} {k}: {len(out[k])} logs ({time.time() - t:.0f}s)", flush=True)
    return out, r


def manifest_violations(m, pair):
    """Structural checks of the manifest against the constants in this file (never against itself)."""
    v = []
    spec = (m.get("pairs") or {}).get(pair)
    if spec is None:
        return [f"{pair} is not in the manifest"]
    if spec.get("pool", "").lower() != ADDR[pair]["pool"].lower():
        v.append(f"{pair}: manifest pool {spec.get('pool')} != the expected pool {ADDR[pair]['pool']}")
    if not isinstance(spec.get("toBlock"), int) or spec["toBlock"] <= 0:
        v.append(f"{pair}: toBlock missing or invalid")
    ev = spec.get("events") or {}
    for k in REQUIRED_EVENT_STREAMS:
        if k not in ev:
            v.append(f"{pair}: required event stream '{k}' is missing from the manifest")
            continue
        e = ev[k]
        if e.get("signature") != SIGS[k] or e.get("topic0") != T(SIGS[k]):
            v.append(f"{pair}: event stream '{k}' has the wrong signature/topic0")
        if not isinstance(e.get("count"), int) or e["count"] < 0 or len(str(e.get("sha256", ""))) != 64:
            v.append(f"{pair}: event stream '{k}' has an invalid count/sha256")
    if len(str((spec.get("dataset") or {}).get("sha256", ""))) != 64:
        v.append(f"{pair}: dataset sha256 missing")
    return v


def verify_raw(pair, raw, m):
    """Every REQUIRED event stream must be present in the raw file and match the manifest; the stream list comes from code, not the manifest."""
    ok = True
    spec = m["pairs"][pair]
    for k in REQUIRED_EVENT_STREAMS:
        if k not in raw:
            print(f"  FAIL {k}: stream MISSING from the raw file")
            ok = False
            continue
        if k not in (spec.get("events") or {}):
            print(f"  FAIL {k}: stream missing from the manifest")
            ok = False
            continue
        n, hx = canon_hash(raw[k], spec["toBlock"])
        e = spec["events"][k]
        good = (n == e["count"] and hx == e["sha256"])
        ok &= good
        print(f"  {'OK  ' if good else 'FAIL'} {k}: {n} logs" + ("" if good else f" (manifest {e['count']})"))
    return ok


def verify_small_inputs(m):
    """The REQUIRED small inputs (fixed in code) must be listed in the manifest, exist at their canonical committed path, and match the manifest hash.
    Removing one from disk AND from the manifest fails: the required set does not come from the manifest."""
    ok = True
    listed = m.get("smallInputs") or {}
    for f in REQUIRED_SMALL_INPUTS:
        spec = listed.get(f)
        if spec is None:
            print(f"  FAIL required input {f}: not listed in the manifest")
            ok = False
            continue
        p = HERE / SMALL_INPUT_DIR / f
        if spec.get("committedAt") != f"{SMALL_INPUT_DIR}/{f}":
            print(f"  FAIL required input {f}: manifest points at a non-canonical path {spec.get('committedAt')}")
            ok = False
        elif not p.exists():
            print(f"  FAIL required input {f}: MISSING at {p}")
            ok = False
        elif file_sha(p) != spec.get("sha256"):
            print(f"  FAIL required input {f}: hash mismatch")
            ok = False
        else:
            print(f"  OK   required input {f}")
    return ok


def rebuild(pair, cache_dir, out_dir):
    m = load_manifest()
    if pair not in m.get("pairs", {}):
        print(f"FAIL: {pair} is not in the manifest")
        return 3
    spec = m["pairs"][pair]
    bad = manifest_violations(m, pair)
    for line in bad:
        print("  FAIL", line)
    if bad:
        print("MANIFEST DOES NOT DESCRIBE ALL REQUIRED EVENT STREAMS")
        return 5
    if not verify_small_inputs(m):
        print("REQUIRED SMALL INPUTS MISSING OR MODIFIED")
        return 4
    raw, r = fetch_raw(pair, spec["toBlock"], cache_dir)
    ok = verify_raw(pair, raw, m)
    if not ok:
        print("RAW LOGS DO NOT MATCH THE MANIFEST")
        return 1
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"ramses-group-pool-{pair}.json").write_text(json.dumps(raw))
    # build the dataset exactly as build_dataset.py does, from the freshly fetched logs
    import build_dataset as BD
    ti = TimeIndex(r, path=Path(cache_dir) / "headers.json")
    cols, meta = BD.build_from_raw(pair, raw, ti, limit=spec["dataset"].get("gridLimitBlock"))
    hx = dataset_hash(cols)
    good = hx == spec["dataset"]["sha256"]
    print(f"  {'OK  ' if good else 'FAIL'} dataset columns: {len(cols['block'])} swaps")
    if not good:
        return 2
    pickle.dump((cols, meta), open(out_dir / f"dataset_{pair}.pkl", "wb"))
    # an unpacked bundle is self-contained: the verified small inputs sit next to the dataset (RAMSES_RESULTS_DIR=<out_dir>)
    import shutil
    for f in REQUIRED_SMALL_INPUTS:
        shutil.copyfile(HERE / SMALL_INPUT_DIR / f, out_dir / f)
    pools = out_dir / "pools_static.json"
    if not pools.exists() and (HERE / "evidence" / "pools_static.json").exists():
        shutil.copyfile(HERE / "evidence" / "pools_static.json", pools)
    return 0


def verify(pair, raw_path, results_dir=None):
    """Offline verification. FAILS (non-zero) when a required input is missing: the dataset pickle, the manifest entry, the committed
    small inputs, and the raw log file when ``raw_path`` is given."""
    m = load_manifest()
    rc = 0
    if pair not in m.get("pairs", {}):
        print(f"  FAIL {pair} is not in the manifest")
        return 3
    bad = manifest_violations(m, pair)
    for line in bad:
        print("  FAIL", line)
    if bad:
        rc |= 5
    if raw_path:
        rp = Path(raw_path)
        if not rp.exists():
            print(f"  FAIL raw log file MISSING: {rp}")
            rc |= 1
        else:
            rc |= 0 if verify_raw(pair, json.loads(rp.read_text()), m) else 1
    ds = Path(results_dir or RESULTS) / f"dataset_{pair}.pkl"
    if not ds.exists():
        print(f"  FAIL dataset MISSING: {ds}  (rebuild it: python3 bundle.py rebuild {pair})")
        rc |= 2
    else:
        cols, _ = pickle.load(open(ds, "rb"))
        good = dataset_hash(cols) == m["pairs"][pair]["dataset"]["sha256"]
        print(f"  {'OK  ' if good else 'FAIL'} dataset {ds}")
        rc |= 0 if good else 2
    rc |= 0 if verify_small_inputs(m) else 4
    return rc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["make", "rebuild", "verify"])
    ap.add_argument("pair", choices=["SPY", "GLD", "NVDA"])
    ap.add_argument("--cache", default="/tmp/ramses-bundle-cache")
    ap.add_argument("--out", default="/tmp/ramses-bundle-out")
    ap.add_argument("--raw")
    ap.add_argument("--results-dir")
    a = ap.parse_args()
    if a.cmd == "make":
        make(a.pair)
    elif a.cmd == "rebuild":
        sys.exit(rebuild(a.pair, a.cache, a.out))
    else:
        sys.exit(verify(a.pair, a.raw, a.results_dir))


if __name__ == "__main__":
    main()
