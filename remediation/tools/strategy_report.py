#!/usr/bin/env python3
"""Render a strategy-analysis JSON (from strategy_analyze.py) as a plain-language markdown report.

    python3 remediation/tools/strategy_report.py remediation/evidence/strategy-analysis/RUN.json > RUN.md
"""
from __future__ import annotations

import json
import sys


def usd(x, nd=2):
    return "n/a" if x is None else f"${x:,.{nd}f}"


def pct(x, nd=2):
    return "n/a" if x is None else f"{x:,.{nd}f}%"


def failed(sec) -> bool:
    return isinstance(sec, dict) and sec.get("status") == "FAILED"


def render(r: dict) -> str:
    L: list[str] = []
    w = r["window"]
    L += [f"# Strategy evidence: Uniswap v4 NVDA/USDG vault and Ramses NVDA pool",
          f"Window {w['start']} to {w['end']} ({w['days']:.1f} days), blocks {w['fromBlock']:,} to {w['toBlock']:,}. Read only. "
          f"History rebuilt from logs and checked against chain state at block {w['toBlock']:,}.", ""]
    if r.get("failedSections"):
        L += [f"**FAILED sections: {', '.join(r['failedSections'])}.** Numbers below that depend on them are absent, not zero.", ""]
    v = r.get("validation", {})
    L += ["## 0. Does the rebuilt history match the chain?",
          f"**{'Yes, every check matches.' if v.get('ok') else 'NO. At least one check differs; treat the analyses as unverified.'}**", ""]
    for c in v.get("checks", []):
        L.append(f"- {'OK  ' if c['ok'] else 'FAIL'} {c['name']}: rebuilt {c['reconstructed']:,} vs chain {c['onChain']:,}")
    te = r.get("timeIndexError", {})
    if te and not failed(te):
        L += ["", f"Timestamps are interpolated between real headers; measured error median {te['median_s']:.1f}s, 95th percentile {te['p95_s']:.1f}s, max {te['max_s']:.1f}s."]

    L += ["", "## Headline"]
    cap = r.get("profitableCapacity", {})
    for venue, pols in cap.items():
        if not isinstance(pols, dict):
            continue
        for pname, p in pols.items():
            if isinstance(p, dict) and p.get("capacitySentence"):
                L.append(f"- {p['capacitySentence']}")

    sr = r.get("supplierReturns", {})
    L += ["", "## 1. Supplier returns (exchange rate), no deposits or redemptions in the window"]
    for name, m in sr.items():
        if not isinstance(m, dict):
            continue
        if m.get("status") != "OK":
            L.append(f"- {name}: {m.get('status')} {m.get('reason', '')}")
            continue
        d = m["decompositionUnderlying"]
        L += [f"- **{name}**: exchange rate {m['growthPct']:+.4f}% over the window (simple annual rate {m['aprSimplePct']:+.3f}%, compounded {m['apyPct']:+.3f}%). "
              f"Time-weighted and money-weighted returns are equal because there were no supplier flows.",
              f"  - LP fees credited (after the vault's reserve cut) {d['lpFeesCreditedNetOfVaultReserveCut']:.8f}; losses booked {d['lpLossesBooked']:.8f}; "
              f"gains recognised {d['lpGainsRecognised']:.8f}; borrow interest to suppliers {d['borrowInterestToSuppliers']:.8f}; "
              f"market reserve cut {d['borrowInterestToMarketReserve']:.8f}; unexplained residual {d['unexplainedResidual']:.8f} (token units).",
              f"  - Uncollected pool fees at the end, not yet in the exchange rate: {m['notYetInExchangeRate']['uncollectedPoolFeesUnderlying']:.8f}.",
              f"  - Formula check: rebuilt stored rate {'matches' if m['validation']['exchangeRateStored']['ok'] else 'DIFFERS FROM'} the chain; "
              f"current-rate relative error {m['validation']['exchangeRateCurrent']['relativeError']:.2e}."]

    cp = r.get("capitalPlacement", {})
    L += ["", "## 2. Where the capital sat (time-weighted, valued at the oracle)"]
    if failed(cp):
        L.append(f"FAILED: {cp['reason']}")
    else:
        sh = cp["strategyCapitalShare"]
        L += [f"- Strategy capital: in range {pct(100 * sh['lp_in_range'])}, out of range {pct(100 * sh['lp_out_of_range'])}, idle in the vault {pct(100 * sh['vault_idle'])}. "
              f"Some liquidity was in range {pct(100 * cp['timeWithAnyLiquidityInRange'])} of the time."]
        for tok, t in cp["perToken"].items():
            a = t["average"]
            L.append(f"- {tok}: in range {a['lp_in_range']:.6f}, out of range {a['lp_out_of_range']:.6f}, vault idle {a['vault_idle']:.6f}, market local cash {a['market_local_cash']:.6f} "
                     f"(in-range share of the strategy {pct(None if t['inRangeShareOfStrategy'] is None else 100 * t['inRangeShareOfStrategy'])}).")

    fv = r.get("uniswapFeesAndVolume", {})
    rv = r.get("ramsesFeesAndVolume", {})
    L += ["", "## 3. Fees and volume (same window)"]
    for label, sec in (("Uniswap v4 0.30%", fv), ("Ramses CL (dynamic fee)", rv)):
        if failed(sec) or not sec:
            L.append(f"- {label}: FAILED {sec.get('reason', '') if isinstance(sec, dict) else ''}")
            continue
        s = next(v for v in (sec.get("summary"), sec.get("pool")) if isinstance(v, dict))
        L.append(f"- **{label}**: {s['swaps']:,} swaps, volume {usd(s['volumeUsd'], 0)}, LP fees {usd(s['lpFeesUsd'])} ({s['lpFeeBpsOfVolume']:.1f} bps of volume), "
                 f"median trade {usd(s['medianTradeUsd'])}, depth to move price 1% about {usd(s['medianDepthToMove1pctUp'], 0)} up / {usd(s['medianDepthToMove1pctDown'], 0)} down.")
    if fv and not failed(fv):
        a, e = fv["ourActual"], fv["ourEstimate"]["estimatedLpFeesUsd"]
        L.append(f"- Our actual fees: collected {usd(a['grossCollectedUsd'], 4)} gross (reserve cut {usd(a['reserveCutUsd'], 4)}), uncollected at the end {usd(a['pendingUncollectedAtEndUsd'], 4)}; "
                 f"replay estimate {usd(e, 4)} (estimate/actual {fv['estimateVsActualRatio']:.2f}).")
    if rv and not failed(rv):
        fee = rv["feePpm"]
        L.append(f"- Ramses fee moved between {fee['min']} and {fee['max']} ppm (median {fee['median']}); {fee['adjustmentsInWindow']} adjustments in the window. Protocol share {rv['protocolShareAssumed']:.0%}.")

    pf = r.get("portfolioLedger", {})
    L += ["", "## 4. Portfolio ledger, benchmarks and markouts (Uniswap vault pair)"]
    if failed(pf):
        L.append(f"FAILED: {pf['reason']}")
    else:
        g = pf["ledger"]
        L += [f"- Equity {usd(g['startEquityUsd'])} to {usd(g['endEquityUsd'])}; dollar P&L {usd(g['dollarPnlUsd'], 4)} ({g['returnOnStartingEquityPct']:+.3f}% simple, {g['returnAprPct']:+.2f}% a year); "
              f"plus {usd(g['uncollectedPoolFeesAtEndUsd'], 4)} of fees still uncollected.",
              f"- Fees collected {usd(g['feesCollectedGrossUsd'], 4)}, reserve cut {usd(g['reserveCutUsd'], 4)}; inventory result excluding fees {usd(g['inventoryPnlExFeesUsd'], 4)}.",
              f"- Versus holding the starting NVDA/USDG: vault minus hold {usd(pf['versusHolding']['vaultMinusHoldUsd'], 4)}.",
              f"- Versus the delta-matched rebalancing benchmark: shortfall (LVR and rebalancing, before fees) {usd(pf['deltaMatchedBenchmark']['lvrAndRebalancingShortfallUsd'], 4)}. "
              f"This decomposes the inventory result; it is not an extra cost."]
        for h, m in pf["markouts"].items():
            t = m["total"]
            L.append(f"- Markout +{int(h) // 60} min over {t['trades']:,} swaps ({usd(t['notional'], 0)}): pool-price {usd(t['pool_markout_usd'], 2)}, oracle-price {usd(t['oracle_markout_usd'], 2)}, fees earned {usd(t['fee_usd'], 2)}.")
            for sess, s in sorted(m["bySession"].items()):
                if s["notional"]:
                    L.append(f"  - {sess}: notional {usd(s['notional'], 0)}, pool markout {usd(s['pool_markout_usd'], 2)} ({s['pool_markout_usd'] / s['notional'] * 1e4:+.1f} bps), fees {usd(s['fee_usd'], 2)}")

    kc = r.get("keeperCosts", {})
    L += ["", "## 5. Keeper costs"]
    if failed(kc):
        L.append(f"FAILED: {kc['reason']}")
    else:
        for act, a in sorted(kc["byAction"].items()):
            L.append(f"- {act}: {a['count']} sent, median gas {a['medianGas']:,.0f}, cost {a['ethCost']:.6f} ETH ({usd(a['usdCost'])}), reverted {a['reverted']}")
        wal = kc["walletAtEnd"]
        L += [f"- Total {kc['totalEth']:.6f} ETH ({usd(kc['totalUsd'])}). Wallet at the end holds {wal['balanceEth']:.6f} ETH against a {wal['gasFloorEth']:.4f} ETH floor "
              f"({'BELOW the floor, so the worker refuses to act' if wal['belowFloor'] else 'above the floor'}). Last on-chain keeper action: {kc['lastKeeperActionWithEvents']} "
              f"({kc['daysSinceLastAction']:.1f} days before the window end).",
              f"- Waiting and simulated-revert actions: {kc['waitingActions']['status']} ({kc['waitingActions']['reason']})"]

    lp = r.get("liquidityPolicy", {})
    L += ["", "## 6. Liquidity policy evidence (00 section 6a), per token at the end block"]
    if failed(lp):
        L.append(f"FAILED: {lp['reason']}")
    else:
        L.append(f"_{lp['settlementSwapsNote']}. {lp['reserveNote']}._")
        for tok, t in lp["tokens"].items():
            L += [f"- **{tok}**: local cash {t['localCash']:.8f}, strategy value {t['strategyAccountedValue']:.8f} (idle in vault {t['strategyIdleInVault']:.8f}, in the LP {t['strategyLpTokenAmount']:.8f}), "
                  f"loans {t['outstandingBorrows']:.8f}; shares cash/strategy/loans {t['shares']['localCash']:.0%}/{t['shares']['strategy']:.0%}/{t['shares']['loans']:.0%}.",
                  f"  - Immediate redemption capacity: {t['immediateRedemptionCapacity']['normal']:.8f} normally, {t['immediateRedemptionCapacity']['stressedStrategyUnavailable']:.8f} if the strategy exit fails."]
        s = lp["scenario_50_40_10"]
        L.append(f"- Scenario only (not live): 100 = 50 strategy + 40 loans + 10 cash, redeem 25 -> {s['redeem25_of_100']['status']} "
                 f"(cash {s['redeem25_of_100'].get('fromCash')}, strategy {s['redeem25_of_100'].get('fromStrategy')}); if the strategy can return only 12 -> {s['redeem25_strategy_returns_only_12']['status']}.")

    dl = r.get("uniswapDilutionRealisedRanges", {})
    L += ["", "## 7. Dilution of fees as our liquidity grows (realised v4 ranges)"]
    if failed(dl):
        L.append(f"FAILED: {dl['reason']}")
    else:
        for row in dl["rows"]:
            L.append(f"- ${row['sizeUsd']:,}: LP fees {usd(row['lpFeesUsd'])} over the window = {pct(row['lpFeesPerDollarPerYearPct'])} a year on the dollars deployed")

    L += ["", "## 8. Policy replay and capacity (simplified single-range replay)"]
    for venue, pols in cap.items():
        if not isinstance(pols, dict):
            continue
        for pname, p in pols.items():
            if not isinstance(p, dict) or "rows" not in p:
                continue
            L.append(f"- **{venue} / {pname}**")
            for row in p["rows"]:
                L.append(f"  - ${row['size_usd']:,}: fees {usd(row['fees_gross_usd'], 2)} gross, recenters {row['recenters']}, in range {row['time_in_range']:.0%}, "
                         f"deployed {row['avg_deployed_fraction']:.0%}, net vs holding after ops {pct(row['net_after_ops_apr_pct'])} a year")
    L += ["", "## Notes on method and limits"]
    for n in r.get("operatingCostAssumptions", []):
        L.append(f"- {n}")
    L += ["- Reverted keeper transactions that left no vault event cannot be itemised (no explorer); waits and simulated reverts live only in the keeper's own log.",
          "- RAM rewards are valued at zero: suppliers have no enforceable claim to them today.",
          "- Capacity sentences use a simplified replay, not the contract's exact code paths (Phase 2).",
          f"- RPC calls {r.get('rpcCalls')}, cache hits {r.get('cacheHits')}."]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    sys.stdout.write(render(json.load(open(sys.argv[1]))))
