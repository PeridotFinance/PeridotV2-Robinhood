# Concentrated liquidity on volatile pairs: three findings that change what we build

10 November 2026. One page; the evidence is in `REPORT.md` (section numbers in brackets). Nothing was deployed or changed.

## 1. Who keeps the swap fee decides whether liquidity pays, and on our Avalanche pool the LP keeps none of it

| Venue | Share of the swap fee that reaches the LP | Fee income ÷ arbitrage loss, whole window | Latest 30 days |
| --- | --- | --- | --- |
| Pharaoh WAVAX/USDC | 0% (checked on chain) | 0 | 0 |
| Ramses NVDA/USDG and SPY/USDG | 50% since 8 October | 0.80 and 1.51 | 0.29 and 0.35 |
| Uniswap V3 NVDA/USDG 0.05% (where NVDA trades: $12M a day) | 75% | 1.27 | 1.03 |
| Uniswap v4 NVDA/USDG 0.30% (the pool the vault used) | 100% | 1.91 | 0.98 |

Over the 13 full weeks measured, liquidity on WAVAX/USDC lost $1.92M to arbitrage and was paid PHAR worth $1.07M if
sold; the swap fees ($1.72M over the whole 101 days) went to voters. [4, 4.7, 4.9]

**What changes:** the range take-profit on WAVAX/USDC and on Ramses is an order type, not a way to earn. Remove any fee
or yield language from it. If fee income is ever the goal, the venue is a Uniswap pool, not Ramses or Pharaoh.

## 2. A band is not a limit order, and the difference is larger than the fee

- In 6% to 16% of seven-day take-profit orders on NVDA and WAVAX a limit order at the same price would have filled and
  the band did not, because the price turned back before crossing the whole band. Valued when the order expired, those
  orders were 2% to 9% of the order behind (1% to 4% for two-day orders).
- Fees kept by the user: nothing on Pharaoh, 10 to 19 bp per order on Ramses NVDA.
- On SPY the shipped presets (1%, 3%, 5% away, 2% wide) filled in 0 of 302 seven-day orders. [6]

**What changes:** presets per asset (SPY: 30, 60, 100 ticks away and 50 wide; WAVAX: width 100 instead of 200), and the
product must say before placement that the order completes only if the price passes the far edge.

## 3. Nothing we tested makes symmetric LP reliably positive, and more than a third of the live loss was operational

- **Geometry:** no half-width and trigger is positive in every 30-day window on NVDA (three venues), Ramses SPY or WAVAX. [5]
- **Hedged mode:** +1.2% to +1.9% before hedge costs and −1.4% to −3.1% after them over 117 days, at $1,000 and at
  $10,000; a cheaper hedge route through Uniswap V3 still ends at −1.3% to −2.5%. It also cannot be opened today. [7]
- **Session rules:** wider ranges over the weekend beat fixed width in 0 of 34 windows. [9]
- **The live −2.9%:** the ±2,400 policy was stored on 19 October but the old ±1,200 range stayed in the pool until
  2 November. A ±2,400 range placed on the day would have ended at −1.6%. [3]

**What changes:** do not build the hedged D order or a session rule. Do make a policy change move the range, and alarm
when the range in the pool is not the policy's.

## Added after acceptance (questions 7 and 8)

- **Blackhole is not an alternative to Pharaoh for us.** It pays LPs no swap fee either, its WAVAX/USDC pool is a
  fifteenth to a twenty-fifth as deep, and a position that is not staked earns nothing. Its emissions did cover its
  LPs' arbitrage loss (1.14 times over 13 weeks, against 0.56 on Pharaoh). [10]
- **Full range is the safest symmetric geometry and still not a product.** On NVDA it was at or above holding in 66% to
  83% of 30-day windows, never more than 0.8% behind, and paid holders about 1% a year. A reserve that makes depositors
  whole in 95% of windows would cost 0.4% to 0.6% of deposits a year; the 20% fee cut has not covered that on NVDA since
  August. [11]

## Not settled

- Everything rests on one 100 to 130 day history in which fee income per day fell by about half or more on every pool.
