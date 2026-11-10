"""Pin END (a finalized C-Chain block) and snapshot every state read the datasets depend on, raw, at that block.

Outputs (data/avalanche/):
  END.json            END number/hash/timestamp (as two endpoints report it), START block (first block with ts >= 2026-08-01)
  state_at_end.json   raw eth_call results at END + decoded values (pool, tokens, vaults, voter, gauge, xPHAR, feed)

Re-running keeps an existing END.json (delete it to re-pin). Read-only.
"""
from __future__ import annotations

import sys
import time

from avax_lib import (AVAX_USD_PROXY, CL_FACTORY, DATA_AVAX, NFPM, PHAR, POOL, RPC, SEL, START_TS, USDC, USDC_VAULT,
                      VOTER, WAVAX, WAVAX_VAULT, XPHAR, addr_arg, dec_addr_array, dec_string, eth_call, iso, read_json,
                      rpc, s24, s256, uint_arg, word_addr, words, write_json)

END_FILE = DATA_AVAX / "END.json"
STATE_FILE = DATA_AVAX / "state_at_end.json"
SRC = "avax"          # official API node: archive state
SRC2 = "tenderly"     # second opinion on the pinned block hash and on the key reads


def header(source: str, n) -> dict:
    tag = hex(n) if isinstance(n, int) else n
    b = rpc(source, "eth_getBlockByNumber", [tag, False])
    return {"number": int(b["number"], 16), "hash": b["hash"], "timestamp": int(b["timestamp"], 16)}


def first_block_at(ts: int, hi: int) -> int:
    lo = 1
    while lo < hi:
        mid = (lo + hi) // 2
        if header(SRC, mid)["timestamp"] < ts:
            lo = mid + 1
        else:
            hi = mid
    return lo


