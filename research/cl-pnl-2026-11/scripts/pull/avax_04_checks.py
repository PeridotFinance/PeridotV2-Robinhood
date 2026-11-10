"""Checks on the Avalanche datasets. Writes data/avalanche/checks.json (every result, including failures).

  1. structure        no duplicate (block, log_index), ascending order, windows tile [START, END] (from the build report,
                      re-verified here on the CSV files themselves)
  2. re-requests      (a) the whole pool window fetched a second time from another endpoint on another grid (if the
                          `pool_second` cache is complete): record-by-record comparison;
                      (b) 5 random ranges per dataset re-requested live from a source that did NOT serve them (publicnode
                          for ranges served by the tenderly gateway, the Routescan logs API for ranges served by
                          publicnode), with a different split: log counts and full records compared;
                      (c) 5 random 3,000-block ranges of pool swaps from the Routescan logs API (third source): counts
                          and the per-log timestamps compared with the ts column
  3. continuity       last swap's sqrt_price_x96 / tick against slot0() at END; legacy pair's last Sync against
                      getReserves() at END
  4. fee series       FeeAdjustment chain (old == previous new), first old == fee() at START-1, last new == fee() at END
  5. implied fee      for swaps confined to one tick-spacing bucket, exact integer v3 math:
                         token1 in:  net = ceil(L * (sqrtP1 - sqrtP0) / 2^96)                 implied = 1 - net / amount1
                         token0 in:  net = ceil(ceil(L * 2^96 * (sqrtP0 - sqrtP1) / sqrtP0) / sqrtP1)   implied = 1 - net / amount0
                      20 random swaps listed, plus the distribution over every eligible swap
  6. protocol share   feeGrowthGlobal{0,1}X128 at START and END, slot0.feeProtocol at both, SetFeeProtocol events
  7. gauge            sum of NotifyReward amounts per period against tokenTotalSupplyByPeriod at END; rewardRate
  8. feed             last AnswerUpdated against latestRoundData() at END
  9. timestamps       non-decreasing; 40 random swap blocks re-read from a second header endpoint
Read-only.
"""
from __future__ import annotations

import random
import statistics
import sys
import time
from collections import Counter

from avax_lib import (CACHE, DATA_AVAX, POOL, ROUTESCAN, RPC, TOPIC, amount0_delta_up, amount1_delta_up, http_json,
                      ledger_rows, read_csv_gz, read_json, read_json_gz, rpc, write_json)

SEED = 20261110
OUT = DATA_AVAX / "checks.json"


def col(head, rows, name, f=int):
    i = head.index(name)
    return [f(r[i]) for r in rows]


def structure(path) -> dict:
    head, rows = read_csv_gz(path)
    keys = list(zip(col(head, rows, "block"), col(head, rows, "log_index")))
    asc = all(a < b for a, b in zip(keys, keys[1:]))
    res = {"rows": len(rows), "duplicates": len(keys) - len(set(keys)), "strictly_ascending": asc}
    if "ts" in head:
        ts = col(head, rows, "ts")
        res["ts_non_decreasing"] = all(a <= b for a, b in zip(ts, ts[1:]))
    return res


def cached(name: str) -> list[dict]:
    out = []
    for r in ledger_rows(name):
        out.extend(read_json_gz(CACHE / "logs" / name / f"{r['from']}-{r['to']}.json.gz"))
    return out


