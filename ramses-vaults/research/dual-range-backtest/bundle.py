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
    for f in SMALL_INPUTS:
        m["smallInputs"][f] = {"sha256": file_sha(RESULTS / f), "committedAt": f"evidence/bundle/inputs/{f}"}
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


def verify_raw(pair, raw, m):
    ok = True
    for k, spec in m["pairs"][pair]["events"].items():
        n, hx = canon_hash(raw[k], m["pairs"][pair]["toBlock"])
        good = (n == spec["count"] and hx == spec["sha256"])
        ok &= good
        print(f"  {'OK  ' if good else 'FAIL'} {k}: {n} logs" + ("" if good else f" (manifest {spec['count']})"))
    return ok


def rebuild(pair, cache_dir, out_dir):
    m = load_manifest()
    spec = m["pairs"][pair]
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
    pickle.dump((cols, meta), open(out_dir / f"dataset_{pair}.pkl", "wb"))
    return 0 if good else 2


def verify(pair, raw_path):
    m = load_manifest()
    rc = 0
    if raw_path:
        raw = json.loads(Path(raw_path).read_text())
        rc |= 0 if verify_raw(pair, raw, m) else 1
    ds = RESULTS / f"dataset_{pair}.pkl"
    if ds.exists():
        cols, _ = pickle.load(open(ds, "rb"))
        good = dataset_hash(cols) == m["pairs"][pair]["dataset"]["sha256"]
        print(f"  {'OK  ' if good else 'FAIL'} local dataset")
        rc |= 0 if good else 2
    for f, spec in m["smallInputs"].items():
        p = HERE / spec["committedAt"]
        good = p.exists() and file_sha(p) == spec["sha256"]
        print(f"  {'OK  ' if good else 'FAIL'} committed input {f}")
        rc |= 0 if good else 4
    return rc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["make", "rebuild", "verify"])
    ap.add_argument("pair", choices=["SPY", "GLD", "NVDA"])
    ap.add_argument("--cache", default="/tmp/ramses-bundle-cache")
    ap.add_argument("--out", default="/tmp/ramses-bundle-out")
    ap.add_argument("--raw")
    a = ap.parse_args()
    if a.cmd == "make":
        make(a.pair)
    elif a.cmd == "rebuild":
        sys.exit(rebuild(a.pair, a.cache, a.out))
    else:
        sys.exit(verify(a.pair, a.raw))


if __name__ == "__main__":
    main()
