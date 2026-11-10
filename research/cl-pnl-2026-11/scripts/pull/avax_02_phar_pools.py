"""Enumerate PHAR pools, rank them by PHAR held at END, and record the candidates (data/avalanche/phar_pools.json).

Candidates come from two places:
  1. the Pharaoh factories on chain: clFactory.getPool(PHAR, X, tickSpacing) and legacyFactory.getPair(PHAR, X, stable)
     for X in {WAVAX, USDC, USDt};
  2. the DexScreener public pair list for the PHAR token (saved raw as data/avalanche/raw_dexscreener_phar_pairs.json.gz).
Every candidate is then read on chain at END (token0/token1, PHAR balance, reserves or slot0, fee). Read-only.
"""
from __future__ import annotations

from avax_lib import (DATA_AVAX, PHAR, SEL, USDC, WAVAX, addr_arg, dec_string, eth_call, http_json, read_json, rpc, s24,
                      word_addr, words, write_json, write_json_gz)

USDT = "0x9702230A8Ea53601f5cD2dc00fDBc13d4dF4A8c7"
SRC = "avax"
EXTRA_SEL = {"pairFee(address)": "0x841fa66b", "FEE_DENOM()": "0x4886c675", "feeSplit()": "0x6373ea69"}
ZERO = "0x" + "0" * 40


def main() -> None:
    import sys
    if "--reselect" in sys.argv:          # re-apply the selection rule to the candidates already read on chain
        old = read_json(DATA_AVAX / "phar_pools.json")
        select(old["candidates"], old["end_block"], old["raw_calls"])
        o = read_json(DATA_AVAX / "phar_pools.json")
        print("deepest overall", o["deepest_overall"], "\nchosen", o["chosen"], "\ncl", o["cl_reference"])
        return
    st = read_json(DATA_AVAX / "state_at_end.json")
    E, S = st["end"]["number"], st["start"]["number"]
    g = st["decoded"]["gauge"]
    cl, legacy = g["voter.clFactory()"], g["voter.legacyFactory()"]
    raw = {}

    def call(label, to, data, block=E):
        try:
            r = eth_call(SRC, to, data, block)
        except Exception as e:
            raw[label] = {"to": to, "data": data, "block": block, "error": str(e)[:200]}
            return None
        raw[label] = {"to": to, "data": data, "block": block, "result": r}
        return r

    cands: dict[str, dict] = {}
    # 1. factories
    for qn, q in (("WAVAX", WAVAX), ("USDC", USDC), ("USDt", USDT)):
        for ts in (1, 5, 10, 20, 50, 100, 200):
            r = call(f"clFactory.getPool(PHAR,{qn},{ts})", cl, SEL["getPool(address,address,int24)"] + addr_arg(PHAR) + addr_arg(q) + hex(ts)[2:].rjust(64, "0"))
            a = word_addr(words(r)[0]) if r else ZERO
            if a != ZERO:
                cands[a] = {"found_by": f"clFactory.getPool(PHAR,{qn},{ts})", "kind": "cl"}
        for stable in (0, 1):
            r = call(f"legacyFactory.getPair(PHAR,{qn},{bool(stable)})", legacy, SEL["getPair(address,address,bool)"] + addr_arg(PHAR) + addr_arg(q) + hex(stable)[2:].rjust(64, "0"))
            a = word_addr(words(r)[0]) if r else ZERO
            if a != ZERO:
                cands[a] = {"found_by": f"legacyFactory.getPair(PHAR,{qn},stable={bool(stable)})", "kind": "legacy"}
    # 2. DexScreener
    ds = http_json("https://api.dexscreener.com/latest/dex/tokens/" + PHAR)
    write_json_gz(DATA_AVAX / "raw_dexscreener_phar_pairs.json.gz", ds)
    for p in ds.get("pairs") or []:
        a = p["pairAddress"].lower()
        if p.get("chainId") != "avalanche" or len(a) != 42:
            continue
        e = cands.setdefault(a, {"found_by": "dexscreener", "kind": "?"})
        e["dexscreener"] = {"dexId": p.get("dexId"), "labels": p.get("labels"), "base": p["baseToken"]["symbol"], "quote": p["quoteToken"]["symbol"],
                            "liquidity_usd": (p.get("liquidity") or {}).get("usd"), "volume_h24_usd": (p.get("volume") or {}).get("h24"),
                            "priceUsd": p.get("priceUsd")}
    # on-chain facts at END
    for a, e in cands.items():
        e["code_bytes"] = (len(rpc(SRC, "eth_getCode", [a, hex(E)])) - 2) // 2
        r0, r1 = call(f"{a}.token0", a, SEL["token0()"]), call(f"{a}.token1", a, SEL["token1()"])
        if not r0 or not r1 or r0 == "0x":
            e["note"] = "no token0()/token1(): not a v2/v3-style pool"
            continue
        e["token0"], e["token1"] = word_addr(words(r0)[0]), word_addr(words(r1)[0])
        for k in ("token0", "token1"):
            e[k + "_symbol"] = dec_string(call(f"{a}.{k}.symbol", e[k], SEL["symbol()"]))
            e[k + "_decimals"] = words(call(f"{a}.{k}.decimals", e[k], SEL["decimals()"]))[0]
        e["phar_balance"] = str(words(call(f"{a}.pharBalance", PHAR, SEL["balanceOf(address)"] + addr_arg(a)))[0])
        r = call(f"{a}.slot0", a, SEL["slot0()"])
        if r and len(r) >= 2 + 64 * 7:
            w = words(r)
            e["kind"] = "cl"
            e["slot0"] = {"sqrtPriceX96": str(w[0]), "tick": s24(w[1]), "feeProtocol": w[5]}
            e["tickSpacing"] = s24(words(call(f"{a}.tickSpacing", a, SEL["tickSpacing()"]))[0])
            e["fee_ppm"] = words(call(f"{a}.fee", a, SEL["fee()"]))[0]
            e["fee_ppm_at_start"] = (lambda x: words(x)[0] if x and x != "0x" else None)(call(f"{a}.fee@START", a, SEL["fee()"], S))
            e["liquidity"] = str(words(call(f"{a}.liquidity", a, SEL["liquidity()"]))[0])
            continue
        r = call(f"{a}.getReserves", a, SEL["getReserves()"])
        if r and len(r) >= 2 + 64 * 3:
            w = words(r)
            e["kind"] = "legacy"
            e["reserves"] = {"reserve0": str(w[0]), "reserve1": str(w[1]), "blockTimestampLast": w[2]}
            e["stable"] = bool(words(call(f"{a}.stable", a, SEL["stable()"]))[0])
            e["pair.fee()"] = words(call(f"{a}.fee", a, SEL["fee()"]))[0]
            x = call(f"{a}.fee@START", a, SEL["fee()"], S)
            e["pair.fee()_at_start"] = words(x)[0] if x and x != "0x" else None
            e["factory.pairFee(pair)"] = words(call(f"{a}.pairFee", legacy, EXTRA_SEL["pairFee(address)"] + addr_arg(a)))[0]
            e["factory.FEE_DENOM()"] = words(call(f"{a}.FEE_DENOM", legacy, EXTRA_SEL["FEE_DENOM()"]))[0]
            x = call(f"{a}.feeSplit", a, EXTRA_SEL["feeSplit()"])
            e["pair.feeSplit()"] = words(x)[0] if x and x != "0x" else None
    select(cands, E, raw)


