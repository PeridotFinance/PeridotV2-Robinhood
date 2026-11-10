"""Pinned readbacks for the Blackhole WAVAX/USDC comparison -> data/blackhole/state_at_end.json.

Every read is an archive eth_call / eth_getStorageAt on api.avax.network at END (99,924,388) or START-1 (91,716,609),
recorded with its target, signature, arguments, block and raw result so that any number can be re-requested.
Pool discovery is double: the factory's customPoolByPair() for every deployer the docs list, and the factory's
CustomPool / Pool events filtered by the two token topics (Routescan logs API), which would also show a deployer the
docs do not list. Read-only.

usage: python3 -u bh_00_state.py            (re-uses cached calls in data/.cache/blackhole/state_calls.json)
"""
from __future__ import annotations

import time
import urllib.parse

import bh_lib as B

A = B.A
D = B.DOCS
END, S1 = B.END, B.START - 1
CACHE_F = B.CACHE_BH / "state_calls.json"
_cache = A.read_json(CACHE_F) if CACHE_F.exists() else {}
CALLS: list[dict] = []
_n = 0


def rd(to: str, sig: str, args: str = "", block: int = END, label: str = ""):
    """eth_call (cached); returns the list of 32-byte words or None on revert."""
    global _n
    key = f"{to.lower()}|{sig}|{args}|{block}"
    if key not in _cache:
        _cache[key] = B.try_call(to, sig, args, block)
        _n += 1
        if _n % 20 == 0:
            A.write_json(CACHE_F, _cache)
    raw = _cache[key]
    CALLS.append({"label": label, "to": B.checksum(to), "call": sig, "args_hex": args, "block": block, "result": raw})
    return None if raw is None else B.W(raw)


def storage(to: str, slot: int, block: int = END, label: str = "") -> int:
    key = f"{to.lower()}|storage|{slot}|{block}"
    if key not in _cache:
        _cache[key] = A.rpc("avax", "eth_getStorageAt", [to, hex(slot), hex(block)])
    CALLS.append({"label": label, "to": B.checksum(to), "call": f"eth_getStorageAt slot {slot}", "args_hex": "",
                  "block": block, "result": _cache[key]})
    return int(_cache[key], 16)


def ad(w) -> str | None:
    return None if w is None else B.addr_of(w[0])


def string(to: str, sig: str, block: int = END) -> str:
    key = f"{to.lower()}|{sig}||{block}"
    if key not in _cache:
        _cache[key] = B.try_call(to, sig, "", block)
    CALLS.append({"label": "", "to": B.checksum(to), "call": sig, "args_hex": "", "block": block, "result": _cache[key]})
    return A.dec_string(_cache[key]) if _cache[key] else ""


def bal(token: str, who: str, block: int = END) -> int:
    return rd(token, "balanceOf(address)", B.a(who), block)[0]


def gstate(pool: str, block: int = END) -> dict:
    w = rd(pool, "globalState()", "", block)
    return {"price_sqrtX96": str(w[0]), "tick": B.s_int(w[1], 24), "lastFee_ppm": w[2], "pluginConfig": w[3],
            "communityFee_per_1000": w[4], "unlocked": bool(w[5])}


def price_usdc_per_wavax(sq: int) -> float:
    return (sq / 2 ** 96) ** 2 * 1e12


def rs(params: dict):
    key = "routescan|" + urllib.parse.urlencode(sorted(params.items()))
    if key not in _cache:
        time.sleep(0.6)
        _cache[key] = A.http_json(A.ROUTESCAN + "?" + urllib.parse.urlencode(params))
    return _cache[key]