def routescan_logs(address: str, topic0: str | None, lo: int, hi: int) -> list[tuple]:
    """Logs of `address` in [lo, hi] from the Routescan etherscan-style API (keyless; 1,000 logs a page), as
    (block, log_index, topic0, data, timestamp). The range is halved when a slice reaches 5 full pages."""
    out = []
    stack = [(lo, hi)]
    while stack:
        x, y = stack.pop()
        got, page, split = [], 1, False
        while True:
            url = f"{ROUTESCAN}?module=logs&action=getLogs&address={address}&fromBlock={x}&toBlock={y}&page={page}&offset=1000"
            if topic0:
                url += f"&topic0={topic0}"
            d = None
            for attempt in range(5):
                try:
                    d = http_json(url, timeout=60)
                    if isinstance(d.get("result"), list):
                        break
                except Exception as e:  # 429 and friends
                    d = {"result": str(e)[:200]}
                time.sleep(3 + 3 * attempt)
            time.sleep(0.7)
            part = d.get("result")
            if not isinstance(part, list):
                raise RuntimeError(f"routescan: {str(d)[:200]}")
            got.extend(part)
            if len(part) < 1000:
                break
            page += 1
            if page > 5 and y > x:
                split = True
                break
        if split:
            m = (x + y) // 2
            stack.extend([(x, m), (m + 1, y)])
            continue
        out.extend((int(g["blockNumber"], 16), int(g["logIndex"], 16) if g["logIndex"] not in ("0x", "") else 0, g["topics"][0], g["data"], int(g["timeStamp"], 16)) for g in got)
    return sorted(set(out))


def rerequest(served_hosts: set, address: str, topics, lo: int, hi: int) -> tuple[str, set]:
    """Fetch [lo, hi] again from a source that did not serve it: publicnode for ranges served by the tenderly gateway,
    the Routescan logs API for ranges served by publicnode (the tenderly gateway's free daily egress was exhausted
    during the pull, so it cannot be the alternate)."""
    if all("tenderly" in h for h in served_hosts):
        flt = {"address": address, "fromBlock": hex(lo), "toBlock": hex(hi)}
        if topics:
            flt["topics"] = topics
        got = rpc("publicnode", "eth_getLogs", [flt], timeout=120)
        return RPC["publicnode"].split("/")[2], {(int(x["blockNumber"], 16), int(x["logIndex"], 16), x["topics"][0], x["data"]) for x in got}
    single = topics[0] if topics and isinstance(topics[0], str) else None
    allowed = None if not topics else ({topics[0]} if isinstance(topics[0], str) else set(topics[0]))
    got = routescan_logs(address, single, lo, hi)
    return "api.routescan.io", {(b_, i_, t_, d_) for b_, i_, t_, d_, _ in got if allowed is None or t_ in allowed}


