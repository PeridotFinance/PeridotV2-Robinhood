# Ramses group Phase 0: research brief and evidence index

Research and measurement only (handoff 01 Phase 0). No production contract, deployment script or configuration is changed by anything in this directory, and no phase is advanced. Everything here was produced read-only from the public RPC; nothing was signed or sent. This README is the only Markdown published for this work, so it carries the brief, the corrections and the index of evidence files. Neither copy of handoff 00 is edited; where handoff 01 is out of date this README says so instead of changing it.

## 1. Corrections to earlier statements (read first)

1. **"A plain +/-5% single range does about as well with none [no contract change]" was wrong.** The backtest's `single_pm5` is a 500-tick half-width. `RamsesRangeLib.validatePolicy` requires `600 <= halfWidthTicks <= 4800`, so that configuration cannot be deployed. None of the 872 historical rows that use a recenter policy passes the contract's rules (section 5). The nearest deployable single range is 600 ticks (about +/-6.2%).
2. **"GLD's Chainlink feed ... is not an independent price" was not established.** Chainlink's reference-data directory labels the GLD feed `attributeType: dex_state_price`, and its node observations disagree far more than SPY's. Neither fact identifies which pools or weights the feed uses, and no causal test was run (section 7).
3. **"GLD supports $0" is withdrawn as worded.** Scoped statement: in the main grid on the GLD pool, all 90 full-window cells and 58 of 60 last-28-day cells had negative excess return against holding (two hypothetical dual-range-with-swap cells were slightly positive in the last 28 days, and the sub-window cuts have non-negative cells in the middle third), and almost all of those configurations are hypothetical. That is not a derived lending, margin or liquidation cap.
4. **SPY's "0.015%" fee quote is a real fee state, not an error and not the current fee.** `fee()` on the SPY/USDG CL1 pool is dynamic: 100 ppm at the pin, but 150 ppm for 16.2% of the last 30 days (last in force 2026-10-30 20:03Z). The gateway's `hasDynamicFees: false` is wrong for these pools (section 4).
5. All historical numbers are preserved unchanged in `historical-hypothetical/` and labelled hypothetical.

## 2. Partner requirements, reconciled

The operator's link to the partner's page (`chatgpt.com/space/page_e3a36d85...`) returns HTTP 403 to this tooling, so the page itself could not be read. The requirements below are the operator's summary of that feedback; the pinned numbers are mine.

| Requirement (as relayed) | Status in this brief |
| --- | --- |
| Select existing Ramses pools by depth and total execution cost at our intended size, not by defaulting to CL1 | Done as measurement, section 3. At the intended sizes ($50 ranged, $1,000 pair) SPY CL1 is the cheapest candidate (2.0 bp at $1,000), then GLD CL1 (8.0), NVDA CL10 (14.8), META CL50 (35.8); r33/USDG CL200 costs 430 bp to buy $1,000 and cannot be sold into beyond about $50. CL1 is therefore cheapest here by measurement, not by default. Handoff 01 section 0 ("use CL1 pools") is superseded by this selection rule; nothing is chosen on the operator's behalf |
| Candidates: NVDA/USDG CL10, META/USDG CL50, SPY/USDG CL1 at 0.015% | All four pools exist and were read at pinned blocks (section 3). SPY CL1's `fee()` is 100 ppm at the pin and 150 ppm for 16% of the last 30 days, so the quoted 0.015% is a state the pool is sometimes in |
| For r33/USDG evaluate CL200 first, including its quoted 2% fee | `r33/USDG CL200` is `0xc276b9a27ab1e60e32b581db6a23bb1fab0fc30b`: tickSpacing 200, `fee()` 20,000 ppm (2%), static (no `FeeAdjustment` event ever), pool protocol share 5%. On-chain liquidity and tick match the gateway. About 98% of its $43.9k TVL is r33 sitting out of range (gateway: 0.87% of TVL active); the in-range depth is $6 for a 1% move either way and $31 for a 5% fall. 24 h volume $28. See section 3 |
| Direct r33 collateral should preserve r33 exposure; LP participation separately optional | Section 9. The baseline here shows why they must be separate: matched-only recentering converts inventory (NVDA ended about 94% idle USDG), so an LP route does not preserve the starting token exposure |
| Robinhood pricing and liquidation must work locally; HyperEVM redemption is not an atomic fallback | Stated as a requirement; not verified (no HyperEVM evidence was gathered). Measured consequence: the local r33 pools cannot absorb liquidations (depth above), and r33 has no Chainlink feed, so no local price source for r33 is established |

Handoff 01 section 10 says r33/USDG is dropped and there is no r33 concentrated pool. As of the gateway snapshot saved here (1,043 pools, `evidence/pools/gateway-fetch-time.txt`) that is out of date: r33/USDG exists at CL5, CL200 and CL500, and r33/RAM at CL10, CL100 and CL200. The handoff is not edited.

## 3. Candidate pools: pinned facts, depth and execution cost

Method: `pool_quote.py` reads each pool at one recent block (the public RPC keeps about 9 minutes of state): token order, `tickSpacing()`, `fee()`, `slot0`, `liquidity()` and every initialized tick in a window around the price, then walks the ticks with exact constant-liquidity math (the same walk as `test/fork/GroupDepth.t.sol`). Costs are fee plus price impact against the pre-trade mid, in USDG terms. These are local pool-math quotes at the pin, not router quotes, and ignore every other pool. "n/a (window)" means the scanned tick window (500 to 1,800 ticks each side, wider for larger spacings) ran out before the trade filled. Block hashes are in `evidence/pools/pool-quotes.json`.

**Pinned pool facts** (each pool read at its own block; gateway values are unpinned)

| pool | address | pin (L2 block) | spacing | fee() at pin | listed fee | 30-day mean fee | active liquidity | 24 h volume (logs / gateway) | gateway TVL |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| SPY/USDG CL1 | `0x38453c115607463ac284820ce959831042f3df4e` | 104,222,297 | 1 | 100 ppm | 100 ppm | 108 ppm | 3.731e+17 | $960,782 / $1,033,312 | $236,437 |
| NVDA/USDG CL10 | `0xdac1904d823f7d6bcaaf431ceee40c934fed321b` | 104,222,574 | 10 | 100 ppm | 100 ppm | 119 ppm | 5.035e+16 | $102,770 / $114,554 | $87,272 |
| META/USDG CL50 | `0x960f79d8dfec7f2f0c1b24cdac6bb9df1371c6e2` | 104,222,675 | 50 | 150 ppm | 150 ppm | 206 ppm | 1.176e+16 | $26,686 / $29,232 | $111,576 |
| GLD/USDG CL1 | `0x9896af2f6fed9939acc87637b973b6484e6619d8` | 104,222,748 | 1 | 150 ppm | 150 ppm | 206 ppm | 8.158e+16 | $104,936 / $110,696 | $124,680 |
| r33/USDG CL200 | `0xc276b9a27ab1e60e32b581db6a23bb1fab0fc30b` | 104,222,825 | 200 | 20000 ppm | 20000 ppm | 20000 ppm | 5.491e+15 | $28 / $28 | $43,876 |

**One-way execution cost in bp of trade size** (fee + price impact versus the pre-trade mid, local pool math at the pin; buy / sell)

