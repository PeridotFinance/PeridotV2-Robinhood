"""Handoff 08 Phase 1 analyses, reconstructed from logs and checked against live state at the end block.

The collector in ``strategy_evidence.py`` (PR #12) needs an archive node. The public RPC keeps only minutes of
state but serves all logs, so this module rebuilds the same history from events and VALIDATES the rebuild
against state read at the window's end block. Nothing here signs or sends a transaction.
"""
from __future__ import annotations

import datetime as dt
import statistics
from collections import defaultdict
from dataclasses import dataclass, field

from evidence_lib import (EvidenceError, Rpc, TimeIndex, amounts_for_liquidity, iso, session_of, sN, topic_addr, words)
from strategy_data import (ADAPTER, FEED_AGGREGATOR, NVDA, P_NVDA, P_USDG, PAIR_ID, RAMSES_NVDA_GAUGE, RAMSES_NVDA_POOL, RAM,
                           ROLLOUT_BLOCK, USDG, V4_STOCK_IS_TOKEN0, VAULT, BalanceSeries, KEEPERS, Oracle, T, Trade,
                           fee_function, load_adapter_events, load_erc20_flows, load_feed, load_ramses_pool, load_v4,
                           load_vault_events, ramses_trades, v4_trades)
from strategy_ledger import PositionBook, build_position_events, build_principal_timeline, our_token_ids, principal_at
from strategy_metrics import apr, apy, bps, liquidity_scenario, markouts, twr
from strategy_pool import SwapRecord, depth_usd, fee_share, replay_v4
from strategy_state import snapshot

ETH_USD_AGGREGATOR = "0x6091E64eb7138EEF066a80FD3A0d7427B91f2721"
FEED_LOOKBACK_BLOCKS = 6_000_000  # about a week before the window, so the oracle has a value at the start
STATE_RETENTION_BLOCKS = 3_000    # the public node keeps roughly 3,650 blocks of state


def utc_day(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).strftime("%Y-%m-%d")


@dataclass
class Ctx:
    rpc: Rpc
    ti: TimeIndex
    frm: int
    to: int
    t0: float
    t1: float
    snap: dict
    oracle: Oracle
    eth_oracle: Oracle | None
    feed_events: list
    v4_init: dict
    v4_trades_window: list
    v4_records: list          # replay records inside the window (swaps and our position changes)
    v4_book: PositionBook
    vault_events: dict
    adapter_events: dict
    timeline: list
    balances: dict
    ramses: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)


def load_context(rpc: Rpc, ti: TimeIndex, frm: int, to: int, progress=print) -> Ctx:
    head = rpc.head()
    if head - to > STATE_RETENTION_BLOCKS:
        raise EvidenceError(f"--to-block {to} is {head - to} blocks behind the head; the node keeps state for only about "
                            f"{STATE_RETENTION_BLOCKS}. State at the end block is required to validate the reconstruction.")
    if frm < ROLLOUT_BLOCK or to <= frm:
        raise EvidenceError("require rollout <= from-block < to-block")
    progress("state snapshot at the end block")
    snap = snapshot(rpc, to)
    progress("header grid")
    ti.ensure(frm - 10_000, to, progress=progress)
    t0, t1 = ti.ts(frm), ti.ts(to)
    final = to
    progress("price feeds")
    feed_events = load_feed(rpc, final, max(frm - FEED_LOOKBACK_BLOCKS, 0), to)
    eth = load_feed(rpc, final, max(frm - FEED_LOOKBACK_BLOCKS, 0), to, aggregator=ETH_USD_AGGREGATOR)
    progress("uniswap v4 pool history")
    v4 = load_v4(rpc, ti, final, to)
    lp_fee = v4["init"]["fee"]
    trades_all = v4_trades(v4["swaps"], ti, lp_fee, ts_from_block=frm)
    progress("our vault and adapter events")
    vault_events = load_vault_events(rpc, final, to)
    adapter_events = load_adapter_events(rpc, final, to)
    ids = our_token_ids(adapter_events)
    manager = snap["position"]["positionManager"]
    own_mods = build_position_events(v4["mods"], ids, manager)
    all_mods = [m for m in v4["mods"]]
    records, book = replay_v4(v4["init"], trades_all, v4["swaps"], all_mods, ids, record_from_block=frm)
    for r in records:
        if r.kind == "position":
            r.ts = ti.ts(r.block)
    timeline = build_principal_timeline(vault_events)
    progress("token balances")
    balances = {
        "vault_stock": BalanceSeries(load_erc20_flows(rpc, final, NVDA, VAULT, to)),
        "vault_usd": BalanceSeries(load_erc20_flows(rpc, final, USDG, VAULT, to)),
        "pNVDA_stock": BalanceSeries(load_erc20_flows(rpc, final, NVDA, P_NVDA, to)),
        "pUSDG_usd": BalanceSeries(load_erc20_flows(rpc, final, USDG, P_USDG, to)),
    }
    return Ctx(rpc, ti, frm, to, t0, t1, snap, Oracle(feed_events), Oracle(eth) if eth else None, feed_events, v4["init"],
               [t for t in trades_all if t.block >= frm], records, book, vault_events, adapter_events, timeline, balances,
               notes=[f"{len(own_mods)} ModifyLiquidity events belong to our NFTs"])


