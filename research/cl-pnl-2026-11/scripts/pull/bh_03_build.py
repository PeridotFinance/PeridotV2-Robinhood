"""Decode the cached Blackhole logs into data/blackhole/*.csv.gz and write data/blackhole/build_report.json.

usage: python3 -u bh_03_build.py [pool] [gauge] [black]          (default: all)

Conventions (the same as the Pharaoh files of avax_03_build.py unless stated)
  * integers are plain decimal strings; rows are in (block, log_index) order; ts is the block header's own timestamp
    (eth_getBlockByNumber on publicnode, never interpolated), and every log's blockHash is compared with that header;
  * swap amounts are signed from the POOL's side exactly as the Algebra Swap event reports them
    (positive = the pool received; verified on real receipts by bh_02_traces.py);
  * fee_ppm is the fee the pool charged on that swap, in parts per million of the input:
        base = globalState.lastFee = newest Fee(uint16) event before the swap (pool creation onward), and the value the
               pool was initialised with before the first such event (read back by eth_call fee() at START-1 too);
        the pool emits SwapFee(sender, overrideFee, pluginFee) right before every Swap (same transaction, previous
        log index): fee = overrideFee + pluginFee if overrideFee != 0 else base + pluginFee  (SwapCalculation.sol);
  * protocol_share is the fraction of that fee that is NOT added to totalFeeGrowth (i.e. does not reach in-pool
    liquidity, which is all an unstaked position can collect):
        1 - (1 - communityFee/1000) * (1 - pluginFee/fee),   communityFee = newest CommunityFee(uint16) event before
    the swap (pool creation onward);  written with 6 decimals;
  * liquidity file: every Mint (+liquidityAmount) and Burn (-liquidityAmount) from the pool's creation block to END;
    zero-amount burns (fee pokes) are kept;
  * fee events file: Fee, CommunityFee, TickSpacing, Plugin, PluginConfig, CommunityVault from creation to END, with
    the previous value of the same kind as old_value (empty for the first one);
  * black_pool_swaps: classic constant-product pair; amount0/amount1 = in - out from the pair's side; sqrt_price_x96,
    liquidity and tick are DERIVED from the reserves of the Sync event that precedes the Swap in the same transaction
    (sqrt(reserve1 * 2^192 / reserve0), sqrt(reserve0 * reserve1), TickMath tick); fee_ppm from the pair's own
    Fees(sender, amount0, amount1) event of the same swap (fee amount * 1e6 / amount in, rounded) and the factory's
    getFee() (bps) as the nominal value; reserve0/reserve1 are appended as two extra columns.
Read-only: cache and block headers only.
"""
from __future__ import annotations

import sys
from collections import Counter

import bh_lib as B

A = B.A
T = B.topic
REPORT = B.DATA_BH / "build_report.json"
NAME = {T(s): s.split("(")[0] for s in B.EV_POOL}
SWAP, SWAPFEE = T("Swap(address,address,int256,int256,uint160,uint128,int24)"), T("SwapFee(address,uint24,uint24)")
MINT, BURN = T("Mint(address,address,int24,int24,uint128,uint256,uint256)"), T("Burn(address,int24,int24,uint128,uint256,uint256)")
FEE, CFEE, TS_, PLG, PCFG, CVAULT, INIT = (T("Fee(uint16)"), T("CommunityFee(uint16)"), T("TickSpacing(int24)"), T("Plugin(address)"),
                                           T("PluginConfig(uint8)"), T("CommunityVault(address)"), T("Initialize(uint160,int24)"))
EXCESS, FLASH, BURNFEE, COLLECT, SKIM = (T("ExcessTokens(uint256,uint256)"), T("Flash(address,address,uint256,uint256,uint256,uint256)"),
                                         T("BurnFee(address,uint24)"), T("Collect(address,address,int24,int24,uint128,uint128)"),
                                         T("Skim(address,uint256,uint256)"))
CFG_KIND = {FEE: "fee", CFEE: "community_fee", TS_: "tick_spacing", PLG: "plugin", PCFG: "plugin_config", CVAULT: "community_vault"}


