"""Step 5b: Uniswap V3 pools USDG x {NVDA, SPY} -> univ3_pools.json, univ3_<sym>_swaps.csv.gz, univ3_<sym>_liquidity.csv.gz.

Every pool of the two pairs on the UniswapV3Factory is scanned from its creation block to END (streaming, compact
cache: one of these pools has millions of swaps). Per symbol the pool with the most swaps gets
``univ3_<sym>_swaps.csv.gz`` / ``univ3_<sym>_liquidity.csv.gz``; every other fee tier that has swaps gets
``univ3_<sym>_fee<fee>_swaps.csv.gz`` / ``..._liquidity.csv.gz``.

Swap columns as for the Ramses pools: block,log_index,ts,amount0,amount1,sqrt_price_x96,liquidity,tick,fee_ppm,protocol_share
  fee_ppm: the pool's immutable fee tier; protocol_share: 1/n of the swap fee where n is the feeProtocol value in force
  for the swap's INPUT token (SetFeeProtocol(uint8 old0, uint8 old1, uint8 new0, uint8 new1); 0 when n = 0).
Liquidity columns: block,log_index,kind,tick_lower,tick_upper,liquidity_delta (burn negative); standard Uniswap v3
  Mint(address sender, address indexed owner, int24 indexed tickLower, int24 indexed tickUpper, uint128 amount, uint256, uint256).
"""
from __future__ import annotations

import os

from rh_lib import *  # noqa: F401,F403

SWAP_HDR = ["block", "log_index", "ts", "amount0", "amount1", "sqrt_price_x96", "liquidity", "tick", "fee_ppm", "protocol_share"]
LIQ_HDR = ["block", "log_index", "kind", "tick_lower", "tick_upper", "liquidity_delta"]


def share(n: int) -> str:
    return "0" if n == 0 else format(1.0 / n, ".12g")


