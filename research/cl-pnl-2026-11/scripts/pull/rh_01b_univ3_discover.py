"""Step 1b (before END is fixed): Uniswap V3 on Robinhood Chain -> data/robinhood/state/univ3_discovery.json.

What is checked:
  * the repos under /Users/joshua/Peridot hold no Uniswap V3 factory address for chain 4663 (only the Avalanche one in
    peridot-contracts-2-5/operator/src/constants.ts); recorded as a note, the grep itself is not re-run here;
  * Uniswap's own deployment page for Robinhood Chain (chainId 4663) lists UniswapV3Factory
    0x1f7d7550b1b028f7571e69a784071f0205fd2efa; this script checks on chain that the address has code;
  * every pool of the pairs USDG x NVDA and USDG x SPY: PoolCreated(token0, token1, fee) events on that factory (the
    three arguments are indexed, so the filter is exact and covers every fee tier), cross-checked with
    getPool(tokenA, tokenB, fee) for fee tiers 100/500/3000/10000;
  * per pool: its Initialize event. Swaps are counted in rh_05b_univ3.py (one of these pools has millions of swaps).
"""
from __future__ import annotations

from rh_lib import *  # noqa: F401,F403


def pad(a: str) -> str:
    return a.lower().replace("0x", "").rjust(64, "0")


def main():
    r = rpc()
    head = r.head()
    final = ((head - 2000) // GRID) * GRID
    out = {"head_at_discovery": head, "enumerated_upto_block": final, "factory": UNIV3_FACTORY,
           "factory_source": "https://developers.uniswap.org/docs/protocols/v3/deployments/v3-robinhood-chain-deployments (fetched 2026-11-10)",
           "repo_grep_note": "no Uniswap V3 factory for chain 4663 in the repos under /Users/joshua/Peridot (UNIV3_FACTORY in "
                             "peridot-contracts-2-5/operator/src/constants.ts is the Avalanche one; other 'V3' hits are RamsesV3)"}
    code = r.call("eth_getCode", [UNIV3_FACTORY, "latest"])
    out["factory_code_bytes"] = (len(code) - 2) // 2
    out["factory_owner"] = "0x" + r.eth_call(UNIV3_FACTORY, call_data("owner()"))[-40:]
    out["fee_tiers_enabled"] = {str(f): sN(int(r.eth_call(UNIV3_FACTORY, call_data("feeAmountTickSpacing(uint24)", f)), 16), 24)
                                for f in (100, 200, 300, 400, 500, 2500, 3000, 10000)}
    sel = keccak_topic("getPool(address,address,uint24)")[:10]
    pools = []
    for sym, stock in (("NVDA", NVDA), ("SPY", SPY)):
        created = []
        for a, b in ((USDG, stock), (stock, USDG)):
            created += r.logs(UNIV3_FACTORY, [T(SIG["v3_pool_created"]), addr_topic(a), addr_topic(b)], 0, final, final)
        get_pool = {}
        for fee in (100, 500, 3000, 10000):
            get_pool[fee] = "0x" + r.eth_call(UNIV3_FACTORY, sel + pad(USDG) + pad(stock) + format(fee, "064x"))[-40:]
        for l in created:
            w = words(l["data"])
            fee = int(l["topics"][3], 16)
            pool = "0x" + format(w[1], "040x")
            p = {"symbol": sym, "pool": pool, "token0": topic_addr(l["topics"][1]), "token1": topic_addr(l["topics"][2]), "fee": fee,
                 "tick_spacing": sN(w[0], 24), "created_block": l["blockNumber"], "created_tx": l["tx"],
                 "getPool_matches": get_pool.get(fee, "").lower() == pool.lower() if fee in get_pool else None}
            init = r.logs(pool, [T(SIG["v3_init"])], l["blockNumber"], final, final)
            if init:
                iw = words(init[0]["data"])
                p["initialize"] = {"block": init[0]["blockNumber"], "log_index": init[0]["logIndex"], "sqrtPriceX96": str(iw[0]), "tick": sN(iw[1], 24)}
            else:
                p["initialize"] = None
            pools.append(p)
            say(sym, pool, "fee", fee, "spacing", p["tick_spacing"], "created", p["created_block"], "initialize", p["initialize"], "getPool ok", p["getPool_matches"])
        missing = [f for f, a in get_pool.items() if int(a, 16) and a.lower() not in {p["pool"].lower() for p in pools}]
        out.setdefault("getPool", {})[sym] = {str(k): v for k, v in get_pool.items()}
        out.setdefault("getPool_pools_without_PoolCreated_event", {})[sym] = missing
    out["pools"] = pools
    jdump(STATE / "univ3_discovery.json", out)
    say("wrote univ3_discovery.json; rpc calls", r.calls)


if __name__ == "__main__":
    main()
