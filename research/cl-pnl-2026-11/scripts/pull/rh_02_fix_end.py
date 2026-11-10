"""Step 2: fix END and read the state at END that the checks need (state is only readable for minutes).

END = largest multiple of 10,000 that is <= head - 2,000. With --wait the script waits until the head is between
2,000 and 4,000 blocks past a multiple of 10,000, so END is as fresh as the rule allows when the reads start.

Writes data/robinhood/state/END.json and data/robinhood/state/end_state.json (every read: target, signature,
calldata, raw result). Refuses to overwrite an existing END unless --force is given. Every read uses the END block
number explicitly, never ``latest``; a failed read fails the run (re-run to fix a new END).
"""
from __future__ import annotations

import sys
import time

from rh_lib import *  # noqa: F401,F403


class Reader:
    def __init__(self, r, block):
        self.r, self.block, self.reads = r, block, []

    def call(self, to, sig, *args):
        data = call_data(sig, *args)
        raw = self.r.eth_call(to, data, self.block)
        self.reads.append({"to": to, "signature": sig, "data": data, "block": self.block, "result": raw})
        return words(raw)


def main():
    if END_FILE.exists() and "--force" not in sys.argv:
        raise SystemExit(f"{END_FILE} exists; END is already fixed (use --force to fix a new one)")
    disc = jload(DISCOVERY_FILE)
    r = rpc(cached=False)
    head = r.head()
    if "--wait" in sys.argv:
        while not (2000 <= head % GRID <= 4000):
            say("head", head, "waiting for 2000 <= head % 10000 <= 4000")
            time.sleep(15)
            head = r.head()
    end = ((head - 2000) // GRID) * GRID
    t0 = time.time()
    hdr = r.block(end)
    end_info = {"END": end, "hash": hdr["hash"], "timestamp": int(hdr["timestamp"], 16), "iso": iso(int(hdr["timestamp"], 16)),
                "l1BlockNumber": int(hdr["l1BlockNumber"], 16) if "l1BlockNumber" in hdr else None,
                "head_when_fixed": head, "fixed_at_unix": int(t0), "rule": "largest multiple of 10,000 <= head - 2,000"}
    rd = Reader(r, end)
    st = {"END": end, "ramses": {}, "univ3": {}, "v4": {}, "feeds": {}}

    def v3_pool(addr):
        s0 = rd.call(addr, "slot0()")
        return {"address": addr, "sqrtPriceX96": str(s0[0]), "tick": sN(s0[1], 24), "slot0_words": [str(x) for x in s0],
                "feeProtocol_raw": s0[5] if len(s0) > 5 else None,
                "liquidity": str(rd.call(addr, "liquidity()")[0]), "fee": rd.call(addr, "fee()")[0],
                "tickSpacing": sN(rd.call(addr, "tickSpacing()")[0], 24)}

    st["ramses"]["nvda"] = v3_pool(RAMSES_POOLS["nvda"]["pool"])
    st["ramses"]["spy"] = v3_pool(RAMSES_POOLS["spy"]["pool"])
    st["ramses"]["ram_pool"] = v3_pool(RAM_POOL)
    u3 = jload(STATE / "univ3_discovery.json")
    st["univ3"] = {p["pool"]: v3_pool(p["pool"]) for p in u3["pools"]}
    # StateView reads for the known NVDA pool and the 10 most-swapped pools per symbol (all 500+ pools would outlast the state retention)
    want = [p for p in disc["v4_pools"] if p["pool_id"].lower() == V4_NVDA_POOL_ID.lower()]
    for sym in ("NVDA", "SPY"):
        ranked = sorted((p for p in disc["v4_pools"] if p["symbol"] == sym and p.get("swaps_upto_discovery", 0) > 0), key=lambda p: -p["swaps_upto_discovery"])
        want += [p for p in ranked[:10] if p["pool_id"] not in {w["pool_id"] for w in want}]
    for p in want:
        pid = int(p["pool_id"], 16)
        s0 = rd.call(V4_STATE_VIEW, "getSlot0(bytes32)", pid)
        liq = rd.call(V4_STATE_VIEW, "getLiquidity(bytes32)", pid)[0]
        st["v4"][p["pool_id"]] = {"symbol": p["symbol"], "sqrtPriceX96": str(s0[0]), "tick": sN(s0[1], 24), "protocolFee": s0[2], "lpFee": s0[3], "liquidity": str(liq)}
    for name, f in disc["feeds"].items():
        lr = rd.call(f["proxy"], "latestRoundData()")
        agg = rd.call(f["proxy"], "aggregator()")[0]
        st["feeds"][name] = {"proxy": f["proxy"], "aggregator_at_END": "0x" + format(agg, "040x"), "roundId": str(lr[0]), "phase": lr[0] >> 64,
                             "aggregator_round": lr[0] & ((1 << 64) - 1), "answer": str(s256(lr[1])), "startedAt": lr[2], "updatedAt": lr[3]}
    st["reads"] = rd.reads
    st["read_seconds"] = round(time.time() - t0, 1)
    st["head_after_reads"] = r.head()
    jdump(END_STATE_FILE, st)
    jdump(END_FILE, end_info)
    say("END", end_info)
    say("state reads", len(rd.reads), "in", st["read_seconds"], "s; head after reads", st["head_after_reads"])


if __name__ == "__main__":
    main()
