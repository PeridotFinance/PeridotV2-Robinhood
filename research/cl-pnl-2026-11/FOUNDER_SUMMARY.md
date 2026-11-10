# Concentrated liquidity on volatile pairs: three findings that change what we build

10 November 2026. One page; the evidence is in `REPORT.md` (section numbers in brackets). Nothing was deployed or changed.

## 1. Who keeps the swap fee decides whether liquidity pays, and on our Avalanche pool the LP keeps none of it

| Venue | Share of the swap fee that reaches the LP | Fee income ÷ arbitrage loss, whole window | Latest 30 days |
| --- | --- | --- | --- |
| Pharaoh WAVAX/USDC | 0% (checked on chain) | 0 | 0 |
| Ramses NVDA/USDG and SPY/USDG | 50% since 8 October | 0.80 and 1.51 | 0.29 and 0.35 |
| Uniswap V3 NVDA/USDG 0.05% (where NVDA trades: $12M a day) | 75% | 1.27 | 1.03 |
| Uniswap v4 NVDA/USDG 0.30% (the pool the vault used) | 100% | 1.91 | 0.98 |

Liquidity on WAVAX/USDC lost about $2.0M to arbitrage in 101 days and was paid $1.07M of PHAR; the $1.72M of swap fees
went to voters. [4, 4.7, 4.9]

**What changes:** the range take-profit on WAVAX/USDC and on Ramses is an order type, not a way to earn. Remove any fee
or yield language from it. If fee income is ever the goal, the venue is a Uniswap pool, not Ramses or Pharaoh.

## 2. A band is not a limit order, and the difference is larger than the fee

- In 6% to 16% of take-profit orders on NVDA and WAVAX a limit order at the same price would have filled and the band did
  not, because the price turned back before crossing the whole band. Those orders ended 3% to 7% of the order behind.
- Fees kept by the user: nothing on Pharaoh, 10 to 19 bp per order on Ramses NVDA.
- On SPY the shipped presets (1%, 3%, 5% away, 2% wide) filled in 0 of 302 seven-day orders. [6]

**What changes:** presets per asset (SPY: 30, 60, 100 ticks away and 50 wide; WAVAX: width 100 instead of 200), and the
product must say before placement that the order completes only if the price passes the far edge.

## 3. Nothing we tested makes symmetric LP reliably positive, and more than a third of the live loss was operational

- **Geometry:** no half-width and trigger is positive in every 30-day window on NVDA, Ramses SPY or WAVAX. [5]
- **Hedged mode:** +1.2% to +1.9% before hedge costs and −1.4% to −3.1% after them over 117 days, at $1,000 and at
  $10,000; it also cannot be opened today. [7]
- **Session rules:** wider ranges over the weekend beat fixed width in 0 of 34 windows. [9]
- **The live −2.9%:** the ±2,400 policy was stored on 19 October but the old ±1,200 range stayed in the pool until
  2 November. A ±2,400 range placed on the day would have ended at −1.6%. [3]

**What changes:** do not build the hedged D order or a session rule. Do make a policy change move the range, and alarm
when the range in the pool is not the policy's.

## Not settled

- Question 7 (Blackhole WAVAX/USDC against Pharaoh) is still being measured.
- The position engine has not been run on the Uniswap V3 0.05% NVDA pool, the one venue whose numbers might support a
  pilot.
- Everything rests on one 100 to 130 day history in which fee income per day fell by about half or more on every pool.
