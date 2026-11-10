"""Step 5b: Uniswap V3 (factory 0x1f7d7550b1b028f7571e69a784071f0205fd2efa) pools USDG x {NVDA, SPY}.

  rh_05b_univ3.py datasets            the two 0.30% pools, creation block .. END:
                                      univ3_<sym>_swaps.csv.gz, univ3_<sym>_liquidity.csv.gz
  rh_05b_univ3.py counts --minutes=N  the other fee tiers: swap count and USDG volume only (no tables), streamed;
                                      stops after N minutes and records how far each count got (re-run to continue:
                                      the scan cache is kept)
Both write univ3_pools.json (every pool of the two pairs: fee, spacing, creation, counts or dataset files).

Swap columns as for the Ramses pools: block,log_index,ts,amount0,amount1,sqrt_price_x96,liquidity,tick,fee_ppm,protocol_share
  amounts as emitted by the pool (positive = paid into the pool); fee_ppm: the pool's immutable fee (3000);
  protocol_share: FRACTION of the swap fee that goes to the protocol = 1/n, n being the feeProtocol value in force for the
  swap's INPUT token (slot0.feeProtocol packs n0 = value % 16 for token0-in, n1 = value >> 4 for token1-in; changes come
  from SetFeeProtocol(uint8 old0, uint8 old1, uint8 new0, uint8 new1); 0 when n = 0).
Liquidity columns: block,log_index,kind,tick_lower,tick_upper,liquidity_delta (burn negative); standard Uniswap v3
  Mint(address sender, address indexed owner, int24 indexed tickLower, int24 indexed tickUpper, uint128 amount, uint256, uint256).
"""
from __future__ import annotations

import sys
import time

from rh_lib import *  # noqa: F401,F403

DATASET_POOLS = {"NVDA": "0xb944cec30bd4175855215d767adc81f39e5f7e2b", "SPY": "0xa43b424bc609495aed4bcd88d654934b510b0ad9"}
SWAP_HDR = ["block", "log_index", "ts", "amount0", "amount1", "sqrt_price_x96", "liquidity", "tick", "fee_ppm", "protocol_share"]
LIQ_HDR = ["block", "log_index", "kind", "tick_lower", "tick_upper", "liquidity_delta"]
INFO = STATE / "univ3_info.json"


def share(n: int) -> str:
    return "0" if n == 0 else format(1.0 / n, ".12g")


def write_table(end, disc, info):
    pools = []
    for p in disc["pools"]:
        q = {k: p[k] for k in ("symbol", "pool", "token0", "token1", "fee", "tick_spacing", "created_block", "initialize", "getPool_matches")}
        q.update(info["pools"].get(p["pool"], {"swaps": None, "note": "not counted"}))
        pools.append(q)
    jdump(DATA / "univ3_pools.json", {
        "END": end, "factory": UNIV3_FACTORY, "factory_source": disc["factory_source"], "factory_code_bytes": disc["factory_code_bytes"],
        "fee_tiers_enabled_tick_spacing": disc["fee_tiers_enabled"], "getPool": disc["getPool"], "repo_grep_note": disc["repo_grep_note"],
        "usdg_volume_raw_note": "sum of |USDG leg| over the pool's swaps, raw 6-decimal units",
        "pools": pools})