| pool | $50 | $250 | $1,000 | $5,000 | $10,000 | $50,000 |
| --- | --- | --- | --- | --- | --- | --- |
| SPY/USDG CL1 | 1.0 / 1.0 | 1.2 / 1.2 | 2.0 / 2.0 | 4.5 / 5.9 | 5.7 / 10.8 | 24.1 / 77.6 |
| NVDA/USDG CL10 | 1.7 / 1.7 | 4.5 / 4.5 | 14.8 / 14.8 | 80.6 / 69.7 | 192.9 / 138.0 | n/a (window) / n/a (window) |
| META/USDG CL50 | 3.2 / 3.2 | 10.1 / 10.1 | 35.8 / 35.8 | 170.5 / n/a (window) | 333.4 / n/a (window) | n/a (window) / n/a (window) |
| GLD/USDG CL1 | 1.8 / 1.8 | 3.1 / 3.1 | 8.0 / 8.0 | 33.8 / 33.8 | 66.0 / 66.0 | n/a (window) / n/a (window) |
| r33/USDG CL200 | 348.1 / 588.6 | 375.2 / n/a (window) | 430.1 / n/a (window) | 701.2 / n/a (window) | 1018.6 / n/a (window) | n/a (window) / n/a (window) |

**Depth: USDG-equivalent input needed to move the price** (+1% / +5% buying the stock, -1% / -5% selling it)

| pool | +1% | +5% | -1% | -5% |
| --- | --- | --- | --- | --- |
| SPY/USDG CL1 | $68,431 | $163,787 | $32,454 | beyond window |
| NVDA/USDG CL10 | $3,245 | $11,749 | $3,637 | $18,398 |
| META/USDG CL50 | $1,451 | $7,194 | $1,465 | $3,528 |
| GLD/USDG CL1 | $7,684 | $37,721 | $7,759 | beyond window |
| r33/USDG CL200 | $6 | $2,219 | $6 | $31 |

**Fee reconciliation**

| pool | listed | FeeAdjustment events | fee range seen (ppm) | fee() vs events at matched blocks | implied fee from swaps vs event fee | protocol delta / predicted | feeGrowth delta / predicted | share of last 30 days at 150 ppm |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| SPY/USDG CL1 | 100 | 503 | 70-1000 | 16/16 match | median ratio 1.0000, 1.0000, 1.0000 (582,721 swaps) | 1.0000, 1.0000 | 1.0000, 1.0134 | 16.2% |
| NVDA/USDG CL10 | 100 | 1793 | 70-2460 | 18/18 match | median ratio 1.0000, 1.0000, 1.0000 (314,054 swaps) | 1.0000, 1.0000 | 1.0001, 1.0000 | 12.4% |
| META/USDG CL50 | 150 | 71 | 50-2500 | 16/16 match | n/a | 1.0000 | 1.0003 | 22.2% |
| GLD/USDG CL1 | 150 | 12 | 150-444 | 16/16 match | median ratio 1.0000, 1.0000, 1.0000 (90,823 swaps) | 1.0000, 1.0000 | 1.0001, 1.0000 | 22.2% |
| r33/USDG CL200 | 20000 | 0 | 20000-20000 | 16/16 match | n/a | no swaps | no swaps | 0.0% |

**Fee-tier states for SPY over the last 30 days (share of time)**: 100 ppm 83.4%, 125 ppm 0.1%, 150 ppm 16.2%, 175 ppm 0.1%, 200 ppm 0.1%, 250 ppm 0.0%, 300 ppm 0.0%, 1000 ppm 0.0%; last at 150 ppm 2026-10-30T20:03Z.

**Gross swap fee -> protocol share -> holder entitlement** (fork tests at pin 104,116,096; raw token units; reserve cut 20% is the README fixture value)

| pool | fee in force | protocol share of gross | our share of active liquidity | LP collect (token0 / token1) | collect vs (gross - protocol) x share | holders after reserve (token0 / token1) |
| --- | --- | --- | --- | --- | --- | --- |
| GLD | 150 ppm | 0.5000, 0.5000 | 0.4967 | 22,350 / 62,563,885,636,063 | diff 0 / 2 raw | 17,880 / 50,051,108,508,850 |
| NVDA | 100 ppm | 0.5000, 0.5000 | 0.6755 | 60,797 / 292,366,000,972,895 | diff -1 / 3 raw | 48,638 / 233,892,800,778,316 |
| SPY | 100 ppm | 0.5000, 0.5000 | 0.0262 | 10,339,143,280,529 / 7,862 | diff 0 / 0 raw | 8,271,314,624,423 / 6,290 |

## 4. What the fee reconciliation shows (the fee gate is NOT waived; GLD is NOT activated)

`fee_reconcile.py` (read-only; `evidence/fees/fee-reconciliation.json`) reconciles four layers, separately:

