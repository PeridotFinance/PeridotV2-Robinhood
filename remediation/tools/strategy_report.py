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


def checks_block(L: list, checks: list):
    for c in checks:
        L.append(f"- {'OK  ' if c['ok'] else 'FAIL'} {c['name']}: rebuilt {c['reconstructed']:,} vs chain {c['onChain']:,}")


def render(r: dict) -> str:
    L: list[str] = []
    w = r["window"]
    L += ["# Strategy evidence: Uniswap v4 NVDA/USDG vault and Ramses NVDA pool",
          f"Window {w['start']} to {w['end']} ({w['days']:.1f} days), blocks {w['fromBlock']:,} to {w['toBlock']:,}. Read only. "
          f"History rebuilt from logs; the parts listed in section 0 are checked against chain state at block {w['toBlock']:,}.", ""]
    if r.get("failedSections"):
        L += [f"**FAILED sections: {', '.join(r['failedSections'])}.** Numbers that depend on them are absent, not zero.", ""]
    v = r.get("validation", {})
    L += ["## 0. What was checked against the chain",
          f"**{'Every check below matches.' if v.get('ok') else 'AT LEAST ONE CHECK DIFFERS; treat the dependent analyses as unverified.'}**"]
    if v.get("scope"):
        L.append(f"_Scope: {v['scope']}_")
    L.append("")
    checks_block(L, v.get("checks", []))
    rv_ = r.get("ramsesFeesAndVolume", {})
    if isinstance(rv_, dict) and rv_.get("validation"):
        L.append(f"- Ramses pool checks ({'all match' if rv_['validation']['ok'] else 'DIFFER'}):")
        checks_block(L, rv_["validation"]["checks"])
    te = r.get("timeIndexError", {})
    if te and not failed(te):
        L += ["", f"Timestamps are interpolated between real headers; measured error median {te['median_s']:.1f}s, 95th percentile {te['p95_s']:.1f}s, max {te['max_s']:.1f}s."]

    sr = r.get("supplierReturns", {})
    overdue = [n for n, m in sr.items() if isinstance(m, dict) and m.get("markedAtEndBlock", {}).get("checkpointOverdue")]
    if overdue:
        mk = next(m["markedAtEndBlock"] for m in sr.values() if isinstance(m, dict) and "markedAtEndBlock" in m)
        L += ["", f"> **The vault checkpoint is {mk['lastCheckpointAgeDays']:.1f} days old (limit 1 hour).** Booked exchange rates ignore a pending "
                  f"shortfall of {usd(mk['pendingShortfallUsd'], 3)} ({mk['pendingShortfallPctOfBenchmark']:.2f}% of the pair) that the next checkpoint will book against suppliers. "
                  "Section 1 shows both the booked rate and the rate marked to the end block."]

    L += ["", "## Headline"]
    cap = r.get("profitableCapacity", {})
    for venue, pols in cap.items():
        if not isinstance(pols, dict):
            continue
        for pname, p in pols.items():
            if isinstance(p, dict) and p.get("capacitySentence"):
                L.append(f"- {p['capacitySentence']}")
                if p.get("liveSplit"):
                    L.append(f"- {p['liveSplit']['capacitySentence']}")

    L += ["", "## 1. Supplier returns (exchange rate); no supplier deposits or redemptions in the window"]
    for name, m in sr.items():
        if not isinstance(m, dict):
            continue
        if m.get("status") != "OK":
            L.append(f"- {name}: {m.get('status')} {m.get('reason', '')}")
            continue
        d, mk = m["decompositionUnderlying"], m["markedAtEndBlock"]
        L += [f"- **{name}**: booked exchange rate {m['growthPct']:+.4f}% over the window; **marked to the end block {mk['growthPct']:+.4f}%** "
              f"(indicative annual rate {mk['aprSimplePctIndicative']:+.1f}%, extrapolating a 24-day window with a one-way price move). "
              f"Time-weighted and money-weighted returns are equal because there were no supplier flows.",
              f"  - Booked components (token units): LP fees credited after the vault's reserve cut {d['lpFeesCreditedNetOfVaultReserveCut']:.8f}; losses booked {d['lpLossesBooked']:.8f}; "
              f"gains recognised {d['lpGainsRecognised']:.8f}; borrow interest to suppliers {d['borrowInterestToSuppliers']:.8f}; market reserve cut {d['borrowInterestToMarketReserve']:.8f}.",
              f"  - The residual {d['unexplainedResidual']:.8f} is zero by construction (it is the same ledger); the real checks are the end-state matches in section 0.",
              f"  - Uncollected pool fees at the end, not yet in the exchange rate (gross of the reserve cut): {m['notYetInExchangeRate']['uncollectedPoolFeesUnderlying']:.8f}.",
              f"  - Formula check: rebuilt stored rate {'matches' if m['validation']['exchangeRateStored']['ok'] else 'DIFFERS FROM'} the chain; current-rate relative error {m['validation']['exchangeRateCurrent']['relativeError']:.2e}."]

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
        s = next(x for x in (sec.get("summary"), sec.get("pool")) if isinstance(x, dict))
        L.append(f"- **{label}**: {s['swaps']:,} swaps, volume {usd(s['volumeUsd'], 0)}, fees to LPs {usd(s['lpFeesUsd'])} ({s['lpFeeBpsOfVolume']:.1f} bps of volume), "
                 f"median trade {usd(s['medianTradeUsd'])}, depth to move price 1% about {usd(s['medianDepthToMove1pctUp'], 0)} up / {usd(s['medianDepthToMove1pctDown'], 0)} down.")
        if "swapperPaidFeesUsd" in s and label.startswith("Uniswap"):
            L.append(f"  - Swappers paid {usd(s['swapperPaidFeesUsd'])} in total; {usd(s['protocolTakeUsd'])} of that ({100 * s['protocolTakeUsd'] / s['swapperPaidFeesUsd']:.0f}%) is the pool's protocol fee, not LP income.")
    if fv and not failed(fv):
        a, e = fv["ourActual"], fv["ourEstimate"]["estimatedLpFeesUsd"]
        L.append(f"- Our actual fees: collected {usd(a['grossCollectedUsd'], 4)} gross (reserve cut {usd(a['reserveCutUsd'], 4)}), uncollected at the end {usd(a['pendingUncollectedAtEndUsd'], 4)}; "
                 f"replay estimate {usd(e, 4)} (estimate/actual {fv['estimateVsActualRatio']:.2f}).")
    if rv and not failed(rv):
        fee = rv["feePpm"]
        L.append(f"- Ramses fee moved between {fee['min']} and {fee['max']} ppm (median {fee['median']}); {fee['adjustmentsInWindow']} adjustments in the window. Protocol share {rv['protocolShareAssumed']:.0%}, constant over the window.")

    pf = r.get("portfolioLedger", {})
    L += ["", "## 4. Portfolio ledger, benchmarks and markouts"]
    if failed(pf):
        L.append(f"FAILED: {pf['reason']}")
    else:
        g = pf["ledger"]
        L += [f"- Uniswap vault pair, equity {usd(g['startEquityUsd'])} to {usd(g['endEquityUsd'])}; dollar P&L {usd(g['dollarPnlUsd'], 4)} ({g['returnOnStartingEquityPct']:+.3f}% over the window; {g['returnAprPct']:+.1f}% a year is an extrapolation of a 24-day window with a one-way price move, indicative only). "
              f"Uncollected pool fees at the end add about {usd(g['uncollectedPoolFeesAtEndNetOfReserveUsd'], 4)} net of the reserve cut; the start-of-window uncollected amount is not available.",
              f"- Fees collected {usd(g['feesCollectedGrossUsd'], 4)}, reserve cut {usd(g['reserveCutUsd'], 4)}; inventory result excluding fees {usd(g['inventoryPnlExFeesUsd'], 4)}.",
              f"- Versus holding the starting NVDA/USDG: vault minus hold {usd(pf['versusHolding']['vaultMinusHoldUsd'], 4)}.",
              f"- Versus the delta-matched rebalancing benchmark: shortfall (LVR and rebalancing, before fees) {usd(pf['deltaMatchedBenchmark']['lvrAndRebalancingShortfallUsd'], 4)}. "
              f"This decomposes the inventory result; it is not an extra cost.",
              "- Markouts are pool-wide: every swap in the pool, measured from the pool's mid price right after the trade; the fee figure is the pool's total LP fees, not the vault's income."]
        for h, m in pf["markouts"].items():
            t = m["total"]
            L.append(f"  - +{int(h) // 60} min over {t['trades']:,} swaps ({usd(t['notional'], 0)}): pool-price {usd(t['pool_markout_usd'], 2)} ({1e4 * t['pool_markout_usd'] / t['notional']:+.1f} bps), "
                     f"oracle-move {usd(t['oracle_markout_usd'], 2)}, pool LP fees {usd(t['fee_usd'], 2)} ({1e4 * t['fee_usd'] / t['notional']:.1f} bps).")
            for sess, s in sorted(m["bySession"].items()):
                if s["notional"]:
                    L.append(f"    - {sess}: markout {s['pool_markout_usd'] / s['notional'] * 1e4:+.1f} bps vs fees {s['fee_usd'] / s['notional'] * 1e4:.1f} bps (notional {usd(s['notional'], 0)})")
    if rv and not failed(rv) and rv.get("markouts"):
        for h, m in rv["markouts"].items():
            t = m["total"]
            L.append(f"- Ramses pool, +{int(h) // 60} min over {t['trades']:,} swaps ({usd(t['notional'], 0)}): markout {1e4 * t['pool_markout_usd'] / t['notional']:+.1f} bps vs LP fees {1e4 * t['fee_usd'] / t['notional']:.1f} bps.")

    kc = r.get("keeperCosts", {})
    L += ["", "## 5. Keeper costs"]
    if failed(kc):
        L.append(f"FAILED: {kc['reason']}")
    else:
        for act, a in sorted(kc["byAction"].items()):
            L.append(f"- {act}: {a['count']} sent, median gas {a['medianGas']:,.0f}, cost {a['ethCost']:.6f} ETH ({usd(a['usdCost'])})")
        wal, fe = kc["walletAtEnd"], kc["failedOrEventlessTransactions"]
        L += [f"- Total {kc['totalEth']:.6f} ETH ({usd(kc['totalUsd'])}) across both keeper wallets. The cloud keeper wallet holds {wal['balanceEth']:.6f} ETH against a {wal['gasFloorEth']:.4f} ETH floor "
              f"({'BELOW the floor, so the worker refuses to act' if wal['belowFloor'] else 'above the floor'}). Last on-chain keeper action: {kc['lastKeeperActionWithEvents']} "
              f"({kc['daysSinceLastAction']:.1f} days before the window end).",
              f"- Cloud keeper lifetime nonce {fe['cloudKeeperLifetimeNonce']} vs {fe['cloudKeeperTransactionsWithVaultEvents']} transactions with vault events in the window: "
              f"{fe['cloudKeeperTransactionsWithoutEvents']} sends without a vault event (reverted, unrelated, or before the window). Their gas cannot be itemised without an explorer.",
              f"- Waiting and simulated-revert actions: {kc['waitingActions']['status']} ({kc['waitingActions']['reason']})"]

    lp = r.get("liquidityPolicy", {})
    L += ["", "## 6. Liquidity policy evidence (00 section 6a), per token at the end block"]
    if failed(lp):
        L.append(f"FAILED: {lp['reason']}")
    else:
        L.append(f"_{lp['settlementSwapsNote']}. {lp['reserveNote']}._")
        for tok, t in lp["tokens"].items():
            red, bor = t["redemption"], t["borrowing"]
            sim = red["simulatedFullClaimExit"]
            ret = "REVERTS" if sim["reverted"] else format(sim["returned"], ".8f")
            loss = "n/a" if sim["reverted"] else format(sim["realizedLossOnExit"], ".8f")
            L += [f"- **{tok}**: local cash {t['localCash']:.8f}, strategy value {t['strategyAccountedValue']:.8f} (idle in vault {t['strategyIdleInVault']:.8f}, in the LP {t['strategyLpTokenAmount']:.8f}), "
                  f"loans {t['outstandingBorrows']:.8f}; shares cash/strategy/loans {t['shares']['localCash']:.0%}/{t['shares']['strategy']:.0%}/{t['shares']['loans']:.0%}.",
                  f"  - Redemption: local cash {red['localCashOnly']:.8f}. Simulated exit of the full strategy claim ({sim['requestedClaim']:.8f}) returns {ret} and realises a loss of {loss} "
                  f"(observed contract behaviour). Immediate capacity {red['immediateCapacityNormal']:.8f} normally, {red['immediateCapacityIfStrategyExitFails']:.8f} if the strategy exit fails.",
                  f"  - Borrowing: capacity {bor['capacityNormal']:.8f} normally ({bor['getCash']:.8f} cash, cap headroom {bor['capHeadroom']:.8f}), {bor['capacityStressedOracleInvalid']:.8f} if the oracle path is invalid."]
        s = lp["scenario_50_40_10"]
        L.append(f"- Scenario only (not live): 100 = 50 strategy + 40 loans + 10 cash, redeem 25 -> {s['redeem25_of_100']['status']} "
                 f"(cash {s['redeem25_of_100'].get('fromCash')}, strategy {s['redeem25_of_100'].get('fromStrategy')}); if the strategy can return only 12 -> {s['redeem25_strategy_returns_only_12']['status']}.")

    dl = r.get("uniswapDilutionRealisedRanges", {})
    L += ["", "## 7. Dilution of fees as our liquidity grows (realised v4 ranges)"]
    if failed(dl):
        L.append(f"FAILED: {dl['reason']}")
    else:
        for row in dl["rows"]:
            L.append(f"- ${row['sizeUsd']:,}: LP fees {usd(row['lpFeesUsd'])} over the window = {pct(row['lpFeesPerDollarPerYearPct'])} a year on the average pair value including idle capital")

    L += ["", "## 8. Policy replay and capacity (simplified single-range replay, fixed trade stream)"]
    for venue, pols in cap.items():
        if not isinstance(pols, dict):
            continue
        for pname, p in pols.items():
            if not isinstance(p, dict) or "rows" not in p:
                continue
            variants = [(f"{p['startSplit']} start", p["rows"])]
            if p.get("liveSplit"):
                variants.append((f"live supply split ({p['liveSplit']['stockValueFraction']:.0%} NVDA) start", p["liveSplit"]["rows"]))
            for title, rows in variants:
                L.append(f"- **{venue} / {pname}, {title}**")
                for row in rows:
                    L.append(f"  - ${row['size_usd']:,}: fees {usd(row['fees_gross_usd'], 2)} gross, recenters {row['recenters']}, in range {row['time_in_range']:.0%}, "
                             f"deployed {row['avg_deployed_fraction']:.0%}, net vs holding after ops {pct(row['net_after_ops_apr_pct'])} a year (operating cost alone {pct(row['opsOnlyAprPct'])})")
    L += ["", "## Notes on method and limits"]
    for n in r.get("operatingCostAssumptions", []):
        L.append(f"- {n}")
    L += ["- Reverted keeper transactions that left no vault event cannot be itemised (no explorer); waits and simulated reverts live only in the keeper's own log.",
          "- RAM rewards are valued at zero: suppliers have no enforceable claim to them today.",
          "- Capacity sentences use a simplified replay with a fixed trade stream (our size does not change volume), not the contract's exact code paths (Phase 2). They are net of holding the same deposit, in one window with a one-way price move.",
          f"- RPC calls {r.get('rpcCalls')}, cache hits {r.get('cacheHits')}."]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    sys.stdout.write(render(json.load(open(sys.argv[1]))))