def datasets(end, disc, st, r, ti, info):
    by = {p["pool"].lower(): p for p in disc["pools"]}
    for sym, a in DATASET_POOLS.items():
        p = by[a]
        lo = p["created_block"]
        if lo < min(ti.grid):
            raise SystemExit(f"univ3 pool {a} was created below the header grid; run rh_03_headers.py --from={lo}")
        es = st["univ3"][a]
        pe = dedup_sorted(r.logs(a, [T(SIG["proto_a"])], lo, end, end))
        fp_init = (words(pe[0]["data"])[0], words(pe[0]["data"])[1]) if pe else (es["feeProtocol_raw"] % 16, es["feeProtocol_raw"] >> 4)
        fp = Step(fp_init, [(l["blockNumber"], l["logIndex"], (words(l["data"])[2], words(l["data"])[3])) for l in pe])
        fee = p["fee"]
        usdg0 = p["token0"].lower() == USDG.lower()
        acc = {"vol": 0}

        def swap_rows():
            for rows in scan_compact(r, a, [T(SIG["v3_swap"])], lo, end, progress=say):
                for b, i, _topics, data in rows:
                    w = words(data)
                    if len(w) != 5:
                        raise EvidenceError("v3 Swap data is not 5 words")
                    a0, a1 = s256(w[0]), s256(w[1])
                    acc["vol"] += abs(a0 if usdg0 else a1)
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

        stem = f"univ3_{sym.lower()}"
        sw = write_csv_gz(f"{stem}_swaps.csv.gz", SWAP_HDR, swap_rows())
        lq = write_csv_gz(f"{stem}_liquidity.csv.gz", LIQ_HDR, liq_rows())
        fp_end = [es["feeProtocol_raw"] % 16, es["feeProtocol_raw"] >> 4]
        common = {"contract": a, "factory": UNIV3_FACTORY, "symbol": sym, "fee": fee, "tick_spacing": p["tick_spacing"],
                  "token0": "USDG" if usdg0 else sym, "decimals0": 6 if usdg0 else 18, "token1": sym if usdg0 else "USDG", "decimals1": 18 if usdg0 else 6,
                  "initialize": p["initialize"], "created_block": lo}
        info["files"][f"{stem}_swaps.csv.gz"] = dict(
            common, topics=[T(SIG["v3_swap"])], event=SIG["v3_swap"], fee_ppm_source="immutable pool fee",
            **{"fee()_at_END": es["fee"], "fee_match": es["fee"] == fee},
            protocol_share_unit="fraction of the swap fee taken by the protocol = 1/n for the swap's input token (n = feeProtocol nibble; 0 = none)",
            protocol_share_events={"SetFeeProtocol(uint8,uint8,uint8,uint8)": len(pe),
                                   "events": [{"block": l["blockNumber"], "log_index": l["logIndex"], "old0_old1_new0_new1": words(l["data"])} for l in pe],
                                   "initial_feeProtocol0_1": list(fp.initial), "in_force_at_END_from_events": list(fp.last),
                                   "slot0_feeProtocol_at_END_0_1": fp_end, "match": list(fp.last) == fp_end},
            amount_sign="as emitted by the pool: positive = paid into the pool")
        info["files"][f"{stem}_liquidity.csv.gz"] = dict(common, topics=[[T(SIG["v3_mint_std"]), T(SIG["v3_burn"])]], events=[SIG["v3_mint_std"], SIG["v3_burn"]],
                                                         mints=len(mints), burns=len(burns))
        info["pools"][a] = {"swaps": sw["rows"], "usdg_volume_raw": str(acc["vol"]), "mints": len(mints), "burns": len(burns), "complete": True,
                            "counted_through_block": end, "dataset_files": {"swaps": f"{stem}_swaps.csv.gz", "liquidity": f"{stem}_liquidity.csv.gz"},
                            "state_at_END": {k: v for k, v in es.items() if k != "slot0_words"}}
        say("univ3", sym, a, "swaps", sw["rows"], "mints", len(mints), "burns", len(burns), "usdg vol", acc["vol"] / 1e6,
            "feeProtocol match", list(fp.last) == fp_end)
        jdump(INFO, info)


def counts(end, disc, st, r, info, minutes):
    deadline = time.time() + minutes * 60
    todo = [p for p in disc["pools"] if p["pool"].lower() not in DATASET_POOLS.values()]
    todo.sort(key=lambda p: (p["fee"] == 500, p["symbol"]))      # the 0.05% pools are the busy ones: last
    for p in todo:
        a, lo = p["pool"], p["created_block"]
        usdg0 = p["token0"].lower() == USDG.lower()
        n, vol, through, complete = 0, 0, lo - 1, True
        for frm, to, rows in scan_pieces(r, a, [T(SIG["v3_swap"])], lo, end, progress=say):
            for b, i, _t, data in rows:
                w = words(data)
                vol += abs(s256(w[0] if usdg0 else w[1]))
            n += len(rows)
            through = to
            if time.time() > deadline and to < end:
                complete = False
                break
        info["pools"][a] = {"swaps": n if complete else None, "usdg_volume_raw": str(vol) if complete else None, "complete": complete,
                            "counted_through_block": through, "dataset_files": None,
                            "state_at_END": {k: v for k, v in st["univ3"][a].items() if k != "slot0_words"}}
        if not complete:
            info["pools"][a].update({"partial_swaps_through_counted_block": n, "partial_usdg_volume_raw": str(vol),
                                     "note": f"count stopped by the time budget at block {through}; blocks {through + 1}..{end} not scanned"})
        say("univ3 count", p["symbol"], a, "fee", p["fee"], "swaps", n, "usdg vol", vol / 1e6, "complete", complete, "through", through)
        jdump(INFO, info)
        if time.time() > deadline:
            for q in todo:
                if q["pool"] not in info["pools"]:
                    info["pools"][q["pool"]] = {"swaps": None, "complete": False, "counted_through_block": None, "dataset_files": None,
                                                "note": "not scanned: time budget used up"}
            jdump(INFO, info)
            break


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "datasets"
    minutes = float(([a.split("=")[1] for a in sys.argv if a.startswith("--minutes=")] or ["20"])[0])
    end = load_end()["END"]
    disc = jload(STATE / "univ3_discovery.json")
    st = jload(END_STATE_FILE)
    r = rpc()
    info = jload(INFO) if INFO.exists() else {}
    info.setdefault("files", {})
    info.setdefault("pools", {})
    info.update({"factory": UNIV3_FACTORY, "factory_source": disc["factory_source"], "repo_grep_note": disc["repo_grep_note"]})
    if mode == "datasets":
        datasets(end, disc, st, r, time_index(r), info)
    elif mode == "counts":
        counts(end, disc, st, r, info, minutes)
    else:
        raise SystemExit("usage: rh_05b_univ3.py datasets | counts --minutes=N")
    jdump(INFO, info)
    write_table(end, disc, info)


if __name__ == "__main__":
    main()
