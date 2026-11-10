# Hedged LP, one window (one result, not a strategy test): the live NVDA/USDG range position with its NVDA delta shorted in the margin market

Read-only backtest, 2026-11-06. Script `hedged_lp.py`, output `hedged-lp.json`. The method is the handoff 00 section 3
portfolio ledger: one ledger in USD, marked at the RHNVDA oracle, with no outside flows. The returns are time-weighted
and money-weighted alike, because there are no flows. **This is one parameter set, not an optimization.**

## Setup

- **Window:** 2026-10-19 14:39 to 2026-11-06 08:44 UTC, 17.75 days and 277 checkpoints. This is the live pair since the
  2400/600 policy, including the 10.8-day keeper outage. NVDA went from $218.26 to $186.53 (−14.5%).
- **LP leg:** the live pair exactly as it ran: the pair value at each checkpoint, with fees in ($0.189 earned).
- **Hedge leg:** borrow NVDA in the isolated margin market, sell it, and hold USDG collateral. Borrowed NVDA = the LP's NVDA
  holding (its delta).
  - It is rebalanced at a checkpoint when the drift exceeds 5% of the LP's value, and opened and closed at the ends:
    10 adjustments in all.
  - Margin posted: $3.92, half the short's notional. That is 3x by the contract's measure (position value / equity);
    the pair allows 5x.
- **Costs:**
  - **Funding:** NVDA borrow at 2.006% APR, paid in NVDA; USDG collateral earns 0.182% APR. Both rates were read from
    pNVDA and pUSDG at the head and held constant.
  - **Execution:** margin open and close fees are 0 (config); 0.30% swap cost on each adjustment's notional (the NVDA/USDG
    v4 pool fee; price impact ignored); $0.087 of gas per adjustment (2.23M gas, the measured margin close, at the live
    keepers' gas price, ETH at $2,260).

## Result (start equity $23.28: the LP $19.36 plus margin $3.92)

| | end | dollar P&L | return |
| --- | --- | --- | --- |
| **Hedged LP** | $22.32 | **−$0.97** | **−4.16%** |
| Unhedged LP + idle margin cash | $21.61 | −$1.67 | −7.19% |
| Hold the deposited tokens + the same cash | $22.14 | −$1.14 | −4.89% |

**Hedged versus hold: +0.77%.** The hedged LP is ahead only because NVDA fell. Against a pure-USDG holding, which stays at
about $23.28, it is 4.2% behind.

Hedge leg: +$0.706 in total. That breaks down as follows (it explains the ledger result; it does not replace it):

| price and rebalancing | swap cost | gas | NVDA borrow interest | USDG supply interest |
| --- | --- | --- | --- | --- |
| +$1.680 | −$0.095 | **−$0.870** | −$0.009 | +$0.001 |

## What it says

1. **Before execution costs, the hedged LP was flat:** +$0.005 over 17.75 days. Once the NVDA direction is shorted away,
   the $0.19 of fees about offset the position's loss to arbitrage on this path. Hedging does not make this range
   profitable; it removes the direction.
2. **At today's size, costs decide the result.**
   - Gas is 90% of the cost, and fixed per transaction: $0.87 is 4.5% of a $19 LP. At the $1,000 pair cap the same 10
     adjustments would cost 0.09%.
   - Swap cost scales with size: about 0.49% of the LP's value over the window.
   - Extrapolating (arithmetic, not a run): at $1,000 the hedged result would be about −0.55% over 17.75 days, or about
     −11% a year, before any change to the rebalance rule.
3. **Funding is minor at these rates:** 2% NVDA borrow on about $8 cost $0.009. It matters only if NVDA borrow demand rises.
4. **It cannot be run live today.** The margin market's per-position caps are $2 gross and $1 debt; this hedge needs about
   $8 of NVDA debt. The hedge (margin = half the notional) would be liquidated only by a rise of about a third, which this window did
   not test. `CAPS_FOR_1000.md` lists the exact calls and the liquidity a $1,000 version would need.

## Not modelled

- Price impact.
- The intra-checkpoint path. Gaps are seen only at checkpoints, including the 10.8-day outage, which the hedge also could
  not trade through.
- A changing borrow rate.
- Liquidation.
- Rewards (Uniswap pays none).
- Keeper outages beyond the observed one.
- The decision rule. The band and leverage are the only knobs, and they were not tuned.
