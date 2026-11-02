"""Supplier returns (item 1) and the inventory / portfolio ledger (item 4) for the Uniswap vault pair."""
from __future__ import annotations

import math
from collections import defaultdict

from evidence_lib import EvidenceError, amounts_for_liquidity, iso, session_of, words
from strategy_analysis import Ctx, actual_fees
from strategy_data import KEEPERS, NVDA, P_NVDA, P_USDG, T, Trade, USDG
from strategy_ledger import principal_at
from strategy_metrics import apr, apy, bps, markouts, twr

MARKETS = {"pNVDA": {"address": P_NVDA, "side": "stock", "decimals": 18, "balance": "pNVDA_stock"},
           "pUSDG": {"address": P_USDG, "side": "usd", "decimals": 6, "balance": "pUSDG_usd"}}
MARKET_STATE_EVENTS = {
    "Mint": "Mint(address,uint256,uint256)", "Redeem": "Redeem(address,uint256,uint256)",
    "Borrow": "Borrow(address,uint256,uint256,uint256)", "RepayBorrow": "RepayBorrow(address,address,uint256,uint256,uint256)",
    "LiquidateBorrow": "LiquidateBorrow(address,address,uint256,address,uint256)", "AccrueInterest": "AccrueInterest(uint256,uint256,uint256,uint256)",
    "ReservesAdded": "ReservesAdded(address,uint256,uint256)", "ReservesReduced": "ReservesReduced(address,uint256,uint256)",
    "NewReserveFactor": "NewReserveFactor(uint256,uint256)", "NewMarketInterestRateModel": "NewMarketInterestRateModel(address,address)",
}


def exchange_rate(local_cash: int, vault_claim: int, borrows: int, reserves: int, supply: int) -> int:
    """The delegate's exchangeRateStoredInternal: (cash + vault claim + borrows - reserves) * 1e18 / supply."""
    if supply == 0:
        raise EvidenceError("zero supply")
    return (local_cash + vault_claim + borrows - reserves) * 10 ** 18 // supply


def mark_to_end(ctx: Ctx) -> dict:
    """What the NEXT checkpoint would book at the end block: collect pending fees (less the vault reserve cut), value the
    position at the oracle-derived reference price, and scale both principals pro-rata if the pair is short (gains are
    recognised only when both native sides are covered). Mirrors RobinhoodBoostedVaultV3._checkpointPair."""
    snap = ctx.snap
    led, pos, P = snap["vault"]["ledger"], snap["position"], ctx.oracle.price(ctx.t1)
    s = math.sqrt(1e12 / P)  # raw sqrt price implied by the oracle (USDG token0, NVDA token1)
    a0, a1 = amounts_for_liquidity(pos["liquidity"], s, pos["tickLower"], pos["tickUpper"]) if pos["liquidity"] else (0.0, 0.0)
    rb = snap["vault"]["reserveFeeBps"]
    fu, fs = pos.get("pendingFeesUsdgRaw", 0), pos.get("pendingFeesStockRaw", 0)
    net_u, net_s = fu - fu * rb // 10_000, fs - fs * rb // 10_000
    prin_s, prin_u = led["stockPrincipal"] + net_s, led["usdgPrincipal"] + net_u
    assets_s, assets_u = led["stockIdle"] + net_s + a1, led["usdgIdle"] + net_u + a0
    val = lambda x, y: x / 1e18 * P + y / 1e6
    bench, gross = val(prin_s, prin_u), val(assets_s, assets_u)
    outcome = "unchanged"
    if bench and gross < bench:
        scale = gross / bench
        prin_s, prin_u, outcome = int(prin_s * scale), int(prin_u * scale), "loss_recognised"
    elif assets_s >= prin_s and assets_u >= prin_u:
        prin_s, prin_u, outcome = int(assets_s), int(assets_u), "gain_recognised"
    age = ctx.snap["timestamp"] - led["lastCheckpoint"]
    return {"oracleUsd": P, "benchmarkUsd": bench, "grossUsd": gross, "shortfallUsd": max(bench - gross, 0.0),
            "shortfallPctOfBenchmark": 100 * max(bench - gross, 0.0) / bench if bench else 0.0, "outcome": outcome,
            "stockPrincipalMarked": prin_s, "usdgPrincipalMarked": prin_u,
            "lastCheckpointAgeSeconds": age, "maxCheckpointAgeSeconds": snap["vault"]["maxCheckpointAge"],
            "checkpointOverdue": age > snap["vault"]["maxCheckpointAge"]}


