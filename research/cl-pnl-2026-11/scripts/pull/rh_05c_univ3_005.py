"""Step 5c: the Uniswap V3 0.05% pools -> univ3_nvda_005_swaps.csv.gz, univ3_spy_005_swaps.csv.gz (swaps only).

Pools: NVDA/USDG 0xd4eb21209c4d6093f80b5b84f5c45cc093ea14a3 and SPY/USDG 0xa7bb1ac63bbab0c44316e6c8c455213441689167,
creation block .. END. The swap logs come from the compact scan cache that ``rh_05b_univ3.py counts`` filled (pieces
missing from the cache are fetched); only the pools' SetFeeProtocol events are fetched here.

Columns exactly as univ3_nvda_swaps.csv.gz: block,log_index,ts,amount0,amount1,sqrt_price_x96,liquidity,tick,fee_ppm,protocol_share
  fee_ppm = 500 (immutable); protocol_share = fraction of the swap fee taken by the protocol = 1/n for the swap's input
  token (n from SetFeeProtocol(uint8 old0, uint8 old1, uint8 new0, uint8 new1); 0 when n = 0).
Writes its facts to state/univ3_005_info.json (no other existing file is rewritten).
"""
from __future__ import annotations

from rh_lib import *  # noqa: F401,F403

POOLS = {"NVDA": "0xd4eb21209c4d6093f80b5b84f5c45cc093ea14a3", "SPY": "0xa7bb1ac63bbab0c44316e6c8c455213441689167"}
SWAP_HDR = ["block", "log_index", "ts", "amount0", "amount1", "sqrt_price_x96", "liquidity", "tick", "fee_ppm", "protocol_share"]


def share(n: int) -> str:
    return "0" if n == 0 else format(1.0 / n, ".12g")


def main():
    end = load_end()["END"]
    disc = jload(STATE / "univ3_discovery.json")
    st = jload(END_STATE_FILE)
    r = rpc()
    ti = time_index(r)
    by = {p["pool"].lower(): p for p in disc["pools"]}
    info = {"files": {}}
    for sym, a in POOLS.items():
        p = by[a]
        lo, fee = p["created_block"], p["fee"]
        if fee != 500:
            raise EvidenceError("not a 0.05% pool")
        es = st["univ3"][a]
        pe = dedup_sorted(r.logs(a, [T(SIG["proto_a"])], lo, end, end))
        fp_init = (words(pe[0]["data"])[0], words(pe[0]["data"])[1]) if pe else (es["feeProtocol_raw"] % 16, es["feeProtocol_raw"] >> 4)
        fp = Step(fp_init, [(l["blockNumber"], l["logIndex"], (words(l["data"])[2], words(l["data"])[3])) for l in pe])
        usdg0 = p["token0"].lower() == USDG.lower()
        acc = {"vol": 0, "fetched_pieces": 0}
        calls_before = r.calls

        def swap_rows():
            last = (-1, -1)
            for rows in scan_compact(r, a, [T(SIG["v3_swap"])], lo, end, progress=say):
                for b, i, _topics, data in rows:
                    if (b, i) <= last:
                        raise EvidenceError(f"scan cache is not strictly ascending at {(b, i)}")
                    last = (b, i)
                    w = words(data)
                    if len(w) != 5:
                        raise EvidenceError("v3 Swap data is not 5 words")
                    a0, a1 = s256(w[0]), s256(w[1])
                    acc["vol"] += abs(a0 if usdg0 else a1)
                    n0, n1 = fp.at(b, i)
                    yield (b, i, ts_fmt(ti.ts(b)), a0, a1, w[2], w[3], sN(w[4], 24), fee, share(n0 if a0 > 0 else n1))

        name = f"univ3_{sym.lower()}_005_swaps.csv.gz"
        sw = write_csv_gz(name, SWAP_HDR, swap_rows())
        fp_end = [es["feeProtocol_raw"] % 16, es["feeProtocol_raw"] >> 4]
        info["files"][name] = {
            "contract": a, "factory": UNIV3_FACTORY, "symbol": sym, "fee": fee, "tick_spacing": p["tick_spacing"],
            "token0": "USDG" if usdg0 else sym, "decimals0": 6 if usdg0 else 18, "token1": sym if usdg0 else "USDG", "decimals1": 18 if usdg0 else 6,
            "initialize": p["initialize"], "created_block": lo, "topics": [T(SIG["v3_swap"])], "event": SIG["v3_swap"],
            "rows": sw["rows"], "usdg_volume_raw": str(acc["vol"]),
            "swap_logs_from": "compact scan cache (data/.cache/robinhood/scan), filled by rh_05b_univ3.py counts",
            "eth_getLogs_calls_for_swaps_in_this_run": r.calls - calls_before,
            "fee_ppm_source": "immutable pool fee", "fee()_at_END": es["fee"], "fee_match": es["fee"] == fee,
            "protocol_share_unit": "fraction of the swap fee taken by the protocol = 1/n for the swap's input token (n = feeProtocol nibble; 0 = none)",
            "protocol_share_events": {"SetFeeProtocol(uint8,uint8,uint8,uint8)": len(pe),
                                      "events": [{"block": l["blockNumber"], "log_index": l["logIndex"], "old0_old1_new0_new1": words(l["data"])} for l in pe],
                                      "initial_feeProtocol0_1": list(fp.initial), "in_force_at_END_from_events": list(fp.last),
                                      "slot0_feeProtocol_at_END_0_1": fp_end, "match": list(fp.last) == fp_end},
            "amount_sign": "as emitted by the pool: positive = paid into the pool",
            "liquidity_file": None,
        }
        say(sym, a, "rows", sw["rows"], "usdg vol", acc["vol"] / 1e6, "proto events", info["files"][name]["protocol_share_events"],
            "swap getLogs calls", r.calls - calls_before)
        jdump(STATE / "univ3_005_info.json", info)


if __name__ == "__main__":
    main()
