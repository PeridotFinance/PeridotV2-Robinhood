from __future__ import annotations
"""Turn the raw pool logs into compact per-swap columns with timestamps, the fee and protocol share in force."""
import bisect, json, pickle, sys
from common import *
from strategy_data import StepFunction, fee_function

Q96 = 2 ** 96

def build(name: str, ti: TimeIndex):
    raw = json.load(open(CACHE.parent / f"ramses-group-pool-{name}.json"))
    return build_from_raw(name, raw, ti)


def build_from_raw(name: str, raw: dict, ti: TimeIndex, limit: int | None = None):
    """Pure function of the raw logs and the (immutable) header grid; used by bundle.py to rebuild datasets from a cold fetch."""
    c = ADDR[name]
    stock0 = c["stock_is_token0"]
    # fee in force (dynamic) and protocol share in force
    fees = fee_function(raw["feeadj"], 0)
    proto_ev = sorted(raw["proto_b"], key=lambda l: (l["blockNumber"], l["logIndex"]))
    proto_changes = [(l["blockNumber"], l["logIndex"], words(l["data"])[1] / 1e6) for l in proto_ev]
    proto_init = words(proto_ev[0]["data"])[0] / 1e6 if proto_ev else 0.5
    proto = StepFunction(proto_init, proto_changes)
    cols = {k: [] for k in ("block", "idx", "ts", "s_after", "tick_after", "L_after", "in_tok", "in_amt", "fee_ppm", "proto", "a0", "a1")}
    # real headers on the grid around every swap; swaps after the newest grid anchor that exists on chain are dropped
    if limit is None:
        limit = (ti.rpc.head() // ti.step) * ti.step
    swaps = [l for l in raw["swap"] if l["blockNumber"] <= limit]
    if swaps:
        ti.ensure(swaps[0]["blockNumber"], min(swaps[-1]["blockNumber"], limit - 1))
    dropped_tail = len(raw["swap"]) - len(swaps)
    for l in swaps:
        w = words(l["data"])
        a0, a1 = s256(w[0]), s256(w[1])
        b, i = l["blockNumber"], l["logIndex"]
        cols["block"].append(b); cols["idx"].append(i)
        cols["ts"].append(ti.ts(b))
        cols["s_after"].append(w[2] / Q96)
        cols["tick_after"].append(sN(w[4], 24))
        cols["L_after"].append(float(w[3]))
        tok = 0 if a0 > 0 else 1
        cols["in_tok"].append(tok)
        cols["in_amt"].append(float(a0 if tok == 0 else a1))
        cols["fee_ppm"].append(fees(b, i))
        cols["proto"].append(proto(b, i))
        cols["a0"].append(float(a0)); cols["a1"].append(float(a1))
    init_w = words(raw["init"][0]["data"]) if raw["init"] else None
    meta = {"name": name, "n_swaps": len(cols["block"]), "pool_init_block": raw["init"][0]["blockNumber"] if raw["init"] else None,
            "proto_init": proto_init, "proto_changes": [(b, v) for b, _, v in proto_changes], "head": raw["head"],
            "fee_changes": [(l["blockNumber"], words(l["data"])[1]) for l in raw["feeadj"]],
            "first_fee": fees.initial, "grid_limit_block": limit, "swaps_dropped_after_grid": dropped_tail}
    return cols, meta

if __name__ == "__main__":
    r = rpc()
    ti = TimeIndex(r, path=CACHE / "headers.json")
    for name in (sys.argv[1:] or ["SPY", "GLD", "NVDA"]):
        cols, meta = build(name, ti)
        meta["time_index_error"] = ti.measure_error(cols["block"][0], cols["block"][-1], samples=25)
        pickle.dump((cols, meta), open(RESULTS / f"dataset_{name}.pkl", "wb"))
        print(name, meta["n_swaps"], "first ts", iso(int(cols["ts"][0])), "last", iso(int(cols["ts"][-1])), "ti err", meta["time_index_error"], "proto", meta["proto_init"], meta["proto_changes"])