def main():
    end = load_end()["END"]
    disc = jload(STATE / "univ3_discovery.json")
    st = jload(END_STATE_FILE)
    r = rpc()
    ti = time_index(r)
    pools = [dict(p) for p in disc["pools"]]
    meta = {}
    for p in pools:
        a, lo = p["pool"], p["created_block"]
        if lo < min(ti.grid):
            raise SystemExit(f"univ3 pool {a} was created below the header grid; run rh_03_headers.py --from={lo}")
        es = st["univ3"][a]
        pe = dedup_sorted(r.logs(a, [T(SIG["proto_a"])], lo, end, end))
        fp_init = (words(pe[0]["data"])[0], words(pe[0]["data"])[1]) if pe else (es["feeProtocol_raw"] % 16, es["feeProtocol_raw"] >> 4)
        fp = Step(fp_init, [(l["blockNumber"], l["logIndex"], (words(l["data"])[2], words(l["data"])[3])) for l in pe])
        fee = p["fee"]
        usdg0 = p["token0"].lower() == USDG.lower()
        acc = {"vol": 0, "first": None, "last": None}

        def swap_rows():
            for rows in scan_compact(r, a, [T(SIG["v3_swap"])], lo, end, progress=say):
                for b, i, _topics, data in rows:
                    w = words(data)
                    if len(w) != 5:
                        raise EvidenceError("v3 Swap data is not 5 words")
                    a0, a1 = s256(w[0]), s256(w[1])
                    acc["vol"] += abs(a0 if usdg0 else a1)
                    if acc["first"] is None:
                        acc["first"] = b
                    acc["last"] = b
                    n0, n1 = fp.at(b, i)
                    yield (b, i, ts_fmt(ti.ts(b)), a0, a1, w[2], w[3], sN(w[4], 24), fee, share(n0 if a0 > 0 else n1))

        mints = scan_all(r, a, [T(SIG["v3_mint_std"])], lo, end)
        burns = scan_all(r, a, [T(SIG["v3_burn"])], lo, end)

        def liq_rows():
            ev = [("mint", x) for x in mints] + [("burn", x) for x in burns]
            ev.sort(key=lambda e: (e[1][0], e[1][1]))
            for kind, (b, i, tp, data) in ev:
                w = words(data)
                lo_t, hi_t = topic_int(tp[1], 24), topic_int(tp[2], 24)
                if kind == "mint":
                    if len(w) != 4:
                        raise EvidenceError("Uniswap v3 Mint data is not 4 words (sender,amount,amount0,amount1)")
                    d = w[1]
                else:
                    if len(w) != 3:
                        raise EvidenceError("Burn data is not 3 words (amount,amount0,amount1)")
                    d = -w[0]
                yield (b, i, kind, lo_t, hi_t, d)

        tmp_s, tmp_l = f"univ3_tmp_{a}_swaps.csv.gz", f"univ3_tmp_{a}_liquidity.csv.gz"
        sw = write_csv_gz(tmp_s, SWAP_HDR, swap_rows())
        lq = write_csv_gz(tmp_l, LIQ_HDR, liq_rows())
        p["swaps"], p["mints"], p["burns"] = sw["rows"], len(mints), len(burns)
        p["first_swap_block"], p["last_swap_block"] = acc["first"], acc["last"]
        p["usdg_volume_raw"] = str(acc["vol"])
        p["state_at_END"] = {k: v for k, v in es.items() if k != "slot0_words"}
        meta[a] = {"tmp": (tmp_s, tmp_l), "fp": fp, "pe": len(pe), "es": es, "usdg0": usdg0}
        say(p["symbol"], a, "fee", fee, "swaps", p["swaps"], "mints", p["mints"], "burns", p["burns"], "usdg vol", acc["vol"] / 1e6)

    files, table_files = {}, {}
    for sym in ("NVDA", "SPY"):
        ranked = sorted([p for p in pools if p["symbol"] == sym], key=lambda p: -p["swaps"])
        for rank, p in enumerate(ranked):
            a = p["pool"]
            m = meta[a]
            if p["swaps"] == 0:
                for t in m["tmp"]:
                    (DATA / t).unlink()
                continue
            stem = f"univ3_{sym.lower()}" if rank == 0 else f"univ3_{sym.lower()}_fee{p['fee']}"
            os.replace(DATA / m["tmp"][0], DATA / f"{stem}_swaps.csv.gz")
            os.replace(DATA / m["tmp"][1], DATA / f"{stem}_liquidity.csv.gz")
            es, fp, usdg0 = m["es"], m["fp"], m["usdg0"]
            common = {"contract": a, "factory": UNIV3_FACTORY, "symbol": sym, "fee": p["fee"], "tick_spacing": p["tick_spacing"],
                      "token0": "USDG" if usdg0 else sym, "decimals0": 6 if usdg0 else 18, "token1": sym if usdg0 else "USDG", "decimals1": 18 if usdg0 else 6,
                      "initialize": p["initialize"], "created_block": p["created_block"], "rank_by_swaps_within_symbol": rank + 1}
            fp_end = [es["feeProtocol_raw"] % 16, es["feeProtocol_raw"] >> 4]
            files[f"{stem}_swaps.csv.gz"] = dict(
                common, topics=[T(SIG["v3_swap"])], event=SIG["v3_swap"], fee_ppm_source="immutable pool fee",
                **{"fee()_at_END": es["fee"], "fee_match": es["fee"] == p["fee"]},
                protocol_share_events={"SetFeeProtocol(uint8,uint8,uint8,uint8)": m["pe"], "initial_feeProtocol0_1": list(fp.initial),
                                       "in_force_at_END_from_events": list(fp.last), "slot0_feeProtocol_at_END_0_1": fp_end, "match": list(fp.last) == fp_end},
                amount_sign="as emitted by the pool: positive = paid into the pool")
            files[f"{stem}_liquidity.csv.gz"] = dict(common, topics=[[T(SIG["v3_mint_std"]), T(SIG["v3_burn"])]], events=[SIG["v3_mint_std"], SIG["v3_burn"]],
                                                     mints=p["mints"], burns=p["burns"])
            table_files[a] = {"swaps": f"{stem}_swaps.csv.gz", "liquidity": f"{stem}_liquidity.csv.gz"}
            say("univ3", sym, a, "->", stem)
    for p in pools:
        p["dataset_files"] = table_files.get(p["pool"])
    table = {"END": end, "factory": UNIV3_FACTORY, "factory_source": disc["factory_source"], "factory_code_bytes": disc["factory_code_bytes"],
             "fee_tiers_enabled_tick_spacing": disc["fee_tiers_enabled"], "getPool": disc["getPool"], "repo_grep_note": disc["repo_grep_note"],
             "usdg_volume_raw_note": "sum of |USDG leg| over the pool's swaps, raw 6-decimal units",
             "pools": sorted(pools, key=lambda p: (p["symbol"], -p["swaps"]))}
    jdump(DATA / "univ3_pools.json", table)
    jdump(STATE / "univ3_info.json", {"files": files, "pools": table["pools"], "factory": UNIV3_FACTORY, "factory_source": disc["factory_source"],
                                      "repo_grep_note": disc["repo_grep_note"]})


if __name__ == "__main__":
    main()
