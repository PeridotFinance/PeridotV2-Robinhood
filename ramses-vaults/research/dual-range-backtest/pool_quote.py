"""Read-only, pinned verification and execution-cost quotes for candidate Ramses CL pools (partner pool-selection request).

For each pool this reads, at ONE recent block (the public RPC keeps ~9 minutes of state; the block number and hash are
recorded): token0/token1, tickSpacing(), fee(), slot0 (price, tick, feeProtocol), liquidity() and every initialized tick in a
window around the price. From that it computes, with exact constant-liquidity pool math (the same walk as
test/fork/GroupDepth.t.sol), the USDG-denominated cost of buying and selling at several sizes (fee + price impact versus the
pre-trade mid price) and the input needed to move the price 1, 2 and 5% each way. It also counts swap volume over the last
~24h from Swap logs. These are LOCAL pool-math quotes at the pinned block, not router quotes, and they ignore other pools.

  python3 pool_quote.py            writes evidence/pools/pool-quotes.json
"""
from __future__ import annotations

import bisect
import datetime as _dt
import json
import math
import sys

from common import *  # noqa: F401,F403  (rpc, T, words, sN, HERE, USDG)

Q96 = 2 ** 96
USDG_ADDR = USDG.lower()
SIZES_USD = [50, 250, 1_000, 5_000, 10_000, 50_000]

# name -> (pool, window ticks each side). Candidates named in the partner feedback plus the existing GLD pool.
POOLS = {
    "SPY/USDG CL1": ("0x38453c115607463ac284820ce959831042f3df4e", 500),
    "NVDA/USDG CL10": ("0xdac1904d823f7d6bcaaf431ceee40c934fed321b", 800),
    "META/USDG CL50": ("0x960f79d8dfec7f2f0c1b24cdac6bb9df1371c6e2", 1200),
    "GLD/USDG CL1": ("0x9896af2f6fed9939acc87637b973b6484e6619d8", 500),
    "r33/USDG CL200": ("0xc276b9a27ab1e60e32b581db6a23bb1fab0fc30b", 1800),
}
BLOCKS_PER_DAY = 690_000  # ~8 blocks/s on the L2 header clock (measured from two keeper blocks 5 s apart); only bounds the log scan


def tick_sqrt(t):
    return math.exp(t * math.log(1.0001) / 2.0)


def enc_int(x):
    return format(x % (1 << 256), "064x")


def eth_call(r, to, sig, args="", block=None):
    data = "0x" + T(sig)[2:10] + args
    return r.call("eth_call", [{"to": to, "data": data}, hex(block)])


def read_pool(r, pool, window, block):
    w = lambda res: words(res)
    t0 = "0x" + w(eth_call(r, pool, "token0()", block=block))[0].to_bytes(32, "big")[12:].hex()
    t1 = "0x" + w(eth_call(r, pool, "token1()", block=block))[0].to_bytes(32, "big")[12:].hex()
    spacing = sN(w(eth_call(r, pool, "tickSpacing()", block=block))[0], 24)
    fee = w(eth_call(r, pool, "fee()", block=block))[0]
    liq = w(eth_call(r, pool, "liquidity()", block=block))[0]
    s0 = w(eth_call(r, pool, "slot0()", block=block))
    sqrtp, tick, fee_protocol = s0[0], sN(s0[1], 24), s0[5]
    dec = {}
    for tok in (t0, t1):
        dec[tok] = w(eth_call(r, tok, "decimals()", block=block))[0]
    lo_c = math.floor((tick - window) / spacing)
    hi_c = math.floor((tick + window) / spacing)
    ticks = []
    for word in range(lo_c >> 8, (hi_c >> 8) + 1):
        bm = w(eth_call(r, pool, "tickBitmap(int16)", enc_int(word), block=block))[0]
        for b in range(256):
            if (bm >> b) & 1:
                c = word * 256 + b
                if lo_c <= c <= hi_c:
                    t = c * spacing
                    res = w(eth_call(r, pool, "ticks(int24)", enc_int(t), block=block))
                    ticks.append((t, sN(res[1], 128), res[0]))
    ticks.sort()
    return dict(token0=t0, token1=t1, dec0=dec[t0], dec1=dec[t1], spacing=spacing, fee_ppm=fee, liquidity=liq, sqrtp=sqrtp, tick=tick,
                fee_protocol_slot0=fee_protocol, ticks=ticks, window=window)