def main() -> None:
    out: dict = {"pin": {"END": END, "END_hash": B.END_HASH, "END_ts": B.END_TS, "START": B.START,
                         "START_minus_1": S1, "state_host": "api.avax.network"}}
    hdr = A.rpc("avax", "eth_getBlockByNumber", [hex(END), False])
    assert hdr["hash"] == B.END_HASH and int(hdr["timestamp"], 16) == B.END_TS
    out["pin"]["END_header_rechecked"] = True

    # ------------------------------------------------------------------ A: pools
    f = D["AlgebraFactory"]
    disc = {"factory": f, "customPoolByPair": {}, "docs_url": "https://docs.blackhole.xyz/technical-amm-documentation"}
    disc["poolByPair(WAVAX,USDC) [default deployer]"] = ad(rd(f, "poolByPair(address,address)", B.a(B.WAVAX) + B.a(B.USDC)))
    for k, v in D.items():
        if k.startswith("cpd_"):
            disc["customPoolByPair"][k] = {"deployer": v, "pool": ad(rd(
                f, "customPoolByPair(address,address,address)", B.a(v) + B.a(B.WAVAX) + B.a(B.USDC)))}
    r = rs({"module": "logs", "action": "getLogs", "address": f, "fromBlock": 0, "toBlock": END,
            "topic0": B.topic("CustomPool(address,address,address,address)"), "topic2": "0x" + B.a(B.WAVAX),
            "topic3": "0x" + B.a(B.USDC), "topic0_2_opr": "and", "topic2_3_opr": "and", "topic0_3_opr": "and"})
    disc["CustomPool_events(token0=WAVAX,token1=USDC) via Routescan"] = [
        {"block": int(x["blockNumber"], 16), "ts": int(x["timeStamp"], 16), "tx": x["transactionHash"],
         "deployer": B.addr_of(int(x["topics"][1], 16)), "pool": B.addr_of(int(x["data"], 16))} for x in r["result"]]
    r = rs({"module": "logs", "action": "getLogs", "address": f, "fromBlock": 0, "toBlock": END,
            "topic0": B.topic("Pool(address,address,address)"), "topic1": "0x" + B.a(B.WAVAX),
            "topic2": "0x" + B.a(B.USDC), "topic0_1_opr": "and", "topic1_2_opr": "and"})
    disc["Pool_events(token0=WAVAX,token1=USDC) via Routescan"] = len(r["result"]) if isinstance(r["result"], list) else r["result"]
    pf = D["PairFactory"]
    disc["classic PairFactory.getPair(WAVAX,USDC,stable=false)"] = ad(rd(pf, "getPair(address,address,bool)", B.a(B.WAVAX) + B.a(B.USDC) + B.u(0)))
    disc["classic PairFactory.getPair(WAVAX,USDC,stable=true)"] = ad(rd(pf, "getPair(address,address,bool)", B.a(B.WAVAX) + B.a(B.USDC) + B.u(1)))
    disc["classic PairFactory.getPair(USDC,WAVAX,stable=false)"] = ad(rd(pf, "getPair(address,address,bool)", B.a(B.USDC) + B.a(B.WAVAX) + B.u(0)))
    disc["classic PairFactory.allPairsLength()"] = rd(pf, "allPairsLength()")[0]
    # sanity of the getPair signature: the same call finds the BLACK pair
    disc["classic PairFactory.getPair(USDC,BLACK,false) [signature sanity]"] = ad(rd(pf, "getPair(address,address,bool)", B.a(B.USDC) + B.a(B.BLACK) + B.u(0)))
    out["A_discovery"] = disc

    tok = {}
    for t in (B.WAVAX, B.USDC, B.BLACK):
        tok[t] = {"symbol": string(t, "symbol()"), "decimals": rd(t, "decimals()")[0]}
    out["tokens"] = tok

    gm = D["GaugeManager"]
    pools = {"v1_ts200 (dataset pool)": B.POOL}
    pools.update(B.OTHER_POOLS)
    plist = {}
    for name, p in pools.items():
        g = gstate(p)
        sq = int(g["price_sqrtX96"])
        e = {"address": p, "token0": ad(rd(p, "token0()")), "token1": ad(rd(p, "token1()")),
             "tickSpacing": rd(p, "tickSpacing()")[0], "fee()_ppm": rd(p, "fee()")[0], "globalState": g,
             "liquidity": str(rd(p, "liquidity()")[0]), "factory": ad(rd(p, "factory()")),
             "plugin": ad(rd(p, "plugin()")), "communityVault": ad(rd(p, "communityVault()")),
             "deployer (AlgebraPoolApiStorage.pairToDeployer)": ad(rd(D["AlgebraPoolApiStorage"], "pairToDeployer(address)", B.a(p))),
             "balance_WAVAX": str(bal(B.WAVAX, p)), "balance_USDC": str(bal(B.USDC, p)),
             "getReserves": [str(x) for x in rd(p, "getReserves()")],
             "price_USDC_per_WAVAX": price_usdc_per_wavax(sq)}
        e["tvl_usd_at_pool_price"] = int(e["balance_WAVAX"]) / 1e18 * e["price_USDC_per_WAVAX"] + int(e["balance_USDC"]) / 1e6
        gauge = ad(rd(gm, "gauges(address)", B.a(p)))
        e["gauge"] = gauge
        e["gauge_isAlive"] = bool(rd(gm, "isAlive(address)", B.a(gauge))[0])
        e["gauge_rewardRate"] = str(rd(gauge, "rewardRate()")[0])
        plist[name] = e
    out["A_pools"] = plist
    best = max(plist.items(), key=lambda kv: kv[1]["tvl_usd_at_pool_price"])
    assert best[1]["address"] == B.POOL
    out["A_dataset_pool_choice"] = {"pool": B.POOL, "rule": "largest token balances (USD at each pool's own price) at END",
                                    "tvl_usd_by_pool": {k: round(v["tvl_usd_at_pool_price"], 2) for k, v in plist.items()}}

    P = B.POOL
    pe = dict(plist["v1_ts200 (dataset pool)"])
    pe["at_START_minus_1"] = {
        "globalState": gstate(P, S1), "liquidity": str(rd(P, "liquidity()", "", S1)[0]), "fee()_ppm": rd(P, "fee()", "", S1)[0],
        "tickSpacing": rd(P, "tickSpacing()", "", S1)[0], "plugin": ad(rd(P, "plugin()", "", S1)),
        "communityVault": ad(rd(P, "communityVault()", "", S1)),
        "totalFeeGrowth0Token": str(rd(P, "totalFeeGrowth0Token()", "", S1)[0]),
        "totalFeeGrowth1Token": str(rd(P, "totalFeeGrowth1Token()", "", S1)[0]),
        "getCommunityFeePending": [str(x) for x in rd(P, "getCommunityFeePending()", "", S1)],
        "getPluginFeePending": [str(x) for x in rd(P, "getPluginFeePending()", "", S1)],
        "getReserves": [str(x) for x in rd(P, "getReserves()", "", S1)]}
    pe["at_END_extra"] = {
        "totalFeeGrowth0Token": str(rd(P, "totalFeeGrowth0Token()")[0]),
        "totalFeeGrowth1Token": str(rd(P, "totalFeeGrowth1Token()")[0]),
        "getCommunityFeePending": [str(x) for x in rd(P, "getCommunityFeePending()")],
        "getPluginFeePending": [str(x) for x in rd(P, "getPluginFeePending()")],
        "prevTickGlobal": B.s_int(rd(P, "prevTickGlobal()")[0], 24), "nextTickGlobal": B.s_int(rd(P, "nextTickGlobal()")[0], 24),
        "safelyGetStateOfAMM": [str(x) for x in rd(P, "safelyGetStateOfAMM()")]}
    pe["factory.customPoolByPair(deployer,WAVAX,USDC)"] = ad(rd(f, "customPoolByPair(address,address,address)", B.a(B.POOL_DEPLOYER) + B.a(B.WAVAX) + B.a(B.USDC)))
    pe["factory.defaultCommunityFee()"] = (rd(f, "defaultCommunityFee()") or [None])[0]
    pe["factory.defaultFee()"] = (rd(f, "defaultFee()") or [None])[0]
    pe["factory.defaultTickspacing()"] = (rd(f, "defaultTickspacing()") or [None])[0]
    pl = pe["plugin"]
    pe["plugin_reads"] = {"pool()": ad(rd(pl, "pool()")), "incentive() [virtual pool]": ad(rd(pl, "incentive()")),
                          "defaultPluginConfig()": rd(pl, "defaultPluginConfig()")[0],
                          "incentive() at START-1": ad(rd(pl, "incentive()", "", S1))}
    pe["pluginConfig_flags"] = {"value": pe["globalState"]["pluginConfig"],
                                "decoded": [n for b, n in ((1, "BEFORE_SWAP"), (2, "AFTER_SWAP"), (4, "BEFORE_POSITION_MODIFY"),
                                                           (8, "AFTER_POSITION_MODIFY"), (16, "BEFORE_FLASH"), (32, "AFTER_FLASH"),
                                                           (64, "AFTER_INIT"), (128, "DYNAMIC_FEE")) if pe["globalState"]["pluginConfig"] & b]}
    out["A_dataset_pool"] = pe

    per = {}
    for name, to in (("quoterV2", D["quoterV2"]), ("swapRouter", D["swapRouter"]), ("NFPM_v1", D["NFPM_v1"])):
        per[name] = {"address": to, "factory()": ad(rd(to, "factory()")), "poolDeployer()": ad(rd(to, "poolDeployer()"))}
    per["NFPM_v1"]["farmingCenter()"] = ad(rd(D["NFPM_v1"], "farmingCenter()"))
    per["RouterV2"] = {"address": D["RouterV2"]}
    for s in ("factory()", "swapRouter()", "algebraFactory()", "wETH()"):
        w = rd(D["RouterV2"], s)
        per["RouterV2"][s] = ad(w) if w else None
    out["A_periphery"] = per

    # ------------------------------------------------------------------ B: where the fee goes
    V = pe["communityVault"]
    G = pe["gauge"]
    assert G == B.GAUGE and B.checksum(V) == B.POOL_VAULT
    vault = {}
    for blk, tag in ((END, "END"), (S1, "START-1")):
        vault[tag] = {"communityFeeReceiver": ad(rd(V, "communityFeeReceiver()", "", blk)),
                      "algebraFee_per_1000": rd(V, "algebraFee()", "", blk)[0],
                      "algebraFeeReceiver": ad(rd(V, "algebraFeeReceiver()", "", blk)),
                      "algebraFeeManager": ad(rd(V, "algebraFeeManager()", "", blk)),
                      "hasNewAlgebraFeeProposal": bool(rd(V, "hasNewAlgebraFeeProposal()", "", blk)[0]),
                      "balance_WAVAX": str(bal(B.WAVAX, V, blk)), "balance_USDC": str(bal(B.USDC, V, blk))}
    vault["vaultFactory.poolToVault(pool)"] = ad(rd(D["vaultFactory"], "poolToVault(address)", B.a(P)))
    out["B_community_vault"] = vault
    gf = {}
    for blk, tag in ((END, "END"), (S1, "START-1")):
        gf[tag] = {"dibsPercentage_per_1000": rd(D["GaugeFactoryCL"], "dibsPercentage()", "", blk)[0],
                   "dibs": ad(rd(D["GaugeFactoryCL"], "dibs()", "", blk))}
    out["B_gauge_factory_cl"] = gf

    g = {"address": G}
    for s in ("rewardToken()", "bonusRewardToken()", "internal_bribe()", "external_bribe()", "VE()", "DISTRIBUTION()",
              "farmingCenter()", "algebraEternalFarming()", "nonfungiblePositionManager()", "poolAddress()", "communityVault()", "owner()"):
        g[s] = ad(rd(G, s))
    for s in ("rewardRate()", "DURATION()", "totalSupply()", "rewardForDuration()", "rewardPerTokenStored()", "lastUpdateTime()"):
        g[s] = str(rd(G, s)[0])
    g["emergency()"] = bool(rd(G, "emergency()")[0])
    g["isForPair()"] = bool(rd(G, "isForPair()")[0])
    g["stakedFees()"] = [str(x) for x in rd(G, "stakedFees()")]
    g["periodFinish()"] = rd(G, "periodFinish()")
    # storage layout of GaugeCL (verified source): slot 0 ReentrancyGuard._status, 1 Ownable._owner, 2 VE,
    # 3 DISTRIBUTION, 4 internal_bribe, 5 external_bribe, 6 DURATION, 7 _periodFinish (internal), 8 rewardRate
    s6, s7, s8 = storage(G, 6), storage(G, 7), storage(G, 8)
    g["storage_slot6_DURATION"] = s6
    g["storage_slot7__periodFinish"] = s7
    g["storage_slot8_rewardRate"] = str(s8)
    g["storage_layout_check"] = (s6 == int(g["DURATION()"]) and s8 == int(g["rewardRate()"]))
    g["_periodFinish_iso"] = A.iso(s7)
    g["balances_at_END"] = {"WAVAX": str(bal(B.WAVAX, G)), "USDC": str(bal(B.USDC, G)), "BLACK": str(bal(B.BLACK, G))}
    g["rewardRate_at_START_minus_1"] = str(rd(G, "rewardRate()", "", S1)[0])
    out["C_gauge"] = g
    ib = g["internal_bribe()"]
    out["B_internal_bribe"] = {"address": ib, "balance_WAVAX": str(bal(B.WAVAX, ib)), "balance_USDC": str(bal(B.USDC, ib))}

    m = {"gauges(pool)": ad(rd(gm, "gauges(address)", B.a(P))), "poolForGauge(gauge)": ad(rd(gm, "poolForGauge(address)", B.a(G))),
         "isGauge": bool(rd(gm, "isGauge(address)", B.a(G))[0]), "isAlive": bool(rd(gm, "isAlive(address)", B.a(G))[0]),
         "isCLGauge": bool(rd(gm, "isCLGauge(address)", B.a(G))[0]), "claimable(gauge)": str(rd(gm, "claimable(address)", B.a(G))[0]),
         "internal_bribes(gauge)": ad(rd(gm, "internal_bribes(address)", B.a(G))), "minter()": ad(rd(gm, "minter()")),
         "voter()": ad(rd(gm, "voter()")), "feeDistributionTimestmap(pool)": rd(gm, "feeDistributionTimestmap(address)", B.a(P))[0],
         "gaugesDistributionTimestmap(gauge)": rd(gm, "gaugesDistributionTimestmap(address)", B.a(G))[0],
         "farmingParam()": [B.addr_of(x) for x in rd(gm, "farmingParam()")], "version()": string(gm, "version()"),
         "implementation_at_END (EIP-1967 slot)": B.addr_of(int(A.rpc("avax", "eth_getStorageAt", [gm, "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc", hex(END)]), 16))}
    out["C_gauge_manager"] = m

    ef = g["algebraEternalFarming()"]
    key = rd(ef, "incentiveKeys(address)", B.a(P))
    key_hex = "".join(B.u(x) for x in key)
    inc_id = "0x" + B.keccak256(bytes.fromhex(key_hex)).hex()
    inc = rd(ef, "incentives(bytes32)", inc_id[2:])
    vp = B.addr_of(inc[2])
    far = {"eternalFarming": ef, "incentiveKey": {"rewardToken": B.addr_of(key[0]), "bonusRewardToken": B.addr_of(key[1]),
                                                 "pool": B.addr_of(key[2]), "nonce": key[3]},
           "incentiveId": inc_id,
           "incentive": {"totalReward": str(inc[0]), "bonusReward": str(inc[1]), "virtualPoolAddress": vp,
                         "minimalPositionWidth_ticks": inc[3], "deactivated": bool(inc[4]), "pluginAddress": B.addr_of(inc[5])},
           "isEmergencyWithdrawActivated": bool(rd(ef, "isEmergencyWithdrawActivated()")[0]),
           "farmingCenter()": ad(rd(ef, "farmingCenter()")), "nonfungiblePositionManager()": ad(rd(ef, "nonfungiblePositionManager()")),
           "farmingCenter.virtualPoolAddresses(pool)": ad(rd(g["farmingCenter()"], "virtualPoolAddresses(address)", B.a(P)))}
    vps = {}
    for blk, tag in ((END, "END"), (S1, "START-1")):
        rates = rd(vp, "rewardRates()", "", blk)
        res = rd(vp, "rewardReserves()", "", blk)
        if rates is None:
            vps[tag] = None
            continue
        vps[tag] = {"currentLiquidity": str(rd(vp, "currentLiquidity()", "", blk)[0]),
                    "pool.liquidity()": str(rd(P, "liquidity()", "", blk)[0]),
                    "rewardRates": [str(x) for x in rates], "rewardReserves": [str(x) for x in res],
                    "prevTimestamp": rd(vp, "prevTimestamp()", "", blk)[0],
                    "globalTick": B.s_int(rd(vp, "globalTick()", "", blk)[0], 24),
                    "deactivated": bool(rd(vp, "deactivated()", "", blk)[0])}
        vps[tag]["staked_share_of_active_liquidity"] = int(vps[tag]["currentLiquidity"]) / int(vps[tag]["pool.liquidity()"])
    far["virtual_pool"] = vps
    far["eternalFarming_BLACK_balance_at_END"] = str(bal(B.BLACK, ef))
    out["C_farming"] = far

    mi = m["minter()"]
    out["C_minter"] = {"address": mi, "weekly()": str(rd(mi, "weekly()")[0]), "active_period()": rd(mi, "active_period()")[0],
                       "epochCount()": rd(mi, "epochCount()")[0], "teamRate()": rd(mi, "teamRate()")[0],
                       "MAX_BPS()": rd(mi, "MAX_BPS()")[0], "_black()": ad(rd(mi, "_black()"))}
    out["C_minter"]["active_period_iso"] = A.iso(out["C_minter"]["active_period()"])
    out["C_black"] = {"address": B.BLACK, "totalSupply": str(rd(B.BLACK, "totalSupply()")[0])}

    # ------------------------------------------------------------------ C: BLACK price pools
    cands = {"vAMM-USDC/BLACK": "0x0D9Fd6dd9b1FF55fB0A9bB0e5f1B6a2D65b741A3", "vAMM-WAVAX/BLACK": "0x14E4a5beD2e5E688Ee1a5CA3A4914250D1abd573",
             "CL200 WAVAX/BLACK": "0x9A6142eF0766915dB02066f791D969C22eba1dcA", "CL200 USDC/BLACK": "0xa38d178a08DD88eC1517d4585929052631fCd1B8",
             "CL default-deployer WAVAX/BLACK": "0x61c48716F6E03229b1E250AeFDdf429c48E75165"}
    px = price_usdc_per_wavax(int(pe["globalState"]["price_sqrtX96"]))
    bp = {}
    for name, p in cands.items():
        e = {"address": B.checksum(p), "token0": ad(rd(p, "token0()")), "token1": ad(rd(p, "token1()")),
             "balance_BLACK": str(bal(B.BLACK, p)), "balance_WAVAX": str(bal(B.WAVAX, p)), "balance_USDC": str(bal(B.USDC, p))}
        e["quote_side_usd"] = int(e["balance_WAVAX"]) / 1e18 * px + int(e["balance_USDC"]) / 1e6
        bp[name] = e
    bpair = B.BLACK_PAIR
    r = rd(bpair, "getReserves()")
    bp["vAMM-USDC/BLACK"].update({"getReserves": [str(x) for x in r], "stable()": bool(rd(bpair, "stable()")[0]),
                                  "BLACK_price_usd_from_reserves": (r[0] / 1e6) / (r[1] / 1e18),
                                  "PairFactory.getFee(pair,false)": (rd(pf, "getFee(address,bool)", B.a(bpair) + B.u(0)) or [None])[0],
                                  "PairFactory.getFee(pair,false) at START-1": (rd(pf, "getFee(address,bool)", B.a(bpair) + B.u(0), S1) or [None])[0],
                                  "reserves_at_START_minus_1": [str(x) for x in rd(bpair, "getReserves()", "", S1)]})
    out["C_black_pools"] = bp
    out["C_black_price_pool_choice"] = {"pool": bpair, "rule": "largest quote-side (WAVAX or USDC) balance in USD at END among BLACK pools found via factory.customPoolByPair events, factory.poolByPair and PairFactory.getPair",
                                        "quote_side_usd": {k: round(v["quote_side_usd"], 2) for k, v in bp.items()}}

    # ------------------------------------------------------------------ Pharaoh pool, same block
    ph = B.PHARAOH_POOL
    s0 = rd(ph, "slot0()")
    e = {"address": ph, "slot0": {"sqrtPriceX96": str(s0[0]), "tick": B.s_int(s0[1], 24), "feeProtocol": s0[5]},
         "liquidity": str(rd(ph, "liquidity()")[0]), "fee()_ppm": rd(ph, "fee()")[0], "tickSpacing": rd(ph, "tickSpacing()")[0],
         "token0": ad(rd(ph, "token0()")), "factory": ad(rd(ph, "factory()")),
         "balance_WAVAX": str(bal(B.WAVAX, ph)), "balance_USDC": str(bal(B.USDC, ph)),
         "price_USDC_per_WAVAX": price_usdc_per_wavax(s0[0])}
    e["tvl_usd_at_pool_price"] = int(e["balance_WAVAX"]) / 1e18 * e["price_USDC_per_WAVAX"] + int(e["balance_USDC"]) / 1e6
    q = B.PHARAOH_QUOTER
    e["quoter"] = {"address": q, "deployer()": ad(rd(q, "deployer()")), "WETH9()": ad(rd(q, "WETH9()"))}
    dep = e["quoter"]["deployer()"]
    w = rd(dep, "RamsesV3Factory()")
    e["quoter"]["deployer.RamsesV3Factory()"] = ad(w) if w else None
    e["factory.getPool(WAVAX,USDC,10)"] = ad(rd(B.PHARAOH_FACTORY, "getPool(address,address,int24)", B.a(B.WAVAX) + B.a(B.USDC) + B.u(10)))
    out["E_pharaoh_pool"] = e

    # ------------------------------------------------------------------ C: claim threshold / anti-sybil probes
    voter = m["voter()"]
    probes = {}
    for target, tname in ((voter, "VoterV3"), (gm, "GaugeManager"), (G, "GaugeCL"), (ef, "AlgebraEternalFarming"), (g["farmingCenter()"], "FarmingCenter")):
        for sig, args in (("timeThresholdForGauge(address)", B.a(G)), ("timeThreshold()", ""), ("minStakeTime()", ""), ("lockDuration()", ""), ("cooldown()", "")):
            w = rd(target, sig, args)
            probes[f"{tname}.{sig}"] = "reverts (no such function)" if w is None else (w[0] if w else "empty return")
    out["C_claim_threshold_probes"] = {
        "probes": probes,
        "farming incentive minimalPositionWidth (ticks)": far["incentive"]["minimalPositionWidth_ticks"],
        "source_reading": "GaugeCL.deposit/withdraw/getReward, FarmingCenter.enterFarming/exitFarming/collectRewards and AlgebraEternalFarming._enterFarming (verified sources on Routescan) contain no time condition; the only entry condition is tickUpper - tickLower >= minimalPositionWidth"}
    out["C_black"].update({"name": string(B.BLACK, "name()"), "symbol": tok[B.BLACK]["symbol"], "decimals": tok[B.BLACK]["decimals"]})
    out["A_docs_vs_chain"] = {
        "docs 'AlgebraPool' row 0xA02Ec3Ba...": "is the WAVAX/USDC tick-spacing-1 pool of the v1 deployer (factory.customPoolByPair), not an implementation",
        "docs 'GaugeManager(New Implementation)'": "0x93C601C31145185787dAb3617D6B419894dC0B8f",
        "GaugeManager implementation in the EIP-1967 slot at END": m["implementation_at_END (EIP-1967 slot)"],
        "docs VoterV3": D["VoterV3"], "GaugeManager.voter() at END": voter, "docs Minter": D["Minter"], "GaugeManager.minter() at END": mi,
        "docs 'Fee Structure: Static fee model' for concentrated pools": "consistent with pluginConfig = 2 (no DYNAMIC_FEE flag) and the Fee event history of the dataset pool"}

    A.write_json(CACHE_F, _cache)
    out["calls"] = CALLS
    A.write_json(B.DATA_BH / "state_at_end.json", out)
    for k in ("A_dataset_pool_choice", "B_community_vault", "B_gauge_factory_cl", "C_farming", "C_minter", "C_black_price_pool_choice"):
        print(k, out[k])
    print("gauge", {k: v for k, v in g.items() if "storage" in k or "period" in k.lower() or "reward" in k.lower()})
    print("pharaoh", out["E_pharaoh_pool"])
    print("periphery", per)
    print(len(CALLS), "calls recorded")


if __name__ == "__main__":
    main()
