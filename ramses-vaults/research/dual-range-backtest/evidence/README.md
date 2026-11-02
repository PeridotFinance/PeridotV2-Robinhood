# Ramses group Phase 0: research brief and evidence index

Research and measurement only (handoff 01 Phase 0). No production contract, deployment script or configuration is changed by anything in this directory. Everything here was produced read-only from the public RPC; nothing was signed or sent. This README is the only Markdown that is published for this work, so it carries the brief, the corrections and the index of evidence files.

## 1. Corrections to earlier statements (read first)

1. **"A plain +/-5% single range does about as well with none [no contract change]" was wrong.** The backtest's `single_pm5` is a 500-tick half-width. `RamsesRangeLib.validatePolicy` requires `600 <= halfWidthTicks <= 4800`, so that configuration cannot be deployed. The same applies to every historical grid cell: none of the 872 historical rows that use a recenter policy passes (section 3). The nearest deployable single range is 600 ticks (about +/-6.2%); it had not been backtested until section 4.
2. **"GLD's Chainlink feed ... is not an independent price" was not established.** What is established is narrower: Chainlink's reference-data directory labels the GLD feed `attributeType: dex_state_price`, and its node observations disagree far more than SPY's. Neither fact identifies which pools or what weights the feed uses, and no causal test was run. Section 6 lists exactly what the evidence does and does not show.
3. **"GLD supports $0" is withdrawn as worded.** The supportable statement is scoped to the tested LP strategies (section 6): every tested LP configuration on the GLD pool had negative excess return against holding in both windows. That is not a derived lending, margin or liquidation cap.
4. All historical numbers are preserved unchanged in `historical-hypothetical/` and labelled below as hypothetical configurations.

## 2. What the contract allows (the feasibility check)

`feasibility.py` mirrors `RamsesRangeLib.validatePolicy` and the recenter behaviour the engine used to assume away. `run_baseline.py` refuses to run any configuration it rejects, using each pool's actual tick spacing (SPY 1, GLD 1, **NVDA 10**; `pools_static.json`).

| Rule (contract) | Value |
| --- | --- |
| half-width | `600 <= halfWidthTicks <= 4800` and a multiple of the pool's tick spacing |
| trigger | `tickSpacing <= triggerTicks <= halfWidthTicks / 2` (integer division) |
| cooldown | `3600 s <= minInterval <= 7 days`, whole seconds |
| rolling limit | `1 <= maxPerDay <= 24` |
| loss bound | `maxLossBps <= 100` |
| cap | `maxRangedValueUsd > 0` |
| structure | ONE ranged position per pair; recenter has no swap (matched value only); full range is the adapter default (`minUsableTick..maxUsableTick`), not a policy |

Mismatches found in the engine and fixed (the old behaviour is what produced the historical rows):

* **Trigger.** The default `core_trigger_frac = 0.70` gives 420 ticks for a 600-tick half-width; the contract allows at most 300. `Config.trigger_ticks` is now an explicit integer and the check rejects the legacy fraction.
* **Cooldown.** The historical grid used 900 s; the contract minimum is 3600 s.
* **Spacing.** The engine defaulted to spacing 1 for every pool; NVDA's is 10.
* **Full range.** The engine used `floor(MIN_TICK)+spacing .. floor(MAX_TICK)-spacing`; the contract uses `TickMath.minUsableTick/maxUsableTick` (truncation toward zero), e.g. -887272..887272 for spacing 1 and -887270..887270 for spacing 10. Checked separately by `full_range_violations` and a test.
* **Reserve cut.** The engine paid holders 100% of LP fees. The vault's `reserveFeeBps` (20% fixture value in `ramses-vaults/README.md`, configuration not a decision) now comes off at accrual and is reported as "reserve cut".
* **Ranged-value cap.** `maxRangedValueUsd` is now an engine input (it limits deployment per side to half of it).

Tests: 47 pass (25 engine + 22 feasibility/engine-field tests; one violating and one passing case per rule). Mutation check: 35 mutations (15 old + 5 engine + 15 feasibility), all killed (`logs/mutations-engine-feasibility.log`).

Declared as not modelled: the integer oracle tick versus the engine's float tick, `_requireBandCoversGate`, the operator's `deployLossBps`, dividends/`uiMultiplier`, settlement swaps, borrow demand, and RAM holder entitlement.

## 3. Historical grid: every recenter-policy row is hypothetical