def recheck_ranges(name: str, address: str, topics, a: int, b: int, rng: random.Random, logs: list[dict]) -> list[dict]:
    led = ledger_rows(name)
    res = []
    for _ in range(5):
        span = rng.randint(7000, 45000)
        lo = rng.randint(a, b - span)
        hi = lo + span - 1
        served = {r["host"] for r in led if r["to"] >= lo and r["from"] <= hi}
        mid = lo + rng.randint(span // 4, 3 * span // 4)       # a split that is not on the original grid
        old = {(x["b"], x["i"], x["t"][0], x["d"]) for x in logs if lo <= x["b"] <= hi}
        try:
            src, n1 = rerequest(served, address, topics, lo, mid)
            src, n2 = rerequest(served, address, topics, mid + 1, hi)
            new = n1 | n2
            res.append({"from": lo, "to": hi, "split_at": mid, "originally_served_by": sorted(served), "re_requested_from": src,
                        "count_in_dataset": len(old), "count_re_requested": len(new), "identical_records": new == old})
        except Exception as e:
            res.append({"from": lo, "to": hi, "split_at": mid, "originally_served_by": sorted(served), "count_in_dataset": len(old), "error": str(e)[:300]})
    return res


def liquidity_at_end(st: dict, last_swap_key: tuple, last_swap_liquidity: int) -> dict:
    """liquidity() at END must equal the last swap's liquidity plus every Mint/Burn after that swap whose range contains
    the END tick (tick_lower <= tick < tick_upper); the price does not move without a swap."""
    head, rows = read_csv_gz(DATA_AVAX / "pharaoh_wavax_usdc_liquidity.csv.gz")
    tick = st["pool"]["slot0"]["tick"]
    after = [dict(zip(head, r)) for r in rows if (int(r[0]), int(r[1])) > last_swap_key]
    inr = [d for d in after if int(d["tick_lower"]) <= tick < int(d["tick_upper"])]
    delta = sum(int(d["liquidity_delta"]) for d in inr)
    return {"last_swap_liquidity": str(last_swap_liquidity), "liquidity()_at_END": st["pool"]["liquidity"],
            "mint_burn_events_after_last_swap": len(after), "of_which_in_range_at_END_tick": inr,
            "sum_in_range_liquidity_delta": str(delta),
            "last_swap_liquidity_plus_delta_equals_liquidity_at_END": last_swap_liquidity + delta == int(st["pool"]["liquidity"])}


def main() -> None:
    if "--only-liquidity" in sys.argv:       # refresh this one offline check inside an existing checks.json
        C = read_json(OUT)
        st = read_json(DATA_AVAX / "state_at_end.json")["decoded"]
        ls = C["price_continuity"]["last_swap"]
        C["price_continuity"]["liquidity_reconciliation"] = liquidity_at_end(st, (ls["block"], ls["log_index"]), int(ls["liquidity"]))
        write_json(OUT, C)
        print(C["price_continuity"]["liquidity_reconciliation"])
        return
    rng = random.Random(SEED)
    end = read_json(DATA_AVAX / "END.json")
    st = read_json(DATA_AVAX / "state_at_end.json")["decoded"]
    rep = read_json(DATA_AVAX / "build_report.json")
    pools = read_json(DATA_AVAX / "phar_pools.json")
    a, b = end["start"]["number"], end["end"]["number"]
    C: dict = {"seed": SEED, "end": end["end"], "start": end["start"], "run_at_unix": int(time.time())}

    # ---- 1. structure
    C["structure"] = {}
    for f in sorted(DATA_AVAX.glob("*.csv.gz")):
        C["structure"][f.name] = structure(f)
    C["structure"]["requested_ranges"] = {k: {"windows": v["windows"], "tiling_ok": v["window_tiling_ok"], "sources": v["sources"],
                                               "logs": v["logs"], "note": "every requested range with its count and host is in build_report.json -> <dataset>.ranges"}
                                           for k, v in rep.items()}

    # ---- 2. re-requests
    pool_logs = cached("pool")
    R: dict = {}
    try:
        sec_led = ledger_rows("pool_second")
        complete = sec_led[0]["from"] == a and sec_led[-1]["to"] == b and all(x["to"] + 1 == y["from"] for x, y in zip(sec_led, sec_led[1:]))
    except FileNotFoundError:
        sec_led, complete = [], False
    if complete:
        sec = cached("pool_second")
        k1 = {(x["b"], x["i"]): (x["t"], x["d"], x["tx"], x["h"]) for x in pool_logs}
        k2 = {(x["b"], x["i"]): (x["t"], x["d"], x["tx"], x["h"]) for x in sec}
        R["full_second_pass_pool"] = {
            "first_pass": {"hosts": dict(Counter(r["host"] for r in ledger_rows("pool"))), "windows": len(ledger_rows("pool")), "logs": len(pool_logs)},
            "second_pass": {"hosts": dict(Counter(r["host"] for r in sec_led)), "windows": len(sec_led), "logs": len(sec)},
            "only_in_first": len(set(k1) - set(k2)), "only_in_second": len(set(k2) - set(k1)),
            "same_key_different_content": sum(1 for k in k1 if k in k2 and k1[k] != k2[k]),
            "identical": k1 == k2}
    else:
        R["full_second_pass_pool"] = {"status": "second pass not complete; not compared", "windows_cached": len(sec_led)}
    # the part of the pool window that publicnode served in BOTH passes: compared in full with Routescan
    tail = [r for r in ledger_rows("pool") if "tenderly" not in r["host"]]
    if tail:
        lo, hi = tail[0]["from"], tail[-1]["to"]
        mine = {(x["b"], x["i"], x["t"][0], x["d"]) for x in pool_logs if lo <= x["b"] <= hi}
        try:
            theirs = set()
            for x in range(lo, hi + 1, 5000):
                theirs |= {(b_, i_, t_, d_) for b_, i_, t_, d_, _ in routescan_logs(POOL, None, x, min(x + 4999, hi))}
            R["pool_range_served_by_publicnode_in_both_passes"] = {"from": lo, "to": hi, "compared_with": "api.routescan.io (all topics, 5,000-block slices)",
                                                                    "count_in_dataset": len(mine), "count_routescan": len(theirs), "identical_records": mine == theirs}
        except Exception as e:
            R["pool_range_served_by_publicnode_in_both_passes"] = {"from": lo, "to": hi, "count_in_dataset": len(mine), "error": str(e)[:300]}
    R["random_ranges"] = {"pool": recheck_ranges("pool", POOL, None, a, b, rng, pool_logs)}
    extra = {"gauge": (st["gauge"]["gauge"], None), "feed": (st["chainlink_avax_usd"]["aggregator()"], [TOPIC["AnswerUpdated"]]),
             "phar_pool": (pools["chosen"]["address"], [[TOPIC["SwapV2"], TOPIC["Sync"]]]),
             "phar_cl_pool": (pools["cl_reference"]["address"], None)}
    for name, (addr, topics) in extra.items():
        if name in rep:
            R["random_ranges"][name] = recheck_ranges(name, addr, topics, a, b, rng, cached(name))
    # Routescan (third source), pool swaps only; it also returns each log's timestamp
    head, rows = read_csv_gz(DATA_AVAX / "pharaoh_wavax_usdc_swaps.csv.gz")
    sw = [[int(x) for x in r] for r in rows]
    ix = {n: i for i, n in enumerate(head)}
    rs = []
    for _ in range(5):
        lo = rng.randint(a, b - 3000)
        hi = lo + 2999
        mine = {(r[ix["block"]], r[ix["log_index"]]): r[ix["ts"]] for r in sw if lo <= r[ix["block"]] <= hi}
        try:
            got = routescan_logs(POOL, TOPIC["Swap"], lo, hi)
            theirs = {(b_, i_): ts_ for b_, i_, _, _, ts_ in got}
            rs.append({"from": lo, "to": hi, "count_in_dataset": len(mine), "count_routescan": len(theirs),
                       "same_keys": set(mine) == set(theirs), "timestamps_equal": mine == theirs})
        except Exception as e:
            rs.append({"from": lo, "to": hi, "count_in_dataset": len(mine), "error": str(e)[:200]})
        time.sleep(1.0)
    R["routescan_third_source_pool_swaps"] = {"host": "api.routescan.io", "ranges": rs}
    C["re_requests"] = R

    # ---- 3. continuity
    last = sw[-1]
    s0 = st["pool"]["slot0"]
    C["price_continuity"] = {
        "last_swap": {"block": last[ix["block"]], "log_index": last[ix["log_index"]], "sqrt_price_x96": str(last[ix["sqrt_price_x96"]]),
                      "tick": last[ix["tick"]], "liquidity": str(last[ix["liquidity"]])},
        "slot0_at_END": {"block": b, "sqrt_price_x96": s0["sqrtPriceX96"], "tick": s0["tick"], "liquidity()": st["pool"]["liquidity"]},
        "sqrt_price_equal": str(last[ix["sqrt_price_x96"]]) == s0["sqrtPriceX96"], "tick_equal": last[ix["tick"]] == s0["tick"],
        "liquidity_equal": str(last[ix["liquidity"]]) == st["pool"]["liquidity"],
        "liquidity_note": "liquidity() can differ from the last swap's value when an in-range Mint/Burn follows the last swap",
        "blocks_between_last_swap_and_END": b - last[ix["block"]]}
    C["price_continuity"]["liquidity_reconciliation"] = liquidity_at_end(st, (last[ix["block"]], last[ix["log_index"]]), last[ix["liquidity"]])
    first = sw[0]
    C["price_continuity"]["window_start"] = {
        "slot0_at_START_minus_1": st["pool"]["at_start"]["slot0_at_start_minus_1"], "first_swap_block": first[ix["block"]],
        "note": "the first swap of the file traded against the state at START-1 (no swap can precede it inside the window)"}
    if "phar_cl_pool" in rep:
        e = pools["candidates"][pools["cl_reference"]["address"]]
        ls = rep["phar_cl_pool"].get("last_swap") or {}
        C["price_continuity"]["phar_cl_pool"] = {"last_swap": ls, "slot0_at_END": e["slot0"],
                                                  "sqrt_price_equal": ls.get("sqrt_price_x96") == e["slot0"]["sqrtPriceX96"], "tick_equal": ls.get("tick") == e["slot0"]["tick"]}
    if "phar_pool" in rep:
        e = pools["candidates"][pools["chosen"]["address"]]
        syncs = [x for x in cached("phar_pool") if x["t"][0] == TOPIC["Sync"]]
        r0, r1 = (int(syncs[-1]["d"][2:66], 16), int(syncs[-1]["d"][66:130], 16)) if syncs else (None, None)
        C["price_continuity"]["phar_pool_legacy"] = {"last_sync_in_window": {"block": syncs[-1]["b"] if syncs else None, "reserve0": str(r0), "reserve1": str(r1)},
                                                      "getReserves_at_END": e["reserves"],
                                                      "equal": syncs != [] and str(r0) == e["reserves"]["reserve0"] and str(r1) == e["reserves"]["reserve1"]}

    # ---- 4. fee series
    fs = rep["pool"]["fee_series"]
    C["fee_series"] = {**fs, "fee()_at_START_minus_1": st["pool"]["at_start"]["fee_ppm_at_start_minus_1"], "fee()_at_END": st["pool"]["fee_ppm"],
                       "first_old_equals_fee_at_START_minus_1": fs["first_old_fee"] == st["pool"]["at_start"]["fee_ppm_at_start_minus_1"],
                       "last_new_equals_fee_at_END": fs["last_new_fee"] == st["pool"]["fee_ppm"],
                       "last_swap_fee_ppm": last[ix["fee_ppm"]]}
    if "phar_cl_pool" in rep:
        e = pools["candidates"][pools["cl_reference"]["address"]]
        f2 = rep["phar_cl_pool"]["fee_series"]
        C["fee_series_phar_cl_pool"] = {**f2, "fee()_at_START": e["fee_ppm_at_start"], "fee()_at_END": e["fee_ppm"],
                                        "last_new_equals_fee_at_END": (f2["last_new_fee"] if f2["fee_adjustment_events"] else f2["fee_before_first_log"]) == e["fee_ppm"],
                                        "first_old_equals_fee_at_START": f2["first_old_fee"] in (None, e["fee_ppm_at_start"])}

    # ---- 5. implied fee
    sp = st["pool"]["tickSpacing"]
    p0 = int(st["pool"]["at_start"]["slot0_at_start_minus_1"]["sqrtPriceX96"])
    t0 = st["pool"]["at_start"]["slot0_at_start_minus_1"]["tick"]
    elig = []          # (index, implied_ppm, fee_ppm, gross, side)
    for k, r in enumerate(sw):
        p1, t1, L = r[ix["sqrt_price_x96"]], r[ix["tick"]], r[ix["liquidity"]]
        a0, a1 = r[ix["amount0"]], r[ix["amount1"]]
        if t0 // sp == t1 // sp and p1 != p0 and L > 0:
            if a1 > 0 and p1 > p0:
                net, gross, side = amount1_delta_up(p0, p1, L), a1, "token1_in"
            elif a0 > 0 and p1 < p0:
                net, gross, side = amount0_delta_up(p1, p0, L), a0, "token0_in"
            else:
                net = None
            if net is not None and gross > 0:
                elig.append((k, (gross - net) * 1e6 / gross, r[ix["fee_ppm"]], gross, side))
        p0, t0 = p1, t1
    big = [e for e in elig if (e[4] == "token1_in" and e[3] >= 10 ** 8) or (e[4] == "token0_in" and e[3] >= 10 ** 19)]
    sample = sorted(rng.sample(big, 20))
    diffs_all = [e[1] - e[2] for e in elig]
    diffs_big = [e[1] - e[2] for e in big]
    C["implied_fee"] = {
        "eligibility": f"previous swap's tick and this swap's tick in the same {sp}-tick bucket, price moved, input side positive",
        "size_filter_for_the_sample": "gross input >= 100 USDC (token1 in) or >= 10 WAVAX (token0 in), so integer rounding is below ~0.01 ppm of the amount",
        "swaps_total": len(sw), "eligible": len(elig), "eligible_and_large": len(big),
        "sample_of_20": [{"block": sw[e[0]][ix["block"]], "log_index": sw[e[0]][ix["log_index"]], "side": e[4], "gross_in": str(e[3]),
                          "implied_fee_ppm": round(e[1], 4), "fee_ppm": e[2], "diff_ppm": round(e[1] - e[2], 4)} for e in sample],
        "sample_median_implied_ppm": round(statistics.median(e[1] for e in sample), 4),
        "sample_median_fee_ppm": statistics.median(e[2] for e in sample),
        "sample_median_abs_diff_ppm": round(statistics.median(abs(e[1] - e[2]) for e in sample), 4),
        "sample_max_abs_diff_ppm": round(max(abs(e[1] - e[2]) for e in sample), 4),
        "all_eligible_large": {"n": len(big), "median_diff_ppm": round(statistics.median(diffs_big), 4),
                               "share_within_1ppm": round(sum(1 for d in diffs_big if abs(d) <= 1) / len(big), 6),
                               "share_within_5ppm": round(sum(1 for d in diffs_big if abs(d) <= 5) / len(big), 6),
                               "max_abs_diff_ppm": round(max(abs(d) for d in diffs_big), 2)},
        "all_eligible_below_size_filter_note": "every eligible swap at or above the size filter is in all_eligible_large; any deviation above its max_abs_diff_ppm therefore comes from a swap below the filter (not investigated further)",
        "all_eligible": {"n": len(elig), "median_diff_ppm": round(statistics.median(diffs_all), 4),
                         "share_within_1ppm": round(sum(1 for d in diffs_all if abs(d) <= 1) / len(elig), 6),
                         "share_within_5ppm": round(sum(1 for d in diffs_all if abs(d) <= 5) / len(elig), 6)},
    }
    worst = sorted(big, key=lambda e: -abs(e[1] - e[2]))[:10]
    C["implied_fee"]["largest_deviations_large_swaps"] = [
        {"block": sw[e[0]][ix["block"]], "log_index": sw[e[0]][ix["log_index"]], "side": e[4], "gross_in": str(e[3]),
         "implied_fee_ppm": round(e[1], 3), "fee_ppm": e[2]} for e in worst]

    # ---- 6. protocol share
    ps, pe = st["pool"]["at_start"], st["pool"]
    C["protocol_share"] = {
        "slot0.feeProtocol_at_START": ps["slot0"]["feeProtocol"], "slot0.feeProtocol_at_END": pe["slot0"]["feeProtocol"],
        "denominator": 1000000, "denominator_source": "RamsesV3Pool.sol FEE_DENOM = 1_000_000 (verified source on Sourcify, match)",
        "SetFeeProtocol_events_in_window": rep["pool"]["logs_per_topic0"].get("SetFeeProtocol", 0) + rep["pool"]["logs_per_topic0"].get("SetFeeProtocolLegacy", 0),
        "feeGrowthGlobal0X128_START": ps["feeGrowthGlobal0X128"], "feeGrowthGlobal0X128_END": pe["feeGrowthGlobal0X128"],
        "feeGrowthGlobal1X128_START": ps["feeGrowthGlobal1X128"], "feeGrowthGlobal1X128_END": pe["feeGrowthGlobal1X128"],
        "lp_fee_growth_unchanged_over_window": ps["feeGrowthGlobal0X128"] == pe["feeGrowthGlobal0X128"] and ps["feeGrowthGlobal1X128"] == pe["feeGrowthGlobal1X128"],
        "grossFeeGrowthGlobal0X128_START": ps["grossFeeGrowthGlobal0X128"], "grossFeeGrowthGlobal0X128_END": pe["grossFeeGrowthGlobal0X128"],
        "grossFeeGrowthGlobal1X128_START": ps["grossFeeGrowthGlobal1X128"], "grossFeeGrowthGlobal1X128_END": pe["grossFeeGrowthGlobal1X128"]}

    # ---- 7. gauge
    if "gauge" in rep:
        head_g, rows_g = read_csv_gz(DATA_AVAX / "pharaoh_gauge_notify.csv.gz")
        g = st["gauge"]
        per = Counter()
        for r in rows_g:
            d = dict(zip(head_g, r))
            per[(d["token"], int(d["period"]))] += int(d["amount"])
        cmp_ = []
        for tok, e in g["reward_tokens"].items():
            for k, v in e.items():
                if k.startswith("tokenTotalSupplyByPeriod["):
                    p = int(k[len("tokenTotalSupplyByPeriod["):-1])
                    if int(v) or per.get((tok, p)):
                        cmp_.append({"token": tok, "symbol": e["symbol"], "period": p, "tokenTotalSupplyByPeriod_at_END": v,
                                     "sum_of_notify_events_in_file": str(per.get((tok, p), 0)), "equal": int(v) == per.get((tok, p), 0)})
        phar = g["reward_tokens"][g["voter.ram()"]]
        P = g["voter.getPeriod()"]
        C["gauge"] = {"period_vs_state": cmp_,
                      "rewardRate(PHAR)_at_END": phar["rewardRate"],
                      "tokenTotalSupplyByPeriod[current]/604800": str(int(phar[f"tokenTotalSupplyByPeriod[{P}]"]) // 604800),
                      "rewardRate_equals_period_supply_over_week": int(phar["rewardRate"]) == int(phar[f"tokenTotalSupplyByPeriod[{P}]"]) // 604800,
                      "current_period": P, "period_definition": "floor(block.timestamp / 604800)",
                      "end_ts_over_week": end["end"]["timestamp"] // 604800}

    # ---- 8. feed
    if "feed" in rep:
        head_f, rows_f = read_csv_gz(DATA_AVAX / "feed_avax_usd.csv.gz")
        lf = dict(zip(head_f, rows_f[-1]))
        lr = st["chainlink_avax_usd"]["latestRoundData"]
        C["feed"] = {"last_row": lf, "latestRoundData_at_END": lr,
                     "answer_equal": lf["answer"] == lr["answer"], "updated_at_equal": int(lf["updated_at"]) == lr["updatedAt"],
                     "round_id_equal_low_64_bits": int(lf["round_id"]) == int(lr["roundId"]) & ((1 << 64) - 1),
                     "proxy_phase_id": int(lr["roundId"]) >> 64}

    # ---- 9. timestamps
    blocks = sorted({r[ix["block"]] for r in rng.sample(sw, 40)})
    mine = {r[ix["block"]]: r[ix["ts"]] for r in sw if r[ix["block"]] in set(blocks)}
    bad = 0
    for bl in blocks:
        h = rpc("avax", "eth_getBlockByNumber", [hex(bl), False], min_gap=1.0)
        if int(h["timestamp"], 16) != mine[bl]:
            bad += 1
    C["timestamps"] = {"blocks_rechecked": len(blocks), "second_header_source": RPC["avax"].split("/")[2], "mismatches": bad,
                       "first_swap_ts": sw[0][ix["ts"]], "last_swap_ts": sw[-1][ix["ts"]],
                       "all_within_window": end["start"]["timestamp"] <= sw[0][ix["ts"]] and sw[-1][ix["ts"]] <= end["end"]["timestamp"]}

    write_json(OUT, C)
    import json
    slim = {k: v for k, v in C.items() if k not in ("structure",)}
    print(json.dumps(slim, indent=1)[:9000])
    print(json.dumps(C["structure"], indent=1)[:3000])


if __name__ == "__main__":
    main()
