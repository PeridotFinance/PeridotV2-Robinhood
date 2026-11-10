# Concentrated-liquidity P&L on non-stable pairs: NVDA/USDG, SPY/USDG, WAVAX/USDC

Research report for Peridot's lead adviser, 10 November 2026. Read-only: nothing was deployed, signed or changed.
Everything below is rebuilt from the tables in `data/` by `scripts/run_all.sh`.

**Status: sections 2 to 4 (method, premises, fee versus LVR) are final. Sections 5 to 9 (geometry, bands, hedged mode, reward timing, session-aware width) are being filled from runs in progress.**

## 1. Verdicts

| # | Question | Verdict |
| --- | --- | --- |
| 1 | Fee versus LVR by regime | Fee income covers arbitrage loss only where the LP keeps the fee: about 1.9 times the loss on the Uniswap v4 0.30% NVDA pool over 130 days, 0.8 on Ramses NVDA where half the fee goes to the protocol, and zero on Pharaoh WAVAX/USDC where all of it does. On every stock pool the ratio is lowest in regular hours and highest at the weekend, and it falls as volatility rises. In the latest 30 days it is about 1.0 on v4 and 0.3 on Ramses. |
| 2 | Range geometry | Pending. |
| 3 | One-sided bands | Pending. |
| 4 | Hedged mode at scale | Pending. |
| 5 | Reward timing | Pending. |
| 6 | Session-aware allocation | Pending. |

## 2. Method, stated once

### 2.1 Data

| Pool | Address or id | Fee paid by swappers | LP share of the fee | Swaps | Window (UTC) |
| --- | --- | --- | --- | --- | --- |
| Uniswap v4 NVDA/USDG | pool id `0x3bb34a44…4bf1` | 0.30% LP fee, plus a protocol fee on top (0.35% in total on 58% of swaps) | all of the 0.30% | 205,833 | 2 Jul to 10 Nov |
| Uniswap v4 SPY/USDG | pool id `0xfe2a80bb…26cd` | same | same | 544,277 | 14 Jul to 10 Nov |
| Ramses NVDA/USDG (CL10) | `0xdac1904d…321b` | dynamic, 70 to 2,460 ppm, usually 100 to 300 | 95% until 8 Oct, 50% since | 363,159 | 4 Aug to 10 Nov |
| Ramses SPY/USDG (CL1) | `0x38453c11…df4e` | dynamic, usually 100 to 150 ppm | 95% until 8 Oct, 50% since | 691,804 | 19 Aug to 10 Nov |
| Pharaoh WAVAX/USDC (CL10) | `0xf01449C0…9534` | dynamic, 450 to 56,000 ppm, 850 at the end | **0%** | 933,549 | 1 Aug to 10 Nov |

- **Source.** Every swap, mint, burn and fee-change event of each pool, from public RPC logs, to a fixed end block
  (Robinhood Chain 110,790,000; Avalanche 99,924,388). Oracle rounds, gauge reward notifications and reward-token pool
  swaps come the same way. External reference prices (Binance AVAXUSDT minutes; Yahoo NVDA and SPY bars including
  pre-market and after-hours) are saved raw in `data/external/` because those sources expire.
- **Completeness checks.** For each table: ascending order with no duplicate log; the v3 swap equation reproduces each
  swap's amounts from the previous swap's price (more than 99.99% of eligible swaps to within 2 raw units); the last
  swap's price, tick and liquidity equal the pool's own state at the end block; liquidity rebuilt from mints and burns
  matches the swap events at 400 sampled points. The Avalanche pool was fetched twice from different endpoints and
  compared log for log (1,167,991 logs, no difference). Results are in `data/robinhood/state/checks.json` and
  `data/avalanche/checks.json`.
- **Not pulled.** The Uniswap v4 0.01% NVDA pool (id `0x6444a8e0…29c5`), which has more volume than the 0.30% pool the
  vault used, and the Uniswap V3 pools. Statements about "v4" below are about the 0.30% pools only.
