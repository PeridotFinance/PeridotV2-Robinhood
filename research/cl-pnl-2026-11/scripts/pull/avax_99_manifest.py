"""Write MANIFEST.json and SHA256SUMS for data/avalanche and data/external.

usage: python3 avax_99_manifest.py [avalanche] [external]      (default: both)
Reads only local files (datasets, build_report.json, checks.json, the fetch reports).
"""
from __future__ import annotations

import gzip
import sys
import time

from avax_lib import DATA_AVAX, DATA_EXT, block_timestamps, iso, read_json, sha256_file, write_json


def csv_span(path) -> dict:
    """rows, first/last block and first/last time of a CSV dataset (time column: ts, updated_at or open_time)."""
    with gzip.open(path, "rt") as f:
        head = f.readline().rstrip("\n").split(",")
        first = last = None
        n = 0
        for line in f:
            if n == 0:
                first = line
            last = line
            n += 1
    out = {"rows": n, "columns": head}
    if n:
        fr, lr = first.rstrip("\n").split(","), last.rstrip("\n").split(",")
        if "block" in head:
            out["first_block"], out["last_block"] = int(fr[head.index("block")]), int(lr[head.index("block")])
        for tcol in ("ts", "updated_at", "open_time"):
            if tcol in head:
                t0, t1 = int(fr[head.index(tcol)]), int(lr[head.index(tcol)])
                if tcol == "open_time":
                    t0, t1 = t0 // 1000, t1 // 1000
                out["time_column"] = tcol
                out["first_time"], out["last_time"] = t0, t1
                out["iso_range"] = [iso(t0), iso(t1)]
                break
    return out


def sums(d) -> None:
    lines = []
    for f in sorted(d.iterdir()):
        if f.is_file() and f.name not in ("SHA256SUMS",) and not f.name.startswith(".") and not f.name.endswith(".tmp"):
            lines.append(f"{sha256_file(f)}  {f.name}")
    (d / "SHA256SUMS").write_text("\n".join(lines) + "\n")


