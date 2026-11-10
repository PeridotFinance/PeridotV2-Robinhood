"""Step 7: RAM rewards and a RAM price series.

gauge_<sym>_notify.csv.gz: NotifyReward(address indexed from, address indexed reward, uint256 amount, uint256 period)
  on the NVDA and SPY gauges, full history: block,log_index,ts,from,token,amount,period.
ram_pool_swaps.csv.gz: swaps of the WETH/RAM Ramses CL pool (top TVL among RAM pools in the gateway snapshot) with
  interpolated ts >= 2026-08-01T00:00:00Z, same columns as the Ramses swap files (token0 = WETH, token1 = RAM, both 18 dp;
  feed_eth_usd converts the WETH quote to USD).
"""
from __future__ import annotations

import calendar

from rh_lib import *  # noqa: F401,F403

RAM_SINCE = calendar.timegm((2026, 8, 1, 0, 0, 0))


def main():
    end = load_end()["END"]
    disc = jload(DISCOVERY_FILE)
    st = jload(END_STATE_FILE)
    r = rpc()
    ti = time_index(r)
    infos = {}

    # ---- gauges -------------------------------------------------------------------------------------------
    topic = T(SIG["notify_reward"])
    for sym, gauge in GAUGES.items():
        logs = dedup_sorted(r.logs(gauge, [topic], 0, end, end))
        if logs and logs[0]["blockNumber"] < min(ti.grid):
            raise SystemExit(f"gauge {sym}: first event at {logs[0]['blockNumber']} is below the header grid; run rh_03_headers.py --from=")
        rows, tokens, senders = [], {}, {}
        for l in logs:
            w = words(l["data"])
            if len(l["topics"]) != 3 or len(w) != 2:
                raise EvidenceError("NotifyReward layout is not (indexed from, indexed token, amount, period)")
            frm, tok = topic_addr(l["topics"][1]), topic_addr(l["topics"][2])
            rows.append((l["blockNumber"], l["logIndex"], ts_fmt(ti.ts(l["blockNumber"])), frm, tok, w[0], w[1]))
            t = tokens.setdefault(tok, {"events": 0, "amount_raw": 0})
            t["events"] += 1
            t["amount_raw"] += w[0]
            senders[frm] = senders.get(frm, 0) + 1
        info = write_csv_gz(f"gauge_{sym}_notify.csv.gz", ["block", "log_index", "ts", "from", "token", "amount", "period"], rows)
        info.update({"gauge": gauge, "topic0": topic, "signature": SIG["notify_reward"],
                     "by_token": {k: {"events": v["events"], "amount_raw": str(v["amount_raw"])} for k, v in tokens.items()}, "by_sender": senders,
                     "ram_token": RAM.lower(), "amount_decimals_note": "amount is the raw token amount (RAM has 18 decimals); period is the event's 4th field as emitted"})
        infos[f"gauge_{sym}"] = info
        say("gauge", sym, info["rows"], info["by_token"])

    # ---- RAM pool --------------------------------------------------------------------------------------------
    pool = RAM_POOL
    static = disc["pool_static"]["ram_pool"]
    keys = sorted(k for k in ti.grid if k % GRID == 0)
    # last grid block at or before the cut: every swap from there is fetched, then filtered on the interpolated ts
    g0 = max(k for k in keys if ti.grid[k][0] <= RAM_SINCE)
    g1 = keys[keys.index(g0) + 1]
    t0, t1 = ti.grid[g0][0], ti.grid[g1][0]
    start_block = g0 + -(-(RAM_SINCE - t0) * (g1 - g0) // (t1 - t0))  # first block whose interpolated ts >= the cut
    swaps = scan_all(r, pool, [T(SIG["v3_swap"])], g0, end, progress=say)   # [block, log_index, topics[1:], data]
    ctl = {k: dedup_sorted(r.logs(pool, [T(SIG[k])], 0, end, end)) for k in ("feeadj", "setfee", "proto_a", "proto_b", "proto_c", "v3_init")}
    if ctl["proto_a"] or ctl["proto_c"]:
        raise EvidenceError("RAM pool: unexpected SetFeeProtocol variant")
    end_state = st["ramses"]["ram_pool"]
    fe = sorted(ctl["feeadj"] + ctl["setfee"], key=log_key)
    fee = Step(words(fe[0]["data"])[0], [(l["blockNumber"], l["logIndex"], words(l["data"])[1]) for l in fe]) if fe else Step(end_state["fee"], [])
    pe = ctl["proto_b"]
    proto = Step(words(pe[0]["data"])[0], [(l["blockNumber"], l["logIndex"], words(l["data"])[1]) for l in pe]) if pe else Step(end_state["feeProtocol_raw"], [])
    rows = []
    for b, i, _topics, data in swaps:
        t = ti.ts(b)
        if t < RAM_SINCE:
            continue
        w = words(data)
        rows.append((b, i, ts_fmt(t), s256(w[0]), s256(w[1]), w[2], w[3], sN(w[4], 24), fee.at(b, i), format(proto.at(b, i) / 1e6, "g")))
    info = write_csv_gz("ram_pool_swaps.csv.gz",
                        ["block", "log_index", "ts", "amount0", "amount1", "sqrt_price_x96", "liquidity", "tick", "fee_ppm", "protocol_share"], rows)
    iw = words(ctl["v3_init"][0]["data"]) if ctl["v3_init"] else None
    info.update({
        "pool": pool, "static": static, "token0": static["token0().symbol"], "decimals0": static["token0().decimals"],
        "token1": static["token1().symbol"], "decimals1": static["token1().decimals"], "tick_spacing": end_state["tickSpacing"],
        "price_note": "price of RAM in WETH = 1 / (sqrt_price_x96 / 2**96)**2 (token1 = RAM per token0 = WETH, both 18 dp); multiply by feed_eth_usd (8 dp) for USD",
        "since_unix": RAM_SINCE, "since_iso": iso(RAM_SINCE), "start_block_first_with_interpolated_ts_ge_cut": start_block, "fetched_from_block": g0,
        "swaps_fetched_before_cut_dropped": len(swaps) - len(rows),
        "initialize": {"block": ctl["v3_init"][0]["blockNumber"], "sqrtPriceX96": str(iw[0]), "tick": sN(iw[1], 24)} if iw else None,
        "fee_events": {"FeeAdjustment": len(ctl["feeadj"]), "SetFee": len(ctl["setfee"]), "initial_fee_ppm": fee.initial,
                       "fee_ppm_in_force_at_END_from_events": fee.last, "fee()_at_END": end_state["fee"], "match": fee.last == end_state["fee"],
                       "source_when_no_events": "fee() at END" if not fe else None},
        "protocol_share_events": {"SetFeeProtocol(uint24,uint24)": len(pe), "initial_ppm": proto.initial,
                                  "changes": [{"block": c[0], "log_index": c[1], "new_ppm": c[2]} for c in proto.changes],
                                  "in_force_at_END_from_events_ppm": proto.last, "slot0_feeProtocol_at_END": end_state["feeProtocol_raw"],
                                  "match": proto.last == end_state["feeProtocol_raw"], "source_when_no_events": "slot0().feeProtocol at END" if not pe else None},
        "topics": {"swap": T(SIG["v3_swap"])},
    })
    infos["ram_pool"] = info
    say("ram pool swaps", info["rows"], "fee match", info["fee_events"]["match"], "proto match", info["protocol_share_events"]["match"])
    jdump(STATE / "rewards_info.json", infos)


if __name__ == "__main__":
    main()