- **Archive state.** None was available on Robinhood Chain (the public node keeps minutes of state). Everything is
  rebuilt from logs and checked against state at the end block, so no claim here depends on historical state.

### 2.2 The ledger

One portfolio ledger per position, in USD with USDG and USDC at $1:

- **Start equity:** the deposit at the oracle price.
- **During:** the position's tokens inside the range, idle tokens beside it, swap fees as they accrue (after the pool's
  protocol share and, for a vault, the 20% reserve cut), borrow interest, swap costs of any hedge, gas.
- **End equity:** the same inventory at the oracle price, or at the pool price when the feed is stale, with both sides
  of every comparison valued at the same price.
- **Benchmark:** always holding the deposited tokens untouched. Dollar P&L and percentage return are reported
  separately; there are no flows after the start, so time-weighted and money-weighted returns coincide.
- **Rewards (RAM, PHAR):** never inside a return. They are a separate line, in tokens and in USD "if sold" at the
  token's pool price in the week earned, less a stated sale cost. Whether a mechanism exists to sell or distribute them
  is stated per venue.
- **LVR:** only a decomposition. For a position it is the shortfall against a portfolio that holds the same stock
  quantity at every sample and trades at the pool price without fees; it is reported next to fees and never subtracted
  from a return a second time. The identity (equity change = benchmark P&L − LVR + fees − gas) is asserted in every run.

### 2.3 Fee income and arbitrage loss on real swaps (question 1)

For a swap in which the pool's liquidity receives `b` stock and `u` dollars (signed, fee removed):

- **Fee income** is the input amount times the fee in force, times the LP share in force.
- **Arbitrage loss at horizon h** is `−(b × price(t + h) + u)`: what the liquidity's own trade is worth when its
  inventory change is valued at the pool's mid price h later. At h = 0 this is exactly the shortfall against a
  delta-matched rebalancing portfolio at swap resolution. Longer horizons let uninformed flow revert and let a slow pool
  catch up with the market; they also add inventory noise.
- **Horizons reported:** +1 minute and +1 hour, with the full curve from 0 to 24 hours shown once.
- **Cross-check against the outside market.** On the same swaps, valuing at the external price one hour later gives the
  same loss as valuing at the pool's own price one hour later: v4 NVDA $9,791 against $9,835; Ramses NVDA $4,414 against
  $4,104; Ramses SPY $5,846 against $5,383; Pharaoh $2.07M against $2.06M (stock pools: swaps since 12 October with a
  fresh minute bar; Pharaoh: whole window). v4 SPY is the loosest, $49,186 against $34,180.
- **Sessions** are New York time: regular 09:30 to 16:00; extended 04:00 to 09:30 and 16:00 to 20:00; overnight 20:00
  to 04:00 on trading nights; weekend from Friday 20:00 to Sunday 20:00. Labor Day (7 September) counts as weekend.
- **Volatility buckets:** each unbroken run of one session (one regular session, one overnight, one weekend) gets its
  own realised volatility from 5-minute pool prices; runs are split into quarters of time within each pool.
- **Forecast.** The naive loss is σ²/4 × L√P per unit time (σ²/8 of value for a full range). The fee-adjusted forecast
  multiplies it by P_trade = 1 / (1 + γ / (σ√(Δt/2))) (Milionis, Moallemi, Roughgarden 2023), with γ the fee and Δt the
  block time (0.1 s on Robinhood Chain, 1 s on Avalanche). It is shown beside the measurement and never fitted to it.

### 2.4 The position engine (questions 2, 4, 5, 6)

The range engine is the one built and mutation-tested for the earlier Ramses study, vendored unmodified
(`scripts/vendor/`). It replays a pool's real swaps against the vault contract's rules:

- one range centred on the oracle tick;
- a recentre when the oracle has moved by the trigger or left the range;
- **no swap at a recentre:** only matched value is redeployed and the surplus token stays idle;
- 1 hour cooldown and a maximum number of recentres per rolling day;
- a recentre that would lose more than 10 bp of pair value at the oracle is refused;
- nothing while the feed is older than 12 hours or the primary market is shut; the keeper looks every 300 seconds;
- fees are diluted by our own liquidity (observed volume held fixed).

