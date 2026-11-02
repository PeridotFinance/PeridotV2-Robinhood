"""Ramses venue over the same window, plus dilution and profitable-capacity runs for both venues."""
from __future__ import annotations

import statistics
from collections import defaultdict

from evidence_lib import EvidenceError, iso, words, topic_addr
from strategy_analysis import Ctx, our_fee_estimate, summarize_trades, utc_day
from strategy_data import (RAM, RAMSES_NVDA_GAUGE, RAMSES_NVDA_POOL, RAMSES_NVDA_STOCK_IS_TOKEN0, T, Trade, V4_STOCK_IS_TOKEN0,
                           fee_function, load_ramses_pool, ramses_trades)
from strategy_metrics import markouts
from strategy_pool import fee_share
from strategy_sim import Costs, Policy, Result, simulate

SIZES_USD = (10, 1_000, 10_000, 50_000)
SWEEP_USD = (10, 100, 1_000, 5_000, 10_000, 25_000, 50_000, 100_000, 250_000, 500_000)


def load_ramses(ctx: Ctx) -> dict:
    rpc, ti = ctx.rpc, ctx.ti
    raw = load_ramses_pool(rpc, ctx.to, RAMSES_NVDA_POOL, ctx.to, frm=0 if False else ctx.frm)
    # fee history needs every FeeAdjustment since the pool started
    fee_events = load_ramses_pool(rpc, ctx.to, RAMSES_NVDA_POOL, ctx.to, frm=0)["fee_adj"]
    fee_at = fee_function(fee_events, ctx.snap["ramsesPool"]["fee"])
    trades = ramses_trades(raw["swaps"], ti, fee_at, RAMSES_NVDA_STOCK_IS_TOKEN0)
    proto = ctx.snap["ramsesPool"]["feeProtocolPpm"] / 1e6
    notify = defaultdict(lambda: {"events": 0, "amountRaw": 0})
    for l in rpc.logs(RAMSES_NVDA_GAUGE, [T("NotifyReward(address,address,uint256,uint256)")], ctx.frm, ctx.to, ctx.to):
        tok = topic_addr(l["topics"][2])
        notify[tok]["events"] += 1
        notify[tok]["amountRaw"] += words(l["data"])[0]
    return {"trades": trades, "feeAt": fee_at, "fee_events_total": len(fee_events), "fee_events_window": [l for l in fee_events if l["blockNumber"] >= ctx.frm],
            "protocolShare": proto, "mints": len(raw["mints"]), "burns": len(raw["burns"]), "notify": dict(notify),
            "protocolEvents": len(raw["proto"])}


def analyse_ramses(ctx: Ctx, rm: dict) -> dict:
    proto = rm["protocolShare"]
    summary = summarize_trades(rm["trades"], lambda t: 1.0 - proto)
    fees = [t.fee_ppm for t in rm["trades"]]
    return {
        "venue": "Ramses CL NVDA/USDG (dynamic fee, 50% protocol share)", "pool": RAMSES_NVDA_POOL, "summary": summary,
        "feePpm": {"min": min(fees), "median": statistics.median(fees), "max": max(fees), "adjustmentsInWindow": len(rm["fee_events_window"])},
        "protocolShareAssumed": proto,
        "protocolShareNote": f"read from slot0 at the end block; {rm['protocolEvents']} SetFeeProtocol events seen in the window, so it is treated as constant",
        "lpFeeNote": "LP fee = swap fee x (1 - protocol share). Whether that LP share is paid through collect() or through the gauge for staked positions "
                     "is a fork-test question for handoff 01; this is the economic upper bound, not a proven distributable income.",
        "mintsInWindow": rm["mints"], "burnsInWindow": rm["burns"],
        "gaugeNotifyRewardsInWindow": rm["notify"],
        "markouts": markouts(rm["trades"], (60, 3600), ctx.oracle, fee_fraction=1.0 - proto),
        "rewardNote": "RAM is not valued here: without an enforceable distribution mechanism for suppliers its value to them is zero (00 section 3).",
    }


# ------------------------------------------------------------------------------------------- dilution


def v4_dilution(ctx: Ctx, avg_pair_value: float) -> dict:
    """Realised v4 ranges and swaps with our liquidity scaled to each size (fees dilute; inventory effects scale linearly)."""
    base = our_fee_estimate(ctx, 1.0)["estimatedLpFeesUsd"]
    rows = []
    for size in SIZES_USD:
        k = size / avg_pair_value
        fees = our_fee_estimate(ctx, k)["estimatedLpFeesUsd"]
        rows.append({"sizeUsd": size, "scale": k, "lpFeesUsd": fees, "lpFeesPerDollarPerYearPct": None})
    seconds = ctx.t1 - ctx.t0
    for r in rows:
        r["lpFeesPerDollarPerYearPct"] = 100 * r["lpFeesUsd"] / r["sizeUsd"] * (365.25 * 86400) / seconds
    return {"basis": "realised ranges, replayed swaps, our share = liquidity / (others + ours)", "actualScaleFees": base, "rows": rows}