def swap(st, gross, zero_for_one):
    """Constant-liquidity walk across the initialized ticks. Returns (out_raw, final_sqrt, exhausted). ``gross`` is the raw input
    including the fee. Beyond the scanned window liquidity is unknown: the walk stops there and reports ``exhausted``."""
    s = st["sqrtp"] / Q96
    L = float(st["liquidity"])
    fee = st["fee_ppm"] / 1e6
    ticks = st["ticks"]
    tick = st["tick"]
    rem = float(gross)
    out = 0.0
    if zero_for_one:
        bounds = [(t, n) for t, n, _ in ticks if t <= tick][::-1]
        edge = tick_sqrt(tick - st["window"])
    else:
        bounds = [(t, n) for t, n, _ in ticks if t > tick]
        edge = tick_sqrt(tick + st["window"])
    i = 0
    while rem > 0:
        if i < len(bounds):
            st_next, net_l = tick_sqrt(bounds[i][0]), bounds[i][1]
            crossing = True
        else:
            st_next, net_l, crossing = edge, 0, False
        if zero_for_one:
            if L <= 0:
                return out, s, True
            need_net = L * (1.0 / st_next - 1.0 / s)
        else:
            if L <= 0:
                return out, s, True
            need_net = L * (st_next - s)
        need_gross = need_net / (1.0 - fee)
        if rem <= need_gross:
            net = rem * (1.0 - fee)
            if zero_for_one:
                s_new = 1.0 / (1.0 / s + net / L)
                out += L * (s - s_new)
            else:
                s_new = s + net / L
                out += L * (1.0 / s - 1.0 / s_new)
            return out, s_new, False
        if zero_for_one:
            out += L * (s - st_next)
        else:
            out += L * (1.0 / s - 1.0 / st_next)
        rem -= need_gross
        s = st_next
        if not crossing:
            return out, s, True
        L = L - net_l if zero_for_one else L + net_l
        i += 1
    return out, s, False


def usdg_per_stock(st):
    r = (st["sqrtp"] / Q96) ** 2
    usdg0 = st["token0"] == USDG_ADDR
    d_stock = st["dec1"] if usdg0 else st["dec0"]
    return (10 ** d_stock) / (r * 1e6) if usdg0 else r * (10 ** d_stock) / 1e6


def costs(st):
    usdg0 = st["token0"] == USDG_ADDR
    p = usdg_per_stock(st)
    d_stock = st["dec1"] if usdg0 else st["dec0"]
    rows = []
    for size in SIZES_USD:
        # BUY stock with `size` USDG
        gross = size * 1e6
        out, s_end, ex_b = swap(st, gross, zero_for_one=usdg0)
        q = out / 10 ** d_stock
        buy = None if ex_b else (1 - q * p / size) * 1e4
        # SELL stock worth `size` at mid
        q_in = size / p * 10 ** d_stock
        out2, s_end2, ex_s = swap(st, q_in, zero_for_one=not usdg0)
        sell = None if ex_s else (1 - (out2 / 1e6) / size) * 1e4
        rows.append({"sizeUsd": size, "buyCostBps": buy, "sellCostBps": sell, "buyExhaustedWindow": ex_b, "sellExhaustedWindow": ex_s,
                     "roundTripBps": (buy + sell) if buy is not None and sell is not None else None,
                     "feeOnlyBps": st["fee_ppm"] / 100.0})
    return rows


def walk_to(st, target, zero_for_one):
    """Gross raw input (including fee) that moves the pool's sqrt price to ``target``; None if the scanned window runs out first."""
    s = st["sqrtp"] / Q96
    L = float(st["liquidity"])
    fee = st["fee_ppm"] / 1e6
    tick = st["tick"]
    if zero_for_one:
        bounds = [(t, n) for t, n, _ in st["ticks"] if t <= tick][::-1]
        edge = tick_sqrt(tick - st["window"])
    else:
        bounds = [(t, n) for t, n, _ in st["ticks"] if t > tick]
        edge = tick_sqrt(tick + st["window"])
    total_net, i = 0.0, 0
    while True:
        crossing = i < len(bounds)
        nxt = tick_sqrt(bounds[i][0]) if crossing else edge
        if zero_for_one:
            stop = max(nxt, target)
            if L <= 0:
                return None
            total_net += L * (1.0 / stop - 1.0 / s)
        else:
            stop = min(nxt, target)
            if L <= 0:
                return None
            total_net += L * (stop - s)
        s = stop
        if stop == target:
            return total_net / (1.0 - fee)
        if not crossing:
            return None
        net_l = bounds[i][1]
        L = L - net_l if zero_for_one else L + net_l
        i += 1