**Reproduction of the live result.** Replaying the live NVDA/USDG pair from the swap table alone, 19 October to
6 November, gives fees of $0.186 against $0.189 collected on chain, an end value of $17.75 against $17.69, and −2.67%
against holding versus −2.94% actual (`out/gate1_reproduce_live.json`). The replay has to start from the range that was
really in the pool: see section 3.

### 2.5 Limits that apply to every number

- **One history.** About 100 to 130 days per pool, in which NVDA fell about 7%, SPY rose about 2% and AVAX rose about
  31%. Rolling 30-day windows overlap heavily and are not independent samples.
- **Fixed flow.** Our liquidity is assumed not to change who trades or how much; adding it only dilutes the fee.
- **Fee income is falling.** LP fee income per day in the last two weeks is $427 on v4 NVDA against $1,182 for the whole
  window, $31 against $160 on Ramses NVDA and $71 against $286 on Ramses SPY. No figure here is a projection.
- **Early closes** (13:00 New York) are treated as normal days.

## 3. Premises in the brief that the chain does not support

These change what several questions can mean, so they come before the results.

1. **The live position was not ±2,400 for most of its window.** The 2,400 / 600 policy was stored on 19 October
   (block 92,657,111), but the range in the pool stayed the earlier one, ticks 221340 to 223800 (±1,200), until the first
   recentre under the new policy on 2 November, after the 10.8-day keeper outage. A stored policy does not move an
   existing range; only a trigger does. Had a ±2,400 range been placed on 19 October, the replay gives −1.60% against
   holding instead of −2.67%, with fees of $0.141 instead of $0.186.
2. **The live Uniswap v4 NVDA/USDG vault holds no liquidity.** It was unwound on 9 November and allocation is paused.
3. **Liquidity providers on Pharaoh WAVAX/USDC receive no swap fee.** The pool's protocol share is 1,000,000 ppm. Checked
   directly: over about 940 blocks with swaps the pool's gross fee counter rose and its LP fee counter did not move
   (`data/facts/pharaoh-wavax-usdc-lp-fee-check.json`). LP income there is PHAR emissions only.
4. **Neither live Pharaoh vault holds a WAVAX/USDC position.** They hold USDC/USDt and sAVAX/WAVAX. WAVAX/USDC is the
   pool the margin stack and the range take-profit prototype use.
5. **No Peridot position has earned RAM on mainnet,** and the Ramses vault is not deployed. PHAR accrued to the two
   Pharaoh vaults (2.5 and 0.9 PHAR) has never been harvested. Question 5 can only be answered from the gauge's rules.
6. **A hedge cannot be opened today.** Robinhood margin caps are $2 of position and $1 of debt, about 0.032 NVDA is
   lendable, there is no SPY borrow market, and WAVAX borrowing is paused on Avalanche with no short pair.

Sources and line references for each are in `data/facts/MECHANICS.md`.

## 4. Question 1: fee versus LVR by regime

**Verdict.** Fee income exceeds arbitrage loss where the LP keeps the fee and the market is calm or shut, and nowhere
else. Over the whole window the ratio is 1.9 on the v4 0.30% NVDA pool, 4.6 on v4 SPY, 0.8 on Ramses NVDA, 1.5 on Ramses
SPY and 0 on Pharaoh WAVAX/USDC. In the latest 30 days it is 0.98, 1.14, 0.29, 0.35 and 0.

![Fee income divided by arbitrage loss by session](charts/q1_ratio_by_session.png)

### 4.1 Whole window, all liquidity in the pool

