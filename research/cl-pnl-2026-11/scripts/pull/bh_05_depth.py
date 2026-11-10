"""Sell-side and buy-side depth of the Blackhole and Pharaoh WAVAX/USDC pools at END, by direct quote.

For each pool and each target move of the POOL PRICE (-1 %, -5 %, -10 % by selling WAVAX; +1 %, +5 %, +10 % by
selling USDC) the script bisects on the exact-input amount of the protocol's own QuoterV2.quoteExactInputSingle,
called by archive eth_call at END on api.avax.network, until the post-trade sqrt price is at the target:

    target_sqrt = floor(sqrt(sqrtP0^2 * (10000 + move_bp) / 10000))          (price = sqrtP^2, so a -1 % price move)
    result      = the smallest amount_in found whose sqrtPriceX96After is at or past the target
                  (bisection stops when hi - lo <= max(1, hi / 1e9))

Every quote call (amount in, amount out, sqrt price after) is stored in data/blackhole/depth_at_end.json so the table
can be re-derived offline. One extra call per target passes the target as the quoter's price limit with a very large
amount in: the swap then stops at the target and returns the same amount out (and, on the Algebra quoter, the amount
in it consumed), an independent check of the bisection.

    Blackhole: QuoterV2 0x3e18...b689  quoteExactInputSingle((tokenIn, tokenOut, deployer, amountIn, limitSqrtPrice))
               -> (amountOut, amountIn, sqrtPriceX96After, initializedTicksCrossed, gasEstimate, fee)
    Pharaoh:   QuoterV2 0xB729...2138  quoteExactInputSingle((tokenIn, tokenOut, amountIn, tickSpacing, sqrtPriceLimitX96))
               -> (amountOut, sqrtPriceX96After, initializedTicksCrossed, gasEstimate)

Derived per target (formulas also written into the JSON):
    usd_in            sells: amount_in_WAVAX * P0;  buys: amount_in_USDC          (P0 = pre-trade pool price, USDC per WAVAX)
    avg_price         sells: USDC out / WAVAX in;   buys: USDC in / WAVAX out
    cost_bp           sells: (1 - avg_price / P0) * 1e4;  buys: (avg_price / P0 - 1) * 1e4     (includes the swap fee)
    liquidity within +/-x %  =  USDC out of the sell to -x %  +  WAVAX out of the buy to +x % * P0
Read-only.  usage: python3 -u bh_05_depth.py
"""
from __future__ import annotations

import math

import bh_lib as B

A = B.A
END = B.END
MIN_SQRT, MAX_SQRT = 4295128739, 1461446703485210103287273052203988822378723970342
CACHE_F = B.CACHE_BH / "depth_calls.json"
_cache = A.read_json(CACHE_F) if CACHE_F.exists() else {}

POOLS = {
    "blackhole": {"pool": B.POOL, "quoter": B.DOCS["quoterV2"], "kind": "algebra"},
    "pharaoh": {"pool": B.PHARAOH_POOL, "quoter": B.PHARAOH_QUOTER, "kind": "ramses_v3"},
}
CALLS: dict[str, list] = {k: [] for k in POOLS}


def quote(name: str, sell_wavax: bool, amount_in: int, limit: int = 0) -> dict | None:
    p = POOLS[name]
    tin, tout = (B.WAVAX, B.USDC) if sell_wavax else (B.USDC, B.WAVAX)
    if p["kind"] == "algebra":
        sig = "quoteExactInputSingle((address,address,address,uint256,uint160))"
        args = B.a(tin) + B.a(tout) + B.a(B.POOL_DEPLOYER) + B.u(amount_in) + B.u(limit)
    else:
        sig = "quoteExactInputSingle((address,address,uint256,int24,uint160))"
        args = B.a(tin) + B.a(tout) + B.u(amount_in) + B.u(10) + B.u(limit)
    key = f"{name}|{int(sell_wavax)}|{amount_in}|{limit}"
    if key not in _cache:
        _cache[key] = B.try_call(p["quoter"], sig, args, END)
        if len(_cache) % 10 == 0:
            A.write_json(CACHE_F, _cache)
    raw = _cache[key]
    rec = {"direction": "sell_WAVAX" if sell_wavax else "sell_USDC", "amount_in": str(amount_in), "limit_sqrt_price": str(limit)}
    if raw is None:
        rec["reverted"] = True
        CALLS[name].append(rec)
        return None
    w = B.W(raw)
    if p["kind"] == "algebra":
        rec.update({"amount_out": str(w[0]), "amount_in_used": str(w[1]), "sqrt_price_after": str(w[2]), "ticks_crossed": w[3], "fee_ppm": w[5]})
    else:
        rec.update({"amount_out": str(w[0]), "sqrt_price_after": str(w[1]), "ticks_crossed": w[2]})
    CALLS[name].append(rec)
    return rec


