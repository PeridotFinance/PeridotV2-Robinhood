"""Checks on the Blackhole datasets -> data/blackhole/checks.json. Read-only (cache, publicnode, Routescan).

usage: python3 -u bh_04_checks.py [structure] [rerequest] [replay] [depth]        (default: all)

structure  every CSV: no duplicate (block, log_index), strictly ascending, ts non-decreasing; requested log ranges tile
           the intended block range (from build_report.json) with their counts.
rerequest  5 random ranges per pulled dataset, fetched again (a) from publicnode with a split that is not on the
           original grid and (b) from a second source, the Routescan logs API; compared record by record on
           (block, log_index, topic0, data).
replay     one pass over the raw pool logs in (block, log_index) order with the tick map rebuilt from the liquidity
           file (pool creation onward):
             * replayed active liquidity == pool.liquidity() read at START-1 and at END, and == the liquidity field of
               every Swap event;
             * last swap's price / tick / liquidity against globalState() / liquidity() at END, with the Mint / Burn
               rows after the last swap listed;
             * fee validation: for every swap that crosses no initialised tick, with P_prev the previous swap's
               post price and L the event liquidity,
                   token0 in: net = getAmount0Delta(P_post, P_prev, L, roundUp)      (L * |d(1/sqrtP)|)
                   token1 in: net = getAmount1Delta(P_prev, P_post, L, roundUp)      (L * |d sqrtP|)
                   implied_fee_ppm = (amount_in - net) * 1e6 / amount_in,  compared with fee_ppm of the CSV;
             * (ExcessTokens are valued with the liquidity the pool used: the post-swap liquidity inside swap(),
               the pre-change liquidity inside mint() / burn())
             * gross swap fees of the window (sum of amount_in * fee_ppm / 1e6 per token) against what left the pool
               for the community vault (vault balance change + vault withdrawals + change of the pool's pending
               community fee) and against the growth of totalFeeGrowth0Token / totalFeeGrowth1Token, which is
               reproduced exactly from the ExcessTokens events alone.
depth      the six Blackhole quotes of depth_at_end.json recomputed offline from the tick map at END (v3 integer math
           with the 500 ppm fee) and compared with the quoter's amounts.
"""
from __future__ import annotations

import bisect
import random
import sys
import time
from collections import Counter

import bh_lib as B

A = B.A
T = B.topic
Q96, Q128 = 1 << 96, 1 << 128
OUT = B.DATA_BH / "checks.json"
SWAP, MINT, BURN = T("Swap(address,address,int256,int256,uint160,uint128,int24)"), T("Mint(address,address,int24,int24,uint128,uint256,uint256)"), T("Burn(address,int24,int24,uint128,uint256,uint256)")
EXCESS, FLASH = T("ExcessTokens(uint256,uint256)"), T("Flash(address,address,uint256,uint256,uint256,uint256)")
SEED = 20261110


def csv_rows(path):
    """Stream a .csv.gz: yields the header first, then one row (list of strings) at a time."""
    import csv
    import gzip
    with gzip.open(path, "rt", newline="") as f:
        yield from csv.reader(f)


def structure_of(path) -> dict:
    it = csv_rows(path)
    head = next(it)
    kb, ki = head.index("block"), head.index("log_index")
    kt = head.index("ts") if "ts" in head else None
    n = dup = 0
    asc = ts_ok = True
    last, last_ts, first = None, None, None
    for r in it:
        k = (int(r[kb]), int(r[ki]))
        if last is not None:
            dup += k == last
            asc = asc and k > last
        if kt is not None:
            t = int(r[kt])
            ts_ok = ts_ok and (last_ts is None or t >= last_ts)
            last_ts = t
        if first is None:
            first = k
        last = k
        n += 1
    d = {"rows": n, "duplicates": dup, "strictly_ascending": asc}       # strictly ascending keys imply no duplicate
    if kt is not None:
        d["ts_non_decreasing"] = ts_ok
    if n:
        d["first_block"], d["last_block"] = first[0], last[0]
    return d


def cached(name: str) -> list[dict]:
    out = []
    for r in A.ledger_rows(name):
        out.extend(A.read_json_gz(A.CACHE / "logs" / name / f"{r['from']}-{r['to']}.json.gz"))
    return out