# --------------------------------------------------------------------------------------- validation


def validate_reconstruction(ctx: Ctx) -> dict:
    """Compare the event-sourced state with state read at the end block. Any mismatch is reported, not hidden."""
    snap, checks = ctx.snap, []

    def check(name, got, want, tol=0):
        ok = abs(got - want) <= tol
        checks.append({"name": name, "reconstructed": got, "onChain": want, "tolerance": tol, "ok": ok})

    led = snap["vault"]["ledger"]
    sp, up = principal_at(ctx.timeline, ctx.to)
    check("vault stockPrincipal", sp, led["stockPrincipal"])
    check("vault usdgPrincipal", up, led["usdgPrincipal"])
    check("vault NVDA balance", ctx.balances["vault_stock"].at(ctx.to), snap["vault"]["vaultToken"]["NVDA"])
    check("vault USDG balance", ctx.balances["vault_usd"].at(ctx.to), snap["vault"]["vaultToken"]["USDG"])
    check("pNVDA local cash", ctx.balances["pNVDA_stock"].at(ctx.to), snap["pNVDA"]["getCash"] - snap["pNVDA"]["vaultWithdrawableAssets"])
    check("pUSDG local cash", ctx.balances["pUSDG_usd"].at(ctx.to), snap["pUSDG"]["getCash"] - snap["pUSDG"]["vaultWithdrawableAssets"])
    pos = snap["position"]
    live = ctx.v4_book.live()
    check("our live liquidity", sum(p.liq for p in live), pos["liquidity"])
    if pos["liquidity"]:
        check("our live range lower", live[0].lower if live else 0, pos["tickLower"])
        check("our live range upper", live[0].upper if live else 0, pos["tickUpper"])
    check("v4 pool tick (replayed)", ctx.v4_book.pool_tick, snap["v4Pool"]["tick"])
    check("v4 pool active liquidity (replayed from Swap + ModifyLiquidity)", ctx.v4_book.pool_liquidity, snap["v4Pool"]["liquidity"])
    return {"ok": all(c["ok"] for c in checks),
            "scope": "vault principals and balances, market local cash, our position (liquidity and range), and the v4 pool tick and active liquidity. "
                     "NOT validated against chain state: swap volumes, fees and markouts (fees are cross-checked against collected fees in section 3); "
                     "the Ramses pool is checked separately in its own section.",
            "checks": checks}


# ------------------------------------------------------------------------------- 3. fees and volume