def market_activity(ctx: Ctx, market: str) -> dict:
    counts = {}
    for name, sig in MARKET_STATE_EVENTS.items():
        counts[name] = len(ctx.rpc.logs(market, [T(sig)], ctx.frm, ctx.to, ctx.to))
    return counts


def _accrual_state(m: dict, ctx: Ctx, block: int) -> tuple[int, int]:
    """(borrows, reserves) at ``block`` given the last stored accrual and the per-native-block rate at the end block.

    Interest is simple over the native blocks since the last accrual (Compound accrual); the rate at the end block
    is applied over the whole span, which is accurate here because borrows and cash barely move."""
    native = ctx.ti.l1(block)
    span = max(native - m["accrualBlockNumber"], 0.0)
    b = m["totalBorrows"]
    interest = int(b * m["borrowRatePerBlock"] * span / 1e18)
    return b + interest, m["totalReserves"] + interest * m["reserveFactorMantissa"] // 10 ** 18


def analyse_supplier_returns(ctx: Ctx) -> dict:
    out = {}
    for name, cfg in MARKETS.items():
        m = ctx.snap[name]
        act = market_activity(ctx, cfg["address"])
        state_changing = {k: v for k, v in act.items() if v and k not in ("NewReserveFactor",)}
        side_idx = 0 if cfg["side"] == "stock" else 1
        res = {"address": cfg["address"], "eventsInWindow": act}
        if state_changing:
            res["status"] = "NOT_MODELLED"
            res["reason"] = "market supply/borrow activity inside the window; the decomposition below assumes none"
            out[name] = res
            continue
        led = ctx.snap["vault"]["ledger"]
        supply = m["totalSupply"]
        local_end = ctx.balances[cfg["balance"]].at(ctx.to)
        claim_end = led["stockPrincipal"] if cfg["side"] == "stock" else led["usdgPrincipal"]
        # validate the formula against the contract's own stored and current rates at the end block
        stored = exchange_rate(local_end, claim_end, m["totalBorrows"], m["totalReserves"], supply)
        b_end, r_end = _accrual_state(m, ctx, ctx.to)
        current = exchange_rate(local_end, claim_end, b_end, r_end, supply)
        res["validation"] = {
            "exchangeRateStored": {"reconstructed": stored, "onChain": m["exchangeRateStored"], "ok": stored == m["exchangeRateStored"]},
            "exchangeRateCurrent": {"reconstructed": current, "onChain": m["exchangeRateCurrent"],
                                    "relativeError": abs(current - m["exchangeRateCurrent"]) / m["exchangeRateCurrent"]},
        }
        # window start: the ledger principal in force at the first sample, borrows/reserves at the stored accrual
        first_block = ctx.frm
        sp0, up0 = principal_at(ctx.timeline, first_block, 0)
        claim_start = sp0 if cfg["side"] == "stock" else up0
        local_start = ctx.balances[cfg["balance"]].at(first_block, 0)
        b0, r0 = _accrual_state(m, ctx, first_block)
        rate_start = exchange_rate(local_start, claim_start, b0, r0, supply)
        window_s = ctx.t1 - ctx.t0
        # decomposition of the change in supplier assets (underlying units)
        net_start = local_start + claim_start + b0 - r0
        net_end = local_end + claim_end + b_end - r_end
        fees_net = losses = gains = 0
        for pt in ctx.timeline:
            if pt.block < first_block or pt.block > ctx.to:
                continue
            if pt.kind == "fees":
                d = pt.detail
                fees_net += (d["stockFees"] - d["stockReserved"]) if cfg["side"] == "stock" else (d["usdgFees"] - d["usdgReserved"])
            elif pt.kind == "checkpoint":
                d = pt.detail
                before = d["stockBefore"] if cfg["side"] == "stock" else d["usdgBefore"]
                after = pt.stock_principal if cfg["side"] == "stock" else pt.usdg_principal
                if after >= before:
                    gains += after - before
                else:
                    losses += after - before
        interest = b_end - b0
        interest_to_suppliers = interest - (r_end - r0)
        reserve_cut_market = r_end - r0
        residual = (net_end - net_start) - (fees_net + gains + losses + interest_to_suppliers)
        dec = 10 ** cfg["decimals"]
        pending_stock = ctx.snap["position"].get("pendingFeesStockRaw", 0)
        pending_usd = ctx.snap["position"].get("pendingFeesUsdgRaw", 0)
        pending = pending_stock if cfg["side"] == "stock" else pending_usd
        simple = rate_start and (current / rate_start - 1.0)
        mk = mark_to_end(ctx)
        claim_marked = mk["stockPrincipalMarked"] if cfg["side"] == "stock" else mk["usdgPrincipalMarked"]
        rate_marked = exchange_rate(local_end, claim_marked, b_end, r_end, supply)
        simple_marked = rate_start and (rate_marked / rate_start - 1.0)
        res["markedAtEndBlock"] = {
            "exchangeRateIfCheckpointedNow": rate_marked, "growthPct": 100 * simple_marked,
            "aprSimplePctIndicative": 100 * apr(simple_marked, window_s),
            "pendingShortfallUsd": mk["shortfallUsd"], "pendingShortfallPctOfBenchmark": mk["shortfallPctOfBenchmark"],
            "lastCheckpointAgeDays": mk["lastCheckpointAgeSeconds"] / 86400, "checkpointOverdue": mk["checkpointOverdue"],
            "note": "the booked exchange rate only moves at a checkpoint; this is where it would stand if the keeper checkpointed at the end block. "
                    "Annualised figures extrapolate a 24-day window with a one-way price move and are indicative only."}
        res.update({
            "status": "OK", "windowSeconds": window_s,
            "exchangeRateStart": rate_start, "exchangeRateEnd": current, "bookedOnly": True,
            "supplierAssetsStart": net_start / dec, "supplierAssetsEnd": net_end / dec,
            "growthPct": 100 * simple, "aprSimplePct": 100 * apr(simple, window_s), "apyPct": 100 * apy(simple, window_s),
            "timeWeightedReturnPct": 100 * twr([rate_start, current]),
            "moneyWeightedNote": "no supplier mints/redeems in the window, so the money-weighted return equals the time-weighted return",
            "supplierFlowsInWindow": 0,
            "decompositionUnderlying": {
                "lpFeesCreditedNetOfVaultReserveCut": fees_net / dec,
                "lpLossesBooked": losses / dec,
                "lpGainsRecognised": gains / dec,
                "borrowInterestToSuppliers": interest_to_suppliers / dec,
                "borrowInterestToMarketReserve": reserve_cut_market / dec,
                "unexplainedResidual": residual / dec,
            },
            "decompositionPctOfStart": {k: 100 * v / net_start for k, v in {
                "lpFees": fees_net, "lpLosses": losses, "lpGains": gains, "borrowInterest": interest_to_suppliers}.items()},
            "notYetInExchangeRate": {"uncollectedPoolFeesUnderlying": pending / dec},
            "reserveCutNote": "the vault keeps reserveFeeBps of collected fees for the loss reserve; the market keeps its reserve factor of borrow interest",
        })
        out[name] = res
    return out


