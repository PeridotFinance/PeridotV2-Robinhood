"""Fetch every log of one contract over [START, END] into the per-chunk cache (resumable).

usage: python3 avax_01_fetch_logs.py <dataset> [source] [chunk]
  dataset: pool | pool_second | gauge | gauge_second | feed | feed_second | phar_pool | phar_pool_second |
           phar_cl_pool | phar_cl_pool_second | legacy_factory
           (addresses come from data/avalanche/state_at_end.json / phar_pools.json; a *_second dataset is the same
           contract fetched again from another endpoint on a different block grid, used only by the checks)
  source : tenderly (default) | publicnode
  chunk  : blocks per request (default 20000)

pool, gauge, phar_cl_pool and legacy_factory are fetched with no topic filter, so every event type the contract emitted
in the window is on disk and the CSV builder can count logs per topic0. Two datasets are topic-filtered: feed
(AnswerUpdated only) and phar_pool (Swap and Sync only). Read-only (eth_getLogs).

The run that produced the published datasets (2026-11-10):
  pool            tenderly 20000   blocks 91716610..99816609 (405 windows), then the gateway's daily egress allowance ran
                                   out (HTTP 429) and the same command with `publicnode 20000` served 99816610..99924388
  pool_second     publicnode 33333
  gauge, legacy_factory, phar_pool, phar_cl_pool, feed      publicnode 50000
"""
from __future__ import annotations

import sys
import time

from avax_lib import DATA_AVAX, POOL, TOPIC, fetch_logs, ledger_rows, read_json


def dataset_address(name: str) -> tuple[str, list | None]:
    """(address, topics filter or None)."""
    st = read_json(DATA_AVAX / "state_at_end.json")["decoded"]
    base = name[:-7] if name.endswith("_second") else name
    if base == "pool":
        return POOL, None
    if base == "gauge":
        return st["gauge"]["gauge"], None
    if base == "feed":            # the aggregator behind the proxy; only AnswerUpdated is kept
        return st["chainlink_avax_usd"]["aggregator()"], [TOPIC["AnswerUpdated"]]
    if base == "phar_pool":       # Solidly-style pair: Swap and Sync only
        return read_json(DATA_AVAX / "phar_pools.json")["chosen"]["address"], [[TOPIC["SwapV2"], TOPIC["Sync"]]]
    if base == "phar_cl_pool":
        return read_json(DATA_AVAX / "phar_pools.json")["cl_reference"]["address"], None
    if base == "legacy_factory":  # fee-setting events of the legacy pair factory (few)
        return st["gauge"]["voter.legacyFactory()"], None
    raise SystemExit(f"unknown dataset {name}")


def main() -> None:
    name = sys.argv[1]
    source = sys.argv[2] if len(sys.argv) > 2 else "tenderly"
    chunk = int(sys.argv[3]) if len(sys.argv) > 3 else 20000
    end = read_json(DATA_AVAX / "END.json")
    a, b = end["start"]["number"], end["end"]["number"]
    addr, topics = dataset_address(name)
    t = time.time()
    print(f"{name}: {addr} blocks {a}..{b} via {source}, chunk {chunk}", flush=True)
    logs = fetch_logs(name, addr, a, b, source=source, chunk=chunk, topics=topics, log=lambda s: print(s, flush=True))
    rows = ledger_rows(name)
    print(f"{name}: {len(logs)} logs in {len(rows)} cached windows, {time.time() - t:.0f}s", flush=True)
    # the windows must tile [a, b] exactly
    pos = a
    for r in rows:
        if r["from"] != pos:
            raise SystemExit(f"gap or overlap at {pos}: next window starts {r['from']}")
        pos = r["to"] + 1
    if pos != b + 1:
        raise SystemExit(f"windows end at {pos - 1}, expected {b}")
    print(f"{name}: windows tile {a}..{b} with no gap; ledger sum = {sum(r['count'] for r in rows)}", flush=True)


if __name__ == "__main__":
    main()