def load(name: str, a: int, b: int, names: dict | None = None) -> tuple[list[dict], dict]:
    rows = A.ledger_rows(name)
    pos = a
    logs: list[dict] = []
    for r in rows:
        assert r["from"] == pos, f"{name}: gap/overlap at {pos}"
        part = A.read_json_gz(A.CACHE / "logs" / name / f"{r['from']}-{r['to']}.json.gz")
        assert len(part) == r["count"], f"{name}: cached count differs from the ledger in {r['from']}-{r['to']}"
        for x in part:
            assert r["from"] <= x["b"] <= r["to"], f"{name}: log outside its window"
        logs.extend(part)
        pos = r["to"] + 1
    assert pos == b + 1, f"{name}: windows end at {pos - 1}, expected {b}"
    keys = [(x["b"], x["i"]) for x in logs]
    ascending = all(k1 < k2 for k1, k2 in zip(keys, keys[1:]))
    if not ascending:
        logs.sort(key=lambda x: (x["b"], x["i"]))
        keys = [(x["b"], x["i"]) for x in logs]
    dup = len(keys) - len(set(keys))
    assert dup == 0, f"{name}: {dup} duplicate (block, log_index)"
    nm = names or NAME
    info = {"dataset": name, "requested_from": a, "requested_to": b, "windows": len(rows),
            "sources": dict(Counter(r["host"] for r in rows)), "logs": len(logs), "served_in_ascending_order": ascending,
            "duplicates": dup, "window_tiling_ok": True,
            "logs_per_topic0": {nm.get(t, t): n for t, n in Counter(x["t"][0] for x in logs).most_common()},
            "ranges": [[r["from"], r["to"], r["count"], r["host"]] for r in rows]}
    return logs, info


def stamp(logs: list[dict], info: dict) -> dict[int, int]:
    hd = A.block_timestamps({x["b"] for x in logs}, source="publicnode")
    bad = sum(1 for x in logs if hd[x["b"]][1] != x["h"])
    info.setdefault("header_checks", []).append({"blocks": len(hd), "header_source": A.RPC["publicnode"].split("/")[2],
                                                  "logs_checked": len(logs), "block_hash_mismatches": bad})
    assert bad == 0, "log blockHash differs from the canonical header"
    return {b: v[0] for b, v in hd.items()}


def s24t(topic_hex: str) -> int:
    return B.s_int(int(topic_hex, 16), 24)