def avalanche() -> None:
    end = read_json(DATA_AVAX / "END.json")
    st = read_json(DATA_AVAX / "state_at_end.json")["decoded"]
    rep = read_json(DATA_AVAX / "build_report.json")
    pools = read_json(DATA_AVAX / "phar_pools.json")
    checks = read_json(DATA_AVAX / "checks.json") if (DATA_AVAX / "checks.json").exists() else {"status": "checks not run"}
    gp = read_json(DATA_AVAX / "gauge_periods.json") if (DATA_AVAX / "gauge_periods.json").exists() else None
    block_range = [end["start"]["number"], end["end"]["number"]]

    def src(ds: str) -> dict:
        r = rep[ds]
        return {"method": "eth_getLogs (JSON-RPC), keyless public endpoint", "hosts_and_windows": r["sources"], "windows": r["windows"],
                "requested_block_range": block_range, "topic_filter": r.get("topic_filter", "none (every log of the contract)"),
                "logs_returned": r["logs"], "logs_per_topic0": r["logs_per_topic0"],
                "per_window_ledger": f"build_report.json -> {ds}.ranges ([from, to, count, host])",
                "timestamps": "eth_getBlockByNumber batches from avalanche-c-chain-rpc.publicnode.com; each log's blockHash checked against that header"}

    cl = pools["candidates"][pools["cl_reference"]["address"]]
    lg = pools["candidates"][pools["chosen"]["address"]]
    desc = {
        "pharaoh_wavax_usdc_swaps.csv.gz": ("pool", "Every Swap of the Pharaoh WAVAX/USDC CL pool " + "0xf01449C0bA930B6e2CaCA3DEF3CCBd7a3E589534" + ". token0 = WAVAX (18 dp), token1 = USDC (6 dp). amount0/amount1 signed from the pool's side as in the event. fee_ppm = fee in force at that log (FeeAdjustment series; before the first event, fee() at START-1)."),
        "pharaoh_wavax_usdc_liquidity.csv.gz": ("pool", "Every Mint and Burn of the same pool. liquidity_delta = +amount (mint) / -amount (burn); zero-amount burns kept."),
        "pharaoh_wavax_usdc_fee_events.csv.gz": ("pool", "FeeAdjustment(uint24 oldFee, uint24 newFee) [kind=fee] and SetFeeProtocol(uint24 old, uint24 new) [kind=fee_protocol] of the same pool."),
        "pharaoh_gauge_notify.csv.gz": ("gauge", "NotifyReward(address indexed from, address indexed reward, uint256 amount, uint256 period) (and Bribe, same shape, if any) of the pool's gauge " + st["gauge"]["gauge"] + ". period = floor(timestamp / 604800)."),
        "phar_pool_swaps.csv.gz": ("phar_pool", f"Swaps of the deepest PHAR pool quoted in WAVAX or USDC: the Solidly-style (constant-product, stable=false) PHAR/WAVAX pair {pools['chosen']['address']}. token0 = {lg['token0_symbol']} ({lg['token0_decimals']} dp), token1 = {lg['token1_symbol']} ({lg['token1_decimals']} dp). amount0/amount1 = amountIn - amountOut. reserve0/reserve1 = the Sync emitted just before the Swap in the same transaction (post-swap reserves). DERIVED columns (the pair has no v3 state): sqrt_price_x96 = floor(sqrt(reserve1 * 2^192 / reserve0)), liquidity = floor(sqrt(reserve0 * reserve1)), tick = getTickAtSqrtRatio(sqrt_price_x96). fee_ppm = pair fee from the legacy factory's SetPairFee events (denominator 1e6), anchored on pair.fee() at START."),
        "phar_wavax_cl_swaps.csv.gz": ("phar_cl_pool", f"Swaps of the deepest PHAR CL pool quoted in WAVAX or USDC: RamsesV3 PHAR/WAVAX {pools['cl_reference']['address']} (tick spacing {cl['tickSpacing']}). token0 = {cl['token0_symbol']} ({cl['token0_decimals']} dp), token1 = {cl['token1_symbol']} ({cl['token1_decimals']} dp). Same columns and conventions as pharaoh_wavax_usdc_swaps; all columns are event values."),
        "feed_avax_usd.csv.gz": ("feed", "AnswerUpdated(int256 indexed current, uint256 indexed roundId, uint256 updatedAt) of the Chainlink AVAX/USD aggregator " + st["chainlink_avax_usd"]["aggregator()"] + " behind proxy 0x0A77230d17318075983913bC2145DB16C7366156 (8 decimals). round_id is the aggregator's round; the proxy's roundId = (phaseId << 64) + round_id."),
    }
    files = []
    for f in sorted(DATA_AVAX.iterdir()):
        if not f.is_file() or f.name in ("MANIFEST.json", "SHA256SUMS") or f.name.startswith(".") or f.name.endswith(".tmp"):
            continue
        e = {"path": f"data/avalanche/{f.name}", "bytes": f.stat().st_size, "sha256": sha256_file(f)}
        if f.name.endswith(".csv.gz"):
            e.update(csv_span(f))
            if "iso_range" not in e and e.get("rows"):       # no time column: read the two block headers
                hd = block_timestamps([e["first_block"], e["last_block"]], log=lambda *_: None)
                e["first_time"], e["last_time"] = hd[e["first_block"]][0], hd[e["last_block"]][0]
                e["iso_range"] = [iso(e["first_time"]), iso(e["last_time"])]
                e["time_column"] = None
                e["iso_range_source"] = "timestamps of the first and last block (eth_getBlockByNumber, publicnode); the file has no time column"
            ds, text = desc.get(f.name, (None, ""))
            e["description"] = text
            if ds and ds in rep:
                e["source"] = src(ds)
                e["build"] = rep[ds]["files"].get(f.name)
        else:
            e["description"] = {
                "END.json": "the pinned END block and the START block (first block with timestamp >= 2026-08-01T00:00:00Z)",
                "state_at_end.json": "raw eth_call results at END (and at START / START-1 where stated) with decoded values: pool, tokens, vault pools, voter, gauge, xPHAR, Chainlink proxy",
                "phar_pools.json": "PHAR pool candidates (factories + DexScreener), on-chain facts at END, ranking by PHAR balance, the selection",
                "raw_dexscreener_phar_pairs.json.gz": "raw DexScreener reply for the PHAR token (pair list used only to find candidates)",
                "build_report.json": "per dataset: every requested block range with count and host, logs per topic0, fee-series facts",
                "checks.json": "all check results",
                "gauge_periods.json": "tokenTotalSupplyByPeriod(period, PHAR / xPHAR) of the gauge at END for every period overlapping the window",
            }.get(f.name, "")
        files.append(e)

    g = st["gauge"]
    facts = {
        "pool": {"address": "0xf01449C0bA930B6e2CaCA3DEF3CCBd7a3E589534", "contract": "RamsesV3Pool (Sourcify: match)", "token0": st["pool"]["token0"], "token0_symbol": st["tokens"]["WAVAX"]["symbol"],
                 "token0_decimals": st["tokens"]["WAVAX"]["decimals"], "token1": st["pool"]["token1"], "token1_symbol": st["tokens"]["USDC"]["symbol"],
                 "token1_decimals": st["tokens"]["USDC"]["decimals"], "tickSpacing": st["pool"]["tickSpacing"], "factory": st["pool"]["factory"],
                 "fee_ppm_at_END": st["pool"]["fee_ppm"], "fee_ppm_at_START": st["pool"]["at_start"]["fee_ppm"],
                 "fee_is_dynamic": True, "fee_distinct_values_in_window": rep["pool"]["fee_series"]["distinct_fees"] if "pool" in rep else None,
                 "slot0_at_END": st["pool"]["slot0"], "liquidity_at_END": st["pool"]["liquidity"],
                 "protocol_fee_share": {"slot0.feeProtocol": st["pool"]["slot0"]["feeProtocol"], "denominator": 1000000,
                                        "factory.poolFeeProtocol(pool)": st["pool"]["factory.poolFeeProtocol(pool)"], "factory.feeProtocol() (default)": st["pool"]["factory.feeProtocol()"],
                                        "at_START": st["pool"]["at_start"]["slot0"]["feeProtocol"]},
                 "lp_share_of_swap_fees": {
                     "statement": "feeProtocol = 1,000,000 of 1,000,000 throughout the window: the whole swap fee is booked as protocol fee, none to in-pool LP fee growth",
                     "evidence": ["slot0().feeProtocol = 1000000 at START-1, START and END (eth_call)",
                                  "no SetFeeProtocol event of the pool in the window (0 of 1,167,991 logs)",
                                  "feeGrowthGlobal0X128 and feeGrowthGlobal1X128 are identical at START and END while grossFeeGrowthGlobal{0,1}X128 grew (checks.protocol_share)",
                                  "RamsesV3Pool.sol: protocol part of a step = feeAmount * feeProtocol / FEE_DENOM, FEE_DENOM = 1_000_000 (Sourcify verified source, lines 58 and 536-538)"],
                     "swaps_file_has_no_protocol_share_column": "by specification; the share is the constant above"},
                 "verified_by": "eth_call at END (api.avax.network, slot0 also on tenderly); denominators and event signatures from the verified source on Sourcify"},
        "task_premise_mismatches": {
            "pool_candidates_in_task": st["task_candidates"],
            "pool_candidates_note": "0x13fa49... has no code on Avalanche (addresses.MD lists it as a USDT/P pool of another deployment); 0xf51B4E... is a gauge whose pool() is the USDC/USDt pool 0x9bfe31.... The WAVAX/USDC pool is 0xf01449... (PHARAOH_POOL in contracts/script/DeployAvalancheMarginMainnet.s.sol and contracts/range-tp/test/RangeFixture.sol).",
            "vault_pools": st["vaults"],
            "vault_pools_note": "the two live liquidity vaults hold USDt/USDC and sAVAX/WAVAX positions, not WAVAX/USDC; only the margin / range take-profit code references the WAVAX/USDC pool",
            "usdc_address": st["task_usdc_address_note"]},
        "gauge": {"address": g["gauge"], "found_by": "voter.gaugeForPool(pool) at END; gauge.pool() and voter.poolForGauge(gauge) point back to the pool",
                  "voter": g["voter"], "isAlive": g["voter.isAlive(gauge)"], "implementation": g["gauge.implementation"], "beacon": g["gauge.beacon"],
                  "reward_token_list_at_END": {t: g["reward_tokens"][t]["symbol"] for t in g["gauge.getRewardTokens()"]},
                  "rewardRate_at_END": {g["reward_tokens"][t]["symbol"]: g["reward_tokens"][t]["rewardRate"] for t in g["reward_tokens"]},
                  "left_at_END": {g["reward_tokens"][t]["symbol"]: g["reward_tokens"][t]["left"] for t in g["reward_tokens"]},
                  "current_period": g["voter.getPeriod()"], "timeThresholdForGauge_seconds": g["voter.timeThresholdForGauge(gauge)"],
                  "claims_in_window_by_reward_token": rep.get("gauge", {}).get("claims_in_window_by_reward_token"),
                  "per_period_supply": "gauge_periods.json" if gp else None,
                  "not_understood_not_used": {k: v for k, v in g.items() if k.startswith("voter.gaugeRewardsPerPeriod")},
                  "not_understood_note": "voter.gaugeRewardsPerPeriod(gauge, period) returned 0 for the three latest periods although the voter notified non-zero PHAR in each; the meaning of that mapping was not investigated and no dataset uses it"},
        "phar_and_xphar": {
            "0x13A466998Ce03Db73aBc2d4DF3bBD845Ed1f28E7": {"symbol": st["tokens"]["PHAR"]["symbol"], "name": st["tokens"]["PHAR"]["name"], "is": "PHAR (voter.ram())", "voter.ram()": g["voter.ram()"]},
            "0xE8164Ea89665DAb7a553e667F81F30CfDA736B9A": {"symbol": st["tokens"]["XPHAR"]["symbol"], "name": st["tokens"]["XPHAR"]["name"], "is": "xPHAR (voter.xRam())", "voter.xRam()": g["voter.xRam()"],
                                                           "xPHAR.PHAR()": st["xphar"]["PHAR()"]},
            "reward_form": {
                "verified_on_chain": [
                    "every NotifyReward of the gauge in the window carries reward = PHAR (see pharaoh_gauge_notify.csv.gz, token column); none carries xPHAR",
                    f"voter.xRatio() = {g['voter.xRatio()']} at END and {g['voter.xRatio()_at_start']} at START (share of emissions converted to xPHAR)",
                    "tokenTotalSupplyByPeriod(period, xPHAR) = 0 for every period overlapping the window (gauge_periods.json)",
                    f"xPHAR.SLASHING_PENALTY() = {st['xphar']['SLASHING_PENALTY()']} of BASIS() = {st['xphar']['BASIS()']} (instant-exit penalty), paused() = {st['xphar']['paused()']}",
                    "ClaimRewards events in the window by reward token: see gauge.claims_in_window_by_reward_token"],
                "from_repo_doc_not_re_verified": [
                    "PHAR is a plain ERC20 (no fee, pause or blacklist) and xPHAR is non-transferable except for exempt addresses: docs/deployment-handoffs/avalanche-vault-source-references.md (source reading at block 99216397)",
                    "a claim within timeThresholdForGauge seconds of a liquidity change is forfeited (same document)"]}},
        "phar_price_pools": {"deepest_overall": pools["deepest_overall"], "chosen_for_phar_pool_swaps": pools["chosen"], "cl_reference": pools["cl_reference"],
                             "ranking": [{"address": a, "kind": pools["candidates"][a]["kind"], "pair": pools["candidates"][a]["token0_symbol"] + "/" + pools["candidates"][a]["token1_symbol"],
                                          "phar_balance": pools["candidates"][a]["phar_balance"]} for a in pools["ranked_by_phar_balance"]],
                             "note": "the deepest pool overall is PHAR/p33 (quote token p33 has no USD reference of its own), so the price series uses the deepest pool quoted in WAVAX; PHAR/USD = PHAR/WAVAX x AVAX/USD"},
        "chainlink_avax_usd": st["chainlink_avax_usd"],
    }
    man = {"generated_at_unix": int(time.time()), "generated_at_iso": iso(time.time()),
           "window": {"start": end["start"], "end": end["end"], "end_rule": "node 'finalized' tag minus 64 blocks at pin time; header identical on api.avax.network and tenderly gateway"},
           "endpoints": {"logs_primary": "avalanche-mainnet.gateway.tenderly.co (pool) / avalanche-c-chain-rpc.publicnode.com (gauge, feed, PHAR pools, legacy factory)",
                         "logs_second_pass": "avalanche-c-chain-rpc.publicnode.com (pool, different grid)", "headers": "avalanche-c-chain-rpc.publicnode.com",
                         "state": "api.avax.network (archive eth_call), avalanche-mainnet.gateway.tenderly.co", "third_source_spot_checks": "api.routescan.io etherscan-style logs API",
                         "not_used": "AVALANCHE_RPC env endpoint, any Alchemy endpoint, any operator host config"},
           "files": files, "facts": facts, "checks": checks,
           "limitations": [
               "The keyless tenderly gateway stopped serving (HTTP 429, daily egress allowance used up) after it had delivered pool logs for blocks 91716610..99816609; blocks 99816610..99924388 of the pool were served by publicnode in both passes and were therefore compared record by record with the Routescan logs API instead (checks.re_requests.pool_range_served_by_publicnode_in_both_passes).",
               "Gauge, feed, PHAR pools and legacy factory logs were served once, by publicnode; they were not pulled a second time in full. Five random ranges of each were compared with Routescan, the feed's round ids are consecutive, and the gauge's per-period sums equal the contract state.",
               "The gauge period that contains START (2952, began 2026-07-30) was notified before the window: its NotifyReward is not in pharaoh_gauge_notify.csv.gz; its amount is in gauge_periods.json (state read at END).",
               "phar_pool_swaps.csv.gz is a constant-product pair: sqrt_price_x96, liquidity and tick are derived from the event reserves, not event fields. The deepest PHAR pool overall (PHAR/p33) was not pulled because p33 has no USD reference.",
               "PHAR/USD is not stored: PHAR is quoted in WAVAX in both PHAR files; convert with feed_avax_usd or the AVAX/USDT klines.",
               "No Pharaoh documentation URL was consulted for the reward form; the statement rests on contract reads and events only.",
               "Facts marked from_repo_doc_not_re_verified were read from the repository's own source-reference document, not re-derived here.",
           ],
           "scripts": "scripts/pull/avax_00_pin_state.py, avax_01_fetch_logs.py, avax_02_phar_pools.py, avax_03_build.py, avax_04_checks.py, avax_05_gauge_periods.py, avax_99_manifest.py (library avax_lib.py)",
           "run_sequence": [
               "python3 avax_00_pin_state.py                      # pins END (kept if END.json exists), state reads on api.avax.network, slot0 second opinion on the tenderly gateway",
               "python3 avax_01_fetch_logs.py pool tenderly 20000  # served 91716610..99816609, then HTTP 429 (daily egress allowance used up)",
               "python3 avax_01_fetch_logs.py pool publicnode 20000  # same cache and grid: served the remaining 99816610..99924388",
               "python3 avax_01_fetch_logs.py pool_second publicnode 33333",
               "python3 avax_02_phar_pools.py ; python3 avax_02_phar_pools.py --reselect",
               "for d in gauge legacy_factory phar_pool phar_cl_pool feed: python3 avax_01_fetch_logs.py $d publicnode 50000",
               "python3 avax_03_build.py                           # headers from publicnode, batches of 500",
               "python3 avax_05_gauge_periods.py",
               "python3 avax_04_checks.py ; python3 avax_04_checks.py --only-liquidity",
               "python3 avax_99_manifest.py"],
           "side_effect_on_this_machine": "the keyless tenderly gateway (avalanche-mainnet.gateway.tenderly.co) answers HTTP 429 to this IP until its egress allowance resets: response header x-tdly-egress-reset-timestamp = 1794387856579 ms = 2026-11-11T09:04:16Z"}
    write_json(DATA_AVAX / "MANIFEST.json", man)
    sums(DATA_AVAX)
    for e in files:
        print(e["path"], e.get("rows", ""), e.get("iso_range", ""), e["sha256"][:12])