def select(cands: dict, E: int, raw: dict) -> None:
    """Rank by PHAR held at END. The price series needs a quote asset with a USD reference, so the pool used for
    phar_pool_swaps is the deepest one quoted in WAVAX or USDC; the deepest pool overall is recorded as well."""
    ranked = sorted((a for a, e in cands.items() if "phar_balance" in e), key=lambda a: -int(cands[a]["phar_balance"]))
    quoted = [a for a in ranked if {cands[a]["token0"], cands[a]["token1"]} & {WAVAX.lower(), USDC.lower()}]
    out = {"end_block": E, "ranked_by_phar_balance": ranked,
           "deepest_overall": {"address": ranked[0], "pair": cands[ranked[0]]["token0_symbol"] + "/" + cands[ranked[0]]["token1_symbol"],
                               "phar_balance": cands[ranked[0]]["phar_balance"]},
           "chosen": {"address": quoted[0], "kind": cands[quoted[0]]["kind"],
                      "why": "largest PHAR balance at END among pools quoted in WAVAX or USDC (a quote asset with a USD reference)"},
           "cl_reference": None, "candidates": cands, "raw_calls": raw}
    # the deepest concentrated-liquidity pool against WAVAX/USDC, kept as a second series with the exact columns of dataset A
    for a in quoted:
        if cands[a]["kind"] == "cl":
            out["cl_reference"] = {"address": a, "why": "largest PHAR balance among CL pools quoted in WAVAX or USDC"}
            break
    write_json(DATA_AVAX / "phar_pools.json", out)
    for a in ranked:
        e = cands[a]
        print(a, e["kind"], e["token0_symbol"], "/", e["token1_symbol"], "PHAR", int(e["phar_balance"]) / 1e18,
              {k: e[k] for k in ("tickSpacing", "fee_ppm", "fee_ppm_at_start", "stable", "pair.fee()", "pair.fee()_at_start", "factory.FEE_DENOM()", "pair.feeSplit()") if k in e}, e["found_by"])
    print("chosen", out["chosen"], "cl", out["cl_reference"])


if __name__ == "__main__":
    main()