| Pool | Days | Volume a day | Swap fee paid | LP fee income | Loss at +1 min | Loss at +1 h | Fee ÷ loss (+1 min) | Fee ÷ loss (+1 h) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Uniswap v4 NVDA/USDG | 130 | $400,838 | $172,455 | $156,076 | $26,622 | $81,900 | 5.86 | 1.91 |
| Uniswap v4 SPY/USDG | 119 | $1,000,305 | $415,211 | $355,620 | $10,629 | $76,666 | 33.46 | 4.64 |
| Ramses NVDA/USDG | 98 | $1,736,059 | $24,016 | $15,803 | $15,306 | $19,871 | 1.03 | 0.80 |
| Ramses SPY/USDG | 82 | $3,340,822 | $28,882 | $24,000 | $13,745 | $15,936 | 1.75 | 1.51 |
| Pharaoh WAVAX/USDC | 101 | $22,261,920 | $1,718,851 | $0 | $1,800,003 | $2,003,077 | 0 | 0 |

- **The fee tier and who keeps it decide the sign.** Ramses trades four to eight times the v4 volume in the same
  stocks, but at about 1 bp instead of 30 bp its liquidity collects a tenth of the fees for a comparable loss.
- **Ramses under today's rules is worse than its whole-window figure.** Until 8 October LPs kept 95% of the fee; since
  then 50%. From 9 October the ratio at +1 hour is 0.30 on NVDA and 0.37 on SPY. Applying the 50% share to the whole
  window would give 0.60 and 0.90.
- **Pharaoh:** swappers paid $1.72M, which is 0.86 of the liquidity's loss, and all of it went to the protocol side.
- **The 0.30% pools need the long horizon.** A pool with a 0.35% cost to trade sits up to that far from the market, so
  one minute after a swap its own price has not yet moved; the +1 minute ratios of 5.9 and 33 overstate. The +1 hour
  figures are the ones to use for v4, and section 4.5 shows they keep falling out to 24 hours (1.44 and 1.82).

### 4.2 By session (fee ÷ loss at +1 hour)

| Pool | Regular | Extended | Overnight | Weekend |
| --- | --- | --- | --- | --- |
| Uniswap v4 NVDA/USDG | 1.18 | 2.26 | 5.24 | 5.96 |
| Uniswap v4 SPY/USDG | 2.30 | 5.07 | 10.05 | 41.21 |
| Ramses NVDA/USDG | 0.54 | 0.64 | 0.89 | 2.00 |
| Ramses SPY/USDG | 1.13 | 1.34 | 1.92 | 3.16 |
| Pharaoh WAVAX/USDC | 0 | 0 | 0 | 0 |

Dollars a day, LP fee income / loss at +1 hour:

| Pool | Regular | Extended | Overnight | Weekend |
| --- | --- | --- | --- | --- |
| Uniswap v4 NVDA/USDG | $2,626 / $2,217 | $1,188 / $525 | $1,085 / $207 | $407 / $68 |
| Uniswap v4 SPY/USDG | $5,026 / $2,185 | $2,848 / $562 | $2,218 / $221 | $2,413 / $59 |
| Ramses NVDA/USDG | $237 / $436 | $138 / $215 | $140 / $158 | $152 / $76 |
| Ramses SPY/USDG | $530 / $470 | $270 / $201 | $261 / $136 | $185 / $59 |
| Pharaoh WAVAX/USDC | $0 / $26,363 | $0 / $19,829 | $0 / $18,989 | $0 / $16,076 |

- **Regular hours carry the loss.** They are 19% of the week and 66% of the v4 NVDA loss, 65% of v4 SPY, 41% of Ramses
  NVDA and 46% of Ramses SPY.
- **The weekend is where liquidity is paid best for the risk it takes while there:** the loss per day is 3% to 17% of
  the regular-hours loss while fee income per day is 16% to 64% of it. This is why withdrawing over weekends recovered
  nothing. It says nothing about the gap at Monday's open: that shows up as loss in Monday's extended and regular
  sessions, where it is counted.
- **WAVAX has no session effect in the loss** (25%, 28%, 23%, 24% of the loss across the four clocks), as expected of a
  24-hour asset.

### 4.3 By realised volatility (fee ÷ loss at +1 hour; the quarter's annualised volatility in brackets)