1. **Fee-change events versus `fee()`.** At 16 to 18 sampled blocks per pool inside the state horizon, including the blocks either side of a fee change (NVDA changed 100 to 150 ppm inside the window), `fee()` equals the fee derived from `FeeAdjustment` events every time. The fee the pool actually charged: for single-band swaps `net = L x (change in 1/sqrtP or sqrtP)` implies fee `1 - net/gross`; across 582,721 SPY, 314,054 NVDA and 90,823 GLD swaps the implied fee equals the event-derived fee in force (median ratio 1.0000, every regime). So the fee-change events are the fee charged, and the quoted-versus-measured differences are fee states over time, not data errors: SPY 70 to 1,000 ppm (100 now), NVDA 70 to 2,460 (150 now, listed 100), META 50 to 2,500 (150 now), GLD 150 to 444 (150 now; 30-day mean 206 ppm, 78% of the time at 222 ppm). r33 CL200 is static at 20,000 ppm.
2. **Gross swap fee, protocol share and LP entitlement (pinned window of about 3,300 blocks).** Summing input x fee in force over the window's swaps, the pool's `protocolFees()` change equals gross x protocol share (ratio 1.0000 on every token with swaps, no `CollectProtocol` in the window), and the `feeGrowthGlobal` change equals the LP remainder per unit of liquidity (ratio 1.0000 to 1.0134; the 1.3% deviation is band-crossing swaps where the Swap event's post-swap liquidity differs from the liquidity the fee accrued against). Protocol share is 50% on SPY, NVDA, GLD and META (5% on r33), changed from 5% to 50% at blocks 76,901,418 (SPY), 76,907,586 (NVDA) and 88,775,308 (GLD, META).
3. **Exact two-layer position (fork tests at pin 104,116,096).** Protocol delta is exactly 50% of gross; the vault position's `collect` equals (gross - protocol) x our share of active liquidity to within 0 to 3 raw units. Holder entitlement after the 20% reserve cut is 80% of that collect.
4. **Listed fees.** Gateway `feeTier` equals the current `fee()` for SPY (100), NVDA (100 at the quote and fork pins, 150 minutes later), GLD (150), META (150) and r33 (20,000).

What remains open (so the gate stays closed): the gateway's `hasDynamicFees: false` is inconsistent with the event history; GLD's time-weighted fee (206 ppm, 30 days) differs from its listed 150 and that difference is an operator decision, not mine; holder entitlement has not been compared against a third-party LP's Collect history; and the gate in handoff 01 section 1a also requires the profitable-capacity and liquidation-capacity gates, which are not met by anything here (section 6).

## 5. What the contract allows, what the engine omits, and keeper latency

**Feasibility.** `feasibility.py` mirrors `RamsesRangeLib.validatePolicy` at each pool's actual tick spacing (SPY 1, GLD 1, NVDA 10) and `run_baseline.py` refuses any configuration it rejects.

| Rule (contract) | Value |
| --- | --- |
| half-width | `600 <= halfWidthTicks <= 4800` and a multiple of the pool's tick spacing |
| trigger | `tickSpacing <= triggerTicks <= halfWidthTicks / 2` (integer division) |
| cooldown | `3600 s <= minInterval <= 7 days`, whole seconds |
| rolling limit | `1 <= maxPerDay <= 24` |
| loss bound | `maxLossBps <= 100` |
| cap | `maxRangedValueUsd > 0` (uint128 of USD x 1e18) |
| structure | ONE ranged position per pair; recenter has no swap (matched value only); full range is the adapter default (`minUsableTick..maxUsableTick`), not a policy |

Inputs are validated by field width before any rule runs: negative values (for example a negative daily limit), NaN, infinities, non-numbers, booleans, and values above the uint16/uint32/uint8/uint128 widths are rejected with a stated violation instead of crashing or slipping through a comparison. 55 tests, 41 mutations all killed (`logs/mutations-engine-feasibility.log`).

Engine mismatches found and fixed: the default trigger fraction 0.70 gave 420 ticks for a 600-tick range (maximum 300); the historical cooldown was 900 s (minimum 3600 s); the spacing defaulted to 1 for every pool (NVDA's is 10); the full range was one spacing narrower than the contract's at both ends; fees were paid to holders without the reserve cut; `maxRangedValueUsd` was not an input.

**Contract guards the engine omits or only approximates** (documented before any further strategy optimization):

* Checkpoint discipline: `rebalance` reverts `CheckpointStale` unless a checkpoint ran within `maxCheckpointAge`; the engine has no checkpoint concept, so the keeper's checkpoint cadence appears only as the assumed maintenance cost.
* Loss accounting: the vault recognizes shared loss pro rata across both sides' native principals and applies the reserve and settlement waterfall; the engine tracks one equity number, so it cannot say which side bears a loss.
* Allocation circuit breaker (`allocationCapExceeded` sets `allocationPaused` and skips the recenter), pair caps (`PairCapExceeded`), `allocationPaused`, `emergencyMode`, `swapsPaused`, `PairNotEnabled`, and the stock token's `oraclePaused` flag are not modelled.
* Oracle validity: `InvalidReferencePrice`, `IncompleteOracleRound`, `InvalidOracleAnswer`, the sequencer check and `maxStaleness` (at most 12 h) are approximated by an age limit and a weekend/holiday session block; the session block is stricter than the contract, which only checks age and the pause flag.
* Price bounds: the allocation bound (`maxPriceDeviationBps`, at most 5%, engine uses 1.5%) is applied before the recenter, the contract applies it after; the wider removal bound (`maxRemovalDeviationBps`) and `_requireBandCoversGate` (removal tolerance against that bound) are not modelled and can block a real recenter.
* Loss bounds: the contract applies both `maxLossBps` (at most 100) and the operator's `deployLossBps`, which is zero (strict) until configured; the engine allows a 10 bp loss on a recenter.
* Deadline: the keeper must supply a deadline no more than `maxDeadlineDelay` (at most 30 minutes); a late transaction reverts `InvalidDeadline`.
* Integer oracle tick versus the engine's float tick; dividends and `uiMultiplier`; borrow demand and lending-side utilization.

**Keeper execution latency.** The baseline rows named without a suffix evaluate on every swap and oracle update and execute at that instant at the post-swap pool price: an idealised, zero-latency upper bound. The deployed Uniswap keeper polls every 300 seconds (`INTERVAL_SECONDS`), refuses to act below a 0.0003 ETH gas floor, simulates before sending, and a reverted simulation retries only on the next poll. `*_poll300` rows model the polling interval only (`poll_s = 300`); transaction inclusion delay (about a second on this L2) and the price movement between decision and inclusion are not modelled, so a recenter that the contract would reject (`RecenterNotNeeded`, loss bound) or that an adversary moved the pool against is also not modelled. The Ramses keeper's cadence is an assumption. **Any faster-than-300 s keeper and any swap-enabled recenter is hypothetical** and must be labelled so; none is reported as a result here.

## 6. Baselines (contract-feasible, no swap at recenter, 1 h cooldown, 20% reserve cut, RAM at zero)

Common configuration: one position, half-width 600 ticks (+/-6.2%), trigger 300 (the contract maximum for 600; sensitivity at 100), at most 24 recenters per rolling day, per-recenter loss bound 10 bp, protocol share 50% (fork-measured), feed guard 12 h with weekend and holiday sessions blocked and a 150 bp pool-versus-oracle gate. Holding means the same starting 50/50 inventory left untouched; full range is the separately validated mode. Recenter gas ($0.0663) and maintenance ($0.5737/day, the hourly checkpoint cadence the vault requires) are the Uniswap vault keeper's measured costs used as a **proxy** (PR #13 evidence r3), not Ramses measurements; the same evidence's observed average ($0.1428/day, with the keeper out of gas part of the time) is shown as the lower sensitivity. Maintenance is subtracted after the run, not inside the engine. **Returns are shown before and after all assumed costs, and LVR is never subtracted from a return versus holding**; it appears only in the separate fees - LVR - gas - maintenance line, which compares against a delta-matched rebalancing portfolio.

NVDA here is the Ramses NVDA/USDG pool (`0xdac1904d...`, spacing 10), not the live Uniswap vault. SPY covers 74.4 days from 2026-08-19, NVDA 90.0 days from 2026-08-04; windows are single short samples.

### 6A. Current caps: $1,000 pair, $50 ranged value

These are the caps the operator named as existing; the $50 matches `maxRangedValueUsd` in `UpgradeConcentratedLiquidity.s.sol` (the live Uniswap vault), and I did not locate a $1,000 pair-cap constant to cite. 95% of capital is idle by construction. **At these caps every configuration's result is the maintenance assumption**: fees to holders over the whole window are $0.24 to $1.71 against an assumed $42.69 (SPY) or $51.64 (NVDA) of maintenance, and before maintenance every configuration except the trigger-100 NVDA row (recenter gas $11.07) is within 0.1% of holding. With the lower observed-average maintenance the result is still about -1.0% (SPY) and -1.3% (NVDA) over the window.


### SPY: CURRENT CAPS: $1,000 pair, $50 ranged value (the existing caps named by the operator; $50 matches the live Uniswap vault's maxRangedValueUsd)

Tick spacing 1, 655,414 swaps, pool head block 104,071,867, starting capital $1,000, ranged cap $50.

**Full window**: 2026-08-19T22:30:12Z to 2026-11-02T08:18:27Z (74.4 days)

| run | return | hold | excess before maintenance | return after all costs | excess after all costs (window / annualised) | fees to holders | reserve cut | recenter gas | maintenance (assumed) | idle capital (avg) | recenters | worst week vs hold | blocked |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| single600_trig300 | -0.58% | -0.62% | +0.04% | -4.85% | -4.23% / -20.7%/yr | $0.46 | $0.12 | $0.000 | $42.69 | 95% | 0 | $-0.11 | none |
| single600_trig300_poll300 | -0.58% | -0.62% | +0.04% | -4.85% | -4.23% / -20.7%/yr | $0.46 | $0.12 | $0.000 | $42.69 | 95% | 0 | $-0.11 | none |
| single600_trig100 | -0.72% | -0.62% | -0.10% | -4.99% | -4.36% / -21.4%/yr | $0.47 | $0.12 | $1.392 | $42.69 | 95% | 21 | $-0.29 | none |
| live1200_trig300_perday4 | -0.60% | -0.62% | +0.02% | -4.87% | -4.25% / -20.8%/yr | $0.24 | $0.06 | $0.000 | $42.69 | 95% | 0 | $-0.05 | none |
| single1200_trig600 | -0.60% | -0.62% | +0.02% | -4.87% | -4.25% / -20.8%/yr | $0.24 | $0.06 | $0.000 | $42.69 | 95% | 0 | $-0.05 | none |
| full_range | -0.59% | -0.62% | +0.03% | -4.86% | -4.24% / -20.8%/yr | $0.28 | $0.07 | $0.000 | $42.69 | 0% | 0 | $-0.06 | none |

**Last 28 days**: 2026-10-05T00:01:03Z to 2026-11-02T08:18:27Z (28.3 days)

| run | return | hold | excess before maintenance | return after all costs | excess after all costs (window / annualised) | fees to holders | reserve cut | recenter gas | maintenance (assumed) | idle capital (avg) | recenters | worst week vs hold | blocked |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| single600_trig300 | -0.80% | -0.81% | +0.00% | -2.43% | -1.62% / -20.9%/yr | $0.08 | $0.02 | $0.000 | $16.26 | 95% | 0 | $-0.14 | none |
| single600_trig300_poll300 | -0.80% | -0.81% | +0.00% | -2.43% | -1.62% / -20.9%/yr | $0.08 | $0.02 | $0.000 | $16.26 | 95% | 0 | $-0.14 | none |
| single600_trig100 | -0.87% | -0.81% | -0.06% | -2.50% | -1.69% / -21.8%/yr | $0.08 | $0.02 | $0.663 | $16.26 | 95% | 10 | $-0.33 | none |
| live1200_trig300_perday4 | -0.81% | -0.81% | +0.00% | -2.43% | -1.62% / -20.9%/yr | $0.04 | $0.01 | $0.000 | $16.26 | 95% | 0 | $-0.07 | none |
| single1200_trig600 | -0.81% | -0.81% | +0.00% | -2.43% | -1.62% / -20.9%/yr | $0.04 | $0.01 | $0.000 | $16.26 | 95% | 0 | $-0.07 | none |
| full_range | -0.81% | -0.81% | +0.00% | -2.43% | -1.62% / -20.9%/yr | $0.05 | $0.01 | $0.000 | $16.26 | 0% | 0 | $-0.08 | none |

**Fees minus LVR minus costs (a different benchmark from holding; not subtracted from any return above)**

| run (full window) | fees to holders | LVR vs delta-matched benchmark | recenter gas | maintenance (assumed) | fees - LVR - gas | fees - LVR - gas - maintenance | maintenance if the observed average $0.1428/day: excess after all costs |
| --- | --- | --- | --- | --- | --- | --- | --- |
| single600_trig300 | $0.46 | $0.47 | $0.000 | $42.69 | $-0.00 | $-42.69 | -1.02% / -5.0%/yr |
| single600_trig300_poll300 | $0.46 | $0.47 | $0.000 | $42.69 | $-0.00 | $-42.69 | -1.02% / -5.0%/yr |
| full_range | $0.28 | $0.29 | $0.000 | $42.69 | $-0.01 | $-42.70 | -1.04% / -5.1%/yr |

Excess versus holding = fees to holders + inventory effect - recenter gas (baseline: $0.46 + $-0.03 - $0.000); end state idle USDG $476, idle stock $469; RAM accrued 4.5 tokens, valued at zero.

### NVDA: CURRENT CAPS: $1,000 pair, $50 ranged value (the existing caps named by the operator; $50 matches the live Uniswap vault's maxRangedValueUsd)

Tick spacing 10, 331,486 swaps, pool head block 104,187,711, starting capital $1,000, ranged cap $50.

**Full window**: 2026-08-04T11:08:43Z to 2026-11-02T11:27:06Z (90.0 days)

| run | return | hold | excess before maintenance | return after all costs | excess after all costs (window / annualised) | fees to holders | reserve cut | recenter gas | maintenance (assumed) | idle capital (avg) | recenters | worst week vs hold | blocked |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| single600_trig300 | -0.28% | -0.32% | +0.04% | -5.44% | -5.12% / -20.8%/yr | $1.70 | $0.43 | $1.326 | $51.64 | 95% | 20 | $-3.35 | none |
| single600_trig300_poll300 | -0.29% | -0.32% | +0.03% | -5.45% | -5.13% / -20.8%/yr | $1.70 | $0.43 | $1.658 | $51.64 | 95% | 25 | $-3.52 | none |
| single600_trig100 | -1.25% | -0.32% | -0.94% | -6.42% | -6.10% / -24.7%/yr | $1.71 | $0.43 | $11.072 | $51.64 | 95% | 167 | $-3.93 | cooldown 2605 (cooldown 0.5 d) |
| live1200_trig300_perday4 | -0.36% | -0.32% | -0.04% | -5.52% | -5.21% / -21.1%/yr | $0.88 | $0.22 | $1.326 | $51.64 | 95% | 20 | $-1.77 | none |
| single1200_trig600 | -0.27% | -0.32% | +0.05% | -5.43% | -5.11% / -20.7%/yr | $0.89 | $0.22 | $0.398 | $51.64 | 95% | 6 | $-1.76 | none |
| full_range | -0.21% | -0.32% | +0.10% | -5.38% | -5.06% / -20.5%/yr | $1.08 | $0.27 | $0.000 | $51.64 | 0% | 0 | $-2.02 | none |

**Last 28 days**: 2026-10-05T00:00:42Z to 2026-11-02T11:27:06Z (28.5 days)

| run | return | hold | excess before maintenance | return after all costs | excess after all costs (window / annualised) | fees to holders | reserve cut | recenter gas | maintenance (assumed) | idle capital (avg) | recenters | worst week vs hold | blocked |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| single600_trig300 | -6.18% | -5.86% | -0.32% | -7.82% | -1.95% / -25.1%/yr | $0.37 | $0.09 | $0.530 | $16.34 | 95% | 8 | $-3.86 | none |
| single600_trig300_poll300 | -6.17% | -5.86% | -0.31% | -7.81% | -1.95% / -24.9%/yr | $0.36 | $0.09 | $0.530 | $16.34 | 95% | 8 | $-3.77 | none |
| single600_trig100 | -6.53% | -5.86% | -0.67% | -8.16% | -2.30% / -29.5%/yr | $0.37 | $0.09 | $3.978 | $16.34 | 95% | 60 | $-4.38 | cooldown 1363 (cooldown 0.12 d) |
| live1200_trig300_perday4 | -6.05% | -5.86% | -0.19% | -7.69% | -1.82% / -23.4%/yr | $0.19 | $0.05 | $0.530 | $16.34 | 95% | 8 | $-2.00 | none |
| single1200_trig600 | -6.01% | -5.86% | -0.15% | -7.65% | -1.79% / -22.9%/yr | $0.19 | $0.05 | $0.133 | $16.34 | 95% | 2 | $-2.02 | none |
| full_range | -6.02% | -5.86% | -0.16% | -7.66% | -1.80% / -23.0%/yr | $0.21 | $0.05 | $0.000 | $16.34 | 0% | 0 | $-2.25 | none |

**Fees minus LVR minus costs (a different benchmark from holding; not subtracted from any return above)**

| run (full window) | fees to holders | LVR vs delta-matched benchmark | recenter gas | maintenance (assumed) | fees - LVR - gas | fees - LVR - gas - maintenance | maintenance if the observed average $0.1428/day: excess after all costs |
| --- | --- | --- | --- | --- | --- | --- | --- |
| single600_trig300 | $1.70 | $3.69 | $1.326 | $51.64 | $-3.31 | $-54.95 | -1.25% / -5.1%/yr |
| single600_trig300_poll300 | $1.70 | $3.71 | $1.658 | $51.64 | $-3.67 | $-55.31 | -1.26% / -5.1%/yr |
| full_range | $1.08 | $2.18 | $0.000 | $51.64 | $-1.10 | $-52.74 | -1.18% / -4.8%/yr |

Excess versus holding = fees to holders + inventory effect - recenter gas (baseline: $1.70 + $0.02 - $1.326); end state idle USDG $475, idle stock $474; RAM accrued 32.4 tokens, valued at zero.


### 6B. Research scenarios (clearly NOT the current caps)

$10,000 pair with the ranged cap equal to capital (the same engine, larger size). These are research scenarios for understanding the strategy, not a statement of what the contract is configured to allow.

### SPY: RESEARCH SCENARIO: $10,000 pair, ranged cap = capital (not a current cap)

Tick spacing 1, 655,414 swaps, pool head block 104,071,867, starting capital $10,000, ranged cap $10,000.

**Full window**: 2026-08-19T22:30:12Z to 2026-11-02T08:18:27Z (74.4 days)

| run | return | hold | excess before maintenance | return after all costs | excess after all costs (window / annualised) | fees to holders | reserve cut | recenter gas | maintenance (assumed) | idle capital (avg) | recenters | worst week vs hold | blocked |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| single600_trig300 | +0.20% | -0.62% | +0.82% | -0.22% | +0.40% / +2.0%/yr | $89.38 | $22.35 | $0.000 | $42.69 | 2% | 0 | $-21.00 | none |
| single600_trig300_poll300 | +0.20% | -0.62% | +0.82% | -0.22% | +0.40% / +2.0%/yr | $89.38 | $22.35 | $0.000 | $42.69 | 2% | 0 | $-21.00 | none |
| single600_trig100 | +0.01% | -0.62% | +0.63% | -0.42% | +0.20% / +1.0%/yr | $79.77 | $19.94 | $1.392 | $42.69 | 16% | 21 | $-47.21 | none |
| live1200_trig300_perday4 | -0.19% | -0.62% | +0.43% | -0.62% | +0.00% / +0.0%/yr | $46.52 | $11.63 | $0.000 | $42.69 | 1% | 0 | $-10.76 | none |
| single1200_trig600 | -0.19% | -0.62% | +0.43% | -0.62% | +0.00% / +0.0%/yr | $46.52 | $11.63 | $0.000 | $42.69 | 1% | 0 | $-10.76 | none |
| single600_trig300_noreserve | +0.43% | -0.62% | +1.05% | -0.00% | +0.62% / +3.0%/yr | $111.73 | $0.00 | $0.000 | $42.69 | 3% | 0 | $-20.98 | none |
| single600_trig300_gas040 | +0.20% | -0.62% | +0.82% | -0.22% | +0.40% / +2.0%/yr | $89.38 | $22.35 | $0.000 | $42.69 | 2% | 0 | $-21.00 | none |
| single600_trig300_cap5k | -0.20% | -0.62% | +0.42% | -0.63% | -0.01% / -0.0%/yr | $45.43 | $11.36 | $0.000 | $42.69 | 51% | 0 | $-10.50 | none |
| single600_trig300_histshare | +0.84% | -0.62% | +1.46% | +0.42% | +1.04% / +5.1%/yr | $153.79 | $38.45 | $0.000 | $42.69 | 3% | 0 | $-21.75 | none |
| full_range | -0.59% | -0.62% | +0.03% | -1.02% | -0.40% / -2.0%/yr | $2.78 | $0.70 | $0.000 | $42.69 | 0% | 0 | $-0.63 | none |

**Last 28 days**: 2026-10-05T00:01:03Z to 2026-11-02T08:18:27Z (28.3 days)

| run | return | hold | excess before maintenance | return after all costs | excess after all costs (window / annualised) | fees to holders | reserve cut | recenter gas | maintenance (assumed) | idle capital (avg) | recenters | worst week vs hold | blocked |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| single600_trig300 | -0.76% | -0.81% | +0.05% | -0.92% | -0.11% / -1.5%/yr | $16.13 | $4.03 | $0.000 | $16.26 | 0% | 0 | $-28.12 | none |
| single600_trig300_poll300 | -0.76% | -0.81% | +0.05% | -0.92% | -0.11% / -1.5%/yr | $16.13 | $4.03 | $0.000 | $16.26 | 0% | 0 | $-28.12 | none |
| single600_trig100 | -0.74% | -0.81% | +0.07% | -0.90% | -0.09% / -1.2%/yr | $14.27 | $3.57 | $0.663 | $16.26 | 14% | 10 | $-23.18 | none |
| live1200_trig300_perday4 | -0.78% | -0.81% | +0.03% | -0.94% | -0.14% / -1.8%/yr | $8.25 | $2.06 | $0.000 | $16.26 | 0% | 0 | $-14.29 | none |
| single1200_trig600 | -0.78% | -0.81% | +0.03% | -0.94% | -0.14% / -1.8%/yr | $8.25 | $2.06 | $0.000 | $16.26 | 0% | 0 | $-14.29 | none |
| full_range | -0.81% | -0.81% | +0.00% | -0.97% | -0.16% / -2.1%/yr | $0.48 | $0.12 | $0.000 | $16.26 | 0% | 0 | $-0.83 | none |

**Fees minus LVR minus costs (a different benchmark from holding; not subtracted from any return above)**

| run (full window) | fees to holders | LVR vs delta-matched benchmark | recenter gas | maintenance (assumed) | fees - LVR - gas | fees - LVR - gas - maintenance | maintenance if the observed average $0.1428/day: excess after all costs |
| --- | --- | --- | --- | --- | --- | --- | --- |
| single600_trig300 | $89.38 | $88.19 | $0.000 | $42.69 | $1.20 | $-41.49 | +0.72% / +3.5%/yr |
| single600_trig300_poll300 | $89.38 | $88.19 | $0.000 | $42.69 | $1.20 | $-41.49 | +0.72% / +3.5%/yr |
| full_range | $2.78 | $2.92 | $0.000 | $42.69 | $-0.14 | $-42.83 | -0.08% / -0.4%/yr |

Excess versus holding = fees to holders + inventory effect - recenter gas (baseline: $89.38 + $-6.94 - $0.000); end state idle USDG $179, idle stock $5; RAM accrued 883.1 tokens, valued at zero.

**Windows (baseline, before maintenance) and dilution**

| window | excess (window) | annualised |
| --- | --- | --- |
| t1 2026-08-19 to 2026-09-13 | +0.47% | +7.0%/yr |
| t2 2026-09-14 to 2026-10-08 | +0.07% | +1.0%/yr |
| t3 2026-10-08 to 2026-11-02 | -0.05% | -0.7%/yr |
| full, single600_trig300 at $1,000 | +0.85% | +4.2%/yr |
| full, full_range at $1,000 | +0.03% | +0.1%/yr |
| full, single600_trig300 at $50,000 | +0.74% | +3.6%/yr |
| full, full_range at $50,000 | +0.03% | +0.1%/yr |

### NVDA: RESEARCH SCENARIO: $10,000 pair, ranged cap = capital (not a current cap)

Tick spacing 10, 331,486 swaps, pool head block 104,187,711, starting capital $10,000, ranged cap $10,000.

**Full window**: 2026-08-04T11:08:43Z to 2026-11-02T11:27:06Z (90.0 days)

| run | return | hold | excess before maintenance | return after all costs | excess after all costs (window / annualised) | fees to holders | reserve cut | recenter gas | maintenance (assumed) | idle capital (avg) | recenters | worst week vs hold | blocked |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| single600_trig300 | +1.86% | -0.32% | +2.17% | +1.34% | +1.66% / +6.7%/yr | $33.89 | $8.47 | $1.326 | $51.64 | 88% | 20 | $-398.58 | none |
| single600_trig300_poll300 | +1.78% | -0.32% | +2.10% | +1.27% | +1.58% / +6.4%/yr | $23.51 | $5.88 | $1.658 | $51.64 | 92% | 25 | $-452.84 | none |
| single600_trig100 | +1.60% | -0.32% | +1.92% | +1.08% | +1.40% / +5.7%/yr | $31.20 | $7.80 | $11.072 | $51.64 | 88% | 167 | $-424.63 | cooldown 2605 (cooldown 0.5 d) |
| live1200_trig300_perday4 | +1.51% | -0.32% | +1.83% | +1.00% | +1.31% / +5.3%/yr | $65.80 | $16.45 | $1.326 | $51.64 | 55% | 20 | $-279.09 | none |
| single1200_trig600 | +1.65% | -0.32% | +1.97% | +1.13% | +1.45% / +5.9%/yr | $59.35 | $14.84 | $0.398 | $51.64 | 61% | 6 | $-287.82 | none |
| single600_trig300_noreserve | +1.94% | -0.32% | +2.26% | +1.42% | +1.74% / +7.1%/yr | $42.44 | $0.00 | $1.326 | $51.64 | 88% | 20 | $-397.99 | none |
| single600_trig300_gas040 | +1.79% | -0.32% | +2.11% | +1.27% | +1.59% / +6.4%/yr | $33.89 | $8.47 | $8.000 | $51.64 | 88% | 20 | $-399.58 | none |
| single600_trig300_cap5k | +2.75% | -0.32% | +3.07% | +2.24% | +2.55% / +10.4%/yr | $80.90 | $20.23 | $1.326 | $51.64 | 71% | 20 | $-336.75 | none |
| single600_trig300_histshare | +2.13% | -0.32% | +2.45% | +1.61% | +1.93% / +7.8%/yr | $61.79 | $15.45 | $1.326 | $51.64 | 88% | 20 | $-396.47 | none |
| full_range | -0.22% | -0.32% | +0.10% | -0.73% | -0.42% / -1.7%/yr | $10.27 | $2.57 | $0.000 | $51.64 | 0% | 0 | $-20.19 | none |

**Last 28 days**: 2026-10-05T00:00:42Z to 2026-11-02T11:27:06Z (28.5 days)

| run | return | hold | excess before maintenance | return after all costs | excess after all costs (window / annualised) | fees to holders | reserve cut | recenter gas | maintenance (assumed) | idle capital (avg) | recenters | worst week vs hold | blocked |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| single600_trig300 | -7.90% | -5.86% | -2.03% | -8.06% | -2.20% / -28.1%/yr | $28.16 | $7.04 | $0.530 | $16.34 | 62% | 8 | $-375.81 | none |
| single600_trig300_poll300 | -8.25% | -5.86% | -2.38% | -8.41% | -2.55% / -32.6%/yr | $27.51 | $6.88 | $0.530 | $16.34 | 62% | 8 | $-378.03 | none |
| single600_trig100 | -9.06% | -5.86% | -3.19% | -9.22% | -3.36% / -43.0%/yr | $25.17 | $6.29 | $3.978 | $16.34 | 63% | 60 | $-382.38 | cooldown 1363 (cooldown 0.12 d) |
| live1200_trig300_perday4 | -7.74% | -5.86% | -1.87% | -7.90% | -2.04% / -26.1%/yr | $21.55 | $5.39 | $0.530 | $16.34 | 41% | 8 | $-267.97 | none |
| single1200_trig600 | -8.38% | -5.86% | -2.51% | -8.54% | -2.68% / -34.3%/yr | $22.81 | $5.70 | $0.133 | $16.34 | 37% | 2 | $-295.89 | none |
| full_range | -6.02% | -5.86% | -0.16% | -6.19% | -0.33% / -4.2%/yr | $2.14 | $0.53 | $0.000 | $16.34 | 0% | 0 | $-22.50 | none |

**Fees minus LVR minus costs (a different benchmark from holding; not subtracted from any return above)**

| run (full window) | fees to holders | LVR vs delta-matched benchmark | recenter gas | maintenance (assumed) | fees - LVR - gas | fees - LVR - gas - maintenance | maintenance if the observed average $0.1428/day: excess after all costs |
| --- | --- | --- | --- | --- | --- | --- | --- |
| single600_trig300 | $33.89 | $143.66 | $1.326 | $51.64 | $-111.09 | $-162.74 | +2.04% / +8.3%/yr |
| single600_trig300_poll300 | $23.51 | $105.93 | $1.658 | $51.64 | $-84.08 | $-135.72 | +1.97% / +8.0%/yr |
| full_range | $10.27 | $21.77 | $0.000 | $51.64 | $-11.50 | $-63.14 | -0.03% / -0.1%/yr |

Excess versus holding = fees to holders + inventory effect - recenter gas (baseline: $33.89 + $184.62 - $1.326); end state idle USDG $9564, idle stock $0; RAM accrued 222.5 tokens, valued at zero.

**Windows (baseline, before maintenance) and dilution**

| window | excess (window) | annualised |
| --- | --- | --- |
| t1 2026-08-04 to 2026-09-03 | -1.86% | -22.6%/yr |
| t2 2026-09-03 to 2026-10-03 | -0.44% | -5.4%/yr |
| t3 2026-10-05 to 2026-11-02 | -2.03% | -26.1%/yr |
| full, single600_trig300 at $1,000 | +2.14% | +8.7%/yr |
| full, full_range at $1,000 | +0.10% | +0.4%/yr |
| full, single600_trig300 at $50,000 | +2.10% | +8.5%/yr |
| full, full_range at $50,000 | +0.09% | +0.4%/yr |


* **SPY** (zero recenters: no trigger fired, so this is a static +/-6.2% range). Before maintenance +0.82% over holding (+4.0%/yr): fees to holders $89.4, reserve cut $22.3, inventory effect -$6.9. After all assumed costs (maintenance $42.69) the return is -0.22% and the excess +0.40% (+2.0%/yr), +0.72% with the observed-average maintenance. Fees minus LVR minus gas is +$1.20, about break-even; minus maintenance it is -$41.49. Last 28 days: +0.05% before maintenance, -0.11% after. Full range earns $2.8 of fees.
* **NVDA** (Ramses pool). Before maintenance +2.17% over holding (+8.8%/yr), but that is **not fee income**: fees to holders $33.9, inventory effect +$184.6. With no swap at recenter each redeploy takes only matched value, the NVDA side was used up in the rally, and the position ended as $9,564 idle USDG (88% idle on average), i.e. mostly cash while NVDA fell. Fees minus LVR minus gas is -$111.1 and -$162.7 after maintenance. The result flips by window: first third -1.86%, middle third -0.44%, last 28 days -2.03% (-26%/yr, -28%/yr after maintenance) with a worst week of -$376. With 300-second polling the baseline's fees fall to $23.5 and the last-28-day excess to -2.38%.
* **Idle capital** is structural: matched-only redeploys leave the surplus side idle. A half-capital ranged cap raised the NVDA headline (+12.4%/yr) only because it keeps both idle sides balanced longer; that is the same inventory artefact, not a better strategy.
* **Blocked maintenance.** No due recenter was blocked by a stale feed or a closed session in any run (the guard paths are exercised by unit tests, not by these windows; the SPY feed is older than 12 h about half of the time, so this is not a statement that it is always fresh). The only blocks were cooldown blocks at trigger 100 on NVDA (2,605 evaluations, 0.5 day).
* **Reserve and RAM.** The 20% reserve cost holders about 0.23% over the SPY window. RAM accrued (883 tokens SPY, 222 NVDA) is valued at zero.
* Maintenance is $0.5737/day regardless of size: 2.1%/yr of $10,000, 0.42%/yr of $50,000, 21%/yr of $1,000.

**Scoped conclusion.** For the contract-feasible single-range configuration, fee income roughly equalled adverse selection on SPY and fell well short on NVDA; the positive NVDA headline versus holding is an inventory path that reverses in the latest window; at the current caps the strategy cannot pay for its own keeper under the assumed maintenance cost. Nothing here supports lending reserves, borrow caps or liquidation capacity, and these are two short samples of a modelled strategy, not a yield estimate.

## 7. GLD

**Feed label (captured).** `gld/directory-feeds-robinhood-mainnet.json` is the raw response (SHA-256 `69056e59...1674`, fetched 2026-11-02T11:50:59Z, HTTP 200, `etag "0cf583b21207184301c0feee7492fc24"`, `last-modified 2026-11-02 08:50:42 GMT`; headers in `gld/directory-headers.txt`) from the exact URL **`https://reference-data-directory.vercel.app/feeds-robinhood-mainnet.json`**. `gld/directory-selected-feeds.json` holds the SPY, NVDA and GLD entries. For GLD (proxy `0x470A51258068043bd43dC0a56245625C9fE86eB0`, contract `0xeA3F8E77a98d7877b6F8164569a8e82454cCcDB8`): `docs.attributeType = "dex_state_price"`, `productSubType = "Reference"`, `marketHours = "Crypto"`, `assetClass = "Crypto"`, `assetSubClass = "Pegged Asset"`, `feedCategory = "new"`. SPY and NVDA are `attributeType "other"`, `productSubType "calculatedPrice"`, `marketHours "us_equities_24/5"`. Of the 58 directory entries, 11 are `dex_state_price` (BTC.B, WEETH, USDS, EURC, WSTETH, WBTC, LBTC, CBBTC, SYRUPUSDC, GLD, USDE); GLD is the only tokenized stock or commodity among them.

**What this establishes.** The directory classifies the GLD feed as a DEX-state reference price on 24/7 hours, unlike the equity feeds. **It does not identify the source pools, venues, weights or window, and it does not show the feed can be moved by trading.**

**Supporting observations (they do not identify the source).** From `gld/gld_independence.json`, `gld_twap.json`, `gld_v4_compare.json`, `weekend_drift.json`: node-observation spread per report is 0.12% median for GLD against 0.002% for SPY; on 14 weekend reports the answer changed 13 times; the answer sits a median 0.022% from the Ramses GLD pool on weekends (0.044% median for a trailing 5-minute average over all reports; Uniswap v4 GLD pools 0.06 to 0.07%); the feed did not react when the Ramses GLD pool sat at MAX_TICK for 28.5 minutes on 2026-09-28, which argues against direct dependence on the spot price. Observation dispersion and close tracking cannot identify source pools. `gld/large-file-hashes.txt` records the SHA-256 of the large local probe files.

**Scoped statement about the tested LP strategies:** section 1, item 3. Almost all of those configurations are hypothetical (section 5), and no contract-feasible baseline was run on GLD in this checkpoint. GLD exit depth (about $7.7k per 1% move; see the pinned depth table) is a separate input to any lending or liquidation cap, and GLD is not activated.

## 8. Pins, hashes, keeper receipts, input bundle, reproduction

`pins-and-receipts.json` holds full hashes and headers for every pin (all `l1BlockNumber` checks pass), `pools/pool-quotes.json` the per-pool pins. Short form:

| Use | L2 block | Native (l1BlockNumber) | Block hash | Provenance |
| --- | --- | --- | --- | --- |
| Fee-entitlement fork tests (`logs/fork-final.log`, 3 pairs, 3 tests each, all pass) | 104,116,096 | 26,326,238 | `0x9211f82fbe82275c5ebd239719fa4aaef504a2fc857fc9397c55cb06de776cbb` | printed as `pin 104116096 26326238` in the log |
| Depth fork tests (`logs/fork-depth-all.log`, 3 pairs, all pass) | 104,096,178 | 26,326,064 | `0x878484914d4b76373cfb8a15e8208719b2ddf51d8373e29174464cbd1b0362f1` | **recovered**: the log prints only `DEPTH_BLOCK: 26326064`. The L2 number came from an earlier subagent report; its header's native number matches the log, but about a hundred consecutive L2 blocks share one native number, so this is consistent with, not derived from, the log |
| Keeper verification | 104,172,613 | 26,326,728 | `0xa98daed63190376b96b042414b59527f67a88fd20d53fb88ceedd1432bf8d439` | 2026-11-02T11:14:39Z |
| SPY / GLD dataset head | 104,071,867 | 26,325,854 | `0xbad7a6a33c1bec4a6f432e13b92bcf50edba64825420c41910bb666196175b0d` | `bundle/manifest-v1.json` |
| NVDA dataset head / grid anchor | 104,187,711 / 104,180,000 | 26,326,860 / 26,326,793 | see `pins-and-receipts.json` | 8 swaps after the anchor dropped |

**Keeper transactions (RPC receipts, status 1):** checkpoint `0xcad407716d6b7299784206d773d376ca75f6148d98d7c14b98cafc9f7403476d` (block 104,165,913, nonce 138, gas 523,686, effective gas price 20,094,000 wei, 0.00001052 ETH, 19 logs) and rebalance `0x7cb6988a23127109837531ecf2fdf2c3f4e95d499a4eb750ac8dac3116078031` (block 104,165,952, nonce 139, gas 735,438, 0.00001486 ETH, 31 logs), both from `0x978e2ADF18da8B846552932c0aB2B1193da1fAc4` to the production vault. Keeper nonce 140 and balance 0.005269526 ETH at block 104,193,993. **Mined reverted sends: none found.** The PR #13 evidence found nonce 138 equal to 138 transactions that left vault events; the two receipts explain nonces 138 and 139, so no nonce is unexplained. The `CheckpointStale` and `execution reverted` lines in the keeper log are simulation or `estimateGas` rejections that were never mined: no receipt, no cost.

**Input bundle (hash-verified, versioned, no hosted copy assumed).** The raw pool logs (about 810 MB) and pickled datasets (about 60 MB) are too large for Git and are not committed. They are immutable chain data, so `bundle/manifest-v1.json` pins, per pool, the block range, event signatures and a SHA-256 over the canonical log set and over the built dataset columns; `bundle.py` rebuilds and verifies them from the public RPC: `python3 bundle.py rebuild PAIR` fetches cold, checks every event hash against the manifest, builds the dataset and checks its hash (`bundle.py verify PAIR` checks a local copy). The small inputs (feed answers `answer_updated.json`, gauge notifications `gauge_notify.json`) are committed under `bundle/inputs/` and hashed in the manifest. State reads (liquidity, `fee()` at a block) are not recoverable from the RPC after about 9 minutes and are not part of the bundle; the pinned quotes and fee reconciliation record their own results and block hashes instead. Verification status: the **GLD inputs were rebuilt cold** (empty cache, fresh header grid) and all 12 event-log hashes and the dataset-column hash matched the manifest (`logs/bundle-rebuild-GLD.log`); the local SPY and NVDA datasets and the committed small inputs verify against the manifest (`logs/bundle-verify-local.log`), but a cold rebuild of SPY and NVDA (about 810 MB of logs and several thousand header reads) has not been run. Hosting the raw files as a release asset would be a publication decision for the operator; nothing was uploaded.

**Commands (read-only, from this directory, shared cache):**

```
python3 -m unittest test_engine test_feasibility               # 55 tests
python3 mutate_engine.py                                       # 41 mutations, all killed
python3 bundle.py rebuild GLD                                  # cold fetch + hash verification (also SPY, NVDA)
python3 run_baseline.py SPY caps && python3 run_baseline.py NVDA caps          # current caps
python3 run_baseline.py SPY research && python3 run_baseline.py NVDA research  # research scenarios
python3 render_baseline.py caps SPY NVDA ; python3 render_baseline.py research SPY NVDA
python3 pool_quote.py ; python3 fee_reconcile.py ; python3 render_pools.py     # pinned pools and fees (needs the live RPC within minutes)
python3 classify_historical.py <dir with the historical jsonl>
python3 collect_evidence.py
```

Fork tests: `FOUNDRY_PROFILE=ramses forge test --match-path 'ramses-vaults/test/fork/Group*.t.sol'` with `REMEDIATION_FORK_BLOCK` and `REMEDIATION_NATIVE_BLOCK` set to a pin above (the public RPC keeps only minutes of state, so the recorded logs are the evidence for those pins).

## 9. Handoff 01 requirements carried forward, and r33 kept separate

No separate partner document was readable (section 2). Carried forward from handoff 01 sections 0 to 2 as the operator's recorded requirements, with changes noted:

* Identify pools **by address**, assert `tickSpacing()`, read `fee()` at the pin, and stop and report if it differs from the listing. Handoff 01 asserts spacing 1; the partner feedback replaces "default to CL1" with selection by measured depth and cost (section 3), so the assertion becomes the pool's recorded spacing (SPY 1, GLD 1, NVDA 10, META 50, r33 200).
* Fee entitlement is verified per pool with a fork test of the exact configuration (done for SPY, GLD and the NVDA control; protocol share 50%); META and r33 CL200 have no fork test yet. The fee gate is not waived (section 4).
* Margin and liquidation caps come from measured depth, not TVL (section 3).
* No feed, no market; never substitute pool spot for SPY or GLD; the age limit and a session check fail closed. GLD's feed label is `dex_state_price` (section 7); whether it is acceptable is operator decision 1.
* Enable allocation and margin **per pair**, only for pairs that pass fee entitlement, the profitable-capacity gate and the liquidation-capacity gate. SPY is the likely first; GLD stays unactivated.
* The Ramses NVDA pair (decision 8) has tick spacing 10: half-widths must be multiples of 10 if it joins.

### Direct r33 collateral exposure (kept separate from the SPY/GLD/USDG collateral set)

r33 is not part of the SPY/GLD/USDG collateral set. The partner's requirement is that **direct r33 collateral preserves r33 exposure and LP participation is a separate option**; this brief keeps them separate in two ways. (1) An LP route is not exposure-preserving: matched-only recentering converts inventory (section 6B), so r33 posted as collateral must be the plain asset, not an LP position. (2) The group shares one controller with cross-collateral, so **an r33 market with a collateral factor above zero would count toward SPY/GLD/USDG borrowers' liquidity and put the group's suppliers behind r33.** That is a change from handoff 01 tier 0 (collateral factor 0), and nothing here proposes it. What the evidence says about the preconditions: r33 has no Chainlink feed (handoff 01 section 10, not re-measured); the only local r33/USDG pools are thin (CL200: $6 of depth per 1% move, $31 to a 5% fall, 2% fee, $28 daily volume), so a local price source built on them would be manipulable and liquidations could not exit; redemption on HyperEVM is not an atomic fallback and was not evaluated. Before any collateral factor above zero, handoff 01 requires `research/R33-RAM.md` (who controls the r33 contract: owner, mint, pause, blacklist, transfer tax; which price source is acceptable). That has not been done.

## 10. Open items

* The partner's original document (HTTP 403 here) and any requirement it contains beyond the operator's summary.
* The gateway's `hasDynamicFees: false`, GLD's 206 ppm 30-day mean fee against the listed 150, and a third-party LP Collect comparison.
* Fork tests for META and r33 CL200; the profitable-capacity and liquidation-capacity gates; operator decisions in handoff 01 section 13.
* Not done: dividend/`uiMultiplier` fork test, a real signed recenter, a contract-feasible baseline on GLD, RAM holder entitlement, Ramses-vault gas measurements, a longer or more volatile SPY sample (its baseline never recentered), a cold rebuild verification for SPY and NVDA (GLD was verified cold).
* Wording to avoid: the baselines are short samples of a modelled strategy, not a yield estimate and not a safety claim.
