"""Step 3: the 10,000-block header grid to END -> headers.csv.gz. It starts at 27,500,000 or, when a v4 pool that
gets a swap dataset was initialised earlier, at the grid block at or below that Initialize (or at --from=<block>).

Existing on-grid headers are copied from /Users/joshua/Peridot/evidence-cache/headers.json (read only) into our own
cache; the missing part is fetched with eth_getBlockByNumber. Columns: block,timestamp.
"""
from __future__ import annotations

import sys

from rh_lib import *  # noqa: F401,F403


def main():
    end = load_end()["END"]
    lo = GRID_START
    for a in sys.argv[1:]:
        if a.startswith("--from="):
            lo = (int(a.split("=")[1]) // GRID) * GRID
    # the v4 datasets need timestamps from their pools' first swap: extend the grid down to the earlier Initialize of
    # the known NVDA pool and of the most-swapped SPY pool (as counted at discovery)
    disc = jload(DISCOVERY_FILE)
    cand = [p for p in disc["v4_pools"] if p["pool_id"].lower() == V4_NVDA_POOL_ID.lower()]
    spy = [p for p in disc["v4_pools"] if p["symbol"] == "SPY" and p.get("swaps_upto_discovery", 0) > 0]
    if spy:
        cand.append(max(spy, key=lambda p: p["swaps_upto_discovery"]))
    for p in cand:
        lo = min(lo, (p["init_block"] // GRID) * GRID)
    u3 = jload(STATE / "univ3_discovery.json")
    for p in u3["pools"]:
        lo = min(lo, (p["created_block"] // GRID) * GRID)
    r = rpc()
    ti = time_index(r)
    before = len(ti.grid)
    ti.ensure(lo, end, progress=say)
    grid = {k: v for k, v in ti.grid.items() if k % GRID == 0 and k <= end}
    lo_have = min(grid)
    blocks = list(range(lo_have, end + 1, GRID))
    missing = [b for b in blocks if b not in grid]
    if missing:
        raise SystemExit(f"header grid has {len(missing)} holes, first {missing[:5]}")
    # END's own header must equal the pinned END timestamp
    e = load_end()
    if grid[end][0] != e["timestamp"]:
        raise SystemExit("grid timestamp at END differs from END.json")
    info = write_csv_gz("headers.csv.gz", ["block", "timestamp"], ((b, grid[b][0]) for b in blocks))
    non_monotonic = sum(1 for a, b in zip(blocks, blocks[1:]) if grid[b][0] < grid[a][0])
    info.update({"grid_step": GRID, "first_block": blocks[0], "last_block": blocks[-1], "fetched_now": len(ti.grid) - before,
                 "copied_from_existing_cache": before, "non_monotonic_steps": non_monotonic})
    jdump(STATE / "headers_info.json", info)
    say(info)


if __name__ == "__main__":
    main()