def reached(sell_wavax: bool, sq_after: int, target: int) -> bool:
    return sq_after <= target if sell_wavax else sq_after >= target


def solve(name: str, sell_wavax: bool, sq0: int, liq: int, move_bp: int) -> dict:
    target = math.isqrt(sq0 * sq0 * (10000 + move_bp) // 10000)
    # first guess from the active liquidity (no fee): dx = L (1/sqrtP' - 1/sqrtP), dy = L (sqrtP' - sqrtP)
    if sell_wavax:
        guess = liq * (sq0 - target) * (1 << 96) // (sq0 * target)
    else:
        guess = liq * (target - sq0) // (1 << 96)
    guess = max(guess, 1000)
    lo, hi, hi_q = 0, None, None
    amt = guess
    exhausted = None
    for _ in range(60):
        q = quote(name, sell_wavax, amt)
        if q is None:
            raise SystemExit(f"{name}: quote reverted for amount {amt}")
        sa = int(q["sqrt_price_after"])
        if reached(sell_wavax, sa, target):
            hi, hi_q = amt, q
            break
        lo = amt
        amt *= 2
    if hi is None:
        raise SystemExit("no bracket")
    # liquidity ran out before the target if the price fell to the quoter's default limit
    if (sell_wavax and int(hi_q["sqrt_price_after"]) <= MIN_SQRT + 1) or (not sell_wavax and int(hi_q["sqrt_price_after"]) >= MAX_SQRT - 1):
        exhausted = True
    while hi - lo > max(1, hi // 10 ** 9):
        mid = (lo + hi) // 2
        q = quote(name, sell_wavax, mid)
        sa = int(q["sqrt_price_after"])
        if reached(sell_wavax, sa, target):
            hi, hi_q = mid, q
        else:
            lo = mid
    res = {"move_bp": move_bp, "target_sqrt_price": str(target), "amount_in": str(hi), "amount_out": hi_q["amount_out"],
           "sqrt_price_after": hi_q["sqrt_price_after"], "bracket_lo_not_reaching_target": str(lo),
           "ticks_crossed": hi_q.get("ticks_crossed")}
    if exhausted:
        # the smallest amount that drives the price to the limit = the largest sale that still fills
        res["exhausted"] = True
    # independent check: let the quoter stop at the target itself
    big = hi * 4
    ql = quote(name, sell_wavax, big, target)
    if ql is not None:
        res["limit_price_check"] = {"amount_in_offered": str(big), "amount_out": ql["amount_out"], "sqrt_price_after": ql["sqrt_price_after"],
                                    "amount_in_used": ql.get("amount_in_used"),
                                    "amount_out_rel_diff_vs_bisection": abs(int(ql["amount_out"]) - int(hi_q["amount_out"])) / int(hi_q["amount_out"])}
        if ql.get("amount_in_used"):
            res["limit_price_check"]["amount_in_rel_diff_vs_bisection"] = abs(int(ql["amount_in_used"]) - hi) / hi
    return res


def main() -> None:
    st = A.read_json(B.DATA_BH / "state_at_end.json")
    out = {"block": END, "block_hash": B.END_HASH, "block_iso": A.iso(B.END_TS), "state_host": "api.avax.network (archive eth_call)",
           "method": "bisection on the exact-input amount of each protocol's QuoterV2.quoteExactInputSingle until sqrtPriceX96After is at the target; see the script header",
           "formulas": {"target_sqrt": "floor(sqrt(sqrtP0^2 * (10000 + move_bp) / 10000))",
                        "P0_usdc_per_wavax": "(sqrtP0 / 2^96)^2 * 1e12",
                        "usd_in": "sell_WAVAX: amount_in / 1e18 * P0 ; sell_USDC: amount_in / 1e6",
                        "avg_price": "sell_WAVAX: (amount_out / 1e6) / (amount_in / 1e18) ; sell_USDC: (amount_in / 1e6) / (amount_out / 1e18)",
                        "cost_bp": "sell_WAVAX: (1 - avg_price / P0) * 1e4 ; sell_USDC: (avg_price / P0 - 1) * 1e4  (includes the swap fee)",
                        "price_reached_pct": "((sqrt_price_after / sqrtP0)^2 - 1) * 100",
                        "liquidity_within_band_usd": "USDC out of the sell to -x % / 1e6  +  WAVAX out of the buy to +x % / 1e18 * P0"},
           "pools": {}}
    for name, p in POOLS.items():
        if name == "blackhole":
            sq0 = int(st["A_dataset_pool"]["globalState"]["price_sqrtX96"])
            liq = int(st["A_dataset_pool"]["liquidity"])
            bw, bu = int(st["A_dataset_pool"]["balance_WAVAX"]), int(st["A_dataset_pool"]["balance_USDC"])
            fee = st["A_dataset_pool"]["fee()_ppm"]
        else:
            sq0 = int(st["E_pharaoh_pool"]["slot0"]["sqrtPriceX96"])
            liq = int(st["E_pharaoh_pool"]["liquidity"])
            bw, bu = int(st["E_pharaoh_pool"]["balance_WAVAX"]), int(st["E_pharaoh_pool"]["balance_USDC"])
            fee = st["E_pharaoh_pool"]["fee()_ppm"]
        p0 = (sq0 / 2 ** 96) ** 2 * 1e12
        e = {"pool": p["pool"], "quoter": p["quoter"], "sqrt_price_x96_pre": str(sq0), "P0_usdc_per_wavax": p0,
             "active_liquidity": str(liq), "fee_ppm": fee, "balance_WAVAX": str(bw), "balance_USDC": str(bu),
             "tvl_usd_at_pool_price": bw / 1e18 * p0 + bu / 1e6, "targets": []}
        # quoter sanity: a 1-wei-scale quote must start from the pool's own price
        tiny = quote(name, True, 10 ** 12)
        e["quoter_vs_pool_check"] = {"amount_in": "1000000000000", "sqrt_price_after": tiny["sqrt_price_after"],
                                     "rel_diff_to_pool_sqrt_price": abs(int(tiny["sqrt_price_after"]) - sq0) / sq0}
        res = {}
        for sell, bp in ((True, -100), (True, -500), (True, -1000), (False, 100), (False, 500), (False, 1000)):
            r = solve(name, sell, sq0, liq, bp)
            ain, aout = int(r["amount_in"]), int(r["amount_out"])
            if sell:
                r["direction"] = "sell_WAVAX"
                r["wavax_in"] = ain / 1e18
                r["usdc_out"] = aout / 1e6
                r["usd_in_at_P0"] = ain / 1e18 * p0
                r["avg_price"] = (aout / 1e6) / (ain / 1e18)
                r["cost_bp"] = (1 - r["avg_price"] / p0) * 1e4
            else:
                r["direction"] = "sell_USDC"
                r["usdc_in"] = ain / 1e6
                r["wavax_out"] = aout / 1e18
                r["usd_in_at_P0"] = ain / 1e6
                r["avg_price"] = (ain / 1e6) / (aout / 1e18)
                r["cost_bp"] = (r["avg_price"] / p0 - 1) * 1e4
            r["price_reached_pct"] = ((int(r["sqrt_price_after"]) / sq0) ** 2 - 1) * 100
            res[bp] = r
            e["targets"].append(r)
            print(name, r["direction"], bp, "in", ain, "out", aout, "cost_bp", round(r["cost_bp"], 2), "reached%", round(r["price_reached_pct"], 4),
                  "check", r.get("limit_price_check", {}).get("amount_out_rel_diff_vs_bisection"), flush=True)
            A.write_json(CACHE_F, _cache)
        e["liquidity_within_band_usd"] = {f"+/-{x // 100}%": int(res[-x]["amount_out"]) / 1e6 + int(res[x]["amount_out"]) / 1e18 * p0 for x in (100, 500, 1000)}
        e["liquidity_within_band_parts"] = {f"+/-{x // 100}%": {"usdc_below (out of the sell)": int(res[-x]["amount_out"]) / 1e6,
                                                               "wavax_above (out of the buy)": int(res[x]["amount_out"]) / 1e18} for x in (100, 500, 1000)}
        e["quote_calls"] = CALLS[name]
        out["pools"][name] = e
    A.write_json(CACHE_F, _cache)
    A.write_json(B.DATA_BH / "depth_at_end.json", out)
    for name, e in out["pools"].items():
        print(name, "P0", e["P0_usdc_per_wavax"], "TVL", round(e["tvl_usd_at_pool_price"]), "L", e["active_liquidity"], "band", e["liquidity_within_band_usd"], len(e["quote_calls"]), "calls")


if __name__ == "__main__":
    main()
