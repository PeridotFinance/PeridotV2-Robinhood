"""Step 5: Uniswap v4 pools USDG x {NVDA, SPY} -> v4_pools.json, v4_<sym>_swaps.csv.gz, v4_<sym>_liquidity.csv.gz.

Pool list: PoolManager Initialize events with (currency0, currency1) = the token pair in either order (the currencies
are indexed, so the topic filter is exact). There are several hundred such pools, so the swaps of ALL of them are
fetched in one scan (all pool ids OR-ed in topic position 1, 100,000-block windows) and counted per pool up to END.
Datasets: the known NVDA/USDG pool id and the SPY/USDG pool with the most swaps.

Swap columns: block,log_index,ts,amount0,amount1,sqrt_price_x96,liquidity,tick,fee_ppm
  amounts on the POOL's side: the v4 event reports the swapper's delta, so both signs are flipped; fee_ppm is the
  event's fee field. Liquidity columns: block,log_index,sender,tick_lower,tick_upper,liquidity_delta,salt.
"""
from __future__ import annotations

from rh_lib import *  # noqa: F401,F403


def main():
    end = load_end()["END"]
    disc = jload(DISCOVERY_FILE)
    st = jload(END_STATE_FILE)
    r = rpc()
    ti = time_index(r)

    pools = [dict(p) for p in disc["v4_pools"]]
    ids = [p["pool_id"] for p in pools]          # the id list (and so the scan cache key) is the one fixed at discovery
    by_id = {p["pool_id"]: p for p in pools}
    usdg0 = {p["pool_id"]: p["currency0"].lower() == USDG.lower() for p in pools}
    scan_lo = (min(p["init_block"] for p in pools) // V4_SCAN_STEP) * V4_SCAN_STEP
    stats = {pid: {"swaps": 0, "vol": 0, "first": None, "last": None} for pid in ids}
    keep = {V4_NVDA_POOL_ID.lower()}             # raw rows are kept for the dataset pools only; the SPY pool needs a second pass
    kept = {}

    def scan(keep_ids, count):
        seen_last = (-1, -1)
        for rows in v4_scan_swaps(r, ids, scan_lo, end, progress=say):
            for x in rows:
                k = (x[1], x[2])
                if k <= seen_last:
                    raise EvidenceError(f"v4 scan is not strictly ascending at {k}")
                seen_last = k
                pid = x[0]
                if count:
                    w = words(x[3])
                    s = stats[pid]
                    s["swaps"] += 1
                    s["vol"] += abs(sN(w[0] if usdg0[pid] else w[1], 128))
                    if s["first"] is None:
                        s["first"] = x[1]
                    s["last"] = x[1]
                if pid.lower() in keep_ids:
                    kept.setdefault(pid, []).append(x)

    scan(keep, True)
    # pools initialised between discovery and END: listed, and scanned one by one
    now = v4_enumerate(r, end)
    new = [p for p in now if p["pool_id"] not in by_id]
    for p in new:
        pid = p["pool_id"]
        stats[pid] = {"swaps": 0, "vol": 0, "first": None, "last": None}
        u0 = p["currency0"].lower() == USDG.lower()
        for l in r.logs(V4_POOL_MANAGER, [T(SIG["v4_swap"]), pid], p["init_block"], end, end):
            w = words(l["data"])
            s = stats[pid]
            s["swaps"] += 1
            s["vol"] += abs(sN(w[0] if u0 else w[1], 128))
            s["first"] = s["first"] if s["first"] is not None else l["blockNumber"]
            s["last"] = l["blockNumber"]
            kept.setdefault(pid, []).append([pid, l["blockNumber"], l["logIndex"], l["data"]])
        pools.append(p)
    for p in pools:
        s = stats[p["pool_id"]]
        p.pop("swaps_upto_discovery", None)
        p["swaps"] = s["swaps"]
        p["usdg_volume_raw"] = str(s["vol"])
        p["first_swap_block"], p["last_swap_block"] = s["first"], s["last"]
        if p["pool_id"] in st["v4"]:
            p["state_at_END"] = st["v4"][p["pool_id"]]

    chosen = {}
    for sym in ("NVDA", "SPY"):
        cand = [p for p in pools if p["symbol"] == sym and p["swaps"] > 0]
        most = max(cand, key=lambda p: p["swaps"]) if cand else None
        most_vol = max(cand, key=lambda p: int(p["usdg_volume_raw"])) if cand else None
        if sym == "NVDA":
            pick = [p for p in pools if p["pool_id"].lower() == V4_NVDA_POOL_ID.lower()]
            if not pick:
                raise EvidenceError("the known NVDA/USDG v4 pool id has no Initialize event")
            chosen[sym] = pick[0]
        elif most:
            chosen[sym] = most
        if sym in chosen:
            chosen[sym] = dict(chosen[sym], most_swaps_of_symbol=most["pool_id"] if most else None,
                               most_usdg_volume_of_symbol=most_vol["pool_id"] if most_vol else None)
    table = {"END": end, "pool_manager": V4_POOL_MANAGER, "state_view": V4_STATE_VIEW, "usdg": USDG, "nvda": NVDA, "spy": SPY,
             "initialize_signature": SIG["v4_init"], "initialize_topic0": T(SIG["v4_init"]),
             "method": "eth_getLogs on the PoolManager with topics [Initialize, any id, currency0, currency1] for both orders of each pair; "
                       "swaps counted from one scan of Swap events with all listed pool ids, blocks %d..END" % scan_lo,
             "pools_total": len(pools), "pools_by_symbol": {s: sum(1 for p in pools if p["symbol"] == s) for s in ("NVDA", "SPY")},
             "pools_with_swaps_by_symbol": {s: sum(1 for p in pools if p["symbol"] == s and p["swaps"]) for s in ("NVDA", "SPY")},
             "swaps_total_by_symbol": {s: sum(p["swaps"] for p in pools if p["symbol"] == s) for s in ("NVDA", "SPY")},
             "pools_initialised_after_discovery": [p["pool_id"] for p in new],
             "dataset_pool": {s: p["pool_id"] for s, p in chosen.items()},
             "usdg_volume_raw_note": "sum of |USDG leg| over the pool's swaps, raw 6-decimal units",
             "pools": sorted(pools, key=lambda p: (p["symbol"], -p["swaps"], p["init_block"]))}
    jdump(DATA / "v4_pools.json", table)
    for sym in ("NVDA", "SPY"):
        for p in [p for p in table["pools"] if p["symbol"] == sym][:6]:
            say(sym, p["pool_id"], "fee", p["fee"], "spacing", p["tick_spacing"], "hooks", p["hooks"], "init", p["init_block"], "swaps", p["swaps"],
                "usdg vol", int(p["usdg_volume_raw"]) / 1e6)

    need = {p["pool_id"].lower() for p in chosen.values()} - {k.lower() for k in kept}
    if need:
        scan(need, False)                        # second pass over the cached windows for the chosen SPY pool
    infos = {}
    for sym, p in chosen.items():
        sw = sorted(kept.get(p["pool_id"], []), key=lambda x: (x[1], x[2]))
        if len(sw) != p["swaps"]:
            raise EvidenceError(f"v4 {sym}: kept {len(sw)} rows but counted {p['swaps']}")
        md = dedup_sorted(r.logs(V4_POOL_MANAGER, [T(SIG["v4_modify"]), p["pool_id"]], p["init_block"], end, end))
        if sw and sw[0][1] < min(ti.grid):
            raise SystemExit(f"v4 {sym}: swaps start at {sw[0][1]}, below the header grid; run rh_03_headers.py --from={sw[0][1]}")

        def swap_rows():
            for _, b, i, data in sw:
                w = words(data)
                if len(w) != 6:
                    raise EvidenceError("v4 Swap data is not 6 words")
                yield (b, i, ts_fmt(ti.ts(b)), -sN(w[0], 128), -sN(w[1], 128), w[2], w[3], sN(w[4], 24), w[5])

        def mod_rows():
            for l in md:
                w = words(l["data"])
                if len(w) != 4:
                    raise EvidenceError("v4 ModifyLiquidity data is not 4 words")
                yield (l["blockNumber"], l["logIndex"], topic_addr(l["topics"][2]), sN(w[0], 24), sN(w[1], 24), s256(w[2]), "0x" + format(w[3], "064x"))

        s_info = write_csv_gz(f"v4_{sym.lower()}_swaps.csv.gz",
                              ["block", "log_index", "ts", "amount0", "amount1", "sqrt_price_x96", "liquidity", "tick", "fee_ppm"], swap_rows())
        l_info = write_csv_gz(f"v4_{sym.lower()}_liquidity.csv.gz",
                              ["block", "log_index", "sender", "tick_lower", "tick_upper", "liquidity_delta", "salt"], mod_rows())
        fees = {}
        for x in sw:
            f = words(x[3])[5]
            fees[f] = fees.get(f, 0) + 1
        infos[sym.lower()] = {"pool": p, "swaps": s_info, "liquidity": l_info, "swap_fee_field_histogram": {str(k): v for k, v in sorted(fees.items())},
                              "topics": {"swap": [T(SIG["v4_swap"]), p["pool_id"]], "modify": [T(SIG["v4_modify"]), p["pool_id"]]}}
        say("v4", sym, p["pool_id"], "swaps", s_info["rows"], "mods", l_info["rows"], "fee field histogram", fees)
    jdump(STATE / "v4_info.json", infos)


if __name__ == "__main__":
    main()
