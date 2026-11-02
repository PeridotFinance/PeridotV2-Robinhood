"""Read-only, pinned verification and execution-cost quotes for candidate Ramses CL pools (partner pool-selection request).

For each pool this reads, at ONE recent block (the public RPC keeps ~9 minutes of state; block number, hash and native number are
recorded): token0/token1, tickSpacing(), fee(), the full slot0 words, liquidity(), every tickBitmap word in the scanned range and every
initialized tick's (liquidityGross, liquidityNet). **All of it is saved** to evidence/pools/inputs/<slug>.json so the quotes can be
replayed offline later (the RPC cannot serve these historical state reads again):

  python3 quote_offline.py evidence/pools/inputs/<slug>.json      # recompute the quotes from the saved inputs, no network

Scan modes: "full" reads the entire bitmap (MIN_TICK..MAX_TICK) so liquidity running out is a real capacity statement; "window" reads
a range around the price (used for tick-spacing-1 pools whose full bitmap is ~7,000 words) and can only report scan_window_exhausted.
The quote walk is quote_math.py (zero-liquidity gaps are crossed for free; four distinct end statuses); it is validated against the
deployed pool on a fork by test/fork/PoolQuoteReplay.t.sol + replay_pool.py.

  python3 pool_quote.py [name-substring ...]     writes inputs and merges into evidence/pools/pool-quotes.json
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from common import *  # noqa: F401,F403  (Rpc, T, words, sN, HERE, USDG)
import quote_math as QM

USDG_ADDR = USDG.lower()
SIZES_USD = [50, 250, 1_000, 5_000, 10_000, 50_000]
WINDOW_TICKS = 5_000          # scan half-width for tick-spacing-1 pools
BLOCKS_PER_DAY = 690_000       # ~8 blocks/s on the L2 header clock; only bounds the 24 h Swap-log scan
INPUT_DIR = HERE / "evidence" / "pools" / "inputs"
OUT = HERE / "evidence" / "pools" / "pool-quotes.json"

# name -> (pool, spacing, scan mode). Candidates named in the partner feedback plus the existing GLD pool.
POOLS = {
    "SPY/USDG CL1": ("0x38453c115607463ac284820ce959831042f3df4e", 1, "window"),
    "NVDA/USDG CL10": ("0xdac1904d823f7d6bcaaf431ceee40c934fed321b", 10, "full"),
    "META/USDG CL50": ("0x960f79d8dfec7f2f0c1b24cdac6bb9df1371c6e2", 50, "full"),
    "GLD/USDG CL1": ("0x9896af2f6fed9939acc87637b973b6484e6619d8", 1, "window"),
    "r33/USDG CL200": ("0xc276b9a27ab1e60e32b581db6a23bb1fab0fc30b", 200, "full"),
    "r33/USDG CL500": ("0x9f301fa81ccdda903a3590e7b9abee12e035709e", 500, "full"),   # route comparison; fork-validated like the others
}
SLUG = lambda name: name.replace("/", "-").replace(" ", "_")
POOLS_DIR = HERE / "evidence" / "pools"


def snapshot_path(name, inputs, subdir=""):
    """Snapshots are stored BY BLOCK AND HASH, never overwritten: inputs/<slug>/<l2 block>-<hash prefix>.json."""
    pin = inputs["pin"]
    return INPUT_DIR / subdir / SLUG(name) / f"{pin['l2Block']}-{pin['hash'][2:10]}.json"


def save_inputs(name, inputs, subdir=""):
    """Write the snapshot (refusing to replace a different snapshot at the same path) and return (path relative to evidence/pools, sha256 of the file)."""
    p = snapshot_path(name, inputs, subdir)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(inputs, indent=1)
    if p.exists() and p.read_text() != text:
        raise RuntimeError(f"refusing to overwrite a different snapshot at {p}")
    p.write_text(text)
    return str(p.relative_to(POOLS_DIR)), hashlib.sha256(text.encode()).hexdigest()


def file_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def enc_int(x):
    return format(x % (1 << 256), "064x")


def _rpc():
    return Rpc(cache_dir=None, min_interval=0.3)


def eth_call(r, to, sig, args="", block=None):
    return r.call("eth_call", [{"to": to, "data": "0x" + T(sig)[2:10] + args}, hex(block)])


def read_inputs(pool: str, spacing: int, mode: str, block: int) -> dict:
    r = _rpc()
    w = lambda res: words(res)
    t0 = "0x" + w(eth_call(r, pool, "token0()", block=block))[0].to_bytes(32, "big")[12:].hex()
    t1 = "0x" + w(eth_call(r, pool, "token1()", block=block))[0].to_bytes(32, "big")[12:].hex()
    sp = sN(w(eth_call(r, pool, "tickSpacing()", block=block))[0], 24)
    assert sp == spacing, f"tickSpacing {sp} != expected {spacing}"
    fee = w(eth_call(r, pool, "fee()", block=block))[0]
    liq = w(eth_call(r, pool, "liquidity()", block=block))[0]
    s0 = w(eth_call(r, pool, "slot0()", block=block))
    tick = sN(s0[1], 24)
    dec0 = w(eth_call(r, t0, "decimals()", block=block))[0]
    dec1 = w(eth_call(r, t1, "decimals()", block=block))[0]
    if mode == "full":
        from_tick, to_tick = QM.MIN_TICK, QM.MAX_TICK
    else:
        from_tick, to_tick = tick - WINDOW_TICKS, tick + WINDOW_TICKS
    lo_c, hi_c = from_tick // sp, to_tick // sp          # floor division: compressed tick of the bound
    word_lo, word_hi = lo_c >> 8, hi_c >> 8

    def get_word(word):
        rr = _rpc()
        return word, w(eth_call(rr, pool, "tickBitmap(int16)", enc_int(word), block=block))[0]

    with ThreadPoolExecutor(3) as ex:
        bitmap = dict(ex.map(get_word, range(word_lo, word_hi + 1)))
    tick_list = []
    for word, bm in bitmap.items():
        for b in range(256):
            if (bm >> b) & 1:
                c = word * 256 + b
                if lo_c <= c <= hi_c:
                    tick_list.append(c * sp)

    def get_tick(t):
        rr = _rpc()
        res = w(eth_call(rr, pool, "ticks(int24)", enc_int(t), block=block))
        return t, (res[0], sN(res[1], 128))

    with ThreadPoolExecutor(3) as ex:
        tick_data = dict(ex.map(get_tick, tick_list))
    b = r.call("eth_getBlockByNumber", [hex(block), False])
    return {"schema": "ramses-pool-inputs-1", "pool": pool, "pin": {"l2Block": block, "hash": b["hash"], "timestamp": int(b["timestamp"], 16), "native": int(b["l1BlockNumber"], 16)},
            "token0": t0, "token1": t1, "dec0": dec0, "dec1": dec1, "tickSpacing": sp, "fee_ppm": fee,
            "slot0": {"sqrtPriceX96": str(s0[0]), "tick": tick, "rawWords": [str(x) for x in s0], "feeProtocol": s0[5]},
            "liquidity": str(liq),
            "scan": {"mode": mode, "fromTick": from_tick, "toTick": to_tick, "wordLo": word_lo, "wordHi": word_hi},
            "bitmap": {str(k): hex(v) for k, v in sorted(bitmap.items()) if v},
            "ticks": {str(t): {"liquidityGross": str(g), "liquidityNet": str(n)} for t, (g, n) in sorted(tick_data.items())}}


def volume_24h(r, pool, inputs, head):
    sig = "Swap(address,address,int256,int256,uint160,uint128,int24)"
    logs = r.logs(pool, [T(sig)], head - BLOCKS_PER_DAY, head, head - 200)
    usdg0 = inputs["token0"] == USDG_ADDR
    vol, n = 0.0, 0
    for l in logs:
        wd = words(l["data"])
        vol += abs(sN(wd[0] if usdg0 else wd[1], 256)) / 1e6
        n += 1
    return {"swaps": n, "usdgVolume": vol, "fromBlock": head - BLOCKS_PER_DAY, "toBlock": head}


def record_from_inputs(name: str, inputs: dict, vol=None, inputs_file=None, inputs_sha=None, replay_file=None) -> dict:
    q = QM.quote_all(inputs, USDG_ADDR, SIZES_USD)
    st = QM.make_state(inputs)
    # total executable capacity: the most the pool can pay out for an unbounded input (the pool keeps filling at collapsing prices)
    import copy as _c
    usdg0 = st["token0"].lower() == USDG_ADDR
    d_stock = st["dec1"] if usdg0 else st["dec0"]
    sell = QM.swap(_c.deepcopy(st), 1e40, not usdg0)
    buy = QM.swap(_c.deepcopy(st), 1e40, usdg0)
    capacity = {"maxUsdgFromSelling": sell["out"] / 1e6, "sellStatus": sell["status"],
                "maxStockUsdFromBuyingAtMid": buy["out"] / 10 ** d_stock * QM.usdg_per_stock(st, USDG_ADDR), "buyStatus": buy["status"]}
    rec = {"executableCapacity": capacity, "pool": inputs["pool"], "pinL2Block": inputs["pin"]["l2Block"], "pinHash": inputs["pin"]["hash"], "pinTimestamp": inputs["pin"]["timestamp"],
           "pinNative": inputs["pin"]["native"], "inputsFile": inputs_file, "inputsSha256": inputs_sha, "forkReplayFile": replay_file, "token0": inputs["token0"], "token1": inputs["token1"],
           "dec0": inputs["dec0"], "dec1": inputs["dec1"], "tickSpacing": inputs["tickSpacing"], "fee_ppm": inputs["fee_ppm"],
           "slot0FeeProtocolRaw": inputs["slot0"]["feeProtocol"], "liquidity": int(inputs["liquidity"]), "tick": inputs["slot0"]["tick"],
           "usdgPerStock": QM.usdg_per_stock(st, USDG_ADDR), "initializedTicksScanned": len(inputs["ticks"]), "scan": inputs["scan"],
           "quoteLimitPct": q["limitPct"], "quotes": q["rows"], "depth": QM.depth(inputs, USDG_ADDR)}
    if vol is not None:
        rec["volume24h"] = vol
    return rec


def latest_replay(name):
    docs = sorted((POOLS_DIR / "fork-replay").glob(f"{SLUG(name)}@*.json"), key=lambda f: int(f.stem.split("@")[1]))
    return docs[-1] if docs else None


def from_inputs():
    """Rebuild pool-quotes.json from SAVED snapshots. Where a fork replay exists for the pool, the quote is built from exactly the snapshot that
    replay proved (bound by block, hash and file sha256); the 24 h volume comes from logs at that block."""
    r = rpc()
    doc = {"schema": "ramses-pool-quotes-3", "readOnly": True, "generated": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "method": "offline quote_math.py over saved pinned snapshots (evidence/pools/inputs/<pool>/<block>-<hash>.json); one block per pool; not router quotes; independent one-way quotes plus a "
                     "sequential round trip (buy then sell in the evolving pool state, so impact is returned: it measures reversal cost, NOT the cost of exiting a position); statuses distinguish "
                     "filled / price_limit_rejection / capacity_exhausted / scan_window_exhausted",
           "sizesUsd": SIZES_USD, "pools": {}}
    prev_all = json.loads(OUT.read_text()).get("pools", {}) if OUT.exists() else {}
    for name in POOLS:
        rep = latest_replay(name)
        if rep is None:
            print("no fork replay for", name, "- skipped (a quote without a proof is not published)")
            continue
        rdoc = json.loads(rep.read_text())
        f = POOLS_DIR / rdoc["inputsFile"]
        if file_sha256(f) != rdoc["inputsSha256"]:
            raise SystemExit(f"{f} does not match the sha256 recorded by its fork replay")
        inputs = json.loads(f.read_text())
        prev = prev_all.get(name, {})
        vol = prev["volume24h"] if prev.get("pinHash") == inputs["pin"]["hash"] and "volume24h" in prev else volume_24h(r, inputs["pool"], inputs, inputs["pin"]["l2Block"])
        doc["pools"][name] = record_from_inputs(name, inputs, vol, rdoc["inputsFile"], rdoc["inputsSha256"], str(rep.relative_to(POOLS_DIR)))
        print("rebuilt", name, "pin", inputs["pin"]["l2Block"], "proof", rep.name, flush=True)
    OUT.write_text(json.dumps(doc, indent=1))


def main():
    if "--from-inputs" in sys.argv:
        return from_inputs()
    only = sys.argv[1:]
    r = rpc()
    doc = json.loads(OUT.read_text()) if OUT.exists() else {}
    doc.update({"schema": "ramses-pool-quotes-2", "readOnly": True, "generated": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "method": "offline quote_math.py over saved pinned inputs (evidence/pools/inputs); one block per pool; not router quotes; independent one-way quotes plus a sequential "
                          "round trip (buy then sell in the evolving pool state); statuses distinguish filled / price_limit_rejection / capacity_exhausted / scan_window_exhausted",
                "sizesUsd": SIZES_USD})
    doc.setdefault("pools", {})
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, (addr, spacing, mode) in POOLS.items():
        if only and not any(o in name for o in only):
            continue
        head = r.head()
        block = head - 5
        inputs = read_inputs(addr, spacing, mode, block)
        rel, sha = save_inputs(name, inputs)
        vol = volume_24h(r, addr, inputs, block)
        rec = record_from_inputs(name, inputs, vol, rel, sha, None)
        doc["pools"][name] = rec
        print(name, "block", block, "spacing", spacing, mode, "fee", inputs["fee_ppm"], "ticks", len(inputs["ticks"]), "vol24h", round(vol["usdgVolume"]), flush=True)
        for row in rec["quotes"]:
            f = lambda x: x["status"] if x["costBps"] is None else f"{x['costBps']:.1f}"
            print("   $%-6d buy %-22s sell %-22s seqRT %s" % (row["sizeUsd"], f(row["buy"]), f(row["sell"]), f(row["sequentialRoundTrip"])), flush=True)
        OUT.write_text(json.dumps(doc, indent=1))
    print("wrote", OUT)


if __name__ == "__main__":
    main()