def pool(st: dict) -> dict:
    pre, ipre = load("bh_pool_pre", B.POOL_CREATED, B.START - 1)
    win, iwin = load("bh_pool", B.START, B.END)
    info = {"pre_window": ipre, "window": iwin}
    assert all(x["t"][0] in NAME for x in pre + win), "unknown topic0 in pool logs"
    cfg_ev = [x for x in pre + win if x["t"][0] in CFG_KIND]
    swaps = [x for x in win if x["t"][0] == SWAP]
    ts = stamp(swaps + cfg_ev + [x for x in pre if x["t"][0] == INIT], iwin)

    # ---- initialise event, config history
    init = [x for x in pre + win if x["t"][0] == INIT]
    assert len(init) == 1
    w = A.words(init[0]["d"])
    info["initialize"] = {"block": init[0]["b"], "log_index": init[0]["i"], "ts": ts[init[0]["b"]], "tx": init[0]["tx"],
                          "sqrt_price_x96": str(w[0]), "tick": B.s_int(w[1], 24)}
    hist = {k: [] for k in CFG_KIND.values()}
    for x in cfg_ev:
        v = A.words(x["d"])[0]
        k = CFG_KIND[x["t"][0]]
        if k in ("plugin", "community_vault"):
            v = B.addr_of(v)
        elif k == "tick_spacing":
            v = B.s_int(v, 24)
        hist[k].append((x["b"], x["i"], ts[x["b"]], v))
    info["config_history_from_creation"] = {k: [[b, i, A.iso(t), v] for b, i, t, v in rows] for k, rows in hist.items()}
    s1 = st["A_dataset_pool"]["at_START_minus_1"]
    endg = st["A_dataset_pool"]["globalState"]

    def last_before(kind, block):
        vals = [v for b, i, t, v in hist[kind] if b < block]
        return vals[-1] if vals else None
    info["config_cross_check"] = {
        "fee: last Fee event before START == fee() at START-1": last_before("fee", B.START) == s1["fee()_ppm"],
        "fee: last Fee event <= END == fee() at END": (hist["fee"][-1][3] if hist["fee"] else None) == st["A_dataset_pool"]["fee()_ppm"],
        "communityFee: last event before START == globalState at START-1": last_before("community_fee", B.START) == s1["globalState"]["communityFee_per_1000"],
        "communityFee: last event <= END == globalState at END": (hist["community_fee"][-1][3] if hist["community_fee"] else None) == endg["communityFee_per_1000"],
        "tickSpacing: last event <= END == tickSpacing() at END": (hist["tick_spacing"][-1][3] if hist["tick_spacing"] else None) == st["A_dataset_pool"]["tickSpacing"],
        "pluginConfig: last event <= END == globalState at END": (hist["plugin_config"][-1][3] if hist["plugin_config"] else None) == endg["pluginConfig"],
        "events_in_window": {k: sum(1 for b, i, t, v in rows if b >= B.START) for k, rows in hist.items()},
    }

    def frows():
        prev = {}
        for x in cfg_ev:
            k = CFG_KIND[x["t"][0]]
            v = A.words(x["d"])[0]
            v = B.addr_of(v) if k in ("plugin", "community_vault") else (B.s_int(v, 24) if k == "tick_spacing" else v)
            yield [x["b"], x["i"], ts[x["b"]], k, prev.get(k, ""), v]
            prev[k] = v
    n = A.write_csv_gz(B.DATA_BH / "blackhole_wavax_usdc_fee_events.csv.gz", ["block", "log_index", "ts", "kind", "old_value", "new_value"], frows())
    info["files"] = {"blackhole_wavax_usdc_fee_events.csv.gz": {
        "rows": n, "first_block": cfg_ev[0]["b"] if cfg_ev else None, "last_block": cfg_ev[-1]["b"] if cfg_ev else None,
        "first_ts": ts[cfg_ev[0]["b"]] if cfg_ev else None, "last_ts": ts[cfg_ev[-1]["b"]] if cfg_ev else None,
        "kinds": dict(Counter(CFG_KIND[x["t"][0]] for x in cfg_ev))}}

    # ---- swaps
    fee = last_before("fee", B.START)
    cfee = last_before("community_fee", B.START)
    assert fee == s1["fee()_ppm"] and cfee == s1["globalState"]["communityFee_per_1000"]
    out = []
    sf_missing = 0
    sf_nonzero = Counter()
    fees_seen, share_seen = Counter(), Counter()
    for k, x in enumerate(win):
        t0 = x["t"][0]
        if t0 == FEE:
            fee = A.words(x["d"])[0]
        elif t0 == CFEE:
            cfee = A.words(x["d"])[0]
        elif t0 == SWAP:
            sfe = win[k - 1] if k > 0 else None
            if not sfe or sfe["t"][0] != SWAPFEE or sfe["tx"] != x["tx"] or sfe["i"] != x["i"] - 1:
                sf_missing += 1
                override = plug = 0
            else:
                override, plug = A.words(sfe["d"])
            if override or plug:
                sf_nonzero[(override, plug)] += 1
            f_eff = (override + plug) if override else (fee + plug)
            lp = (1 - cfee / 1000) * ((1 - plug / f_eff) if f_eff else 1)
            share = 1 - lp
            w = A.words(x["d"])
            out.append([x["b"], x["i"], ts[x["b"]], A.s256(w[0]), A.s256(w[1]), w[2], w[3], A.s256(w[4]), f_eff, f"{share:.6f}"])
            fees_seen[f_eff] += 1
            share_seen[f"{share:.6f}"] += 1
    n = A.write_csv_gz(B.DATA_BH / "blackhole_wavax_usdc_swaps.csv.gz",
                       ["block", "log_index", "ts", "amount0", "amount1", "sqrt_price_x96", "liquidity", "tick", "fee_ppm", "protocol_share"], out)
    info["files"]["blackhole_wavax_usdc_swaps.csv.gz"] = {
        "rows": n, "first_block": out[0][0], "last_block": out[-1][0], "first_ts": out[0][2], "last_ts": out[-1][2],
        "fee_ppm_values": {str(k): v for k, v in sorted(fees_seen.items())}, "protocol_share_values": dict(share_seen),
        "swaps_without_a_SwapFee_event_right_before": sf_missing,
        "swaps_with_nonzero_overrideFee_or_pluginFee": {f"{a}/{b}": c for (a, b), c in sf_nonzero.items()}}
    info["last_swap"] = {"block": out[-1][0], "log_index": out[-1][1], "sqrt_price_x96": str(out[-1][5]), "liquidity": str(out[-1][6]), "tick": out[-1][7]}

    # ---- liquidity, creation..END
    lrows, mints, burns, zb, net = [], 0, 0, 0, 0
    for x in pre + win:
        t0 = x["t"][0]
        if t0 == MINT:       # topics: owner, bottomTick, topTick; data: sender, liquidityAmount, amount0, amount1
            w = A.words(x["d"])
            lrows.append([x["b"], x["i"], "mint", s24t(x["t"][2]), s24t(x["t"][3]), w[1]])
            mints += 1
            net += w[1]
        elif t0 == BURN:     # topics: owner, bottomTick, topTick; data: liquidityAmount, amount0, amount1
            w = A.words(x["d"])
            lrows.append([x["b"], x["i"], "burn", s24t(x["t"][2]), s24t(x["t"][3]), -w[0]])
            burns += 1
            zb += w[0] == 0
            net -= w[0]
    n = A.write_csv_gz(B.DATA_BH / "blackhole_wavax_usdc_liquidity.csv.gz", ["block", "log_index", "kind", "tick_lower", "tick_upper", "liquidity_delta"], lrows)
    info["files"]["blackhole_wavax_usdc_liquidity.csv.gz"] = {
        "rows": n, "first_block": lrows[0][0], "last_block": lrows[-1][0], "mints": mints, "burns": burns,
        "zero_amount_burns": int(zb), "net_liquidity_delta_all_ranges": str(net),
        "rows_before_START": sum(1 for r in lrows if r[0] < B.START)}

    # ---- side events of the window (kept in the report, not as datasets)
    ex = [x for x in win if x["t"][0] == EXCESS]
    info["window_side_events"] = {
        "ExcessTokens": {"events": len(ex), "sum_amount0": str(sum(A.words(x["d"])[0] for x in ex)), "sum_amount1": str(sum(A.words(x["d"])[1] for x in ex))},
        "Flash": sum(1 for x in win if x["t"][0] == FLASH), "Skim": sum(1 for x in win if x["t"][0] == SKIM),
        "BurnFee_nonzero_pluginFee": sum(1 for x in win if x["t"][0] == BURNFEE and A.words(x["d"])[0] != 0),
        "Collect": sum(1 for x in win if x["t"][0] == COLLECT)}
    return info