`historical-classification.json` classifies all 967 rows of `bt_*`, `fin_*`, `cap_*`, `stress_*` and `wider_SPY` against the contract. The 95 rows that pass are all `full_range` rows; the other 872 all use a recenter policy and none passes (full range is a valid mode, although those runs used the engine's old off-by-one tick bounds). Every dual-range or single-range row fails, for these reasons (rows can fail several):

| Reason | Rows |
| --- | --- |
| trigger 0.70 x core half-width exceeds half-width/2 | 872 |
| cooldown 900 s < 3600 s | 859 |
| two layers (contract holds one position per pair) | 685 |
| swap at recenter (`rebal` scenarios; the contract does not swap) | 682 |
| core half-width below 600 ticks (50, 100, 200, 300, 400 or 500) | 872 (all) |
| no `maxRangedValueUsd` (the engine's "uncapped" cannot be expressed on chain) | 872 |

So the earlier dual-range headline numbers (for example SPY dual +/-2%/+/-5% at about 2% net of holding, or the 500-tick single at +6.1%/yr on SPY) describe **hypothetical configurations that need contract changes or invalid parameters**. They are kept for reference only.

## 4. Contract-feasible baseline (SPY and NVDA)

Configuration (identical for both pools except tick spacing): one position, half-width 600 ticks (+/-6.2%), **trigger 300** (the contract maximum for 600; sensitivity at 100), cooldown 3600 s, at most 24 recenters per rolling day, per-recenter loss bound 10 bp, `maxRangedValueUsd` = capital ($10,000 so it does not bind; a half-capital sensitivity is shown), no swap at recenter, reserve cut 20%, protocol share 50% (fork-measured), RAM counted at **zero**, feed guard 12 h / weekend and holiday sessions blocked / 150 bp pool-vs-oracle gate. Holding means the same starting 50/50 inventory left untouched. Full range is the separately validated mode (ticks per the contract).

Costs: recenter gas $0.0663 and maintenance (hourly checkpoint cadence) $0.5737/day are the **Uniswap vault keeper's measured costs used as a proxy** (PR #13 evidence r3), not Ramses measurements. Maintenance is added after the run as an assumed line, not inside the engine; the engine's older $0.40 per recenter is a sensitivity.

NVDA here is the **Ramses NVDA/USDG pool** (`0xdac1904d...`, spacing 10), not the live Uniswap vault. The two pool datasets cover different windows (SPY 74.4 days from 2026-08-19; NVDA 90.0 days from 2026-08-04), each ending when its data was loaded (see `baseline_*.json` for pins and hashes). Windows are single short samples. For NVDA the "last third" starts on the same Monday (2026-10-05) as the last-28-day window because the weekend start is blocked, so those two blocks are identical.

### SPY  (tick spacing 1, 655,414 swaps, pool head block 104,071,867)

**Full window**: 2026-08-19T22:30:12Z to 2026-11-02T08:18:27Z, 74.4 days, $10,000 start

| run | window return | hold | excess (window) | excess (annualised) | fees to holders | reserve cut | recenter gas | idle capital (avg) | recenters | worst week vs hold | blocked maintenance |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| single600_trig300 | +0.20% | -0.62% | +0.82% | +4.0%/yr | $89.4 | $22.3 | $0.00 | 2% | 0 | $-21 | none |
| single600_trig100 | +0.01% | -0.62% | +0.63% | +3.1%/yr | $79.8 | $19.9 | $1.39 | 16% | 21 | $-47 | none |
| single1200_trig600 | -0.19% | -0.62% | +0.43% | +2.1%/yr | $46.5 | $11.6 | $0.00 | 1% | 0 | $-11 | none |
| full_range | -0.59% | -0.62% | +0.03% | +0.1%/yr | $2.8 | $0.7 | $0.00 | 0% | 0 | $-1 | none |

**Last 28 days**: 2026-10-05T00:01:03Z to 2026-11-02T08:18:27Z, 28.3 days, $10,000 start

| run | window return | hold | excess (window) | excess (annualised) | fees to holders | reserve cut | recenter gas | idle capital (avg) | recenters | worst week vs hold | blocked maintenance |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| single600_trig300 | -0.76% | -0.81% | +0.05% | +0.6%/yr | $16.1 | $4.0 | $0.00 | 0% | 0 | $-28 | none |
| single600_trig100 | -0.74% | -0.81% | +0.07% | +0.9%/yr | $14.3 | $3.6 | $0.66 | 14% | 10 | $-23 | none |
| single1200_trig600 | -0.78% | -0.81% | +0.03% | +0.3%/yr | $8.2 | $2.1 | $0.00 | 0% | 0 | $-14 | none |
| full_range | -0.81% | -0.81% | +0.00% | +0.0%/yr | $0.5 | $0.1 | $0.00 | 0% | 0 | $-1 | none |

**First third**: 2026-08-19T22:30:12Z to 2026-09-13T17:46:13Z, 24.8 days, $10,000 start

| run | window return | hold | excess (window) | excess (annualised) | fees to holders | reserve cut | recenter gas | idle capital (avg) | recenters | worst week vs hold | blocked maintenance |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| single600_trig300 | -0.06% | -0.53% | +0.47% | +7.0%/yr | $52.3 | $13.1 | $0.00 | 2% | 0 | $-8 | none |
| single600_trig100 | -0.11% | -0.53% | +0.42% | +6.2%/yr | $48.7 | $12.2 | $0.40 | 12% | 6 | $-12 | none |
| single1200_trig600 | -0.28% | -0.53% | +0.25% | +3.7%/yr | $27.4 | $6.9 | $0.00 | 1% | 0 | $-4 | none |
| full_range | -0.52% | -0.53% | +0.02% | +0.2%/yr | $1.7 | $0.4 | $0.00 | 0% | 0 | $-0 | none |

**Middle third**: 2026-09-14T00:00:28Z to 2026-10-08T13:01:58Z, 24.5 days, $10,000 start

| run | window return | hold | excess (window) | excess (annualised) | fees to holders | reserve cut | recenter gas | idle capital (avg) | recenters | worst week vs hold | blocked maintenance |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| single600_trig300 | +1.07% | +1.00% | +0.07% | +1.0%/yr | $23.4 | $5.9 | $0.00 | 1% | 0 | $-32 | none |
| single600_trig100 | +1.08% | +1.00% | +0.08% | +1.2%/yr | $20.9 | $5.2 | $0.27 | 14% | 4 | $-27 | none |
| single1200_trig600 | +1.04% | +1.00% | +0.04% | +0.5%/yr | $12.0 | $3.0 | $0.00 | 0% | 0 | $-16 | none |
| full_range | +1.00% | +1.00% | +0.00% | +0.0%/yr | $0.7 | $0.2 | $0.00 | 0% | 0 | $-1 | none |

**Last third**: 2026-10-08T13:39:32Z to 2026-11-02T08:18:27Z, 24.8 days, $10,000 start

| run | window return | hold | excess (window) | excess (annualised) | fees to holders | reserve cut | recenter gas | idle capital (avg) | recenters | worst week vs hold | blocked maintenance |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| single600_trig300 | -1.09% | -1.04% | -0.05% | -0.7%/yr | $13.9 | $3.5 | $0.00 | 1% | 0 | $-39 | none |
| single600_trig100 | -1.12% | -1.04% | -0.08% | -1.2%/yr | $11.4 | $2.9 | $0.27 | 17% | 4 | $-39 | none |
| single1200_trig600 | -1.06% | -1.04% | -0.02% | -0.3%/yr | $7.1 | $1.8 | $0.00 | 0% | 0 | $-20 | none |
| full_range | -1.04% | -1.04% | -0.00% | -0.0%/yr | $0.4 | $0.1 | $0.00 | 0% | 0 | $-1 | none |

**Where the excess comes from (full window, baseline)**

| run | excess vs hold | = fees to holders | + inventory effect | - recenter gas | LVR vs delta-matched benchmark | fees - LVR - gas | maintenance gas (assumed) | excess after maintenance (annualised) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| single600_trig300 | $82.4 | $89.4 | $-6.9 | $0.00 | $88.2 | $1.2 | $42.7 | +2.0%/yr |
| full_range | $2.6 | $2.8 | $-0.2 | $0.00 | $2.9 | $-0.1 | $42.7 | -2.0%/yr |

End state of the baseline: idle USDG $179, idle stock $5, max drawdown -1.93%, in range 100% of the time it held a position, RAM tokens accrued 883.1 (valued at zero).

**Sensitivities (full window, annualised excess vs hold / window excess)**

| run | what changes | excess (annualised) | excess (window) | recenters |
| --- | --- | --- | --- | --- |
| single600_trig100 | sensitivity: trigger 100 (more frequent recentering) | +3.1%/yr | +0.63% | 21 |
| single1200_trig600 | sensitivity: wider +/-1200 ticks (~12.7%), trigger 600 | +2.1%/yr | +0.43% | 0 |
| single600_trig300_noreserve | sensitivity: reserve cut 0 (shows what the 20% reserve costs holders) | +5.1%/yr | +1.05% | 0 |
| single600_trig300_gas040 | sensitivity: engine's conservative $0.40 per recenter | +4.0%/yr | +0.82% | 0 |
| single600_trig300_cap5k | sensitivity: maxRangedValueUsd = half of capital (rest stays idle) | +2.1%/yr | +0.42% | 0 |
| single600_trig300_histshare | sensitivity: historical protocol share (5% before 2026-09-30, then 50%) | +7.2%/yr | +1.46% | 0 |
| single600_trig300 @ $1,000 | dilution at a different size | +4.2%/yr | +0.85% | 0 |
| full_range @ $1,000 | dilution at a different size | +0.1%/yr | +0.03% | 0 |
| single600_trig300 @ $50,000 | dilution at a different size | +3.6%/yr | +0.74% | 0 |
| full_range @ $50,000 | dilution at a different size | +0.1%/yr | +0.03% | 0 |

### NVDA  (tick spacing 10, 331,486 swaps, pool head block 104,187,711)

**Full window**: 2026-08-04T11:08:43Z to 2026-11-02T11:27:06Z, 90.0 days, $10,000 start

| run | window return | hold | excess (window) | excess (annualised) | fees to holders | reserve cut | recenter gas | idle capital (avg) | recenters | worst week vs hold | blocked maintenance |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| single600_trig300 | +1.86% | -0.32% | +2.17% | +8.8%/yr | $33.9 | $8.5 | $1.33 | 88% | 20 | $-399 | none |
| single600_trig100 | +1.60% | -0.32% | +1.92% | +7.8%/yr | $31.2 | $7.8 | $11.07 | 88% | 167 | $-425 | cooldown 2605 (cooldown 0.5 d) |
| single1200_trig600 | +1.65% | -0.32% | +1.97% | +8.0%/yr | $59.4 | $14.8 | $0.40 | 61% | 6 | $-288 | none |
| full_range | -0.22% | -0.32% | +0.10% | +0.4%/yr | $10.3 | $2.6 | $0.00 | 0% | 0 | $-20 | none |

**Last 28 days**: 2026-10-05T00:00:42Z to 2026-11-02T11:27:06Z, 28.5 days, $10,000 start

| run | window return | hold | excess (window) | excess (annualised) | fees to holders | reserve cut | recenter gas | idle capital (avg) | recenters | worst week vs hold | blocked maintenance |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| single600_trig300 | -7.90% | -5.86% | -2.03% | -26.1%/yr | $28.2 | $7.0 | $0.53 | 62% | 8 | $-376 | none |
| single600_trig100 | -9.06% | -5.86% | -3.19% | -40.9%/yr | $25.2 | $6.3 | $3.98 | 63% | 60 | $-382 | cooldown 1363 (cooldown 0.12 d) |
| single1200_trig600 | -8.38% | -5.86% | -2.51% | -32.2%/yr | $22.8 | $5.7 | $0.13 | 37% | 2 | $-296 | none |
| full_range | -6.02% | -5.86% | -0.16% | -2.1%/yr | $2.1 | $0.5 | $0.00 | 0% | 0 | $-22 | none |

**First third**: 2026-08-04T11:08:43Z to 2026-09-03T11:04:51Z, 30.0 days, $10,000 start

| run | window return | hold | excess (window) | excess (annualised) | fees to holders | reserve cut | recenter gas | idle capital (avg) | recenters | worst week vs hold | blocked maintenance |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| single600_trig300 | +1.92% | +3.78% | -1.86% | -22.6%/yr | $19.9 | $5.0 | $0.53 | 74% | 8 | $-399 | none |
| single600_trig100 | +1.69% | +3.78% | -2.10% | -25.5%/yr | $22.0 | $5.5 | $3.91 | 72% | 59 | $-425 | cooldown 422 (cooldown 0.29 d) |
| single1200_trig600 | +2.63% | +3.78% | -1.15% | -13.9%/yr | $20.4 | $5.1 | $0.20 | 49% | 3 | $-274 | none |
| full_range | +3.74% | +3.78% | -0.04% | -0.4%/yr | $3.3 | $0.8 | $0.00 | 0% | 0 | $-11 | none |

**Middle third**: 2026-09-03T11:22:55Z to 2026-10-03T11:20:39Z, 30.0 days, $10,000 start

| run | window return | hold | excess (window) | excess (annualised) | fees to holders | reserve cut | recenter gas | idle capital (avg) | recenters | worst week vs hold | blocked maintenance |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| single600_trig300 | +1.76% | +2.20% | -0.44% | -5.4%/yr | $76.8 | $19.2 | $0.40 | 36% | 6 | $-188 | none |
| single600_trig100 | +3.04% | +2.20% | +0.84% | +10.2%/yr | $84.9 | $21.2 | $3.18 | 29% | 48 | $-106 | cooldown 820 (cooldown 0.08 d) |
| single1200_trig600 | +3.34% | +2.20% | +1.14% | +13.8%/yr | $53.6 | $13.4 | $0.13 | 22% | 2 | $-84 | none |
| full_range | +2.22% | +2.20% | +0.02% | +0.3%/yr | $4.5 | $1.1 | $0.00 | 0% | 0 | $-5 | none |

**Last third**: 2026-10-05T00:00:42Z to 2026-11-02T11:27:06Z, 28.5 days, $10,000 start

| run | window return | hold | excess (window) | excess (annualised) | fees to holders | reserve cut | recenter gas | idle capital (avg) | recenters | worst week vs hold | blocked maintenance |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| single600_trig300 | -7.90% | -5.86% | -2.03% | -26.1%/yr | $28.2 | $7.0 | $0.53 | 62% | 8 | $-376 | none |
| single600_trig100 | -9.06% | -5.86% | -3.19% | -40.9%/yr | $25.2 | $6.3 | $3.98 | 63% | 60 | $-382 | cooldown 1363 (cooldown 0.12 d) |
| single1200_trig600 | -8.38% | -5.86% | -2.51% | -32.2%/yr | $22.8 | $5.7 | $0.13 | 37% | 2 | $-296 | none |
| full_range | -6.02% | -5.86% | -0.16% | -2.1%/yr | $2.1 | $0.5 | $0.00 | 0% | 0 | $-22 | none |

**Where the excess comes from (full window, baseline)**

| run | excess vs hold | = fees to holders | + inventory effect | - recenter gas | LVR vs delta-matched benchmark | fees - LVR - gas | maintenance gas (assumed) | excess after maintenance (annualised) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| single600_trig300 | $217.2 | $33.9 | $184.6 | $1.33 | $143.7 | $-111.1 | $51.6 | +6.7%/yr |
| full_range | $9.9 | $10.3 | $-0.4 | $0.00 | $21.8 | $-11.5 | $51.6 | -1.7%/yr |

End state of the baseline: idle USDG $9564, idle stock $0, max drawdown -1.66%, in range 100% of the time it held a position, RAM tokens accrued 222.5 (valued at zero).

**Sensitivities (full window, annualised excess vs hold / window excess)**

| run | what changes | excess (annualised) | excess (window) | recenters |
| --- | --- | --- | --- | --- |
| single600_trig100 | sensitivity: trigger 100 (more frequent recentering) | +7.8%/yr | +1.92% | 167 |
| single1200_trig600 | sensitivity: wider +/-1200 ticks (~12.7%), trigger 600 | +8.0%/yr | +1.97% | 6 |
| single600_trig300_noreserve | sensitivity: reserve cut 0 (shows what the 20% reserve costs holders) | +9.1%/yr | +2.26% | 20 |
| single600_trig300_gas040 | sensitivity: engine's conservative $0.40 per recenter | +8.5%/yr | +2.11% | 20 |
| single600_trig300_cap5k | sensitivity: maxRangedValueUsd = half of capital (rest stays idle) | +12.4%/yr | +3.07% | 20 |
| single600_trig300_histshare | sensitivity: historical protocol share (5% before 2026-09-30, then 50%) | +9.9%/yr | +2.45% | 20 |
| single600_trig300 @ $1,000 | dilution at a different size | +8.7%/yr | +2.14% | 20 |
| full_range @ $1,000 | dilution at a different size | +0.4%/yr | +0.10% | 0 |
| single600_trig300 @ $50,000 | dilution at a different size | +8.5%/yr | +2.10% | 20 |
| full_range @ $50,000 | dilution at a different size | +0.4%/yr | +0.09% | 0 |


## 5. What the baseline shows (scoped to these windows and this configuration)

* **SPY** (74.4 days, one calm sample, zero recenters: no trigger fired, so this is a static +/-6.2% range). Holders gained +0.82% over holding (+4.0%/yr) before maintenance; fees to holders $89.4, reserve cut $22.3, inventory effect -$6.9. Against the delta-matched benchmark, fees minus LVR minus gas is +$1.2: break-even. With the assumed maintenance cost ($42.7 over the window, about 0.43%) the excess is +2.0%/yr at $10,000. Last 28 days: +0.05% (+0.6%/yr), i.e. negative after maintenance. The last third was negative before maintenance. Full range earned $2.8 of fees: about nothing.
* **NVDA** (Ramses pool, 90 days). Headline +2.17% over holding (+8.8%/yr) is **not fee income**: fees to holders were $33.9 and the inventory effect was +$184.6. Fees minus LVR minus gas is **-$111.1** (-1.1% of capital in 90 days). With no swap at recenter, each redeploy takes only the matched value, so the NVDA side was used up during the rally and the position ended as $9,564 idle USDG (88% of capital idle on average). It was therefore mostly cash while NVDA fell. The result flips sign by window: first third -1.86% against holding, middle third -0.44%, last 28 days **-2.03% (-26%/yr)** with a worst week of -$376. Full range earned +0.10%.
* **Idle versus deployed capital** is the structural finding: matched-only redeploys leave the surplus side idle (SPY 2% idle because it barely moved; NVDA 88%). A half-capital cap on NVDA raised the headline (+12.4%/yr) only because it keeps both idle sides balanced longer; it is the same inventory artefact, not a better strategy.
* **Blocked maintenance.** No due recenter was blocked by a stale feed or closed session in any run (the guard paths are exercised by the unit tests, not by these windows; the SPY feed is older than 12 h about half of the time, so this is not a statement that it is always fresh). The only blocks were cooldown blocks at trigger 100 on NVDA (2,605 evaluations, 0.5 day).
* **Reserve and RAM.** The 20% reserve cost holders about 0.23% over the SPY window (+5.1%/yr without it). RAM accrued (883 tokens SPY, 222 NVDA) is valued at zero.
* **Assumed costs matter as much as the SPY result.** Maintenance is $0.5737/day regardless of size: 2.1%/yr of $10,000 but 0.42%/yr of $50,000. These are the Uniswap keeper's costs used as a proxy; Ramses vault costs were not measured.

**Scoped conclusion.** For the contract-feasible single-range configuration tested, fee income roughly equalled adverse selection on SPY and fell well short on NVDA; the positive NVDA headline versus holding is an inventory path that reverses in the latest window. This says nothing about lending reserves, borrow caps or liquidation capacity, and it is two short samples.

## 6. GLD

**Feed label (captured).** `gld/directory-feeds-robinhood-mainnet.json` is the raw response (SHA-256 `69056e59...1674`, fetched 2026-11-02T11:50:59Z, HTTP 200, `etag "0cf583b21207184301c0feee7492fc24"`, `last-modified 2026-11-02 08:50:42 GMT`; headers in `gld/directory-headers.txt`) from the exact URL **`https://reference-data-directory.vercel.app/feeds-robinhood-mainnet.json`**. `gld/directory-selected-feeds.json` holds the SPY, NVDA and GLD entries. For GLD (proxy `0x470A51258068043bd43dC0a56245625C9fE86eB0`, contract `0xeA3F8E77a98d7877b6F8164569a8e82454cCcDB8`): `docs.attributeType = "dex_state_price"`, `productSubType = "Reference"`, `marketHours = "Crypto"`, `assetClass = "Crypto"`, `assetSubClass = "Pegged Asset"`, `feedCategory = "new"`. SPY and NVDA are `attributeType "other"`, `productSubType "calculatedPrice"`, `marketHours "us_equities_24/5"`. Of the 58 directory entries, 11 are `dex_state_price` (BTC.B, WEETH, USDS, EURC, WSTETH, WBTC, LBTC, CBBTC, SYRUPUSDC, GLD, USDE); GLD is the only tokenized stock or commodity among them.

**What this establishes.** The directory classifies the GLD feed as a DEX-state reference price on 24/7 hours, unlike the equity feeds. **It does not identify the source pools, venues, weights or window, and it does not show the feed can be moved by trading.** The directory's metadata is the only direct statement about the source.

**Supporting observations (do not identify the source).** From `gld/gld_independence.json`, `gld_twap.json`, `gld_v4_compare.json`, `weekend_drift.json`: node-observation spread per report is 0.12% median for GLD against 0.002% for SPY; on 14 weekend reports the answer changed 13 times; the answer sits a median 0.022% from the Ramses GLD pool on weekends (0.044% median for a trailing 5-minute average over all reports; Uniswap v4 GLD pools 0.06 to 0.07%); the feed did not react when the Ramses GLD pool sat at MAX_TICK for 28.5 minutes on 2026-09-28, which argues against direct dependence on the spot price. Close price tracking and dispersion cannot identify source pools. `gld/large-file-hashes.txt` records the SHA-256 of the large local probe files (`transmissions.json`, `answer_updated.json`).

**Scoped statement about the tested LP strategies** (replacing "supports $0"): on the GLD pool, none of the tested LP configurations beat holding in the full window. All 90 full-window cells in the main grid (30 configurations x 3 scenarios) were negative (best: full range, -1.1%/yr); over the last 28 days 58 of 60 cells were negative (two hypothetical dual-range-with-swap cells were +1.3% and +4.1%/yr). In the robustness cuts non-negative cells appear only in the middle-third sub-window and in one counterfactual with the oracle guard off. Almost all of those configurations are hypothetical (section 3), and no contract-feasible single-range baseline was run on GLD in this checkpoint. This is a statement about the tested LP strategies and one sample. It is **not** a derived lending, margin or liquidation cap; GLD exit depth (about $7.7k per 1% move and $16.0k to a 5% down move at the depth pin, `depth_table.json`) is a separate input to any such cap.

## 7. Pins, hashes, keeper receipts, reproduction

`pins-and-receipts.json` holds full hashes and headers for every pin (all `l1BlockNumber` checks pass). Short form:

| Use | L2 block | Native (l1BlockNumber) | Block hash | Provenance |
| --- | --- | --- | --- | --- |
| Fee-entitlement fork tests (`logs/fork-final.log`, 3 pairs, 3 tests each, all pass) | 104,116,096 | 26,326,238 | `0x9211f82fbe82275c5ebd239719fa4aaef504a2fc857fc9397c55cb06de776cbb` | printed as `pin 104116096 26326238` in the log |
| Depth fork tests (`logs/fork-depth-all.log`, 3 pairs, all pass) | 104,096,178 | 26,326,064 | `0x878484914d4b76373cfb8a15e8208719b2ddf51d8373e29174464cbd1b0362f1` | **recovered**: the log prints only `DEPTH_BLOCK: 26326064`. The L2 number came from an earlier subagent report; its header's native number matches the log, but about a hundred consecutive L2 blocks share one native number, so this is consistent with, not derived from, the log |
| Keeper verification | 104,172,613 | 26,326,728 | `0xa98daed63190376b96b042414b59527f67a88fd20d53fb88ceedd1432bf8d439` | 2026-11-02T11:14:39Z |
| SPY dataset head | 104,071,867 | 26,325,854 | `0xbad7a6a33c1bec4a6f432e13b92bcf50edba64825420c41910bb666196175b0d` | `baseline_SPY.json` |
| NVDA dataset head / grid anchor | 104,187,711 / 104,180,000 | 26,326,860 / 26,326,793 | see `pins-and-receipts.json` | `baseline_NVDA.json`; 8 swaps after the anchor dropped |

Dataset SHA-256: SPY `d14c4633...ef0f`, NVDA `8dc9ee14...ef20` (full values and the SHA-256 of `engine.py`, `feasibility.py`, `run_baseline.py`, `bt_lib.py`, the feed and gauge inputs are in each `baseline_*.json`). The fork tests read `REMEDIATION_FORK_BLOCK` / `REMEDIATION_NATIVE_BLOCK`; a failed historical read fails the test.

**Keeper transactions (RPC receipts, status 1):** checkpoint `0xcad407716d6b7299784206d773d376ca75f6148d98d7c14b98cafc9f7403476d` (block 104,165,913, nonce 138, gas 523,686, effective gas price 20,094,000 wei, 0.00001052 ETH, 19 logs) and rebalance `0x7cb6988a23127109837531ecf2fdf2c3f4e95d499a4eb750ac8dac3116078031` (block 104,165,952, nonce 139, gas 735,438, 0.00001486 ETH, 31 logs), both from `0x978e2ADF18da8B846552932c0aB2B1193da1fAc4` to the production vault. Keeper nonce 140 and balance 0.005269526 ETH at block 104,193,993. **Mined reverted sends: none found.** The PR #13 evidence found nonce 138 equal to 138 transactions that left vault events; the two receipts above explain nonces 138 and 139, so no nonce is unexplained (a reverted transaction still consumes a nonce). The `CheckpointStale` and `execution reverted` lines in the keeper log are simulation or `estimateGas` rejections that were never mined: no receipt, no cost. Reverted-send gas therefore cannot be itemised and does not need to be.

**Commands (read-only, run from this directory with the shared cache):**

```
python3 -m unittest test_engine test_feasibility            # 47 tests
python3 mutate_engine.py                                      # 35 mutations, all killed
python3 load_pools.py NVDA && python3 build_dataset.py NVDA   # RPC-bound; SPY/GLD datasets were built earlier the same way
python3 run_baseline.py SPY && python3 run_baseline.py NVDA   # writes results/baseline_<PAIR>.json
python3 render_baseline.py SPY NVDA                           # the tables above
python3 classify_historical.py <dir with the historical jsonl> # historical-classification.json
python3 collect_evidence.py                                   # pins-and-receipts.json
```

Fork tests: `FOUNDRY_PROFILE=ramses forge test --match-path 'ramses-vaults/test/fork/Group*.t.sol'` with `REMEDIATION_FORK_BLOCK` and `REMEDIATION_NATIVE_BLOCK` set to a pin above (the public RPC keeps only minutes of state, so the recorded logs, not a rerun, are the evidence for those pins). Logs: `logs/`. Fee-entitlement result: protocol share exactly 50% on SPY, GLD and the NVDA control; LP collect equals (gross - protocol) x our liquidity share; RAM arrives only through `positionManager.getReward`.

## 8. Pool-selection requirements carried forward (handoff 01)

No separate document from the Ramses partner was found in this repository, the handoffs, the local notes or this session's record. What follows is what handoff 01 (sections 0, 1, 2) records as the operator's decisions and requirements; if the partner supplied more, it has not been carried forward and should be pointed to.

* Ramses concentrated-liquidity pools on CL1 (**tick spacing 1**): SPY/USDG listed at 0.01% and GLD/USDG at 0.015%. Identify pools **by address** (SPY `0x38453c115607463ac284820ce959831042f3df4e`, GLD `0x9896af2f6fed9939acc87637b973b6484e6619d8`), assert `tickSpacing() == 1`, read `fee()` at the pin, and **stop and report if it differs**. The fee is dynamic (`FeeAdjustment` events). Measured: SPY volume-weighted fee 104 ppm (listed 100); **GLD 228 ppm over 50 days against the listed 150**, while `fee()` read 150 ppm at the fork-test pin (`dataset_checks.json`, `logs/fork-final.log`). That difference needs an operator decision.
* Fee entitlement is verified per pool by a fork test that mints the exact configuration, swaps, calls `collect` and the manager reward route, and measures what reaches the vault (done for SPY, GLD and the NVDA control; protocol share 50%).
* Margin and liquidation caps come from measured depth, not pool TVL. GLD is thin (about $16.0k to a 5% down move), SPY healthier (about $67k down and $163k up to 5%), the NVDA control thinner than SPY (about $17.6k down, $12.0k up to 5%); all at the depth pin.
* **No feed, no market**; never substitute pool spot for SPY or GLD; the age limit and a session check fail closed. GLD's feed is labelled `dex_state_price` (section 6): whether it is acceptable is operator decision 1, not settled here.
* Enable allocation and margin **per pair**, only for pairs that pass fee entitlement, the profitable-capacity gate and the liquidation-capacity gate; SPY is the likely first, GLD supply-only or tightly capped.
* The Ramses NVDA pair (decision 8) has **tick spacing 10**, not 1: it fails the spacing-1 assertion as written, and half-widths must be multiples of 10 if it joins.

## 9. Direct r33 collateral exposure (kept separate)

r33 and RAM are not part of the SPY/GLD/USDG collateral set. Facts from handoff 01 section 10 (Ramses data of 2026-10-30; **not re-measured here**): neither has a Chainlink feed; both trade in DLMM (bin) pools that the adapter does not support, with no r33/RAM concentrated pool; the RAM side of the r33/RAM pools is about $40k and $2k. The group shares one controller with cross-collateral, so **any r33 market with a collateral factor above zero would make r33 count toward SPY/GLD/USDG borrowers' liquidity and put the group's suppliers behind a thin, unpriced asset.** Nothing here proposes that. The tiers are: 0, supply-only markets with collateral factor 0 and borrowing paused (essentially no exposure, to be confirmed by checking that redeem never prices an un-entered market); 1, LP yield with collateral factor still 0 (needs a bin adapter, a non-USD-pair vault and a manipulation-resistant price); 2, borrowing or margin (not recommended). Before tier 1, handoff 01 requires `research/R33-RAM.md`: who controls the r33 contract (owner, mint, pause, blacklist, transfer tax, read from bytecode and storage) and which price source is acceptable. That has not been done.

## 10. Open items

* Pool-selection requirements beyond handoff 01 (section 8) and the GLD fee difference.
* Operator decisions in handoff 01 section 13 remain open (feeds, range parameters, supply caps, collateral factors, r33/RAM tier).
* Not done: dividend/`uiMultiplier` fork test, a real signed recenter, a fee-model check against a third-party LP's Collect history, a contract-feasible baseline on GLD, RAM holder entitlement, Ramses-vault gas measurements, a longer or more volatile SPY sample (the SPY baseline never recentered).
* Wording to avoid: the baseline is two short samples of a modelled strategy, not a yield estimate and not a safety claim.