| Pool | Calmest quarter | Second | Third | Most volatile |
| --- | --- | --- | --- | --- |
| Uniswap v4 NVDA/USDG | 8.81 (4%) | 3.73 (10%) | 3.12 (20%) | 1.29 (51%) |
| Uniswap v4 SPY/USDG | 205 (0%) | 20.2 (2%) | 4.14 (4%) | 1.41 (22%) |
| Ramses NVDA/USDG | 3.44 (7%) | 1.14 (13%) | 0.59 (24%) | 0.47 (57%) |
| Ramses SPY/USDG | 3.98 (4%) | 1.84 (7%) | 2.00 (10%) | 0.96 (19%) |
| Pharaoh WAVAX/USDC | 0 (36%) | 0 (50%) | 0 (67%) | 0 (125%) |

![Fee income divided by arbitrage loss by volatility quarter](charts/q1_ratio_by_vol.png)

On Ramses NVDA fees cover the loss only in the calmer half of the time (below about 18% annualised). On v4 they cover
it in every quarter, by a margin that shrinks from nearly 9 times to 1.3.

### 4.4 Rolling 30-day windows (one-day step; fee ÷ loss at +1 hour)

| Pool | Windows | Lowest (start) | Highest (start) | Share above 1.0 | Latest (start) |
| --- | --- | --- | --- | --- | --- |
| Uniswap v4 NVDA/USDG | 103 | 0.95 (8 Oct) | 2.62 (4 Sep) | 94% | 0.98 (12 Oct) |
| Uniswap v4 SPY/USDG | 91 | 1.14 (12 Oct) | 33.9 (1 Aug) | 100% | 1.14 (12 Oct) |
| Ramses NVDA/USDG | 70 | 0.28 (8 Oct) | 2.34 (9 Aug) | 41% | 0.29 (12 Oct) |
| Ramses SPY/USDG | 55 | 0.35 (12 Oct) | 3.52 (21 Aug) | 55% | 0.35 (12 Oct) |
| Pharaoh WAVAX/USDC | 73 | 0 | 0 | 0% | 0 |

The lowest window on every stock pool is the most recent one. Two things coincide in it: the highest volatility of the
sample (NVDA fell 14.5% between 19 October and 6 November) and, on Ramses, the cut of the LP share to 50%.

![Cumulative LP fee income and arbitrage loss](charts/q1_cumulative.png)

### 4.5 Horizon sensitivity (fee ÷ loss when each swap is valued h later)

| Pool | At the swap | +1 min | +5 min | +1 h | +4 h | +24 h |
| --- | --- | --- | --- | --- | --- | --- |
| Uniswap v4 NVDA/USDG | 10.14 | 5.86 | 3.46 | 1.91 | 1.69 | 1.44 |
| Uniswap v4 SPY/USDG | 86.4 | 33.5 | 17.1 | 4.64 | 2.69 | 1.82 |
| Ramses NVDA/USDG | 1.14 | 1.03 | 0.93 | 0.80 | 0.69 | 0.46 |
| Ramses SPY/USDG | 1.35 | 1.75 | 1.76 | 1.51 | 1.17 | 1.16 |

![Arbitrage loss relative to fees by horizon](charts/q1_horizon.png)

- The verdict for each pool does not change sign between +1 hour and +24 hours, except Ramses NVDA, which is at 1.03
  at one minute and below 1 from five minutes on.
- Past a few hours the measure picks up the drift of whatever inventory the trend left with the liquidity, which a
  delta-matched benchmark carries too. That is why the +1 hour figure is the one used, and why the +24 hour column is a
  bound, not an estimate.

### 4.6 Forecast beside measurement

| Pool | Realised volatility | Mean P_trade | Naive LVR | Fee-adjusted forecast (LVR × P_trade) | Measured loss, +1 min | Measured loss, +1 h | LP fee − loss (+1 h) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Uniswap v4 NVDA/USDG | 28% | 0.23% | $34,186 | $200 | $26,622 | $81,900 | +$74,177 |
| Uniswap v4 SPY/USDG | 11% | 0.06% | $16,283 | $23 | $10,629 | $76,666 | +$278,954 |
| Ramses NVDA/USDG | 33% | 4.25% | $17,382 | $1,236 | $15,306 | $19,871 | −$4,068 |
| Ramses SPY/USDG | 12% | 3.44% | $15,351 | $694 | $13,745 | $15,936 | +$8,064 |
| Pharaoh WAVAX/USDC | 77% | 10.5% | $2,186,140 | $262,786 | $1,800,003 | $2,003,077 | −$2,003,077 |