def external() -> None:
    bn = read_json(DATA_EXT / "binance_avaxusdt_1m.fetch.json")
    yh = read_json(DATA_EXT / "yahoo_bars.fetch.json")
    files = []
    for f in sorted(DATA_EXT.iterdir()):
        if not f.is_file() or f.name in ("MANIFEST.json", "SHA256SUMS") or f.name.startswith(".") or f.name.endswith(".tmp"):
            continue
        e = {"path": f"data/external/{f.name}", "bytes": f.stat().st_size, "sha256": sha256_file(f)}
        if f.name.endswith(".csv.gz"):
            e.update(csv_span(f))
            if f.name == "avaxusdt_1m.csv.gz":
                e["source"] = {"host": bn["hosts_used"], "url_pattern": bn["url_pattern"], "requests": len(bn["requests"])}
                e["description"] = "Binance spot AVAXUSDT 1-minute klines, closed bars only, open_time in ms"
                e["checks"] = {"expected_minutes": bn["expected_minutes"], "rows": bn["rows"], "gaps": bn["gaps"], "contiguous": bn["gaps"] == [] and bn["expected_minutes"] == bn["rows"]}
            elif f.name in yh["files"]:
                y = yh["files"][f.name]
                e["source"] = {"host": sorted({r["host"] for r in y["requests"] if r["host"]}), "url_pattern": yh["url_pattern"],
                               "requests": [{k: r[k] for k in ("period1", "period2", "bars", "error", "dataGranularity")} for r in y["requests"]],
                               "fetched_at_iso": yh["fetched_at_iso"]}
                e["description"] = f"Yahoo Finance chart API {y['symbol']} {y['interval']} bars with includePrePost=true, exactly as served; ts = bar open (unix s)"
                e["checks"] = {k: y[k] for k in ("requested_from_iso", "requested_to_iso", "null_bars_dropped", "open_bars_dropped", "off_grid_bars_dropped",
                                                  "session_mix_new_york", "pre_post_present", "trading_days")}
        elif f.name.startswith("raw_"):
            e["description"] = "raw API responses the CSV of the same name was built from"
        else:
            e["description"] = "fetch ledger"
        files.append(e)
    man = {"generated_at_unix": int(time.time()), "generated_at_iso": iso(time.time()), "files": files,
           "notes": [
               "AVAX/USDT klines cover the swap window minute by minute (START minute .. last minute closed before END).",
               "Yahoo serves 1m bars for about the last 30 days, 5m for about the last 60 days and 1h further back; each file covers exactly the period in its iso_range, nothing was filled for earlier dates.",
               "Yahoo reports volume 0 on pre-market and after-hours bars; regular-session bars carry volume.",
               "Yahoo 1h bars as served start at these New York times: 04:00, 05:00 .. 09:00 (pre-market; the 09:00 bar is followed by a 09:30 bar), 09:30, 10:30 .. 15:30 (regular), 16:00, 17:00, 18:00, 19:00 (after-hours; the 17:00-19:00 bars are missing on some days). Observed on the file, not taken from documentation.",
               "Extended-hours bars are as served and can contain off-market prints (wide high/low on a single bar); nothing was cleaned.",
               "Bars with a null close were dropped and counted (null_bars_dropped); the bar still open at fetch time was dropped (open_bars_dropped / off_grid_bars_dropped).",
           ],
           "scripts": "scripts/pull/ext_10_binance_klines.py, ext_11_yahoo_bars.py, avax_99_manifest.py"}
    write_json(DATA_EXT / "MANIFEST.json", man)
    sums(DATA_EXT)
    for e in files:
        print(e["path"], e.get("rows", ""), e.get("iso_range", ""), e["sha256"][:12])


if __name__ == "__main__":
    want = set(sys.argv[1:]) or {"avalanche", "external"}
    if "external" in want:
        external()
    if "avalanche" in want:
        avalanche()