def cached_range(names: list[str], lo: int, hi: int) -> set:
    """Cached records of [lo, hi] as (block, log_index, topic0, data); only the windows that overlap are read."""
    out = set()
    for name in names:
        for r in A.ledger_rows(name):
            if r["to"] < lo or r["from"] > hi:
                continue
            for x in A.read_json_gz(A.CACHE / "logs" / name / f"{r['from']}-{r['to']}.json.gz"):
                if lo <= x["b"] <= hi:
                    out.add((x["b"], x["i"], x["t"][0], x["d"]))
    return out


def routescan_logs(address: str, lo: int, hi: int, topic0: str | None = None, topic2: str | None = None) -> set:
    """Logs of `address` in [lo, hi] from the Routescan etherscan-style API (keyless, 1,000 logs a page), optionally
    filtered on topic0 (and topic2), as (block, log_index, topic0, data). A slice is halved after 5 full pages."""
    out = set()
    stack = [(lo, hi)]
    while stack:
        x, y = stack.pop()
        got, page, split = [], 1, False
        while True:
            url = f"{A.ROUTESCAN}?module=logs&action=getLogs&address={address}&fromBlock={x}&toBlock={y}&page={page}&offset=1000"
            if topic0:
                url += f"&topic0={topic0}"
            if topic2:
                url += f"&topic2={topic2}&topic0_2_opr=and"
            d = None
            for attempt in range(6):
                try:
                    d = A.http_json(url, timeout=60)
                    if isinstance(d.get("result"), list):
                        break
                except Exception as e:
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
        out |= {(int(g["blockNumber"], 16), int(g["logIndex"], 16) if g["logIndex"] not in ("0x", "") else 0, g["topics"][0], g["data"]) for g in got}
    return out


def publicnode_logs(address: str, topics, lo: int, hi: int) -> set:
    flt = {"address": address, "fromBlock": hex(lo), "toBlock": hex(hi)}
    if topics:
        flt["topics"] = topics
    got = A.rpc("publicnode", "eth_getLogs", [flt], timeout=120)
    return {(int(x["blockNumber"], 16), int(x["logIndex"], 16), x["topics"][0], x["data"]) for x in got}