# ------------------------------------------------------------------------------------ 4. portfolio


def equity_of(s: dict) -> tuple[float, float]:
    """(total stock tokens, total USD tokens) held by the pair in a sample."""
    stock = s["idle"][0] + s["lpIn"][0] + s["lpOut"][0]
    usd = s["idle"][1] + s["lpIn"][1] + s["lpOut"][1]
    return stock, usd


def delta_matched_pnl(samples: list[dict]) -> float:
    """P&L of a fee-free portfolio that holds, at every sample, the pair's NVDA quantity and rebalances at the oracle price
    (00 section 3). Each interval is earned on the quantity held at its start."""
    return sum(equity_of(a)[0] * (b["pOracle"] - a["pOracle"]) for a, b in zip(samples, samples[1:]))


def lvr_shortfall(benchmark_pnl: float, inventory_pnl_ex_fees: float) -> float:
    """Positive = the pair earned LESS than the delta-matched benchmark before fees (the LVR and rebalancing shortfall)."""
    return benchmark_pnl - inventory_pnl_ex_fees


def analyse_inventory(ctx: Ctx, samples: list[dict]) -> dict:
    if len(samples) < 2:
        raise EvidenceError("not enough samples for the portfolio ledger")
    first, last = samples[0], samples[-1]
    q0_s, q0_u = equity_of(first)
    q1_s, q1_u = equity_of(last)
    p0, p1 = first["pOracle"], last["pOracle"]
    e0, e1 = q0_s * p0 + q0_u, q1_s * p1 + q1_u
    # contributions / withdrawals inside the window, at the oracle price of their block
    flows = 0.0
    n_flows = 0
    for pt in ctx.timeline:
        if pt.kind in ("deposited", "withdrawal") and first["block"] <= pt.block <= last["block"]:
            price = ctx.oracle.price(ctx.ti.ts(pt.block))
            amt = pt.detail["amount"]
            val = amt / 1e18 * price if pt.detail["side"] == "stock" else amt / 1e6
            flows += val if pt.kind == "deposited" else -val
            n_flows += 1
    fees = actual_fees(ctx, first["block"], last["block"])  # only the interval the equity series covers
    net_fees = fees["grossCollectedUsd"] - fees["reserveCutUsd"]
    inventory_pnl = (e1 - e0) - flows - net_fees
    hold_pnl = q0_s * (p1 - p0)
    bench = delta_matched_pnl(samples)
    lvr = lvr_shortfall(bench, inventory_pnl)
    window_s = last["ts"] - first["ts"]
    ret = (e1 - e0 - flows) / e0 if e0 else None
    stale = sum(1 for s in samples if ctx.oracle.age(s["ts"]) > 12 * 3600)
    mk = markouts(ctx.v4_trades_window, (60, 3600), ctx.oracle)
    return {
        "ledger": {
            "startUtc": iso(int(first["ts"])), "endUtc": iso(int(last["ts"])), "startEquityUsd": e0, "endEquityUsd": e1,
            "startInventory": {"NVDA": q0_s, "USDG": q0_u}, "endInventory": {"NVDA": q1_s, "USDG": q1_u},
            "oracleStart": p0, "oracleEnd": p1, "netContributionsUsd": flows, "contributionEvents": n_flows,
            "feesCollectedGrossUsd": fees["grossCollectedUsd"], "reserveCutUsd": fees["reserveCutUsd"],
            "feesCollectedNetOfReserveUsd": net_fees, "uncollectedPoolFeesAtEndUsd": fees["pendingUncollectedAtEndUsd"],
            "uncollectedPoolFeesAtEndNetOfReserveUsd": fees["pendingUncollectedNetOfReserveUsd"],
            "uncollectedPoolFeesAtStart": "NOT_AVAILABLE: no historical state on the public RPC; the start figure is excluded, not zero",
            "interval": {"firstBlock": first["block"], "lastBlock": last["block"]},
            "inventoryPnlExFeesUsd": inventory_pnl,
            "dollarPnlUsd": (e1 - e0) - flows,
            "dollarPnlIncludingUncollectedNetFeesUsd": (e1 - e0) - flows + fees["pendingUncollectedNetOfReserveUsd"],
            "returnOnStartingEquityPct": None if ret is None else 100 * ret,
            "returnAprPct": None if ret is None else 100 * apr(ret, window_s),
            "returnStatedAs": ("simple (non-compounded) return on starting equity; no flows, so TWR = IRR" if n_flows == 0 else
                               "simple return on starting equity net of flows; with flows TWR and IRR differ and are NOT computed here"),
            "annualisationNote": "extrapolates a ~24-day window with a one-way price move; indicative only",
        },
        "versusHolding": {"holdStartInventoryPnlUsd": hold_pnl, "vaultMinusHoldUsd": (e1 - e0 - flows) - hold_pnl,
                          "explanation": "hold = keep the starting NVDA and USDG; the difference is fees minus reserve cut plus inventory effects"},
        "deltaMatchedBenchmark": {"benchmarkPnlUsd": bench, "lvrAndRebalancingShortfallUsd": lvr,
                                  "definition": "benchmark holds, at every sample, the same NVDA quantity as the pair and rebalances at the oracle price without fees. "
                                                "The shortfall is a decomposition of inventory P&L, not an extra cost to subtract again.",
                                  "oracleStaleSamples": stale, "samples": len(samples),
                                  "caveat": "while the feed is stale the benchmark price is frozen, so session gaps appear when the feed resumes"},
        "markouts": mk,
    }