def summarize_trades(trades: list[Trade], lp_fraction=lambda t: 1.0) -> dict:
    if not trades:
        raise EvidenceError("no trades in the window")
    by_day: dict = defaultdict(lambda: {"swaps": 0, "volumeUsd": 0.0, "lpFeeUsd": 0.0})
    by_sess: dict = defaultdict(lambda: {"swaps": 0, "volumeUsd": 0.0, "lpFeeUsd": 0.0})
    vol = fee_total = lp_fee = 0.0
    depth_up, depth_down, liq = [], [], []
    for i, t in enumerate(trades):
        f = lp_fraction(t)
        lpf = t.fee_usd * f
        vol += t.notional
        fee_total += t.fee_usd
        lp_fee += lpf
        for bucket in (by_day[utc_day(t.ts)], by_sess[session_of(int(t.ts))]):
            bucket["swaps"] += 1
            bucket["volumeUsd"] += t.notional
            bucket["lpFeeUsd"] += lpf
        if i % 20 == 0:
            up, down = depth_usd(t.price, t.liq_after, 0.01)
            depth_up.append(up)
            depth_down.append(down)
            liq.append(t.liq_after)
    notionals = sorted(t.notional for t in trades)
    span = trades[-1].ts - trades[0].ts
    return {
        "swaps": len(trades), "volumeUsd": vol, "swapFeesTotalUsd": fee_total, "lpFeesUsd": lp_fee,
        "swapperPaidFeesUsd": sum(t.swapper_fee_usd for t in trades),
        "protocolTakeUsd": sum(t.swapper_fee_usd for t in trades) - fee_total,
        "lpFeeBpsOfVolume": bps(lp_fee, vol), "medianTradeUsd": notionals[len(notionals) // 2],
        "medianDepthToMove1pctUp": statistics.median(depth_up), "medianDepthToMove1pctDown": statistics.median(depth_down),
        "firstSwap": iso(int(trades[0].ts)), "lastSwap": iso(int(trades[-1].ts)), "observedSeconds": span,
        "byDay": dict(sorted(by_day.items())), "bySession": dict(by_sess),
    }


def our_fee_estimate(ctx: Ctx, scale: float = 1.0) -> dict:
    """Fees our liquidity would have earned per the replay (scale>1 shows dilution), per fee_share()."""
    est = 0.0
    for r in ctx.v4_records:
        if r.kind != "swap":
            continue
        est += r.fee_usd * fee_share(r, scale)
    return {"estimatedLpFeesUsd": est}


def actual_fees(ctx: Ctx, lo_block: int | None = None, hi_block: int | None = None) -> dict:
    """Gross fees the vault actually collected in [lo_block, hi_block] (FeesProcessed), valued at the oracle."""
    lo_block = ctx.frm if lo_block is None else lo_block
    hi_block = ctx.to if hi_block is None else hi_block
    gross = reserve = 0.0
    n = 0
    for l in ctx.vault_events["fees"]:
        if l["blockNumber"] < lo_block or l["blockNumber"] > hi_block:
            continue
        w = words(l["data"])
        p = ctx.oracle.price(ctx.ti.ts(l["blockNumber"]))
        gross += w[0] / 1e18 * p + w[1] / 1e6
        reserve += w[2] / 1e18 * p + w[3] / 1e6
        n += 1
    pos = ctx.snap["position"]
    pending = pos.get("pendingFeesStockRaw", 0) / 1e18 * ctx.oracle.price(ctx.t1) + pos.get("pendingFeesUsdgRaw", 0) / 1e6
    cut = ctx.snap["vault"]["reserveFeeBps"] / 1e4
    return {"collections": n, "grossCollectedUsd": gross, "reserveCutUsd": reserve, "pendingUncollectedAtEndUsd": pending,
            "pendingUncollectedNetOfReserveUsd": pending * (1 - cut), "blockRange": [lo_block, hi_block]}


def analyse_uniswap_fees(ctx: Ctx) -> dict:
    summary = summarize_trades(ctx.v4_trades_window)
    est = our_fee_estimate(ctx)
    act = actual_fees(ctx)
    total_est = est["estimatedLpFeesUsd"]
    realised = act["grossCollectedUsd"] + act["pendingUncollectedAtEndUsd"]
    return {"venue": "Uniswap v4 NVDA/USDG 0.30% (swappers pay LP fee + a constant protocol fee)", "pool": summary, "ourEstimate": est, "ourActual": act,
            "estimateVsActualRatio": (total_est / realised) if realised else None,
            "note": "Estimate = sum(LP fee x our active-liquidity share); actual = FeesProcessed gross + uncollected at the end block. "
                    "Opening/closing fee timing (collected before/after the window) explains part of any gap."}


# --------------------------------------------------------------------------------------- 2. capital


def build_samples(ctx: Ctx) -> list[dict]:
    """Capital placement at every trade, position change and oracle update inside the window."""
    recs = sorted([r for r in ctx.v4_records if r.ts >= ctx.t0], key=lambda r: (r.block, r.idx))
    feed_ts = [t for t, _ in ctx.feed_events if ctx.t0 <= t <= ctx.t1]
    out: list[dict] = []
    fi = 0
    last = None

    def state(r: SwapRecord, ts: float, block: int, idx: int) -> dict:
        lp = {"in": [0.0, 0.0], "out": [0.0, 0.0]}  # [stock, usd]
        for lower, upper, liq in r.positions:
            a0, a1 = amounts_for_liquidity(liq, r.sqrt_after, lower, upper)  # token0 = USDG, token1 = NVDA
            bucket = lp["in"] if lower <= r.tick_after < upper else lp["out"]
            bucket[0] += a1 / 1e18
            bucket[1] += a0 / 1e6
        p_pool = (1e12 / (r.sqrt_after ** 2)) if r.sqrt_after else 0.0
        return {"ts": ts, "block": block, "idx": idx, "pOracle": ctx.oracle.price(ts), "pPool": p_pool, "tick": r.tick_after,
                "lpIn": lp["in"], "lpOut": lp["out"],
                "idle": [ctx.balances["vault_stock"].at(block, idx) / 1e18, ctx.balances["vault_usd"].at(block, idx) / 1e6],
                "cash": [ctx.balances["pNVDA_stock"].at(block, idx) / 1e18, ctx.balances["pUSDG_usd"].at(block, idx) / 1e6]}

    for r in recs:
        while fi < len(feed_ts) and feed_ts[fi] < r.ts:
            if last is not None:
                s = dict(last); s["ts"] = feed_ts[fi]; s["pOracle"] = ctx.oracle.price(feed_ts[fi]); s["kind"] = "oracle"
                out.append(s)
            fi += 1
        last = state(r, r.ts, r.block, r.idx)
        last["kind"] = r.kind
        out.append(last)
    return out


def analyse_capital(ctx: Ctx, samples: list[dict]) -> dict:
    """Time-weighted placement of capital, per token and in USD, overall and by session."""
    if len(samples) < 2:
        raise EvidenceError("not enough samples for capital placement")
    keys = ("lp_in_range", "lp_out_of_range", "vault_idle", "market_local_cash")
    tot = {k: [0.0, 0.0] for k in keys}  # [stock-token-seconds, usd-token-seconds]
    usd_s = {k: 0.0 for k in keys}
    sess: dict = defaultdict(lambda: {"seconds": 0.0, **{k: 0.0 for k in keys}})
    seconds = 0.0
    in_range_time = 0.0
    for a, b in zip(samples, samples[1:]):
        dt_ = max(b["ts"] - a["ts"], 0.0)
        if dt_ == 0:
            continue
        vals = {"lp_in_range": a["lpIn"], "lp_out_of_range": a["lpOut"], "vault_idle": a["idle"], "market_local_cash": a["cash"]}
        has_lp_in = (a["lpIn"][0] * a["pOracle"] + a["lpIn"][1]) > 0
        in_range_time += dt_ if has_lp_in else 0.0
        seconds += dt_
        s = sess[session_of(int(a["ts"]))]
        s["seconds"] += dt_
        for k, (stock, usd) in vals.items():
            tot[k][0] += stock * dt_
            tot[k][1] += usd * dt_
            v = (stock * a["pOracle"] + usd) * dt_
            usd_s[k] += v
            s[k] += v
    pair_keys = ("lp_in_range", "lp_out_of_range", "vault_idle")
    pair_total = sum(usd_s[k] for k in pair_keys)
    avg_usd = {k: usd_s[k] / seconds for k in keys}
    per_token = {}
    for idx, name in ((0, "NVDA"), (1, "USDG")):
        parts = {k: tot[k][idx] / seconds for k in keys}
        strat = parts["lp_in_range"] + parts["lp_out_of_range"] + parts["vault_idle"]
        per_token[name] = {"average": parts, "inRangeShareOfStrategy": (parts["lp_in_range"] / strat) if strat else None,
                           "marketLocalCashShareOfTotal": parts["market_local_cash"] / (strat + parts["market_local_cash"]) if (strat + parts["market_local_cash"]) else None}
    return {
        "seconds": seconds, "samples": len(samples),
        "timeWithAnyLiquidityInRange": in_range_time / seconds,
        "averageUsd": avg_usd,
        "strategyCapitalShare": {k: (usd_s[k] / pair_total if pair_total else None) for k in pair_keys},
        "perToken": per_token,
        "bySession": {k: {"hours": v["seconds"] / 3600, **{kk: (v[kk] / v["seconds"] if v["seconds"] else None) for kk in keys}} for k, v in sess.items()},
        "note": "NVDA and USDG are kept separate; USD values use the oracle price (frozen when the feed is stale). Out-of-range liquidity earns no fees.",
    }