- **The naive LVR formula is a good measure of the loss, and a bad forecast of the result.** On the three pools that
  track the market closely it matches the measured loss within about 20% (Ramses NVDA $17.4k against $15.3k to $19.9k;
  Ramses SPY $15.4k against $13.7k to $15.9k; Pharaoh $2.19M against $1.80M to $2.00M).
- **The fee-adjusted figure is one to two orders of magnitude below the measured loss, and that is the finding.** It is
  a statement about a different quantity: what arbitrageurs keep after paying the fee, which on a fast chain is almost
  nothing. The loss itself is still there; the model says the fee hands it back. Whether the LP gets it back depends on
  who receives the fee, which the formula does not contain. A working rule that fits all five rows:
  **LP result against rebalancing ≈ (LP share of the fee) × fees paid − LVR.**
- On v4 the measured +1 hour loss is two to five times the naive figure because volatility computed from a 0.30% pool's
  own sticky price understates the market's, and because an hour of trend is in the measure (section 4.5).

### 4.7 Rewards beside fees and loss (whole pool, full weeks only, valued if sold at that week's price)

| Pool | Weeks | Reward tokens to the gauge | Value if sold | LP fee income | Loss at +1 h | (Reward + fee) ÷ loss | Weeks above 1.0 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Pharaoh WAVAX/USDC | 13 | 17,235,664 PHAR | $1,072,020 | $0 | $1,923,592 | 0.56 | 1 of 13 |
| Ramses NVDA/USDG | 6 | 222,988 RAM | $8,921 | $9,114 | $12,967 | 1.39 | 4 of 6 |
| Ramses SPY/USDG | 6 | 108,103 RAM | $4,185 | $4,814 | $8,138 | 1.11 | 4 of 6 |

- These are ceilings for the pool as a whole: no sale cost is taken, and the reward token's own pool could not absorb
  the whole emission. Position-level figures with a sale cost are in sections 5 and 7.
- **Pharaoh:** emissions paid for 56% of the liquidity's loss. The fee that would have paid for the other half is paid
  to voters.
- **Ramses:** RAM emissions began in the week of 24 September. Two thirds of the NVDA gauge's RAM came in one week
  (150,000 RAM on 8 October); the gauge now receives about 6,000 RAM a week, worth about $190.
- For a Peridot vault RAM and PHAR are worth nothing to suppliers today: neither vault can sell or distribute them
  (`data/facts/MECHANICS.md` sections 3.4 and 4.5).

### 4.8 Recommendation

| Lever | Decision | Parameters |
| --- | --- | --- |
| Symmetric LP on Pharaoh WAVAX/USDC | **Reject.** Not with this venue's fee split: LPs are paid no swap fee and emissions covered 56% of the loss. | none |
| Symmetric LP on Ramses NVDA/USDG or SPY/USDG as a fee product | **Reject** while the LP share is 50%: fee ÷ loss has been 0.30 and 0.37 since 9 October. | none |
| Symmetric LP on a 0.30% Uniswap v4 pool | **Do not sell as yield; keep as the venue for any pilot.** It is the only venue where fees have covered the loss (94% and 100% of 30-day windows), and the margin is gone in the latest month (0.98 and 1.14). Sections 5 and 9 give the geometry and the session rule. | see sections 5 and 9 |
| Use the naive LVR formula as a risk number | **Adopt** as the loss estimate per unit of liquidity, with volatility taken from a fast pool or the outside market, never from a 0.30% pool's own price. | σ²/4 × L√P |
| Use LVR × P_trade as a forecast of LP loss | **Reject.** It forecasts arbitrageur profit. | none |