def rerequest(R: dict) -> None:
    """5 ranges per dataset, each fetched again from publicnode (split at a block that is not on the original grid)
    and from Routescan (one query per topic0 of the dataset's filter; all topics when the dataset is unfiltered).
    Dense datasets: 5 uniformly random ranges. Sparse weekly datasets (gauge RewardAdded / ClaimFees, GaugeManager
    DistributeReward): 4 ranges of +/- 10,000 blocks around randomly chosen events of the dataset (so that the ranges
    are not empty) plus 1 long random range (400,000 to 1,500,000 blocks)."""
    import bh_01_fetch_logs as F
    sets = [("bh_pool", ["bh_pool"], "random", (1500, 5000)), ("bh_pool_pre (all segments)", [s[0] for s in B.PRE_SEGMENTS], "random", (3000, 20000)),
            ("bh_gauge_rf", ["bh_gauge_rf"], "events", (400000, 1500000)), ("bh_vault", ["bh_vault"], "random", (400000, 1500000)),
            ("bh_gm_distribute", ["bh_gm_distribute"], "events", (400000, 1500000)), ("bh_black_pair", ["bh_black_pair"], "random", (20000, 45000))]
    res = R.setdefault("re_requests", {})
    for label, names, mode, (smin, smax) in sets:
        if label in res and all("error" not in x for x in res[label]):
            continue
        rng = random.Random(f"{SEED}-{label}")
        cfg = F.SETS[names[0]]
        a, b = F.SETS[names[0]]["start"], F.SETS[names[-1]]["end"]
        topics = cfg["topics"]
        t0s = [None] if not topics else ([topics[0]] if isinstance(topics[0], str) else list(topics[0]))
        t2 = topics[2] if topics and len(topics) > 2 else None
        ranges = []
        if mode == "events":
            ev = sorted({x[0] for x in cached_range(names, a, b)})
            for blk in rng.sample(ev, min(4, len(ev))):
                ranges.append((max(a, blk - 10000), min(b, blk + 10000)))
            span = rng.randint(smin, smax)
            lo = rng.randint(a, b - span)
            ranges.append((lo, lo + span - 1))
        else:
            for _ in range(5):
                span = rng.randint(smin, smax)
                lo = rng.randint(a, b - span)
                ranges.append((lo, lo + span - 1))
        rows = []
        for lo, hi in ranges:
            span = hi - lo + 1
            mid = lo + rng.randint(span // 4, 3 * span // 4)
            old = cached_range(names, lo, hi)
            row = {"from": lo, "to": hi, "count_in_dataset": len(old)}
            try:
                parts = []
                for x, y in ((lo, mid), (mid + 1, hi)):          # publicnode allows 50,000 blocks a call
                    z = x
                    while z <= y:
                        parts.append(publicnode_logs(cfg["address"], topics, z, min(z + 49999, y)))
                        z += 50000
                new = set().union(*parts)
                row.update({"publicnode_other_split_at": mid, "publicnode_count": len(new), "publicnode_identical": new == old})
                rs = set()
                for t0 in t0s:
                    rs |= routescan_logs(cfg["address"], lo, hi, t0, t2)
                row.update({"routescan_count": len(rs), "routescan_identical": rs == old})
            except Exception as e:
                row["error"] = str(e)[:200]
            rows.append(row)
            print(label, row, flush=True)
        res[label] = rows
        A.write_json(OUT, R)


class TickMap:
    def __init__(self):
        self.net: dict[int, int] = {}
        self.gross: dict[int, int] = {}
        self.ticks: list[int] = []

    def apply(self, lower: int, upper: int, delta: int) -> None:
        for t, sgn in ((lower, 1), (upper, -1)):
            g = self.gross.get(t, 0) + delta
            n = self.net.get(t, 0) + sgn * delta
            if g == 0:
                if t in self.gross:
                    del self.gross[t]
                    del self.net[t]
                    k = bisect.bisect_left(self.ticks, t)
                    del self.ticks[k]
            else:
                if t not in self.gross:
                    bisect.insort(self.ticks, t)
                self.gross[t] = g
                self.net[t] = n
        assert self.gross.get(lower, 1) > 0 and self.gross.get(upper, 1) > 0, "negative gross liquidity on a tick"

    def active(self, tick: int) -> int:
        k = bisect.bisect_right(self.ticks, tick)
        return sum(self.net[t] for t in self.ticks[:k])

    def crossed(self, t_a: int, t_b: int) -> bool:
        lo, hi = (t_a, t_b) if t_a <= t_b else (t_b, t_a)
        k = bisect.bisect_right(self.ticks, lo)
        return k < len(self.ticks) and self.ticks[k] <= hi


def replay(R: dict) -> TickMap:
    st = A.read_json(B.DATA_BH / "state_at_end.json")
    rep = A.read_json(B.DATA_BH / "build_report.json")
    pe = st["A_dataset_pool"]
    s1 = pe["at_START_minus_1"]
    tm = TickMap()
    it = csv_rows(B.DATA_BH / "blackhole_wavax_usdc_liquidity.csv.gz")
    next(it)
    n_pre = 0
    for r in it:
        if int(r[0]) >= B.START:
            break
        tm.apply(int(r[3]), int(r[4]), int(r[5]))
        n_pre += 1
    del it
    L = tm.active(s1["globalState"]["tick"])
    out = {"liquidity_rows_before_START": n_pre, "active_liquidity_replayed_at_START_minus_1": str(L),
           "pool.liquidity()_at_START_minus_1": s1["liquidity"], "equal_at_START_minus_1": str(L) == s1["liquidity"],
           "initialised_ticks_at_START_minus_1": len(tm.ticks)}
    # window pass over the raw logs
    it = csv_rows(B.DATA_BH / "blackhole_wavax_usdc_swaps.csv.gz")
    next(it)
    fee_of = {(int(r[0]), int(r[1])): int(r[8]) for r in it}
    sq_prev, tick_prev = int(s1["globalState"]["price_sqrtX96"]), s1["globalState"]["tick"]
    n_sw = elig = within1 = within1_or_round = l_match = 0
    dev_hist = Counter()
    worst = []
    gross = [0, 0]              # sum of amount_in * fee / 1e6 per input token (all swaps)
    exact_fee = [0, 0]          # sum of (amount_in - net) over eligible swaps
    elig_in = [0, 0]
    vol = [0, 0]
    fg_from_excess = [0, 0]
    excess_sum = [0, 0]
    n_excess = 0
    flashes = 0
    after_last_swap: list = []
    last_swap = None
    pending: list = []          # ExcessTokens events waiting for the liquidity the pool used for them

    def settle(liq_used: int) -> None:
        for e0, e1 in pending:
            if liq_used > 0:
                fg_from_excess[0] += e0 * Q128 // liq_used
                fg_from_excess[1] += e1 * Q128 // liq_used
        pending.clear()
    pending_tx = None
    for r_ in A.ledger_rows("bh_pool"):
        part = A.read_json_gz(A.CACHE / "logs" / "bh_pool" / f"{r_['from']}-{r_['to']}.json.gz")
        for x in part:
            t0 = x["t"][0]
            if pending and x["tx"] != pending_tx:
                settle(L)           # sync() or a burn that emitted nothing else: current liquidity
            if t0 == SWAP:
                w = A.words(x["d"])
                a0, a1, sq, lq, tk = A.s256(w[0]), A.s256(w[1]), w[2], w[3], A.s256(w[4])
                n_sw += 1
                fee = fee_of[(x["b"], x["i"])]
                side = 0 if a0 > 0 else 1
                ain = a0 if side == 0 else a1
                vol[side] += ain
                gross[side] += ain * fee
                crossed = tm.crossed(tick_prev, tk)
                if crossed:
                    L = tm.active(tk)
                l_match += L == lq
                if not crossed:
                    if ain > 0 and sq != sq_prev:
                        elig += 1
                        net = A.amount0_delta_up(sq, sq_prev, lq) if side == 0 else A.amount1_delta_up(sq_prev, sq, lq)
                        fee_amt = ain - net
                        exact_fee[side] += fee_amt
                        elig_in[side] += ain
                        implied = fee_amt * 10 ** 6 / ain
                        d = abs(implied - fee)
                        ok1 = d <= 1.0
                        # integer rounding: the pool floors amount*(1e6-fee)/1e6 and rounds the price, so the fee amount
                        # can differ from amount*fee/1e6 by up to 2 base units
                        okr = abs(fee_amt * 10 ** 6 - ain * fee) <= 2 * 10 ** 6
                        within1 += ok1
                        within1_or_round += ok1 or okr
                        dev_hist["<=0.01" if d <= 0.01 else "<=0.1" if d <= 0.1 else "<=1" if d <= 1 else "<=10" if d <= 10 else ">10"] += 1
                        if not (ok1 or okr) and len(worst) < 10:
                            worst.append({"block": x["b"], "log_index": x["i"], "amount_in": str(ain), "side": side, "implied_ppm": implied, "fee_ppm": fee})
                sq_prev, tick_prev = sq, tk
                last_swap = (x["b"], x["i"], sq, lq, tk)
                after_last_swap = []
                if pending:
                    settle(lq)      # swap(): _calculateSwap runs before _updateReserves, so the POST-swap liquidity
            elif t0 == MINT:
                if pending:
                    settle(L)       # mint(): _updateReserves runs before the position is updated
                w = A.words(x["d"])
                lo_, hi_ = B.s_int(int(x["t"][2], 16), 24), B.s_int(int(x["t"][3], 16), 24)
                tm.apply(lo_, hi_, w[1])
                if lo_ <= tick_prev < hi_:
                    L += w[1]
                after_last_swap.append([x["b"], x["i"], "mint", lo_, hi_, str(w[1])])
            elif t0 == BURN:
                if pending:
                    settle(L)       # burn(): _updateReserves runs before the position is updated
                w = A.words(x["d"])
                lo_, hi_ = B.s_int(int(x["t"][2], 16), 24), B.s_int(int(x["t"][3], 16), 24)
                tm.apply(lo_, hi_, -w[0])
                if lo_ <= tick_prev < hi_:
                    L -= w[0]
                after_last_swap.append([x["b"], x["i"], "burn", lo_, hi_, str(-w[0])])
            elif t0 == EXCESS:
                e0, e1 = A.words(x["d"])
                n_excess += 1
                excess_sum[0] += e0
                excess_sum[1] += e1
                if pending and x["tx"] == pending_tx:
                    settle(L)
                pending.append((e0, e1))
                pending_tx = x["tx"]
            elif t0 == FLASH:
                flashes += 1
    settle(L)
    endg = pe["globalState"]
    out.update({
        "swaps": n_sw,
        "swap_event_liquidity_equals_replayed_active_liquidity": {"equal": l_match, "of": n_sw, "share": l_match / n_sw},
        "active_liquidity_replayed_at_END": str(L), "pool.liquidity()_at_END": pe["liquidity"], "equal_at_END": str(L) == pe["liquidity"],
        "initialised_ticks_at_END": len(tm.ticks),
        "last_swap_vs_state_at_END": {
            "last_swap": {"block": last_swap[0], "log_index": last_swap[1], "sqrt_price_x96": str(last_swap[2]), "liquidity": str(last_swap[3]), "tick": last_swap[4]},
            "globalState.price_at_END": endg["price_sqrtX96"], "globalState.tick_at_END": endg["tick"], "liquidity()_at_END": pe["liquidity"],
            "price_equal": str(last_swap[2]) == endg["price_sqrtX96"], "tick_equal": last_swap[4] == endg["tick"],
            "liquidity_equal_to_last_swap": str(last_swap[3]) == pe["liquidity"],
            "mints_burns_after_last_swap": after_last_swap,
            "liquidity_equal_after_applying_them": str(L) == pe["liquidity"]},
        "fee_validation": {
            "eligible_swaps (no initialised tick between the previous and the new tick, price moved)": elig, "all_swaps": n_sw,
            "within_1_ppm": within1, "share_within_1_ppm": within1 / elig if elig else None,
            "within_1_ppm_or_within_2_base_units_of_rounding": within1_or_round,
            "share_within_1_ppm_or_rounding": within1_or_round / elig if elig else None,
            "abs_deviation_ppm_histogram": dict(dev_hist), "examples_outside_both": worst,
            "aggregate_implied_fee_ppm_token0_in": exact_fee[0] * 10 ** 6 / elig_in[0] if elig_in[0] else None,
            "aggregate_implied_fee_ppm_token1_in": exact_fee[1] * 10 ** 6 / elig_in[1] if elig_in[1] else None},
    })
    # ---- fee accounting of the window
    vl = cached("bh_vault")
    TW, AW = T("TokensWithdrawal(address,address,uint256)"), T("AlgebraTokensWithdrawal(address,address,uint256)")
    wd = {"WAVAX": [0, 0], "USDC": [0, 0]}         # [to communityFeeReceiver, to algebraFeeReceiver]
    recv = Counter()
    other_vault_events = Counter()
    for x in vl:
        if x["b"] < B.START:
            continue
        if x["t"][0] in (TW, AW):
            tok = "0x" + x["t"][1][-40:]
            name = "WAVAX" if tok == B.WAVAX.lower() else "USDC" if tok == B.USDC.lower() else tok
            if name in wd:
                wd[name][0 if x["t"][0] == TW else 1] += int(x["d"], 16)
            recv[("TokensWithdrawal" if x["t"][0] == TW else "AlgebraTokensWithdrawal") + " -> " + B.addr_of(int(x["t"][2], 16))] += 1
        else:
            other_vault_events[x["t"][0]] += 1
    vb = st["B_community_vault"]
    acc = {}
    for k, name, dec in ((0, "WAVAX", 18), (1, "USDC", 6)):
        gross_fee = gross[k] // 10 ** 6
        vault_delta = int(vb["END"][f"balance_{name}"]) - int(vb["START-1"][f"balance_{name}"])
        pend_delta = int(pe["at_END_extra"]["getCommunityFeePending"][k]) - int(s1["getCommunityFeePending"][k])
        left_pool = vault_delta + wd[name][0] + wd[name][1] + pend_delta
        fg_delta = int(pe["at_END_extra"][f"totalFeeGrowth{k}Token"]) - int(s1[f"totalFeeGrowth{k}Token"])
        acc[name] = {
            "volume_in_raw": str(vol[k]), "gross_swap_fee_raw (sum amount_in*fee_ppm/1e6)": str(gross_fee),
            "gross_swap_fee": gross_fee / 10 ** dec,
            "vault_balance_change": str(vault_delta), "vault_withdrawn_to_communityFeeReceiver(gauge)": str(wd[name][0]),
            "vault_withdrawn_to_algebraFeeReceiver": str(wd[name][1]), "pool_pending_community_fee_change": str(pend_delta),
            "community_fee_that_left_the_swaps (sum of the four)": str(left_pool),
            "community_fee / gross_swap_fee": left_pool / gross_fee if gross_fee else None,
            "algebra_cut_of_withdrawals": wd[name][1] / (wd[name][0] + wd[name][1]) if (wd[name][0] + wd[name][1]) else None,
            "totalFeeGrowth_delta_X128": str(fg_delta), "totalFeeGrowth_delta_reproduced_from_ExcessTokens_only": str(fg_from_excess[k]),
            "fee_growth_fully_explained_by_ExcessTokens": fg_delta == fg_from_excess[k],
            "ExcessTokens_sum_raw": str(excess_sum[k]), "ExcessTokens_sum": excess_sum[k] / 10 ** dec,
            "lp_fee_growth_as_share_of_gross_swap_fee": excess_sum[k] / gross_fee if gross_fee else None,
            "lp_share_of_SWAP_fees": 0.0 if fg_delta == fg_from_excess[k] else None}
    out["fee_accounting_window"] = {"per_token": acc, "ExcessTokens_events": n_excess, "Flash_events": flashes,
                                    "vault_withdrawal_events_in_window": dict(recv), "other_vault_events_in_window": dict(other_vault_events),
                                    "note": "totalFeeGrowth changes only in swaps (LP part of the fee), flashes and _updateReserves (ExcessTokens: tokens sent to the pool outside a swap/mint are booked as fee growth); with communityFee = 1000/1000 the LP part of every swap fee is zero"}
    R["replay"] = out
    A.write_json(OUT, R)
    for k in ("equal_at_START_minus_1", "swap_event_liquidity_equals_replayed_active_liquidity", "equal_at_END", "fee_validation", "fee_accounting_window"):
        print(k, out[k])
    print("last swap", {k: v for k, v in out["last_swap_vs_state_at_END"].items()})
    return tm


def depth_offline(R: dict, tm: TickMap | None) -> None:
    st = A.read_json(B.DATA_BH / "state_at_end.json")
    dp = A.read_json(B.DATA_BH / "depth_at_end.json")["pools"]["blackhole"]
    if tm is None:
        tm = TickMap()
        it = csv_rows(B.DATA_BH / "blackhole_wavax_usdc_liquidity.csv.gz")
        next(it)
        for r in it:
            tm.apply(int(r[3]), int(r[4]), int(r[5]))
    pe = st["A_dataset_pool"]
    sq0, tick0, fee = int(pe["globalState"]["price_sqrtX96"]), pe["globalState"]["tick"], pe["fee()_ppm"]
    rows = []
    for tg in dp["targets"]:
        target = int(tg["target_sqrt_price"])
        sell = tg["direction"] == "sell_WAVAX"
        sq, L = sq0, tm.active(tick0)
        gross_in = out_amt = 0
        if sell:
            k = bisect.bisect_right(tm.ticks, tick0) - 1
            while sq > target:
                nxt = A.sqrt_ratio_at_tick(tm.ticks[k]) if k >= 0 else 0
                stop = max(nxt, target)
                net = A.amount0_delta_up(stop, sq, L)
                gross_in += -((-net * 10 ** 6) // (10 ** 6 - fee))
                out_amt += L * (sq - stop) // Q96
                sq = stop
                if sq == nxt and sq > target:
                    L -= tm.net[tm.ticks[k]]
                    k -= 1
        else:
            k = bisect.bisect_right(tm.ticks, tick0)
            while sq < target:
                nxt = A.sqrt_ratio_at_tick(tm.ticks[k]) if k < len(tm.ticks) else 1 << 200
                stop = min(nxt, target)
                net = A.amount1_delta_up(sq, stop, L)
                gross_in += -((-net * 10 ** 6) // (10 ** 6 - fee))
                out_amt += (L << 96) * (stop - sq) // stop // sq
                sq = stop
                if sq == nxt and sq < target:
                    L += tm.net[tm.ticks[k]]
                    k += 1
        rows.append({"direction": tg["direction"], "move_bp": tg["move_bp"], "quoter_amount_in": tg["amount_in"], "offline_amount_in": str(gross_in),
                     "rel_diff_amount_in": abs(gross_in - int(tg["amount_in"])) / int(tg["amount_in"]),
                     "quoter_amount_out": tg["amount_out"], "offline_amount_out": str(out_amt),
                     "rel_diff_amount_out": abs(out_amt - int(tg["amount_out"])) / int(tg["amount_out"])})
        print(rows[-1])
    R["depth_offline_vs_quoter_blackhole"] = {"method": "walk the tick map rebuilt from blackhole_wavax_usdc_liquidity.csv.gz from the END price to the target, v3 integer math, fee 500 ppm on the input of every step",
                                              "rows": rows, "max_rel_diff": max(max(r["rel_diff_amount_in"], r["rel_diff_amount_out"]) for r in rows)}
    A.write_json(OUT, R)


def main() -> None:
    want = set(sys.argv[1:]) or {"structure", "rerequest", "replay", "depth"}
    R = A.read_json(OUT) if OUT.exists() else {}
    R["seed"] = SEED
    R["run_at_unix"] = int(time.time())
    if "structure" in want:
        R["structure"] = {f.name: structure_of(f) for f in sorted(B.DATA_BH.glob("*.csv.gz"))}
        rep = A.read_json(B.DATA_BH / "build_report.json")
        til = {}
        for k, v in (("bh_pool", rep["pool"]["window"]), ("bh_gauge_rf", rep["gauge"]), ("bh_black_pair", rep["black"])):
            rg = v["ranges"]
            til[k] = {"from": rg[0][0], "to": rg[-1][1], "windows": len(rg), "logs": sum(x[2] for x in rg),
                      "tiling_ok": all(x[1] + 1 == y[0] for x, y in zip(rg, rg[1:])), "sources": v["sources"]}
        for seg in rep["pool"]["pre_window_segments"]:
            rg = seg["ranges"]
            til[seg["dataset"]] = {"from": rg[0][0], "to": rg[-1][1], "windows": len(rg), "logs": sum(x[2] for x in rg),
                                   "tiling_ok": all(x[1] + 1 == y[0] for x, y in zip(rg, rg[1:])), "sources": seg["sources"]}
        segs = rep["pool"]["pre_window_segments"]
        til["pre_window_segments_tile_creation_to_START_minus_1"] = (segs[0]["requested_from"] == B.POOL_CREATED and segs[-1]["requested_to"] == B.START - 1
                                                                     and all(x["requested_to"] + 1 == y["requested_from"] for x, y in zip(segs, segs[1:])))
        for name in ("bh_vault", "bh_gm_distribute"):
            rg = A.ledger_rows(name)
            til[name] = {"from": rg[0]["from"], "to": rg[-1]["to"], "windows": len(rg), "logs": sum(x["count"] for x in rg),
                         "tiling_ok": all(x["to"] + 1 == y["from"] for x, y in zip(rg, rg[1:])), "sources": dict(Counter(x["host"] for x in rg))}
        R["requested_ranges"] = til
        R["header_checks"] = {"pool": rep["pool"]["window"].get("header_checks"), "gauge": rep["gauge"].get("header_checks"), "black": rep["black"].get("header_checks")}
        A.write_json(OUT, R)
        print("structure", R["structure"])
        print("ranges", {k: (v if isinstance(v, bool) else (v["windows"], v["logs"], v["tiling_ok"])) for k, v in til.items()})
    tm = None
    if "replay" in want:
        tm = replay(R)
    if "depth" in want:
        depth_offline(R, tm)
    if "rerequest" in want:
        rerequest(R)
    A.write_json(OUT, R)


if __name__ == "__main__":
    main()