def main() -> None:
    if END_FILE.exists():
        end = read_json(END_FILE)
        print("END already pinned:", end["end"])
    else:
        fin = header(SRC, "finalized")
        n = fin["number"] - 64                       # a margin below the node's own "finalized" tag
        h1 = header(SRC, n)
        cross = None
        for src2 in (SRC2, "publicnode"):          # the tenderly gateway refuses everything once its daily egress is used up
            try:
                h2 = header(src2, n)
                cross = src2
                break
            except Exception as e:
                print(f"  header cross-check on {src2} failed: {str(e)[:100]}")
        if cross is None:
            sys.exit("no second endpoint answered for the END header cross-check")
        if h1 != h2:
            sys.exit(f"END header differs between endpoints: {h1} vs {h2}")
        start = first_block_at(START_TS, n)
        hs, hp = header(SRC, start), header(SRC, start - 1)
        assert hp["timestamp"] < START_TS <= hs["timestamp"]
        end = {"end": {**h1, "iso": iso(h1["timestamp"])},
               "start": {**hs, "iso": iso(hs["timestamp"]), "rule": "first block with timestamp >= 2026-08-01T00:00:00Z",
                         "previous_block_timestamp": hp["timestamp"]},
               "finalized_tag_at_pin": fin, "pinned_at_unix": int(time.time()),
               "sources": {"header": RPC[SRC].split("/")[2], "cross_check": RPC[cross].split("/")[2]}}
        write_json(END_FILE, end)
        print("pinned END", end["end"], "START", end["start"])
    E = end["end"]["number"]

    raw: dict[str, dict] = {}

    def read(label: str, to: str, sig: str, args: str = "", block: int = E, source: str = SRC) -> str:
        data = SEL[sig] + args
        try:
            r = eth_call(source, to, data, block)
        except Exception as e:  # a revert is a fact worth recording
            r = None
            raw[label] = {"to": to, "sig": sig, "data": data, "block": block, "source": RPC[source].split("/")[2], "error": str(e)[:300]}
            return ""
        raw[label] = {"to": to, "sig": sig, "data": data, "block": block, "source": RPC[source].split("/")[2], "result": r}
        return r

    S = end["start"]["number"]
    dec: dict = {}

    # --- candidates named in the task, to record why they are not the pool ---
    cand = {}
    for a in ("0x13fa49215C180Acce0f5EEcB7d4900987d407930", "0xf51B4EB0f6801729170d18BEe67C7F5d98985572", POOL):
        code = rpc(SRC, "eth_getCode", [a, hex(E)])
        cand[a] = {"code_bytes": (len(code) - 2) // 2}
    r = read("cand_f51B.pool()", "0xf51B4EB0f6801729170d18BEe67C7F5d98985572", "pool()")
    cand["0xf51B4EB0f6801729170d18BEe67C7F5d98985572"]["pool()"] = word_addr(words(r)[0]) if r else None
    dec["task_candidates"] = cand

    # --- pool A ---
    p = {}
    p["token0"] = word_addr(words(read("pool.token0", POOL, "token0()"))[0])
    p["token1"] = word_addr(words(read("pool.token1", POOL, "token1()"))[0])
    p["tickSpacing"] = s24(words(read("pool.tickSpacing", POOL, "tickSpacing()"))[0])
    p["fee_ppm"] = words(read("pool.fee", POOL, "fee()"))[0]
    p["liquidity"] = str(words(read("pool.liquidity", POOL, "liquidity()"))[0])
    w = words(read("pool.slot0", POOL, "slot0()"))
    p["slot0"] = {"sqrtPriceX96": str(w[0]), "tick": s24(w[1]), "observationIndex": w[2], "observationCardinality": w[3],
                  "observationCardinalityNext": w[4], "feeProtocol": w[5], "unlocked": bool(w[6])}
    p["factory"] = word_addr(words(read("pool.factory", POOL, "factory()"))[0])
    for k in ("feeGrowthGlobal0X128()", "feeGrowthGlobal1X128()", "grossFeeGrowthGlobal0X128()", "grossFeeGrowthGlobal1X128()"):
        p[k[:-2]] = str(words(read("pool." + k, POOL, k))[0])
    w = words(read("pool.protocolFees", POOL, "protocolFees()"))
    p["protocolFees"] = {"token0": str(w[0]), "token1": str(w[1])}
    p["lastPeriod"] = words(read("pool.lastPeriod", POOL, "lastPeriod()"))[0]
    p["factory.poolFeeProtocol(pool)"] = words(read("factory.poolFeeProtocol", p["factory"], "poolFeeProtocol(address)", addr_arg(POOL)))[0]
    p["factory.feeProtocol()"] = words(read("factory.feeProtocol", p["factory"], "feeProtocol()"))[0]
    p["balance_token0"] = str(words(read("pool.bal0", p["token0"], "balanceOf(address)", addr_arg(POOL)))[0])
    p["balance_token1"] = str(words(read("pool.bal1", p["token1"], "balanceOf(address)", addr_arg(POOL)))[0])
    # second endpoint on the two reads the continuity check uses
    r2 = read("pool.slot0@tenderly", POOL, "slot0()", source=SRC2)
    if r2:
        w2 = words(r2)
        p["slot0_second_source_equal"] = (str(w2[0]) == p["slot0"]["sqrtPriceX96"] and s24(w2[1]) == p["slot0"]["tick"])
    else:                                          # gateway unavailable (daily egress allowance): recorded, not fatal
        p["slot0_second_source_equal"] = None
    # the same reads at START (anchors of the fee series / protocol share)
    ps = {"block": S}
    ps["fee_ppm"] = words(read("pool.fee@START", POOL, "fee()", block=S))[0]
    w = words(read("pool.slot0@START", POOL, "slot0()", block=S))
    ps["slot0"] = {"sqrtPriceX96": str(w[0]), "tick": s24(w[1]), "feeProtocol": w[5]}
    ps["liquidity"] = str(words(read("pool.liquidity@START", POOL, "liquidity()", block=S))[0])
    # state just before START: what the first swap of the window traded against
    ps["fee_ppm_at_start_minus_1"] = words(read("pool.fee@START-1", POOL, "fee()", block=S - 1))[0]
    w = words(read("pool.slot0@START-1", POOL, "slot0()", block=S - 1))
    ps["slot0_at_start_minus_1"] = {"sqrtPriceX96": str(w[0]), "tick": s24(w[1]), "feeProtocol": w[5]}
    ps["liquidity_at_start_minus_1"] = str(words(read("pool.liquidity@START-1", POOL, "liquidity()", block=S - 1))[0])
    for k in ("feeGrowthGlobal0X128()", "feeGrowthGlobal1X128()", "grossFeeGrowthGlobal0X128()", "grossFeeGrowthGlobal1X128()"):
        ps[k[:-2]] = str(words(read("pool." + k + "@START", POOL, k, block=S))[0])
    p["at_start"] = ps
    dec["pool"] = p

    # --- tokens ---
    toks = {}
    for name, a in (("WAVAX", WAVAX), ("USDC", USDC), ("PHAR", PHAR), ("XPHAR", XPHAR)):
        toks[name] = {"address": a,
                      "symbol": dec_string(read(f"{name}.symbol", a, "symbol()")),
                      "name": dec_string(read(f"{name}.name", a, "name()")),
                      "decimals": words(read(f"{name}.decimals", a, "decimals()"))[0],
                      "totalSupply": str(words(read(f"{name}.totalSupply", a, "totalSupply()"))[0])}
    dec["tokens"] = toks
    dec["task_usdc_address_note"] = {
        "as_written_in_task": "0xB97EF9Ef8734C71904D8A701F8FA42F4F4aDA7B9E",
        "pool_token1_on_chain": p["token1"],
        "equal": p["token1"].lower() == "0xB97EF9Ef8734C71904D8A701F8FA42F4F4aDA7B9E".lower()}

    # --- the existing liquidity vaults: which pools do they hold? ---
    v = {}
    for name, a in (("usdcVault", USDC_VAULT), ("wavaxVault", WAVAX_VAULT)):
        r = read(f"{name}.pool", a, "pool()")
        pa = word_addr(words(r)[0]) if r else None
        e = {"proxy": a, "pool()": pa}
        if pa:
            t0 = word_addr(words(read(f"{name}.pool.token0", pa, "token0()"))[0])
            t1 = word_addr(words(read(f"{name}.pool.token1", pa, "token1()"))[0])
            e.update({"pool.token0": t0, "pool.token0.symbol": dec_string(read(f"{name}.pool.token0.symbol", t0, "symbol()")),
                      "pool.token1": t1, "pool.token1.symbol": dec_string(read(f"{name}.pool.token1.symbol", t1, "symbol()")),
                      "pool.tickSpacing": s24(words(read(f"{name}.pool.tickSpacing", pa, "tickSpacing()"))[0])})
        v[name] = e
    dec["vaults"] = v

    # --- voter / gauge ---
    g = {}
    gauge = word_addr(words(read("voter.gaugeForPool", VOTER, "gaugeForPool(address)", addr_arg(POOL)))[0])
    g["voter"] = VOTER
    g["gauge"] = gauge
    g["gauge_code_bytes"] = (len(rpc(SRC, "eth_getCode", [gauge, hex(E)])) - 2) // 2
    g["voter.poolForGauge(gauge)"] = word_addr(words(read("voter.poolForGauge", VOTER, "poolForGauge(address)", addr_arg(gauge)))[0])
    g["voter.isAlive(gauge)"] = bool(words(read("voter.isAlive", VOTER, "isAlive(address)", addr_arg(gauge)))[0])
    g["voter.xRatio()"] = words(read("voter.xRatio", VOTER, "xRatio()"))[0]
    g["voter.xRatio()_at_start"] = words(read("voter.xRatio@START", VOTER, "xRatio()", block=S))[0]
    g["voter.ram()"] = word_addr(words(read("voter.ram", VOTER, "ram()"))[0])
    g["voter.xRam()"] = word_addr(words(read("voter.xRam", VOTER, "xRam()"))[0])
    g["voter.clFactory()"] = word_addr(words(read("voter.clFactory", VOTER, "clFactory()"))[0])
    g["voter.legacyFactory()"] = word_addr(words(read("voter.legacyFactory", VOTER, "legacyFactory()"))[0])
    period = words(read("voter.getPeriod", VOTER, "getPeriod()"))[0]
    g["voter.getPeriod()"] = period
    g["voter.timeThresholdForGauge(gauge)"] = words(read("voter.timeThresholdForGauge", VOTER, "timeThresholdForGauge(address)", addr_arg(gauge)))[0]
    g["gauge.pool()"] = word_addr(words(read("gauge.pool", gauge, "pool()"))[0])
    g["gauge.voter()"] = word_addr(words(read("gauge.voter", gauge, "voter()"))[0])
    g["gauge.nfpManager()"] = word_addr(words(read("gauge.nfpManager", gauge, "nfpManager()"))[0])
    g["gauge.feeCollector()"] = word_addr(words(read("gauge.feeCollector", gauge, "feeCollector()"))[0])
    g["gauge.r33()"] = word_addr(words(read("gauge.r33", gauge, "r33()"))[0])
    g["gauge.firstPeriod()"] = words(read("gauge.firstPeriod", gauge, "firstPeriod()"))[0]
    rts = dec_addr_array(read("gauge.getRewardTokens", gauge, "getRewardTokens()"))
    g["gauge.getRewardTokens()"] = rts
    per_tok = {}
    for t in sorted(set(rts + [PHAR.lower(), XPHAR.lower()])):
        e = {"symbol": dec_string(read(f"reward.{t}.symbol", t, "symbol()")),
             "decimals": words(read(f"reward.{t}.decimals", t, "decimals()"))[0],
             "rewardRate": str(words(read(f"gauge.rewardRate.{t}", gauge, "rewardRate(address)", addr_arg(t)))[0]),
             "left": str(words(read(f"gauge.left.{t}", gauge, "left(address)", addr_arg(t)))[0]),
             "gauge_balance": str(words(read(f"gauge.balance.{t}", t, "balanceOf(address)", addr_arg(gauge)))[0])}
        for dp in (0, -1, -2):
            e[f"tokenTotalSupplyByPeriod[{period + dp}]"] = str(words(read(f"gauge.ttsbp.{t}.{period + dp}", gauge, "tokenTotalSupplyByPeriod(uint256,address)", uint_arg(period + dp) + addr_arg(t)))[0])
        per_tok[t] = e
    g["reward_tokens"] = per_tok
    for dp in (0, -1, -2):
        g[f"voter.gaugeRewardsPerPeriod(gauge,{period + dp})"] = str(words(read(f"voter.grpp.{period + dp}", VOTER, "gaugeRewardsPerPeriod(address,uint256)", addr_arg(gauge) + uint_arg(period + dp)))[0])
    # beacon implementation (EIP-1967 beacon slot)
    beacon_slot = "0xa3f0ad74e5423aebfd80d3ef4346578335a9a72aeaee59ff6cb3582b35133d50"
    bs = rpc(SRC, "eth_getStorageAt", [gauge, beacon_slot, hex(E)])
    beacon = word_addr(int(bs, 16))
    g["gauge.beacon"] = beacon
    r = read("beacon.implementation", beacon, "implementation()")
    g["gauge.implementation"] = word_addr(words(r)[0]) if r else None
    dec["gauge"] = g

    # --- xPHAR ---
    x = {}
    x["PHAR()"] = word_addr(words(read("xphar.PHAR", XPHAR, "PHAR()"))[0])
    x["SLASHING_PENALTY()"] = words(read("xphar.SLASHING_PENALTY", XPHAR, "SLASHING_PENALTY()"))[0]
    x["BASIS()"] = words(read("xphar.BASIS", XPHAR, "BASIS()"))[0]
    x["paused()"] = bool(words(read("xphar.paused", XPHAR, "paused()"))[0])
    x["PHAR.balanceOf(xPHAR)"] = str(words(read("phar.balanceOf(xphar)", PHAR, "balanceOf(address)", addr_arg(XPHAR)))[0])
    dec["xphar"] = x

    # --- Chainlink AVAX/USD ---
    c = {"proxy": AVAX_USD_PROXY}
    c["aggregator()"] = word_addr(words(read("feed.aggregator", AVAX_USD_PROXY, "aggregator()"))[0])
    c["aggregator()_at_start"] = word_addr(words(read("feed.aggregator@START", AVAX_USD_PROXY, "aggregator()", block=S))[0])
    c["decimals"] = words(read("feed.decimals", AVAX_USD_PROXY, "decimals()"))[0]
    c["description"] = dec_string(read("feed.description", AVAX_USD_PROXY, "description()"))
    w = words(read("feed.latestRoundData", AVAX_USD_PROXY, "latestRoundData()"))
    c["latestRoundData"] = {"roundId": str(w[0]), "answer": str(s256(w[1])), "startedAt": w[2], "updatedAt": w[3], "answeredInRound": str(w[4])}
    dec["chainlink_avax_usd"] = c

    write_json(STATE_FILE, {"end": end["end"], "start": end["start"], "decoded": dec, "raw_calls": raw,
                            "note": "eth_getStorageAt was used once (gauge beacon slot); every other read is eth_call at the stated block."})
    print("wrote", STATE_FILE)
    import json
    print(json.dumps(dec, indent=1)[:6000])


if __name__ == "__main__":
    main()