def depth(st):
    usdg0 = st["token0"] == USDG_ADDR
    p = usdg_per_stock(st)
    d_stock = st["dec1"] if usdg0 else st["dec0"]
    s0 = st["sqrtp"] / Q96
    res = []
    for direction in ("price_up_buy_stock", "price_down_sell_stock"):
        up = direction == "price_up_buy_stock"
        zfo = usdg0 if up else (not usdg0)           # USDG token0: stock price up <=> sqrt price down <=> token0 (USDG) in
        for pct in (1.0, 2.0, 5.0):
            f = math.sqrt(1 + pct / 100.0) if up else math.sqrt(1 - pct / 100.0)
            target = s0 / f if usdg0 else s0 * f
            gross = walk_to(st, target, zfo)
            if gross is None:
                res.append({"direction": direction, "movePct": pct, "inputUsd": None, "note": "target not reached inside the scanned window"})
                continue
            in_is_usdg = (zfo == usdg0)               # input token is token0 when zfo; token0 is USDG iff usdg0
            usd = gross / 1e6 if in_is_usdg else gross / 10 ** d_stock * p
            res.append({"direction": direction, "movePct": pct, "inputUsd": usd})
    return res


def volume_24h(r, pool, st, head):
    sig = "Swap(address,address,int256,int256,uint160,uint128,int24)"
    logs = r.logs(pool, [T(sig)], head - BLOCKS_PER_DAY, head, head - 200)
    usdg0 = st["token0"] == USDG_ADDR
    vol = 0.0
    n = 0
    for l in logs:
        w = words(l["data"])
        a = sN(w[0] if usdg0 else w[1], 256)
        vol += abs(a) / 1e6
        n += 1
    return {"swaps": n, "usdgVolume": vol, "fromBlock": head - BLOCKS_PER_DAY, "toBlock": head}


def main():
    r = rpc()
    out = {"schema": "ramses-pool-quotes-1", "readOnly": True, "generated": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "command": "python3 " + " ".join(sys.argv), "method": "local constant-liquidity pool math at one pinned block per pool; not router quotes",
           "sizesUsd": SIZES_USD, "pools": {}}
    only = sys.argv[1:]
    for name, (addr, window) in POOLS.items():
        if only and not any(o in name for o in only):
            continue
        head = r.head()
        block = head - 5
        b = r.call("eth_getBlockByNumber", [hex(block), False])
        st = read_pool(r, addr, window, block)
        vol = volume_24h(r, addr, st, block)
        rec = {"pool": addr, "pinL2Block": block, "pinHash": b["hash"], "pinTimestamp": int(b["timestamp"], 16), "pinNative": int(b["l1BlockNumber"], 16),
               "token0": st["token0"], "token1": st["token1"], "dec0": st["dec0"], "dec1": st["dec1"], "tickSpacing": st["spacing"], "fee_ppm": st["fee_ppm"],
               "slot0FeeProtocolRaw": st["fee_protocol_slot0"], "liquidity": st["liquidity"], "tick": st["tick"], "usdgPerStock": usdg_per_stock(st),
               "initializedTicksInWindow": len(st["ticks"]), "windowTicks": window, "volume24h": vol, "costs": costs(st), "depth": depth(st)}
        out["pools"][name] = rec
        print(name, "block", block, "spacing", st["spacing"], "fee", st["fee_ppm"], "ticks", len(st["ticks"]), "vol24h", round(vol["usdgVolume"]), flush=True)
        for c in rec["costs"]:
            print("   $%-6d buy %s sell %s" % (c["sizeUsd"], None if c["buyCostBps"] is None else round(c["buyCostBps"], 1), None if c["sellCostBps"] is None else round(c["sellCostBps"], 1)), flush=True)
    path = HERE / "evidence" / "pools" / "pool-quotes.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=1))
    print("wrote", path)


if __name__ == "__main__":
    main()
