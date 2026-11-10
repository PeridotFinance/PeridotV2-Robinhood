"""Step 1 (before END is fixed): discover what exists, so the END state reads cover every pool.

Writes data/robinhood/state/discovery.json:
  * Uniswap v4 pools USDG x {NVDA, SPY} from PoolManager Initialize events (both currency orders) with swap counts
    (one scan of Swap events with every pool id, cached per 100,000-block window);
  * the RAM pool's static facts (token order, fee, spacing, factory);
  * each feed proxy's aggregator(), decimals(), description(), phaseId() and phaseAggregators(1..phaseId);
  * NotifyReward signature check on the gauges; state-retention probe.
Read only. Re-runnable; results depend on the head at run time (recorded).
"""
from __future__ import annotations

from rh_lib import *  # noqa: F401,F403


def as_addr(res: str) -> str:
    return "0x" + res[-40:]


def as_str(res: str) -> str:
    b = bytes.fromhex(res[2:])
    n = int.from_bytes(b[32:64], "big")
    return b[64:64 + n].decode(errors="replace")


def try_call(r, to, sig, *args, block="latest"):
    try:
        return r.eth_call(to, call_data(sig, *args), block)
    except EvidenceError as e:
        return "ERR " + str(e)[:160]


def main():
    r = rpc()
    head = r.head()
    final = ((head - 2000) // GRID) * GRID  # windows at or before this are cached; the real END is fixed later
    out = {"head_at_discovery": head, "chain_id": r.chain_id()}
    say("head", head, "chain", out["chain_id"])

    # ---- state retention probe -----------------------------------------------------------------------
    probe = {}
    pool = RAMSES_POOLS["nvda"]["pool"]
    for back in (2000, 3000, 6000, 12000, 20000, 40000):
        res = try_call(r, pool, "slot0()", block=head - back)
        probe[str(back)] = "ok" if not res.startswith("ERR") else res
    out["state_retention_probe_blocks_behind_head"] = probe
    say("retention", probe)

    # ---- Ramses pools and the RAM pool: static facts ---------------------------------------------------
    stat = {}
    for name, addr in (("ramses_nvda", RAMSES_POOLS["nvda"]["pool"]), ("ramses_spy", RAMSES_POOLS["spy"]["pool"]), ("ram_pool", RAM_POOL)):
        s = {"address": addr}
        for fn in ("token0()", "token1()", "factory()"):
            s[fn] = as_addr(try_call(r, addr, fn))
        for fn in ("fee()", "tickSpacing()"):
            res = try_call(r, addr, fn)
            s[fn] = int(res, 16) if not res.startswith("ERR") else res
        for tok in ("token0()", "token1()"):
            d = try_call(r, s[tok], "decimals()")
            sy = try_call(r, s[tok], "symbol()")
            s[tok + ".decimals"] = int(d, 16) if not d.startswith("ERR") else d
            s[tok + ".symbol"] = as_str(sy) if not sy.startswith("ERR") else sy
        stat[name] = s
        say(name, s)
    out["pool_static"] = stat

    # ---- feeds ------------------------------------------------------------------------------------------
    feeds = {}
    for name, f in FEEDS.items():
        p = f["proxy"]
        d = {"proxy": p, "aggregator_named_in_task": f["aggregator"]}
        res = try_call(r, p, "aggregator()")
        d["aggregator_now"] = as_addr(res) if not res.startswith("ERR") else res
        res = try_call(r, p, "decimals()")
        d["decimals"] = int(res, 16) if not res.startswith("ERR") else res
        res = try_call(r, p, "description()")
        d["description"] = as_str(res) if not res.startswith("ERR") else res
        res = try_call(r, p, "phaseId()")
        d["phaseId"] = int(res, 16) if not res.startswith("ERR") else res
        d["phaseAggregators"] = {}
        if isinstance(d["phaseId"], int):
            for i in range(1, d["phaseId"] + 1):
                res = try_call(r, p, "phaseAggregators(uint16)", i)
                d["phaseAggregators"][str(i)] = as_addr(res) if not res.startswith("ERR") else res
        agg = d["aggregator_now"]
        if not agg.startswith("ERR"):
            res = try_call(r, agg, "typeAndVersion()")
            d["aggregator_typeAndVersion"] = as_str(res) if not res.startswith("ERR") else res
            res = try_call(r, agg, "decimals()")
            d["aggregator_decimals"] = int(res, 16) if not res.startswith("ERR") else res
        feeds[name] = d
        say("feed", name, d)
    out["feeds"] = feeds

    # ---- gauges: what the gauge emits ----------------------------------------------------------------------
    g = {}
    for sym, gauge in GAUGES.items():
        logs = r.logs(gauge, [T(SIG["notify_reward"])], 0, final, final)
        info = {"gauge": gauge, "notify_reward_events": len(logs), "signature": SIG["notify_reward"], "topic0": T(SIG["notify_reward"])}
        if logs:
            l = logs[-1]
            rc = r.receipt(l["tx"])
            info["sample_tx"] = l["tx"]
            info["sample_tx_gauge_topic0s"] = sorted({x["topics"][0] for x in rc["logs"] if x["address"].lower() == gauge.lower()})
            info["sample_topics_count"] = len(l["topics"])
            info["sample_data_words"] = len(l["data"][2:]) // 64
        # every topic0 the gauge emitted in the last 2,000,000 blocks
        recent = r.logs(gauge, [], max(final - 2_000_000, 0), final, final)
        hist = {}
        for x in recent:
            hist[x["topics"][0]] = hist.get(x["topics"][0], 0) + 1
        info["topic0_histogram_last_2M_blocks"] = hist
        g[sym] = info
        say("gauge", sym, info)
    out["gauges"] = g

    # ---- v4 pools (last: the all-pool swap scan is the long part) -------------------------------------
    pools = v4_enumerate(r, final)
    say("v4 pools found", len(pools), {sym: sum(1 for p in pools if p["symbol"] == sym) for sym in ("NVDA", "SPY")})
    out["v4_pools"] = pools
    out["v4_enumerated_upto"] = final
    jdump(DISCOVERY_FILE, out)
    scan_hi = (final // V4_SCAN_STEP) * V4_SCAN_STEP - 1
    scan_lo = (min(p["init_block"] for p in pools) // V4_SCAN_STEP) * V4_SCAN_STEP
    cnt, total = {}, 0
    for rows in v4_scan_swaps(r, [p["pool_id"] for p in pools], scan_lo, scan_hi, progress=say):
        for pid, b, i, data in rows:
            cnt[pid] = cnt.get(pid, 0) + 1
        total += len(rows)
    for p in pools:
        p["swaps_upto_discovery"] = cnt.get(p["pool_id"], 0)
    out["v4_scan"] = {"from": scan_lo, "to": scan_hi, "swaps": total, "pools_with_swaps": len(cnt)}
    top = sorted(pools, key=lambda p: -p["swaps_upto_discovery"])[:12]
    for p in top:
        say("  ", p["symbol"], p["pool_id"], "fee", p["fee"], "spacing", p["tick_spacing"], "hooks", p["hooks"], "init", p["init_block"], "swaps", p["swaps_upto_discovery"])

    jdump(DISCOVERY_FILE, out)
    say("wrote", DISCOVERY_FILE, "rpc calls", r.calls)


if __name__ == "__main__":
    main()
