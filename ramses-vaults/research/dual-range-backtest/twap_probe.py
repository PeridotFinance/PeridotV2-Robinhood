"""Read-only probe of the deployed Ramses pools' price-observation history (what a TWAP fallback could actually use).

For each candidate pool at one pinned block: slot0 observation index / cardinality / cardinalityNext, the oldest initialized observation (hence the
longest TWAP window the pool can serve today), and observe() for a ladder of windows with the resulting time-weighted tick compared with the spot
tick. Also records how many observations fall in each window, a proxy for how many separate blocks of trading the TWAP is built from.

  python3 twap_probe.py            writes evidence/oracle/twap-probe.json
"""
import datetime as _dt
import json
import math

from common import *  # noqa: F401,F403
from pool_quote import POOLS, eth_call, enc_int

WINDOWS = [60, 300, 900, 1800, 3600, 6 * 3600, 12 * 3600, 24 * 3600, 48 * 3600]


def u(x):
    return x


def main():
    r = rpc()
    head = r.head()
    block = head - 5
    b = r.call("eth_getBlockByNumber", [hex(block), False])
    now = int(b["timestamp"], 16)
    out = {"schema": "ramses-twap-probe-1", "readOnly": True, "pin": {"l2Block": block, "hash": b["hash"], "native": int(b["l1BlockNumber"], 16), "timestamp": now}, "pools": {}}
    for name, (addr, spacing, _mode) in POOLS.items():
        s0 = words(eth_call(r, addr, "slot0()", block=block))
        tick = sN(s0[1], 24)
        idx, card, card_next = s0[2], s0[3], s0[4]
        rec = {"pool": addr, "tick": tick, "observationIndex": idx, "cardinality": card, "cardinalityNext": card_next}

        def obs(i):
            w = words(eth_call(r, addr, "observations(uint256)", enc_int(i), block=block))
            return {"blockTimestamp": w[0], "tickCumulative": sN(w[1], 56), "initialized": bool(w[3])}

        newest = obs(idx)
        oldest_i = (idx + 1) % card if card else 0
        oldest = obs(oldest_i)
        if not oldest["initialized"]:
            oldest = obs(0)
        rec["newestObservationAgeSeconds"] = now - newest["blockTimestamp"]
        rec["oldestObservationAgeSeconds"] = now - oldest["blockTimestamp"] if oldest["initialized"] else None
        ladder = []
        for wsec in WINDOWS:
            try:
                res = r.call("eth_call", [{"to": addr, "data": "0x" + T("observe(uint32[])")[2:10] + enc_int(32) + enc_int(2) + enc_int(wsec) + enc_int(0)}, hex(block)])
                ww = words(res)
                # returns (int56[] tickCumulatives, uint160[] secondsPerLiquidityCumulativeX128s): offsets then two lengths then values
                c_old, c_new = sN(ww[3], 56), sN(ww[4], 56)
                twap_tick = (c_new - c_old) / wsec
                ladder.append({"windowSeconds": wsec, "ok": True, "twapTick": twap_tick, "spotMinusTwapTicks": tick - twap_tick, "spotMinusTwapPct": (math.pow(1.0001, tick - twap_tick) - 1) * 100})
            except Exception as e:
                ladder.append({"windowSeconds": wsec, "ok": False, "error": str(e)[:80]})
        rec["observeLadder"] = ladder
        rec["longestServedWindowSeconds"] = max((x["windowSeconds"] for x in ladder if x["ok"]), default=0)
        out["pools"][name] = rec
        print(f"{name:16s} cardinality {card}/{card_next} idx {idx}; oldest obs age {rec['oldestObservationAgeSeconds']} s; longest served window {rec['longestServedWindowSeconds']} s", flush=True)
    p = HERE / "evidence" / "oracle" / "twap-probe.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=1))
    print("wrote", p)


if __name__ == "__main__":
    main()