def gauge(st: dict) -> dict:
    names = {T(s): s.split("(")[0] for s in ("RewardAdded(uint256)", "Deposit(address,uint256)", "Withdraw(address,uint256)",
                                              "Harvest(address,uint256)", "ClaimFees(address,uint256,uint256)",
                                              "EmergencyActivated(address,uint256)", "EmergencyDeactivated(address,uint256)",
                                              "OwnershipTransferred(address,address)")}
    logs, info = load("bh_gauge", B.POOL_CREATED, B.END, names)
    ra = [x for x in logs if x["t"][0] == T("RewardAdded(uint256)")]
    cf = [x for x in logs if x["t"][0] == T("ClaimFees(address,uint256,uint256)")]
    dist, idist = load("bh_gm_distribute", B.POOL_CREATED, B.END, {T("DistributeReward(address,address,uint256)"): "DistributeReward"})
    ts = stamp(ra + cf + dist, info)
    gmgr = B.DOCS["GaugeManager"]
    week = 604800
    rows = [[x["b"], x["i"], ts[x["b"]], gmgr, B.BLACK, A.words(x["d"])[0], ts[x["b"]] // week * week, "RewardAdded"]
            for x in ra if x["b"] >= B.START]
    n = A.write_csv_gz(B.DATA_BH / "blackhole_gauge_notify.csv.gz", ["block", "log_index", "ts", "from", "token", "amount", "period_or_epoch", "kind"], rows)
    info["files"] = {"blackhole_gauge_notify.csv.gz": {
        "rows": n, "first_block": rows[0][0] if rows else None, "last_block": rows[-1][0] if rows else None,
        "first_ts": rows[0][2] if rows else None, "last_ts": rows[-1][2] if rows else None,
        "sum_amount_raw": str(sum(r[5] for r in rows)),
        "column_notes": {"from": "GaugeManager (gauge.DISTRIBUTION): GaugeCL.notifyRewardAmount pulls the BLACK from it with transferFrom; the event itself is RewardAdded(uint256 reward) and carries no sender",
                         "token": "BLACK (gauge.rewardToken)", "period_or_epoch": "floor(ts / 604800) * 604800 = Thursday 00:00 UTC that starts the epoch in which the notification landed",
                         "kind": "event name"}}}
    # every epoch since the gauge exists (the one in force at START was notified before START)
    d_by_tx = {x["tx"]: x for x in dist}
    epochs = []
    for x in ra:
        amt = A.words(x["d"])[0]
        d = d_by_tx.get(x["tx"])
        epochs.append({"block": x["b"], "log_index": x["i"], "ts": ts[x["b"]], "iso": A.iso(ts[x["b"]]), "tx": x["tx"],
                       "epoch_start": ts[x["b"]] // week * week, "epoch_start_iso": A.iso(ts[x["b"]] // week * week),
                       "amount_raw": str(amt), "amount_BLACK": amt / 1e18, "in_window": x["b"] >= B.START,
                       "GaugeManager.DistributeReward_same_tx_amount_equal": (A.words(d["d"])[0] == amt) if d else None})
    info["distribute_reward_events_of_gauge_manager"] = {"events": len(dist), "ledger": {k: idist[k] for k in ("windows", "sources", "logs", "window_tiling_ok")},
                                                       "all_matched_to_a_RewardAdded_in_the_same_tx_with_equal_amount": all(e["GaugeManager.DistributeReward_same_tx_amount_equal"] for e in epochs) and len(dist) == len(ra)}
    A.write_json(B.DATA_BH / "gauge_epochs.json", {
        "gauge": B.GAUGE, "reward_token": B.BLACK, "note": "one RewardAdded(uint256) per epoch since the gauge was created; amount_BLACK = amount_raw / 1e18; the notification of the epoch that contains START precedes START and is therefore not in blackhole_gauge_notify.csv.gz",
        "epochs": epochs})
    # fee claims: ClaimFees(address indexed from, uint256 claimed0, uint256 claimed1) -> amounts forwarded to the internal bribe
    crow = []
    for x in cf:
        c0, c1 = A.words(x["d"])
        crow.append([x["b"], x["i"], ts[x["b"]], B.addr_of(int(x["t"][1], 16)), c0, c1, ts[x["b"]] // week * week, x["tx"]])
    n = A.write_csv_gz(B.DATA_BH / "blackhole_gauge_fee_claims.csv.gz", ["block", "log_index", "ts", "caller", "claimed0_wavax", "claimed1_usdc", "epoch_start", "tx"], crow)
    inw = [r for r in crow if r[0] >= B.START]
    info["files"]["blackhole_gauge_fee_claims.csv.gz"] = {
        "rows": n, "first_block": crow[0][0] if crow else None, "last_block": crow[-1][0] if crow else None,
        "first_ts": crow[0][2] if crow else None, "last_ts": crow[-1][2] if crow else None,
        "rows_in_window": len(inw), "sum_claimed0_in_window": str(sum(r[4] for r in inw)), "sum_claimed1_in_window": str(sum(r[5] for r in inw)),
        "note": "GaugeCL.ClaimFees(from, claimed0, claimed1): the WAVAX / USDC the gauge handed to its internal bribe (Bribe.notifyRewardAmount), from gauge creation to END"}
    info["gauge_event_counts_in_window"] = dict(Counter(names.get(x["t"][0], x["t"][0]) for x in logs if x["b"] >= B.START))
    info["gauge_event_counts_before_window"] = dict(Counter(names.get(x["t"][0], x["t"][0]) for x in logs if x["b"] < B.START))
    return info


def black(st: dict) -> dict:
    SW, SY, FE = T("Swap(address,uint256,uint256,uint256,uint256,address)"), T("Sync(uint256,uint256)"), T("Fees(address,uint256,uint256)")
    names = {SW: "Swap", SY: "Sync", FE: "Fees", T(B.TRANSFER): "Transfer", T("Approval(address,address,uint256)"): "Approval",
             T("Mint(address,uint256,uint256)"): "Mint", T("Burn(address,uint256,uint256,address)"): "Burn",
             T("Claim(address,address,uint256,uint256)"): "Claim"}
    logs, info = load("bh_black_pair", B.START, B.END, names)
    idx = [k for k, x in enumerate(logs) if x["t"][0] == SW]
    ts = stamp([logs[k] for k in idx], info)
    nominal = st["C_black_pools"]["vAMM-USDC/BLACK"]["PairFactory.getFee(pair,false)"] * 100
    out, unmatched, nofee, fee_dev = [], 0, 0, Counter()
    for k in idx:
        x = logs[k]
        sy = fe = None
        j = k - 1
        while j >= 0 and logs[j]["tx"] == x["tx"] and logs[j]["t"][0] != SW:
            if logs[j]["t"][0] == SY and sy is None:
                sy = logs[j]
            if logs[j]["t"][0] == FE and fe is None:
                fe = logs[j]
            j -= 1
        if sy is None:
            unmatched += 1
            continue
        r0, r1 = A.words(sy["d"])
        w = A.words(x["d"])            # amount0In, amount1In, amount0Out, amount1Out
        fee_ppm = nominal
        if fe is None:
            nofee += 1
        else:
            f0, f1 = A.words(fe["d"])
            num = (f0 if w[0] else 0) + 0
            # per-swap check of the nominal fee against the pair's own Fees event (floor(amountIn * fee_bps / 10000))
            exp0, exp1 = w[0] * (nominal // 100) // 10000, w[1] * (nominal // 100) // 10000
            fee_dev["equal" if (f0, f1) in ((exp0, 0), (0, exp1), (exp0, exp1)) else "different"] += 1
        sq = A.isqrt((r1 << 192) // r0)
        out.append([x["b"], x["i"], ts[x["b"]], w[0] - w[2], w[1] - w[3], sq, A.isqrt(r0 * r1), A.tick_at_sqrt_ratio(sq), fee_ppm, "0.000000", r0, r1])
    n = A.write_csv_gz(B.DATA_BH / "black_pool_swaps.csv.gz",
                       ["block", "log_index", "ts", "amount0", "amount1", "sqrt_price_x96", "liquidity", "tick", "fee_ppm", "protocol_share", "reserve0", "reserve1"], out)
    info["files"] = {"black_pool_swaps.csv.gz": {
        "rows": n, "first_block": out[0][0], "last_block": out[-1][0], "first_ts": out[0][2], "last_ts": out[-1][2],
        "pool": B.BLACK_PAIR, "token0": "USDC (6 decimals)", "token1": "BLACK (18 decimals)",
        "BLACK_price_usd": "reserve0 / 1e6 / (reserve1 / 1e18) of the row  ==  1e12 / (sqrt_price_x96 / 2^96)^2",
        "swaps_without_a_preceding_Sync_in_the_same_tx_dropped": unmatched, "swaps_without_a_Fees_event": nofee,
        "fee_nominal_ppm (PairFactory.getFee = 70 bps at START-1 and END)": nominal,
        "Fees_event_vs_floor(amountIn*70/10000)": dict(fee_dev),
        "protocol_share_note": "0: PairFactory.getReferralFee(pair) = 0 and stakingNFTFee() = 0 at START-1 and END, so the whole fee goes to PairFees for LP-token holders (unstaked LP); read from state, not re-derived per swap",
        "first_row_price_usd": out[0][10] / 1e6 / (out[0][11] / 1e18), "last_row_price_usd": out[-1][10] / 1e6 / (out[-1][11] / 1e18),
        "last_row_reserves": [str(out[-1][10]), str(out[-1][11])]}}
    return info


def main() -> None:
    want = set(sys.argv[1:]) or {"pool", "gauge", "black"}
    st = A.read_json(B.DATA_BH / "state_at_end.json")
    rep = A.read_json(REPORT) if REPORT.exists() else {}
    for k, fn in (("pool", pool), ("gauge", gauge), ("black", black)):
        if k in want:
            rep[k] = fn(st)
            A.write_json(REPORT, rep)
            v = rep[k]
            print(k, {f: x["rows"] for f, x in v["files"].items()})
    if "pool" in rep:
        p = rep["pool"]
        print(p["initialize"], p["config_history_from_creation"], p["config_cross_check"], p["window_side_events"], p["last_swap"],
              p["files"]["blackhole_wavax_usdc_swaps.csv.gz"], p["files"]["blackhole_wavax_usdc_liquidity.csv.gz"], sep="\n")


if __name__ == "__main__":
    main()