def liq_other_v4(ctx: Ctx) -> tuple[list[Trade], list[float]]:
    swaps = [r for r in ctx.v4_records if r.kind == "swap"]
    trades = [t for t in ctx.v4_trades_window]
    if len(swaps) != len(trades):
        # align by (block, idx)
        key = {(r.block, r.idx): r for r in swaps}
        trades = [t for t in trades if (t.block, t.idx) in key]
        swaps = [key[(t.block, t.idx)] for t in trades]
    return trades, [max(r.liq_before_raw - r.our_liq_raw, 0) * 1e-12 for r in swaps]


def liq_other_ramses(trades: list[Trade]) -> list[float]:
    return [trades[i - 1].liq_after if i else trades[0].liq_after for i in range(len(trades))]


def week_chunks(trades: list[Trade], others: list[float], seconds: float = 7 * 86400):
    out, start = [], 0
    t0 = trades[0].ts
    for i, t in enumerate(trades):
        if t.ts - t0 >= seconds:
            if i - start > 50:
                out.append((trades[start:i], others[start:i]))
            start, t0 = i, t.ts
    return out


def policy_runs(label: str, trades: list[Trade], others: list[float], oracle, stock_is_token0: bool, spacing: int,
                lp_fraction: float, costs: Costs, daily_fixed_usd: float, policies: dict, price_move_pct: float | None = None) -> dict:
    out = {}
    seconds = trades[-1].ts - trades[0].ts
    days = seconds / 86400
    for pname, pol in policies.items():
        rows = []
        for size in SWEEP_USD:
            r = simulate(trades, others, oracle, size, pol, stock_is_token0, lp_fraction, 0.2, costs)
            net = r.net_vs_hold_usd - daily_fixed_usd * days
            d = r.asdict()
            d["fixedOpsUsd"] = daily_fixed_usd * days
            d["net_after_ops_usd"] = net
            d["net_after_ops_apr_pct"] = 100 * net / size * (365.25 * 86400) / seconds
            rows.append(d)
        # weekly dispersion at the best positive size (or $1,000 when nothing is positive)
        positive = [r for r in rows if r["net_after_ops_usd"] > 0]
        ref = max(positive, key=lambda r: r["size_usd"]) if positive else next(r for r in rows if r["size_usd"] == 1_000)
        weekly = []
        for ts_, ls_ in week_chunks(trades, others):
            w = simulate(ts_, ls_, oracle, ref["size_usd"], pol, stock_is_token0, lp_fraction, 0.2, costs)
            wsec = ts_[-1].ts - ts_[0].ts
            weekly.append(100 * (w.net_vs_hold_usd - daily_fixed_usd * wsec / 86400) / ref["size_usd"] * (365.25 * 86400) / wsec)
        out[pname] = {"policy": pol.__dict__, "rows": rows, "weeklyNetAprPctAtReferenceSize": {"sizeUsd": ref["size_usd"], "weeks": weekly,
                      "low": min(weekly) if weekly else None, "high": max(weekly) if weekly else None}}
        out[pname]["capacitySentence"] = capacity_sentence(label, pname, rows, out[pname]["weeklyNetAprPctAtReferenceSize"], seconds, price_move_pct)
    return out


def capacity_sentence(venue: str, policy: str, rows: list[dict], weekly: dict, seconds: float, price_move_pct: float | None = None) -> str:
    """The one sentence the operator acts on (00 section 2). Net return versus holding the deposit, after measured keeper cost."""
    days = seconds / 86400
    move = f" The window had a one-way oracle move of {price_move_pct:+.1f}%, which hurts any range position versus holding;" if price_move_pct is not None else ""
    cond = (f"over the observed {days:.0f}-day window with a simplified single-range replay (not the contract's exact code path), measured keeper cost, "
            f"a 20% reserve cut, RAM valued at zero, and net of the cost of simply holding the deposit.{move} one window is not a forecast")
    positive = [r for r in rows if r["net_after_ops_usd"] > 0]
    best = max(rows, key=lambda r: r["net_after_ops_apr_pct"])
    gross = best["fees_gross_apr_pct"]
    if not positive:
        small = rows[0]
        lo, hi = weekly.get("low"), weekly.get("high")
        wk = f" Weekly sub-windows at ${weekly['sizeUsd']:,} ranged {lo:.0f}% to {hi:.0f}% a year." if lo is not None else ""
        return (f"{venue} / {policy}: supports NO tested size ($10 to $500k). Gross fees were about {gross:.0f}% a year at the best size "
                f"(only {best['avg_deployed_fraction']:.0%} of capital deployed on average), but net return versus holding was never positive over the whole window: "
                f"best case {best['net_after_ops_apr_pct']:.1f}% a year at ${best['size_usd']:,}; at ${small['size_usd']} keeper cost alone makes it "
                f"{small['net_after_ops_apr_pct']:.0f}%.{wk} {cond}.")
    top = max(positive, key=lambda r: r["size_usd"])
    lo, hi = weekly.get("low"), weekly.get("high")
    rng = f"range {lo:.0f}% to {hi:.0f}% across weekly sub-windows at ${weekly['sizeUsd']:,}" if lo is not None else "no weekly range"
    nxt = [r for r in rows if r["size_usd"] > top["size_usd"]]
    edge = f" and turns negative by ${nxt[0]['size_usd']:,}" if nxt else " (the largest size tested is still positive)"
    return f"{venue} / {policy}: supports about ${top['size_usd']:,} of our capital at about {top['net_after_ops_apr_pct']:.1f}% net a year ({rng}){edge}, {cond}."
