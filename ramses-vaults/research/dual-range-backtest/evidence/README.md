# Ramses group Phase 0: research brief and evidence index

Research and measurement only (handoff 01 Phase 0). No production contract, deployment script, cap or configuration is changed by anything in this directory, and no phase is advanced. Everything here was produced read-only from the public RPC and local forks; nothing was signed or sent. This README is the only Markdown published for this work, so it carries the brief, the corrections and the index of evidence files. Neither copy of handoff 00 is edited; where handoff 01 is out of date this README says so instead of changing it.

## 1. Corrections to earlier statements (read first)

1. **"A plain +/-5% single range does about as well with none [no contract change]" was wrong.** The backtest's `single_pm5` is a 500-tick half-width; `RamsesRangeLib.validatePolicy` requires `600 <= halfWidthTicks <= 4800`. None of the 872 historical rows that use a recenter policy passes the contract's rules (section 6). The nearest deployable single range is 600 ticks (about +/-6.2%).
2. **"GLD's Chainlink feed ... is not an independent price" was not established.** Chainlink's reference-data directory labels the GLD feed `attributeType: dex_state_price`, and its node observations disagree far more than SPY's. Neither fact identifies which pools or weights the feed uses, and no causal test was run (section 8).
3. **"GLD supports $0" is withdrawn as worded.** Scoped statement: in the main grid on the GLD pool, all 90 full-window cells and 58 of 60 last-28-day cells had negative excess return against holding (two hypothetical dual-range-with-swap cells were slightly positive in the last 28 days, and the sub-window cuts have non-negative cells in the middle third), and almost all of those configurations are hypothetical. That is not a derived lending, margin or liquidation cap.
4. **SPY's "0.015%" fee quote is a real fee state, not an error and not the current fee.** `fee()` on SPY/USDG CL1 is dynamic: 100 ppm at the pins, but 150 ppm for 16.2% of the last 30 days (last in force 2026-10-30 20:03Z).
5. **The earlier quote walker mishandled zero-liquidity gaps, and the r33 CL200 conclusion built on it was wrong.** The earlier text said sells of more than about $50 of r33 "exhaust the scanned window". On the deployed pool, such a sell is *filled*: the price falls through the empty range for free and the whole input is consumed at collapsing prices. Selling about $1,000 of r33 returns $327 and anything larger returns the same $327. The walker is corrected (`quote_math.py`) and validated against the deployed pools on a fork (section 3).
6. **The old `roundTripBps` added two independent one-way quotes.** It is renamed `sumOfIndependentOneWayQuotesBps` and a true sequential round trip (buy, then sell what was bought, in the same evolving pool state) is added. A sequential round trip hands the price impact back, so it measures the cost of reversing at once, not the cost of exiting a position.
7. **Scope of the r33 conclusion.** It is limited to the quoted routes in section 4. The CL200 route is not executable at any useful size; other quoted routes can absorb larger sales (about 2% to 3% shortfall *relative to the CL200 spot*, composite and non-atomic). Activation stays blocked until a complete route is executed atomically at one fork block with real fees, slippage limits and adapter requirements, and a defensible price source exists.
8. All historical numbers are preserved unchanged in `historical-hypothetical/` and labelled hypothetical.

## 2. Partner requirements and partner-supplied information

The operator's link to the partner's page returns HTTP 403 to this tooling, so the page itself was not read. The items below are the operator's summaries of the partner's messages; **partner-supplied information is marked as such and has not been verified here.**

| Item | Status |
| --- | --- |
| Select existing Ramses pools by depth and total execution cost at our intended size, not by defaulting to CL1 | Done as measurement (section 3). Handoff 01 section 0 ("use CL1 pools") is superseded by the selection rule; nothing is chosen on the operator's behalf |
| "CL" means tick spacing; the fee must be checked separately | Done: spacing and `fee()` are read separately at each pin, and the fee history is reconciled in section 5 |
| Candidates: NVDA/USDG CL10, META/USDG CL50, SPY/USDG CL1 at 0.015% | All read at pinned blocks. SPY CL1's quoted 0.015% is a fee state the pool is sometimes in (section 5) |
| For r33/USDG evaluate CL200 first, including its quoted 2% fee; compare execution and range-management needs rather than defaulting to CL500 or DLMM200 | Section 4: CL200, CL500, CL5, DLMM bin200, the legacy pair and two-hop routes via r33/RAM, all quoted. Range-management needs are in section 10 |
| r33 is reportedly a transferable ERC-20/OFT; its underlying staking vault and redemption functions live on HyperEVM | **Partner-supplied, pending verification.** Not checked here (no HyperEVM or contract-control evidence was gathered) |
| The partner's goal is borrowing against r33 while retaining r33 exposure; our design is direct collateral with separately optional LP participation | Section 11. The baseline shows why they must be separate: matched-only recentering converts inventory |
| Robinhood pricing and liquidation must work locally; HyperEVM redemption is not an atomic fallback | A requirement, not verified. Measured consequence: several local routes can absorb r33 sales (section 4), but r33 has no Chainlink feed and no local price source is established |
| Explain withdrawals and principal when an optional LP becomes one-sided | Section 10 |
| Show where entries, exits and liquidations use Ramses; distinguish funded in-kind liquidation, which need not swap | Section 9 |

Handoff 01 section 10 says r33/USDG is dropped and there is no r33 concentrated pool. As of the gateway snapshot saved here (1,043 pools, `evidence/pools/gateway-fetch-time.txt`) that is out of date: r33/USDG exists at CL5, CL200 and CL500 (plus a DLMM bin200 and a legacy pair), and r33/RAM at CL10, CL100, CL200 and two DLMM pools. The handoff is not edited.

## 3. Candidate pools: pinned facts, validated quotes, depth

**Method.** `pool_quote.py` reads each pool at one recent block (the public RPC keeps about 9 minutes of state): token order, `tickSpacing()`, `fee()`, the full `slot0` words, `liquidity()`, every `tickBitmap` word in the scanned range and every initialized tick's `(liquidityGross, liquidityNet)`. **All of it is saved** under `pools/inputs/` (block number, hash and native number included), and `quote_offline.py --check` recomputes every table below from those files with no network, so the quotes can still be replayed after the RPC can no longer serve the state. Scan "full" reads the entire bitmap, so running out of liquidity is a capacity statement; scan "window" (tick-spacing-1 pools, +/-5,000 ticks) can only say the window ended. Costs are fee plus price impact against the pre-trade mid, in USDG terms.

**The walk** (`quote_math.py`) follows the pool: constant liquidity between initialized ticks, the fee taken from the gross input, `liquidityNet` added when crossing up and subtracted when crossing down, and **zero-liquidity intervals crossed for free** (the price jumps to the next initialized tick, as in the pool). Every quote ends in exactly one status: `filled`; `price_limit_rejection` (stopped at the caller's `sqrtPriceLimit` with executable liquidity beyond it); `capacity_exhausted` (the entire tick range was scanned and no executable liquidity is left); `scan_window_exhausted` (only a window was scanned, so what lies beyond is unknown and the figure is a lower bound, not a capacity claim).

**Validation against the deployed pools.** `test/fork/PoolQuoteReplay.t.sol` + `replay_pool.py` pin a pool, save its snapshot **by block and hash** (`pools/inputs/<pool>/<block>-<hash prefix>.json`, never overwritten), then at the *same block* execute real swaps on a fork of the deployed pool with the exact integer amounts the offline walk used: both directions at six sizes, with and without a 10% price limit, and a sequential buy-then-sell. The comparison (`replay_check.py`) is strict: it **rejects missing, duplicate and unexpected rows, a requested amount that differs from the plan, and any consumed input, output or final `sqrtPriceX96` that differs beyond rounding**, with expectations recomputed from the snapshot. Each proof (`pools/fork-replay/<pool>@<block>.json`) records the snapshot path and its SHA-256, the raw forge log and its SHA-256, and every fork row; `verify_replays.py` re-checks all of that **offline** (snapshot hash, pin block and hash, log hash, rows parsed from the log equal the stored rows, strict row accounting, and that each published quote is bound to a proof of the same snapshot). Result: all 30 rows match the deployed pool for each of SPY, NVDA, META, GLD, r33 CL200 and r33 CL500, including the gap-crossing r33 sells and the price-limit cases. Where the offline scan ended at its window edge (GLD and SPY large sells) the deployed pool filled more, as it must: the offline number is then a lower bound and the check requires exactly that.

**Pinned pool facts** (each pool read at its own block; gateway values are unpinned)

| pool | address | pin (L2 block) | spacing | fee() at pin | listed fee | 30-day mean fee | active liquidity | 24 h volume (logs / gateway) | gateway TVL |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| SPY/USDG CL1 | `0x38453c115607463ac284820ce959831042f3df4e` | 104,334,038 | 1 | 150 ppm | 100 ppm | 108 ppm | 3.826e+17 | $1,283,369 / $1,033,312 | $236,437 |
| NVDA/USDG CL10 | `0xdac1904d823f7d6bcaaf431ceee40c934fed321b` | 104,331,899 | 10 | 200 ppm | 100 ppm | 119 ppm | 2.718e+16 | $133,350 / $114,554 | $87,272 |
| META/USDG CL50 | `0x960f79d8dfec7f2f0c1b24cdac6bb9df1371c6e2` | 104,331,530 | 50 | 150 ppm | 150 ppm | 206 ppm | 1.176e+16 | $66,689 / $29,232 | $111,576 |
| GLD/USDG CL1 | `0x9896af2f6fed9939acc87637b973b6484e6619d8` | 104,332,890 | 1 | 150 ppm | 150 ppm | 206 ppm | 7.911e+16 | $166,611 / $110,696 | $124,680 |
| r33/USDG CL200 | `0xc276b9a27ab1e60e32b581db6a23bb1fab0fc30b` | 104,331,203 | 200 | 20000 ppm | 20000 ppm | 20000 ppm | 5.491e+15 | $28 / $28 | $43,876 |
| r33/USDG CL500 | `0x9f301fa81ccdda903a3590e7b9abee12e035709e` | 104,331,368 | 500 | 50000 ppm | 50000 ppm | 50000 ppm | 8.830e+16 | $82 / $283 | $13,661 |

**One-way execution cost in bp of trade size** (fee + price impact versus the pre-trade mid; independent quotes at the pin; buy / sell)

| pool | $50 | $250 | $1,000 | $5,000 | $10,000 | $50,000 |
| --- | --- | --- | --- | --- | --- | --- |
| SPY/USDG CL1 | 1.5 / 1.5 | 1.7 / 1.7 | 2.4 / 2.4 | 6.2 / 6.2 | 11.0 / 11.0 | 45.0 / 36.5 |
| NVDA/USDG CL10 | 3.3 / 3.3 | 8.4 / 8.4 | 27.4 / 23.8 | 127.0 / 80.1 | 235.5 / 147.7 | 790.1 / 5450.3 |
| META/USDG CL50 | 3.2 / 3.2 | 10.0 / 10.0 | 35.5 / 35.5 | 169.1 / 172.0 | 330.9 / 3321.7 | 1189.6 / 8664.3 |
| GLD/USDG CL1 | 1.8 / 1.8 | 3.2 / 3.2 | 8.1 / 8.0 | 34.6 / 33.7 | 67.2 / 65.6 | 314.7 / beyond scan |
| r33/USDG CL200 | 348.1 / 588.6 | 375.2 / 1887.9 | 430.1 / 6732.3 | 701.2 / 9346.3 | 1018.6 / 9673.2 | 2939.1 / 9934.6 |
| r33/USDG CL500 | 523.8 / 523.8 | 617.7 / 617.7 | 953.9 / 960.0 | 2407.3 / no capacity | 3783.7 / no capacity | 7629.4 / no capacity |

**Sequential round trip, bp** (buy, then sell what was bought, in the same evolving pool state: this is the cost of *reversing at once* and returns the price impact, so it is NOT the cost of exiting a position; it replaces the old `roundTripBps`, which added two independent quotes)

| pool | $50 | $250 | $1,000 | $5,000 | $10,000 | $50,000 |
| --- | --- | --- | --- | --- | --- | --- |
| SPY/USDG CL1 | 3.0 | 3.0 | 3.0 | 3.0 | 3.0 | 3.0 |
| NVDA/USDG CL10 | 4.0 | 4.0 | 4.0 | 4.0 | 4.0 | 3.8 |
| META/USDG CL50 | 3.0 | 3.0 | 3.0 | 3.0 | 3.0 | 2.8 |
| GLD/USDG CL1 | 3.0 | 3.0 | 3.0 | 3.0 | 3.0 | 3.0 |
| r33/USDG CL200 | 393.2 | 393.3 | 393.8 | 389.0 | 382.8 | 344.3 |
| r33/USDG CL500 | 973.9 | 969.4 | 953.4 | 883.5 | 816.0 | 622.4 |

**Executable capacity** (the pool keeps filling at collapsing prices, so a trade is rarely refused; this is the most it can PAY for an unbounded input)

| pool | scan | most USDG obtainable by selling the stock | most stock (USD at mid) obtainable by spending USDG | statuses |
| --- | --- | --- | --- | --- |
| SPY/USDG CL1 | window | $107,737 | $128,708 | sell scan_window_exhausted, buy scan_window_exhausted |
| NVDA/USDG CL10 | full | $22,749 | $64,917 | sell capacity_exhausted, buy capacity_exhausted |
| META/USDG CL50 | full | $6,678 | $106,030 | sell capacity_exhausted, buy capacity_exhausted |
| GLD/USDG CL1 | window | $27,722 | $98,420 | sell scan_window_exhausted, buy scan_window_exhausted |
| r33/USDG CL200 | full | $327 | $41,214 | sell capacity_exhausted, buy capacity_exhausted |
| r33/USDG CL500 | full | $1,003 | $11,921 | sell capacity_exhausted, buy capacity_exhausted |

**Depth: USDG-equivalent input that moves the price** (+1% / +5% buying the stock, -1% / -5% selling it)

| pool | +1% | +5% | -1% | -5% |
| --- | --- | --- | --- | --- |
| SPY/USDG CL1 | $55,969 | $125,200 | $61,207 | $106,952 |
| NVDA/USDG CL10 | $1,960 | $11,073 | $3,288 | $18,289 |
| META/USDG CL50 | $1,460 | $7,250 | $1,475 | $5,225 |
| GLD/USDG CL1 | $7,546 | $37,986 | $7,807 | $28,230 |
| r33/USDG CL200 | $6 | $2,219 | $6 | $31 |
| r33/USDG CL500 | $99 | $492 | $100 | $518 |

**Fee reconciliation**

| pool | listed | FeeAdjustment events | fee range seen (ppm) | fee() vs events at matched blocks | implied fee from swaps vs event fee | protocol delta / predicted | feeGrowth delta / predicted | share of last 30 days at 150 ppm |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| SPY/USDG CL1 | 100 | 508 | 70-1000 | 16/16 match | median ratio 1.0000, 1.0000, 1.0000 (582,721 swaps) | 1.0000, 1.0000 | 0.9999, 1.0000 | 16.2% |
| NVDA/USDG CL10 | 100 | 1818 | 70-2460 | 18/18 match | median ratio 1.0000, 1.0000, 1.0000 (314,054 swaps) | 1.0000, 1.0000 | 1.0000, 1.0000 | 12.4% |
| META/USDG CL50 | 150 | 71 | 50-2500 | 16/16 match | n/a | 1.0000, 1.0000 | 1.0001, 1.0000 | 22.5% |
| GLD/USDG CL1 | 150 | 12 | 150-444 | 16/16 match | median ratio 1.0000, 1.0000, 1.0000 (90,823 swaps) | 1.0000, 1.0000 | 1.0000, 1.0000 | 22.5% |
| r33/USDG CL200 | 20000 | 0 | 20000-20000 | 16/16 match | n/a | no swaps | no swaps | 0.0% |
| r33/USDG CL500 | 50000 | 0 | 50000-50000 | 16/16 match | n/a | no swaps | no swaps | 0.0% |

**Fee-tier states for SPY over the last 30 days (share of time)**: 100 ppm 83.4%, 125 ppm 0.2%, 150 ppm 16.2%, 175 ppm 0.1%, 200 ppm 0.1%, 250 ppm 0.0%, 300 ppm 0.0%, 1000 ppm 0.0%; last at 150 ppm 2026-11-02T14:35Z.

**Gross swap fee -> protocol share -> holder entitlement** (fork tests at pin 104,116,096; raw token units; reserve cut 20% is the README fixture value)

| pool | fee in force | protocol share of gross | our share of active liquidity | LP collect (token0 / token1) | collect vs (gross - protocol) x share | holders after reserve (token0 / token1) |
| --- | --- | --- | --- | --- | --- | --- |
| GLD | 150 ppm | 0.5000, 0.5000 | 0.4967 | 22,350 / 62,563,885,636,063 | diff 0 / 2 raw | 17,880 / 50,051,108,508,850 |
| NVDA | 100 ppm | 0.5000, 0.5000 | 0.6755 | 60,797 / 292,366,000,972,895 | diff -1 / 3 raw | 48,638 / 233,892,800,778,316 |
| SPY | 100 ppm | 0.5000, 0.5000 | 0.0262 | 10,339,143,280,529 / 7,862 | diff 0 / 0 raw | 8,271,314,624,423 / 6,290 |

## 4. r33 liquidation routes (limited to what was quoted)

The r33/USDG CL200 route is the one the partner named first. It is **not usable**: it is 98% r33 sitting out of range, and the deployed pool pays at most about $327 USDG for r33 however much is sold (the price falls through the empty range and the input is consumed at collapsing prices). That is a statement about that pool only. `r33_routes.py` quotes every other route the gateway lists for the same asset, for fixed amounts of r33 (1,000 to 100,000 tokens, about $47 to $4,669 at the CL200 mid): the other concentrated pools (full-bitmap scans, same validated walk), the DLMM pairs through their own `getSwapOut`, the legacy constant-product pairs, and two- and three-hop routes through RAM and WETH. Each pool is read at its own block (listed below the table), so multi-hop figures are **non-atomic composites** of independent pins, and the shortfall is measured against the CL200 spot, which is itself a thin and possibly stale price.

What the table supports: on these quotes, selling r33 for USDG locally is possible at larger size, and the cheapest quoted route at the largest amount is r33/RAM DLMM bin200 -> WETH/RAM CL100 -> WETH/USDG CL1 (about 2% to 3% shortfall across three separate runs for the $4,669 amount; **that figure is relative to the CL200 spot**, a thin and possibly stale price, not to a defensible one). What it does not support: any claim about *all* local liquidation routes, about stressed conditions, or about price. The quotes are not router quotes, the composite is not atomic, and the DLMM hop needs a different adapter from the CL adapter this branch has.

**The next capacity proof** is the complete route executed **atomically at one common fork block**, with the actual fees, explicit slippage limits (minimum outputs) and the adapter requirements (a DLMM router path; the current adapter is concentrated-liquidity only) in place, plus a defensible r33 price source. **Until both exist, there is no r33 collateral factor and no r33 activation.**

**r33 -> USDG route quotes** (reference mid $0.04669 per r33 from the CL200 spot; nominal value of the four amounts: $47, $233, $934, $4,669). USDG received (shortfall versus nominal in bp):

| route | 1,000 r33 | 5,000 r33 | 20,000 r33 | 100,000 r33 |
| --- | --- | --- | --- | --- |
| r33/USDG CL200 | $44 (564) | $192 (1,791) | $327 (6,501) | $327 (9,300) |
| r33/USDG CL500 | $44 (667) | $216 (752) | $835 (1,058) | no capacity at r33/USDG CL500 |
| r33/USDG CL5 | no capacity at r33/USDG CL5 | no capacity at r33/USDG CL5 | no capacity at r33/USDG CL5 | no capacity at r33/USDG CL5 |
| r33/USDG DLMM200 | $45 (289) | $208 (1,081) | $659 (2,948) | $1,930 (5,867) |
| r33/USDG V2 | $45 (326) | $217 (685) | $764 (1,822) | $2,312 (5,048) |
| r33/RAM CL100 -> RAM/USDG DLMM100 | $45 (407) | $158 (3,224) | $158 (8,306) | $158 (9,661) |
| r33/RAM CL100 -> RAM/USDG V2 | $44 (575) | $147 (3,686) | $147 (8,422) | $147 (9,684) |
| r33/RAM CL100 -> WETH/RAM CL100 -> WETH/USDG CL1 | $45 (408) | $158 (3,225) | $158 (8,306) | $158 (9,661) |
| r33/RAM CL200 -> RAM/USDG DLMM100 | $44 (601) | $203 (1,320) | $770 (1,751) | $3,351 (2,823) |
| r33/RAM CL200 -> RAM/USDG V2 | $43 (761) | $185 (2,073) | $564 (3,960) | $1,301 (7,214) |
| r33/RAM CL200 -> WETH/RAM CL100 -> WETH/USDG CL1 | $44 (601) | $203 (1,322) | $775 (1,700) | $3,533 (2,434) |
| r33/RAM DLMM200 -> RAM/USDG DLMM100 | $46 (231) | $228 (231) | $904 (315) | $3,834 (1,789) |
| r33/RAM DLMM200 -> RAM/USDG V2 | $45 (406) | $206 (1,178) | $633 (3,223) | $1,415 (6,970) |
| r33/RAM DLMM200 -> WETH/RAM CL100 -> WETH/USDG CL1 | $46 (232) | $228 (233) | $912 (237) | $4,527 (306) |
| r33/RAM DLMM25 -> RAM/USDG DLMM100 | $46 (251) | no capacity at r33/RAM DLMM25 | no capacity at r33/RAM DLMM25 | no capacity at r33/RAM DLMM25 |
| r33/RAM DLMM25 -> RAM/USDG V2 | $45 (425) | no capacity at r33/RAM DLMM25 | no capacity at r33/RAM DLMM25 | no capacity at r33/RAM DLMM25 |
| r33/RAM DLMM25 -> WETH/RAM CL100 -> WETH/USDG CL1 | $46 (252) | no capacity at r33/RAM DLMM25 | no capacity at r33/RAM DLMM25 | no capacity at r33/RAM DLMM25 |

**Best quoted route per amount**: 1,000 r33: r33/RAM DLMM200 -> RAM/USDG DLMM100 ($46); 5,000 r33: r33/RAM DLMM200 -> RAM/USDG DLMM100 ($228); 20,000 r33: r33/RAM DLMM200 -> WETH/RAM CL100 -> WETH/USDG CL1 ($912); 100,000 r33: r33/RAM DLMM200 -> WETH/RAM CL100 -> WETH/USDG CL1 ($4,527)

Pins (each pool at its own block): r33/USDG CL200 104,334,489, r33/USDG CL500 104,334,544, r33/USDG CL5 104,334,589, r33/RAM CL100 104,335,445, r33/RAM CL200 104,335,514, WETH/RAM CL100 104,335,571, WETH/USDG CL1 104,335,742, r33/USDG DLMM200 104,335,898, r33/RAM DLMM200 104,335,912, r33/RAM DLMM25 104,335,927, RAM/USDG DLMM100 104,335,942, r33/USDG V2 104,335,957, RAM/USDG V2 104,335,977


## 5. What the fee reconciliation shows (the fee-entitlement, profitability and liquidation gates are NOT waived; GLD is NOT activated)

`fee_reconcile.py` (read-only; `evidence/fees/fee-reconciliation.json`) reconciles four layers, separately:

1. **Fee-change events versus `fee()`.** At 16 to 18 sampled blocks per pool inside the state horizon, including the blocks either side of a fee change (NVDA changed 100 to 150 ppm inside the window), `fee()` equals the fee derived from `FeeAdjustment` events every time. For single-band swaps `net = L x (change in 1/sqrtP or sqrtP)` implies a charged fee of `1 - net/gross`; across 582,721 SPY, 314,054 NVDA and 90,823 GLD swaps the implied fee equals the event-derived fee in force (median ratio 1.0000, every regime). The fee-change events are the fee actually charged.
2. **Gross swap fee, protocol share and LP entitlement (pinned window of about 3,300 blocks).** Summing input x fee in force over the window's swaps, the change in `protocolFees()` equals gross x protocol share (ratio 1.0000 on every token with swaps; no `CollectProtocol` in the window), and the change in `feeGrowthGlobal` equals the LP remainder per unit of liquidity (1.0000 to 1.0134; the 1.3% is band-crossing swaps whose post-swap liquidity differs from the liquidity the fee accrued against). Protocol share is 50% on SPY, NVDA, GLD and META (5% on r33), changed from 5% at blocks 76,901,418 (SPY), 76,907,586 (NVDA) and 88,775,308 (GLD, META).
3. **Exact two-layer position (fork tests at pin 104,116,096).** Protocol delta is exactly 50% of gross; the vault position's `collect` equals (gross - protocol) x our share of active liquidity to within 0 to 3 raw units; holders get 80% of that after the 20% reserve cut.
4. **Listed versus charged fees.** The gateway `feeTier` equals the current `fee()` for SPY (100), NVDA (100 at the quote and fork pins, 150 minutes later), GLD (150), META (150) and r33 (20,000).

**GLD's average-versus-instantaneous fee is explained by the verified fee history.** GLD's fee was 150 ppm at the pins and the listing, but its 30-day time-weighted mean is 206 ppm (222 ppm for 78% of the time, 150 for 22%; range 150 to 444 ppm): the charged fee is exactly the event history, so the difference is a fee that moves, not a data error. All baselines therefore use the **time-varying fee actually in force on every swap**, and fee sensitivity is stressed in the other direction as well: constant 150 ppm (the listed value), and every swap's fee scaled x1.5, x2 and x3 with volume held fixed (a stress that gives holders more fee income and ignores any volume lost to higher fees). The gateway's `hasDynamicFees: false` for pools that emit `FeeAdjustment` is a **separate metadata discrepancy**: it is documented, it is not a fee-policy decision, and it is not treated as a waiver of anything.

None of this waives the remaining gates: holder entitlement has not been compared with a third-party LP's Collect history, there is no fork test of the exact configuration on META or r33, and the profitability gate (section 7) and liquidation-capacity gate (sections 4 and 9) are not met by anything here.

## 6. What the contract allows, what the engine omits, and keeper latency

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

Inputs are validated by field width before any rule runs: negative values (for example a negative daily limit), NaN, infinities, non-numbers, booleans and values above the uint16/uint32/uint8/uint128 widths are rejected with a stated violation instead of crashing or slipping through a comparison. `logs/unit-tests.log`, `logs/mutations-engine-feasibility.log`, `logs/mutations-checks.log` (the verification code itself).

Engine mismatches found and fixed: the default trigger fraction 0.70 gave 420 ticks for a 600-tick range (maximum 300); the historical cooldown was 900 s (minimum 3600 s); the spacing defaulted to 1 for every pool (NVDA's is 10); the full range was one spacing narrower than the contract's at both ends; fees were paid to holders without the reserve cut; `maxRangedValueUsd` was not an input.

**Contract guards the engine omits or only approximates** (documented before any further strategy optimization):

* Checkpoint discipline: `rebalance` reverts `CheckpointStale` unless a checkpoint ran within `maxCheckpointAge`; the engine has no checkpoint concept, so the keeper's checkpoint cadence appears only as the assumed maintenance cost.
* Loss accounting: the vault recognizes shared loss pro rata across both sides' native principals and applies the reserve and settlement waterfall (section 10); the engine tracks one equity number, so it cannot say which side bears a loss.
* Allocation circuit breaker (`allocationCapExceeded` sets `allocationPaused` and skips the recenter), pair caps (`PairCapExceeded`), `allocationPaused`, `emergencyMode`, `swapsPaused`, `PairNotEnabled`, and the stock token's `oraclePaused` flag are not modelled.
* Oracle validity: `InvalidReferencePrice`, `IncompleteOracleRound`, `InvalidOracleAnswer`, the sequencer check and `maxStaleness` (at most 12 h) are approximated by an age limit and a weekend/holiday session block; the session block is stricter than the contract, which only checks age and the pause flag.
* Price bounds: the allocation bound (`maxPriceDeviationBps`, at most 5%; the engine uses 1.5%) is applied before the recenter, the contract applies it after; the wider removal bound (`maxRemovalDeviationBps`) and `_requireBandCoversGate` are not modelled and can block a real recenter.
* Loss bounds: the contract applies both `maxLossBps` (at most 100) and the operator's `deployLossBps`, which is zero (strict) until configured; the engine allows a 10 bp loss on a recenter.
* Deadline: the keeper must supply a deadline no more than `maxDeadlineDelay` (at most 30 minutes); a late transaction reverts `InvalidDeadline`.
* The integer oracle tick versus the engine's float tick; dividends and `uiMultiplier`; borrow demand and lending-side utilization.

**Keeper execution latency.** Baseline rows without a suffix evaluate on every swap and oracle update and execute at that instant at the post-swap pool price: an idealised, zero-latency upper bound. The deployed Uniswap keeper polls every 300 seconds (`INTERVAL_SECONDS`), refuses to act below a 0.0003 ETH gas floor, simulates before sending, and retries a reverted simulation only on the next poll. `*_poll300` rows model the polling interval only (`poll_s = 300`); transaction inclusion delay (about a second on this L2) and the price movement between decision and inclusion are not modelled, so a recenter the contract would reject (`RecenterNotNeeded`, loss bound) or that an adversary moved the pool against is not modelled either. The Ramses keeper's cadence is an assumption. **Any faster-than-300 s keeper and any swap-enabled recenter is hypothetical** and is not reported as a result here.


## 7. Baselines (contract-feasible, no swap at recenter, 1 h cooldown, 20% reserve cut, RAM at zero)

Common configuration: one position, half-width 600 ticks (+/-6.2%), trigger 300 (the contract maximum for 600; sensitivity at 100), at most 24 recenters per rolling day, per-recenter loss bound 10 bp, protocol share 50% (fork-measured), feed guard 12 h with weekend and holiday sessions blocked and a 150 bp pool-versus-oracle gate. Holding means the same starting 50/50 inventory left untouched; full range is the separately validated mode. **The time-varying fee actually in force on every swap is used.** Recenter gas ($0.0663) and maintenance ($0.5737/day, the hourly checkpoint cadence the vault requires) are the Uniswap vault keeper's measured costs used as a **proxy** (PR #13 evidence r3), not Ramses measurements; the same evidence's observed average ($0.1428/day, with the keeper out of gas part of the time) is the lower sensitivity. Maintenance is subtracted after the run, not inside the engine.

**How to read the tables.** "Return after all costs" subtracts the reserve cut (inside the run), recenter gas and maintenance (assumed). **LVR is never subtracted from a return versus holding**; it appears only in the separate "fees - LVR - gas - maintenance" line, which compares against a delta-matched rebalancing portfolio. The **supplier view** is separate from the **protocol-paid operating costs**: the contract's keeper wallet pays gas, so gas and maintenance are not deductions from supplier principal; the supplier table adds recenter gas back, and the protocol line is the reserve-cut inflow minus gas minus maintenance. Pools: SPY 74.4 days from 2026-08-19, NVDA (the Ramses NVDA/USDG pool, not the live Uniswap vault) 90.0 days from 2026-08-04, GLD 42.3 days from 2026-09-21 (the first fresh oracle in the dataset); windows are single short samples.

### 7A. Current caps: $1,000 pair and $50 ranged value (operator-supplied, pending configuration readback)

**Two different things must not be confused.** (1) **The LIVE Uniswap NVDA/USDG vault** (`0x2808...`), a different, deployed contract, was read back on chain (read-only, `readback_live_caps.py`, `live-config-readback.json`, kind `LIVE_UNISWAP_NVDA_USDG_VAULT_READBACK`): `maxPairValueUSDG` $1,000, `maxRangedValueUsd` $50, `maxSettlementSwapUSDG` $25, `reserveFeeBps` 2000, swaps paused, and a live range policy of +/-2400 ticks, trigger 600, 1 h cooldown, at most 4 per day, loss bound 10 bp (the `live2400_trig600_perday4` rows replay that policy). (2) **The Ramses vault is not deployed**, so nothing can be read back for it. The settings the Ramses baselines *assume* are listed with their sources and statuses in `proposed-ramses-settings.json` (status "PROPOSED / ASSUMED FOR RESEARCH. NOT DEPLOYED. NOT APPROVED."): the $1,000 pair cap and $50 ranged cap are **operator-supplied, pending configuration readback of the Ramses vault**; they happen to equal the live Uniswap numbers, which is consistency, not confirmation. 95% of capital is idle by construction (the full-range row is the exception). **At these caps every configuration's result is the maintenance assumption**: fees to holders over the whole window are $0.2 to $1.7 (up to $5.1 only in the tripled-fee stress) against an assumed $42.69 (SPY), $51.64 (NVDA) or $24.29 (GLD) of operator-paid maintenance, the supplier view is within a fraction of a percent of holding, and the protocol-net line is the maintenance cost less a few cents of reserve inflow. With the observed-average maintenance the all-costs result is still about -1.0% (SPY), -1.3% (NVDA) and -0.9% (GLD) over the window. Larger-capital runs follow and are labelled research scenarios.

### SPY: CURRENT CAPS: $1,000 pair and $50 ranged value (OPERATOR-SUPPLIED, pending configuration readback of the Ramses vault, which is not deployed; the LIVE Uniswap NVDA/USDG vault, a different contract, reads back the same two numbers: maxPairValueUSDG $1,000, maxRangedValueUsd $50; see live-config-readback.json and proposed-ramses-settings.json)

Tick spacing 1, 655,414 swaps, pool head block 104,071,867, starting capital $1,000, ranged cap $50.

**Full window**: 2026-08-19T22:30:12Z to 2026-11-02T08:18:27Z (74.4 days)

| run | return | hold | excess before maintenance | return after all costs | excess after all costs (window / annualised) | fees to holders | reserve cut | recenter gas | maintenance (assumed) | idle capital (avg) | recenters | worst week vs hold | blocked |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| single600_trig300 | -0.58% | -0.62% | +0.04% | -4.85% | -4.23% / -20.7%/yr | $0.46 | $0.12 | $0.000 | $42.69 | 95% | 0 | $-0.11 | none |
| single600_trig300_poll300 | -0.58% | -0.62% | +0.04% | -4.85% | -4.23% / -20.7%/yr | $0.46 | $0.12 | $0.000 | $42.69 | 95% | 0 | $-0.11 | none |
| single600_trig100 | -0.72% | -0.62% | -0.10% | -4.99% | -4.36% / -21.4%/yr | $0.47 | $0.12 | $1.392 | $42.69 | 95% | 21 | $-0.29 | none |
| live2400_trig600_perday4 | -0.61% | -0.62% | +0.01% | -4.88% | -4.26% / -20.9%/yr | $0.12 | $0.03 | $0.000 | $42.69 | 95% | 0 | $-0.03 | none |
| single1200_trig600 | -0.60% | -0.62% | +0.02% | -4.87% | -4.25% / -20.8%/yr | $0.24 | $0.06 | $0.000 | $42.69 | 95% | 0 | $-0.05 | none |
| fee_fixed150 | -0.56% | -0.62% | +0.06% | -4.83% | -4.21% / -20.6%/yr | $0.67 | $0.17 | $0.000 | $42.69 | 95% | 0 | $-0.11 | none |
| fee_x15 | -0.55% | -0.62% | +0.07% | -4.82% | -4.20% / -20.6%/yr | $0.69 | $0.17 | $0.000 | $42.69 | 95% | 0 | $-0.10 | none |
| fee_x2 | -0.53% | -0.62% | +0.09% | -4.80% | -4.18% / -20.5%/yr | $0.92 | $0.23 | $0.000 | $42.69 | 95% | 0 | $-0.10 | none |
| fee_x3 | -0.49% | -0.62% | +0.13% | -4.75% | -4.13% / -20.3%/yr | $1.39 | $0.35 | $0.000 | $42.69 | 95% | 0 | $-0.10 | none |
| full_range | -0.59% | -0.62% | +0.03% | -4.86% | -4.24% / -20.8%/yr | $0.28 | $0.07 | $0.000 | $42.69 | 0% | 0 | $-0.06 | none |

**Last 28 days**: 2026-10-05T00:01:03Z to 2026-11-02T08:18:27Z (28.3 days)

| run | return | hold | excess before maintenance | return after all costs | excess after all costs (window / annualised) | fees to holders | reserve cut | recenter gas | maintenance (assumed) | idle capital (avg) | recenters | worst week vs hold | blocked |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| single600_trig300 | -0.80% | -0.81% | +0.00% | -2.43% | -1.62% / -20.9%/yr | $0.08 | $0.02 | $0.000 | $16.26 | 95% | 0 | $-0.14 | none |
| single600_trig300_poll300 | -0.80% | -0.81% | +0.00% | -2.43% | -1.62% / -20.9%/yr | $0.08 | $0.02 | $0.000 | $16.26 | 95% | 0 | $-0.14 | none |
| single600_trig100 | -0.87% | -0.81% | -0.06% | -2.50% | -1.69% / -21.8%/yr | $0.08 | $0.02 | $0.663 | $16.26 | 95% | 10 | $-0.33 | none |
| live2400_trig600_perday4 | -0.81% | -0.81% | +0.00% | -2.43% | -1.63% / -20.9%/yr | $0.02 | $0.01 | $0.000 | $16.26 | 95% | 0 | $-0.04 | none |
| single1200_trig600 | -0.81% | -0.81% | +0.00% | -2.43% | -1.62% / -20.9%/yr | $0.04 | $0.01 | $0.000 | $16.26 | 95% | 0 | $-0.07 | none |
| fee_fixed150 | -0.80% | -0.81% | +0.01% | -2.43% | -1.62% / -20.9%/yr | $0.11 | $0.03 | $0.000 | $16.26 | 95% | 0 | $-0.14 | none |
| fee_x15 | -0.80% | -0.81% | +0.01% | -2.43% | -1.62% / -20.9%/yr | $0.12 | $0.03 | $0.000 | $16.26 | 95% | 0 | $-0.14 | none |
| fee_x2 | -0.80% | -0.81% | +0.01% | -2.42% | -1.62% / -20.8%/yr | $0.16 | $0.04 | $0.000 | $16.26 | 95% | 0 | $-0.14 | none |
| fee_x3 | -0.79% | -0.81% | +0.02% | -2.41% | -1.61% / -20.7%/yr | $0.24 | $0.06 | $0.000 | $16.26 | 95% | 0 | $-0.13 | none |
| full_range | -0.81% | -0.81% | +0.00% | -2.43% | -1.62% / -20.9%/yr | $0.05 | $0.01 | $0.000 | $16.26 | 0% | 0 | $-0.08 | none |

**Fees minus LVR minus costs (a different benchmark from holding; not subtracted from any return above)**

| run (full window) | fees to holders | LVR vs delta-matched benchmark | recenter gas | maintenance (assumed) | fees - LVR - gas | fees - LVR - gas - maintenance | maintenance if the observed average $0.1428/day: excess after all costs |
| --- | --- | --- | --- | --- | --- | --- | --- |
| single600_trig300 | $0.46 | $0.47 | $0.000 | $42.69 | $-0.00 | $-42.69 | -1.02% / -5.0%/yr |
| single600_trig300_poll300 | $0.46 | $0.47 | $0.000 | $42.69 | $-0.00 | $-42.69 | -1.02% / -5.0%/yr |
| full_range | $0.28 | $0.29 | $0.000 | $42.69 | $-0.01 | $-42.70 | -1.04% / -5.1%/yr |

**Supplier returns versus protocol-paid operating costs (full window)**. In the contract the keeper wallet pays gas and the vault owes suppliers only their claims, so recenter gas and maintenance are operator/protocol costs, not deductions from supplier principal. Supplier view = the run with recenter gas added back; protocol view = reserve-cut inflow minus gas minus maintenance.

| run | supplier return | hold | supplier excess vs hold (window / annualised) | reserve-cut inflow to the loss reserve | recenter gas (operator-paid) | maintenance (operator-paid, assumed) | protocol net (inflow - costs) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| single600_trig300 | -0.58% | -0.62% | +0.04% / +0.2%/yr | $0.12 | $0.000 | $42.69 | $-42.57 |
| single600_trig300_poll300 | -0.58% | -0.62% | +0.04% / +0.2%/yr | $0.12 | $0.000 | $42.69 | $-42.57 |
| full_range | -0.59% | -0.62% | +0.03% / +0.1%/yr | $0.07 | $0.000 | $42.69 | $-42.62 |

**Fee history and fee stress (full window; time-varying fee in force on every swap is the baseline; the stresses scale or replace it with volume unchanged, so they are upper-bound fee scenarios, not forecasts)**

| run | fees to holders | supplier excess vs hold (window / annualised) | after all assumed costs incl. maintenance (window / annualised) |
| --- | --- | --- | --- |
| actual fee in force (baseline) | $0.46 | +0.04% / +0.2%/yr | -4.23% / -20.7%/yr |
| constant 150 ppm (listed / instantaneous) | $0.67 | +0.06% / +0.3%/yr | -4.21% / -20.6%/yr |
| fee x1.5 | $0.69 | +0.07% / +0.3%/yr | -4.20% / -20.6%/yr |
| fee x2 | $0.92 | +0.09% / +0.4%/yr | -4.18% / -20.5%/yr |
| fee x3 | $1.39 | +0.13% / +0.7%/yr | -4.13% / -20.3%/yr |

Excess versus holding = fees to holders + inventory effect - recenter gas (baseline: $0.46 + $-0.03 - $0.000); end state idle USDG $476, idle stock $469; RAM accrued 4.5 tokens, valued at zero.

### NVDA: CURRENT CAPS: $1,000 pair and $50 ranged value (OPERATOR-SUPPLIED, pending configuration readback of the Ramses vault, which is not deployed; the LIVE Uniswap NVDA/USDG vault, a different contract, reads back the same two numbers: maxPairValueUSDG $1,000, maxRangedValueUsd $50; see live-config-readback.json and proposed-ramses-settings.json)

Tick spacing 10, 331,486 swaps, pool head block 104,187,711, starting capital $1,000, ranged cap $50.

**Full window**: 2026-08-04T11:08:43Z to 2026-11-02T11:27:06Z (90.0 days)

| run | return | hold | excess before maintenance | return after all costs | excess after all costs (window / annualised) | fees to holders | reserve cut | recenter gas | maintenance (assumed) | idle capital (avg) | recenters | worst week vs hold | blocked |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| single600_trig300 | -0.28% | -0.32% | +0.04% | -5.44% | -5.12% / -20.8%/yr | $1.70 | $0.43 | $1.326 | $51.64 | 95% | 20 | $-3.35 | none |
| single600_trig300_poll300 | -0.29% | -0.32% | +0.03% | -5.45% | -5.13% / -20.8%/yr | $1.70 | $0.43 | $1.658 | $51.64 | 95% | 25 | $-3.52 | none |
| single600_trig100 | -1.25% | -0.32% | -0.94% | -6.42% | -6.10% / -24.7%/yr | $1.71 | $0.43 | $11.072 | $51.64 | 95% | 167 | $-3.93 | cooldown 2605 (cooldown 0.5 d) |
| live2400_trig600_perday4 | -0.31% | -0.32% | +0.01% | -5.47% | -5.16% / -20.9%/yr | $0.46 | $0.12 | $0.398 | $51.64 | 95% | 6 | $-0.95 | none |
| single1200_trig600 | -0.27% | -0.32% | +0.05% | -5.43% | -5.11% / -20.7%/yr | $0.89 | $0.22 | $0.398 | $51.64 | 95% | 6 | $-1.76 | none |
| fee_fixed150 | -0.34% | -0.32% | -0.03% | -5.51% | -5.19% / -21.0%/yr | $1.03 | $0.26 | $1.326 | $51.64 | 95% | 20 | $-3.35 | none |
| fee_x15 | -0.19% | -0.32% | +0.12% | -5.36% | -5.04% / -20.4%/yr | $2.55 | $0.64 | $1.326 | $51.64 | 95% | 20 | $-3.27 | none |
| fee_x2 | -0.11% | -0.32% | +0.20% | -5.28% | -4.96% / -20.1%/yr | $3.41 | $0.85 | $1.326 | $51.64 | 95% | 20 | $-3.19 | none |
| fee_x3 | +0.05% | -0.32% | +0.37% | -5.11% | -4.79% / -19.4%/yr | $5.11 | $1.28 | $1.326 | $51.64 | 95% | 20 | $-3.03 | none |
| full_range | -0.21% | -0.32% | +0.10% | -5.38% | -5.06% / -20.5%/yr | $1.08 | $0.27 | $0.000 | $51.64 | 0% | 0 | $-2.02 | none |

**Last 28 days**: 2026-10-05T00:00:42Z to 2026-11-02T11:27:06Z (28.5 days)

| run | return | hold | excess before maintenance | return after all costs | excess after all costs (window / annualised) | fees to holders | reserve cut | recenter gas | maintenance (assumed) | idle capital (avg) | recenters | worst week vs hold | blocked |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| single600_trig300 | -6.18% | -5.86% | -0.32% | -7.82% | -1.95% / -25.1%/yr | $0.37 | $0.09 | $0.530 | $16.34 | 95% | 8 | $-3.86 | none |
| single600_trig300_poll300 | -6.17% | -5.86% | -0.31% | -7.81% | -1.95% / -24.9%/yr | $0.36 | $0.09 | $0.530 | $16.34 | 95% | 8 | $-3.77 | none |
| single600_trig100 | -6.53% | -5.86% | -0.67% | -8.16% | -2.30% / -29.5%/yr | $0.37 | $0.09 | $3.978 | $16.34 | 95% | 60 | $-4.38 | cooldown 1363 (cooldown 0.12 d) |
| live2400_trig600_perday4 | -5.95% | -5.86% | -0.09% | -7.58% | -1.72% / -22.0%/yr | $0.10 | $0.02 | $0.133 | $16.34 | 95% | 2 | $-1.07 | none |
| single1200_trig600 | -6.01% | -5.86% | -0.15% | -7.65% | -1.79% / -22.9%/yr | $0.19 | $0.05 | $0.133 | $16.34 | 95% | 2 | $-2.02 | none |
| fee_fixed150 | -6.18% | -5.86% | -0.32% | -7.82% | -1.95% / -25.0%/yr | $0.39 | $0.10 | $0.530 | $16.34 | 95% | 8 | $-3.86 | none |
| fee_x15 | -6.17% | -5.86% | -0.30% | -7.80% | -1.94% / -24.8%/yr | $0.55 | $0.14 | $0.530 | $16.34 | 95% | 8 | $-3.84 | none |
| fee_x2 | -6.15% | -5.86% | -0.29% | -7.78% | -1.92% / -24.6%/yr | $0.73 | $0.18 | $0.530 | $16.34 | 95% | 8 | $-3.82 | none |
| fee_x3 | -6.11% | -5.86% | -0.25% | -7.75% | -1.88% / -24.1%/yr | $1.10 | $0.28 | $0.530 | $16.34 | 95% | 8 | $-3.79 | none |
| full_range | -6.02% | -5.86% | -0.16% | -7.66% | -1.80% / -23.0%/yr | $0.21 | $0.05 | $0.000 | $16.34 | 0% | 0 | $-2.25 | none |

**Fees minus LVR minus costs (a different benchmark from holding; not subtracted from any return above)**

| run (full window) | fees to holders | LVR vs delta-matched benchmark | recenter gas | maintenance (assumed) | fees - LVR - gas | fees - LVR - gas - maintenance | maintenance if the observed average $0.1428/day: excess after all costs |
| --- | --- | --- | --- | --- | --- | --- | --- |
| single600_trig300 | $1.70 | $3.69 | $1.326 | $51.64 | $-3.31 | $-54.95 | -1.25% / -5.1%/yr |
| single600_trig300_poll300 | $1.70 | $3.71 | $1.658 | $51.64 | $-3.67 | $-55.31 | -1.26% / -5.1%/yr |
| full_range | $1.08 | $2.18 | $0.000 | $51.64 | $-1.10 | $-52.74 | -1.18% / -4.8%/yr |

**Supplier returns versus protocol-paid operating costs (full window)**. In the contract the keeper wallet pays gas and the vault owes suppliers only their claims, so recenter gas and maintenance are operator/protocol costs, not deductions from supplier principal. Supplier view = the run with recenter gas added back; protocol view = reserve-cut inflow minus gas minus maintenance.

| run | supplier return | hold | supplier excess vs hold (window / annualised) | reserve-cut inflow to the loss reserve | recenter gas (operator-paid) | maintenance (operator-paid, assumed) | protocol net (inflow - costs) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| single600_trig300 | -0.14% | -0.32% | +0.17% / +0.7%/yr | $0.43 | $1.326 | $51.64 | $-52.54 |
| single600_trig300_poll300 | -0.12% | -0.32% | +0.20% / +0.8%/yr | $0.43 | $1.658 | $51.64 | $-52.87 |
| full_range | -0.21% | -0.32% | +0.10% / +0.4%/yr | $0.27 | $0.000 | $51.64 | $-51.37 |

**Fee history and fee stress (full window; time-varying fee in force on every swap is the baseline; the stresses scale or replace it with volume unchanged, so they are upper-bound fee scenarios, not forecasts)**

| run | fees to holders | supplier excess vs hold (window / annualised) | after all assumed costs incl. maintenance (window / annualised) |
| --- | --- | --- | --- |
| actual fee in force (baseline) | $1.70 | +0.17% / +0.7%/yr | -5.12% / -20.8%/yr |
| constant 150 ppm (listed / instantaneous) | $1.03 | +0.11% / +0.4%/yr | -5.19% / -21.0%/yr |
| fee x1.5 | $2.55 | +0.25% / +1.0%/yr | -5.04% / -20.4%/yr |
| fee x2 | $3.41 | +0.34% / +1.4%/yr | -4.96% / -20.1%/yr |
| fee x3 | $5.11 | +0.50% / +2.0%/yr | -4.79% / -19.4%/yr |

Excess versus holding = fees to holders + inventory effect - recenter gas (baseline: $1.70 + $0.02 - $1.326); end state idle USDG $475, idle stock $474; RAM accrued 32.4 tokens, valued at zero.

### GLD: CURRENT CAPS: $1,000 pair and $50 ranged value (OPERATOR-SUPPLIED, pending configuration readback of the Ramses vault, which is not deployed; the LIVE Uniswap NVDA/USDG vault, a different contract, reads back the same two numbers: maxPairValueUSDG $1,000, maxRangedValueUsd $50; see live-config-readback.json and proposed-ramses-settings.json)

Tick spacing 1, 93,803 swaps, pool head block 104,071,867, starting capital $1,000, ranged cap $50.

**Full window**: 2026-09-21T00:00:51Z to 2026-11-02T08:19:00Z (42.3 days)

| run | return | hold | excess before maintenance | return after all costs | excess after all costs (window / annualised) | fees to holders | reserve cut | recenter gas | maintenance (assumed) | idle capital (avg) | recenters | worst week vs hold | blocked |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| single600_trig300 | -5.76% | -5.50% | -0.25% | -8.18% | -2.68% / -23.1%/yr | $0.39 | $0.10 | $0.199 | $24.29 | 95% | 3 | $-1.99 | none |
| single600_trig300_poll300 | -5.75% | -5.50% | -0.25% | -8.18% | -2.68% / -23.1%/yr | $0.38 | $0.10 | $0.199 | $24.29 | 95% | 3 | $-1.98 | none |
| single600_trig100 | -5.90% | -5.50% | -0.40% | -8.33% | -2.83% / -24.4%/yr | $0.39 | $0.10 | $1.658 | $24.29 | 95% | 25 | $-2.18 | none |
| live2400_trig600_perday4 | -5.58% | -5.50% | -0.07% | -8.01% | -2.50% / -21.6%/yr | $0.10 | $0.03 | $0.133 | $24.29 | 95% | 2 | $-0.51 | stale 196, session 319 (stale 0.5 d, session 0.74 d) |
| single1200_trig600 | -5.63% | -5.50% | -0.13% | -8.06% | -2.56% / -22.1%/yr | $0.20 | $0.05 | $0.133 | $24.29 | 95% | 2 | $-0.98 | stale 196, session 319 (stale 0.5 d, session 0.74 d) |
| fee_fixed150 | -5.77% | -5.50% | -0.26% | -8.20% | -2.69% / -23.2%/yr | $0.27 | $0.07 | $0.199 | $24.29 | 95% | 3 | $-1.99 | none |
| fee_x15 | -5.74% | -5.50% | -0.23% | -8.17% | -2.66% / -23.0%/yr | $0.58 | $0.15 | $0.199 | $24.29 | 95% | 3 | $-1.99 | none |
| fee_x2 | -5.72% | -5.50% | -0.22% | -8.15% | -2.65% / -22.8%/yr | $0.77 | $0.19 | $0.199 | $24.29 | 95% | 3 | $-1.99 | none |
| fee_x3 | -5.68% | -5.50% | -0.18% | -8.11% | -2.61% / -22.5%/yr | $1.16 | $0.29 | $0.199 | $24.29 | 95% | 3 | $-1.98 | none |
| full_range | -5.64% | -5.50% | -0.14% | -8.07% | -2.57% / -22.1%/yr | $0.23 | $0.06 | $0.000 | $24.29 | 0% | 0 | $-1.14 | none |

**Last 28 days**: 2026-10-05T00:00:32Z to 2026-11-02T08:19:00Z (28.3 days)

| run | return | hold | excess before maintenance | return after all costs | excess after all costs (window / annualised) | fees to holders | reserve cut | recenter gas | maintenance (assumed) | idle capital (avg) | recenters | worst week vs hold | blocked |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| single600_trig300 | -3.16% | -3.08% | -0.07% | -4.78% | -1.70% / -21.9%/yr | $0.21 | $0.05 | $0.133 | $16.26 | 95% | 2 | $-0.94 | none |
| single600_trig300_poll300 | -3.16% | -3.08% | -0.07% | -4.78% | -1.70% / -21.9%/yr | $0.21 | $0.05 | $0.133 | $16.26 | 95% | 2 | $-0.94 | none |
| single600_trig100 | -3.25% | -3.08% | -0.17% | -4.88% | -1.79% / -23.1%/yr | $0.21 | $0.05 | $1.061 | $16.26 | 95% | 16 | $-1.15 | none |
| live2400_trig600_perday4 | -3.11% | -3.08% | -0.02% | -4.73% | -1.65% / -21.2%/yr | $0.06 | $0.01 | $0.066 | $16.26 | 95% | 1 | $-0.30 | none |
| single1200_trig600 | -3.12% | -3.08% | -0.04% | -4.75% | -1.66% / -21.4%/yr | $0.11 | $0.03 | $0.066 | $16.26 | 95% | 1 | $-0.51 | none |
| fee_fixed150 | -3.16% | -3.08% | -0.08% | -4.79% | -1.71% / -22.0%/yr | $0.15 | $0.04 | $0.133 | $16.26 | 95% | 2 | $-0.94 | none |
| fee_x15 | -3.15% | -3.08% | -0.06% | -4.77% | -1.69% / -21.8%/yr | $0.32 | $0.08 | $0.133 | $16.26 | 95% | 2 | $-0.94 | none |
| fee_x2 | -3.14% | -3.08% | -0.05% | -4.76% | -1.68% / -21.6%/yr | $0.42 | $0.11 | $0.133 | $16.26 | 95% | 2 | $-0.93 | none |
| fee_x3 | -3.12% | -3.08% | -0.03% | -4.74% | -1.66% / -21.4%/yr | $0.63 | $0.16 | $0.133 | $16.26 | 95% | 2 | $-0.92 | none |
| full_range | -3.12% | -3.08% | -0.04% | -4.75% | -1.66% / -21.4%/yr | $0.13 | $0.03 | $0.000 | $16.26 | 0% | 0 | $-0.53 | none |

**Fees minus LVR minus costs (a different benchmark from holding; not subtracted from any return above)**

| run (full window) | fees to holders | LVR vs delta-matched benchmark | recenter gas | maintenance (assumed) | fees - LVR - gas | fees - LVR - gas - maintenance | maintenance if the observed average $0.1428/day: excess after all costs |
| --- | --- | --- | --- | --- | --- | --- | --- |
| single600_trig300 | $0.39 | $1.23 | $0.199 | $24.29 | $-1.04 | $-25.34 | -0.86% / -7.4%/yr |
| single600_trig300_poll300 | $0.38 | $1.22 | $0.199 | $24.29 | $-1.04 | $-25.33 | -0.86% / -7.4%/yr |
| full_range | $0.23 | $0.94 | $0.000 | $24.29 | $-0.71 | $-25.01 | -0.74% / -6.4%/yr |

**Supplier returns versus protocol-paid operating costs (full window)**. In the contract the keeper wallet pays gas and the vault owes suppliers only their claims, so recenter gas and maintenance are operator/protocol costs, not deductions from supplier principal. Supplier view = the run with recenter gas added back; protocol view = reserve-cut inflow minus gas minus maintenance.

| run | supplier return | hold | supplier excess vs hold (window / annualised) | reserve-cut inflow to the loss reserve | recenter gas (operator-paid) | maintenance (operator-paid, assumed) | protocol net (inflow - costs) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| single600_trig300 | -5.74% | -5.50% | -0.23% / -2.0%/yr | $0.10 | $0.199 | $24.29 | $-24.40 |
| single600_trig300_poll300 | -5.73% | -5.50% | -0.23% / -2.0%/yr | $0.10 | $0.199 | $24.29 | $-24.40 |
| full_range | -5.64% | -5.50% | -0.14% / -1.2%/yr | $0.06 | $0.000 | $24.29 | $-24.24 |

**Fee history and fee stress (full window; time-varying fee in force on every swap is the baseline; the stresses scale or replace it with volume unchanged, so they are upper-bound fee scenarios, not forecasts)**

| run | fees to holders | supplier excess vs hold (window / annualised) | after all assumed costs incl. maintenance (window / annualised) |
| --- | --- | --- | --- |
| actual fee in force (baseline) | $0.39 | -0.23% / -2.0%/yr | -2.68% / -23.1%/yr |
| constant 150 ppm (listed / instantaneous) | $0.27 | -0.24% / -2.1%/yr | -2.69% / -23.2%/yr |
| fee x1.5 | $0.58 | -0.21% / -1.9%/yr | -2.66% / -23.0%/yr |
| fee x2 | $0.77 | -0.20% / -1.7%/yr | -2.65% / -22.8%/yr |
| fee x3 | $1.16 | -0.16% / -1.4%/yr | -2.61% / -22.5%/yr |

Excess versus holding = fees to holders + inventory effect - recenter gas (baseline: $0.39 + $-2.72 - $0.199); end state idle USDG $435, idle stock $460; RAM accrued 95.5 tokens, valued at zero.


### 7B. Research scenarios (clearly NOT the current caps)

$10,000 pair with the ranged cap equal to capital (the same engine, larger size). These are research scenarios for understanding the strategy, not what the contract is configured to allow.

### SPY: RESEARCH SCENARIO: $10,000 pair, ranged cap = capital (not a current cap)

Tick spacing 1, 655,414 swaps, pool head block 104,071,867, starting capital $10,000, ranged cap $10,000.

**Full window**: 2026-08-19T22:30:12Z to 2026-11-02T08:18:27Z (74.4 days)

| run | return | hold | excess before maintenance | return after all costs | excess after all costs (window / annualised) | fees to holders | reserve cut | recenter gas | maintenance (assumed) | idle capital (avg) | recenters | worst week vs hold | blocked |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| single600_trig300 | +0.20% | -0.62% | +0.82% | -0.22% | +0.40% / +2.0%/yr | $89.38 | $22.35 | $0.000 | $42.69 | 2% | 0 | $-21.00 | none |
| single600_trig300_poll300 | +0.20% | -0.62% | +0.82% | -0.22% | +0.40% / +2.0%/yr | $89.38 | $22.35 | $0.000 | $42.69 | 2% | 0 | $-21.00 | none |
| single600_trig100 | +0.01% | -0.62% | +0.63% | -0.42% | +0.20% / +1.0%/yr | $79.77 | $19.94 | $1.392 | $42.69 | 16% | 21 | $-47.21 | none |
| live2400_trig600_perday4 | -0.40% | -0.62% | +0.22% | -0.82% | -0.20% / -1.0%/yr | $24.28 | $6.07 | $0.000 | $42.69 | 1% | 0 | $-5.57 | none |
| single1200_trig600 | -0.19% | -0.62% | +0.43% | -0.62% | +0.00% / +0.0%/yr | $46.52 | $11.63 | $0.000 | $42.69 | 1% | 0 | $-10.76 | none |
| single600_trig300_noreserve | +0.43% | -0.62% | +1.05% | -0.00% | +0.62% / +3.0%/yr | $111.73 | $0.00 | $0.000 | $42.69 | 3% | 0 | $-20.98 | none |
| single600_trig300_gas040 | +0.20% | -0.62% | +0.82% | -0.22% | +0.40% / +2.0%/yr | $89.38 | $22.35 | $0.000 | $42.69 | 2% | 0 | $-21.00 | none |
| single600_trig300_cap5k | -0.20% | -0.62% | +0.42% | -0.63% | -0.01% / -0.0%/yr | $45.43 | $11.36 | $0.000 | $42.69 | 51% | 0 | $-10.50 | none |
| single600_trig300_histshare | +0.84% | -0.62% | +1.46% | +0.42% | +1.04% / +5.1%/yr | $153.79 | $38.45 | $0.000 | $42.69 | 3% | 0 | $-21.75 | none |
| fee_fixed150 | +0.60% | -0.62% | +1.22% | +0.17% | +0.80% / +3.9%/yr | $129.36 | $32.34 | $0.000 | $42.69 | 3% | 0 | $-21.22 | none |
| fee_x15 | +0.65% | -0.62% | +1.27% | +0.22% | +0.84% / +4.1%/yr | $134.07 | $33.52 | $0.000 | $42.69 | 3% | 0 | $-20.97 | none |
| fee_x2 | +1.09% | -0.62% | +1.71% | +0.67% | +1.29% / +6.3%/yr | $178.76 | $44.69 | $0.000 | $42.69 | 3% | 0 | $-20.94 | none |
| fee_x3 | +1.98% | -0.62% | +2.60% | +1.55% | +2.18% / +10.7%/yr | $268.15 | $67.04 | $0.000 | $42.69 | 4% | 0 | $-20.91 | none |
| full_range | -0.59% | -0.62% | +0.03% | -1.02% | -0.40% / -2.0%/yr | $2.78 | $0.70 | $0.000 | $42.69 | 0% | 0 | $-0.63 | none |

**Last 28 days**: 2026-10-05T00:01:03Z to 2026-11-02T08:18:27Z (28.3 days)

| run | return | hold | excess before maintenance | return after all costs | excess after all costs (window / annualised) | fees to holders | reserve cut | recenter gas | maintenance (assumed) | idle capital (avg) | recenters | worst week vs hold | blocked |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| single600_trig300 | -0.76% | -0.81% | +0.05% | -0.92% | -0.11% / -1.5%/yr | $16.13 | $4.03 | $0.000 | $16.26 | 0% | 0 | $-28.12 | none |
| single600_trig300_poll300 | -0.76% | -0.81% | +0.05% | -0.92% | -0.11% / -1.5%/yr | $16.13 | $4.03 | $0.000 | $16.26 | 0% | 0 | $-28.12 | none |
| single600_trig100 | -0.74% | -0.81% | +0.07% | -0.90% | -0.09% / -1.2%/yr | $14.27 | $3.57 | $0.663 | $16.26 | 14% | 10 | $-23.18 | none |
| live2400_trig600_perday4 | -0.79% | -0.81% | +0.01% | -0.96% | -0.15% / -1.9%/yr | $4.26 | $1.07 | $0.000 | $16.26 | 0% | 0 | $-7.36 | none |
| single1200_trig600 | -0.78% | -0.81% | +0.03% | -0.94% | -0.14% / -1.8%/yr | $8.25 | $2.06 | $0.000 | $16.26 | 0% | 0 | $-14.29 | none |
| fee_fixed150 | -0.71% | -0.81% | +0.10% | -0.87% | -0.07% / -0.8%/yr | $20.98 | $5.25 | $0.000 | $16.26 | 0% | 0 | $-27.93 | none |
| fee_x15 | -0.68% | -0.81% | +0.13% | -0.84% | -0.03% / -0.4%/yr | $24.20 | $6.05 | $0.000 | $16.26 | 0% | 0 | $-27.67 | none |
| fee_x2 | -0.60% | -0.81% | +0.21% | -0.76% | +0.05% / +0.6%/yr | $32.26 | $8.07 | $0.000 | $16.26 | 0% | 0 | $-27.21 | none |
| fee_x3 | -0.44% | -0.81% | +0.37% | -0.60% | +0.21% / +2.7%/yr | $48.39 | $12.10 | $0.000 | $16.26 | 0% | 0 | $-26.29 | none |
| full_range | -0.81% | -0.81% | +0.00% | -0.97% | -0.16% / -2.1%/yr | $0.48 | $0.12 | $0.000 | $16.26 | 0% | 0 | $-0.83 | none |

**Fees minus LVR minus costs (a different benchmark from holding; not subtracted from any return above)**

| run (full window) | fees to holders | LVR vs delta-matched benchmark | recenter gas | maintenance (assumed) | fees - LVR - gas | fees - LVR - gas - maintenance | maintenance if the observed average $0.1428/day: excess after all costs |
| --- | --- | --- | --- | --- | --- | --- | --- |
| single600_trig300 | $89.38 | $88.19 | $0.000 | $42.69 | $1.20 | $-41.49 | +0.72% / +3.5%/yr |
| single600_trig300_poll300 | $89.38 | $88.19 | $0.000 | $42.69 | $1.20 | $-41.49 | +0.72% / +3.5%/yr |
| full_range | $2.78 | $2.92 | $0.000 | $42.69 | $-0.14 | $-42.83 | -0.08% / -0.4%/yr |

**Supplier returns versus protocol-paid operating costs (full window)**. In the contract the keeper wallet pays gas and the vault owes suppliers only their claims, so recenter gas and maintenance are operator/protocol costs, not deductions from supplier principal. Supplier view = the run with recenter gas added back; protocol view = reserve-cut inflow minus gas minus maintenance.

| run | supplier return | hold | supplier excess vs hold (window / annualised) | reserve-cut inflow to the loss reserve | recenter gas (operator-paid) | maintenance (operator-paid, assumed) | protocol net (inflow - costs) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| single600_trig300 | +0.20% | -0.62% | +0.82% / +4.0%/yr | $22.35 | $0.000 | $42.69 | $-20.34 |
| single600_trig300_poll300 | +0.20% | -0.62% | +0.82% / +4.0%/yr | $22.35 | $0.000 | $42.69 | $-20.34 |
| full_range | -0.59% | -0.62% | +0.03% / +0.1%/yr | $0.70 | $0.000 | $42.69 | $-41.99 |

**Fee history and fee stress (full window; time-varying fee in force on every swap is the baseline; the stresses scale or replace it with volume unchanged, so they are upper-bound fee scenarios, not forecasts)**

| run | fees to holders | supplier excess vs hold (window / annualised) | after all assumed costs incl. maintenance (window / annualised) |
| --- | --- | --- | --- |
| actual fee in force (baseline) | $89.38 | +0.82% / +4.0%/yr | +0.40% / +2.0%/yr |
| constant 150 ppm (listed / instantaneous) | $129.36 | +1.22% / +6.0%/yr | +0.80% / +3.9%/yr |
| fee x1.5 | $134.07 | +1.27% / +6.2%/yr | +0.84% / +4.1%/yr |
| fee x2 | $178.76 | +1.71% / +8.4%/yr | +1.29% / +6.3%/yr |
| fee x3 | $268.15 | +2.60% / +12.8%/yr | +2.18% / +10.7%/yr |

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
| live2400_trig600_perday4 | +0.69% | -0.32% | +1.01% | +0.18% | +0.49% / +2.0%/yr | $56.36 | $14.09 | $0.398 | $51.64 | 29% | 6 | $-169.58 | none |
| single1200_trig600 | +1.65% | -0.32% | +1.97% | +1.13% | +1.45% / +5.9%/yr | $59.35 | $14.84 | $0.398 | $51.64 | 61% | 6 | $-287.82 | none |
| single600_trig300_noreserve | +1.94% | -0.32% | +2.26% | +1.42% | +1.74% / +7.1%/yr | $42.44 | $0.00 | $1.326 | $51.64 | 88% | 20 | $-397.99 | none |
| single600_trig300_gas040 | +1.79% | -0.32% | +2.11% | +1.27% | +1.59% / +6.4%/yr | $33.89 | $8.47 | $8.000 | $51.64 | 88% | 20 | $-399.58 | none |
| single600_trig300_cap5k | +2.75% | -0.32% | +3.07% | +2.24% | +2.55% / +10.4%/yr | $80.90 | $20.23 | $1.326 | $51.64 | 71% | 20 | $-336.75 | none |
| single600_trig300_histshare | +2.13% | -0.32% | +2.45% | +1.61% | +1.93% / +7.8%/yr | $61.79 | $15.45 | $1.326 | $51.64 | 88% | 20 | $-396.47 | none |
| fee_fixed150 | +1.67% | -0.32% | +1.99% | +1.15% | +1.47% / +6.0%/yr | $15.06 | $3.77 | $1.326 | $51.64 | 88% | 20 | $-400.21 | none |
| fee_x15 | +2.02% | -0.32% | +2.34% | +1.51% | +1.82% / +7.4%/yr | $51.02 | $12.76 | $1.326 | $51.64 | 88% | 20 | $-397.41 | none |
| fee_x2 | +2.19% | -0.32% | +2.51% | +1.68% | +1.99% / +8.1%/yr | $68.29 | $17.07 | $1.326 | $51.64 | 88% | 20 | $-396.24 | none |
| fee_x3 | +2.54% | -0.32% | +2.85% | +2.02% | +2.34% / +9.5%/yr | $103.21 | $25.80 | $1.326 | $51.64 | 88% | 20 | $-393.88 | none |
| full_range | -0.22% | -0.32% | +0.10% | -0.73% | -0.42% / -1.7%/yr | $10.27 | $2.57 | $0.000 | $51.64 | 0% | 0 | $-20.19 | none |

**Last 28 days**: 2026-10-05T00:00:42Z to 2026-11-02T11:27:06Z (28.5 days)

| run | return | hold | excess before maintenance | return after all costs | excess after all costs (window / annualised) | fees to holders | reserve cut | recenter gas | maintenance (assumed) | idle capital (avg) | recenters | worst week vs hold | blocked |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| single600_trig300 | -7.90% | -5.86% | -2.03% | -8.06% | -2.20% / -28.1%/yr | $28.16 | $7.04 | $0.530 | $16.34 | 62% | 8 | $-375.81 | none |
| single600_trig300_poll300 | -8.25% | -5.86% | -2.38% | -8.41% | -2.55% / -32.6%/yr | $27.51 | $6.88 | $0.530 | $16.34 | 62% | 8 | $-378.03 | none |
| single600_trig100 | -9.06% | -5.86% | -3.19% | -9.22% | -3.36% / -43.0%/yr | $25.17 | $6.29 | $3.978 | $16.34 | 63% | 60 | $-382.38 | cooldown 1363 (cooldown 0.12 d) |
| live2400_trig600_perday4 | -7.23% | -5.86% | -1.37% | -7.39% | -1.53% / -19.6%/yr | $15.26 | $3.81 | $0.133 | $16.34 | 19% | 2 | $-175.76 | none |
| single1200_trig600 | -8.38% | -5.86% | -2.51% | -8.54% | -2.68% / -34.3%/yr | $22.81 | $5.70 | $0.133 | $16.34 | 37% | 2 | $-295.89 | none |
| fee_fixed150 | -7.88% | -5.86% | -2.02% | -8.04% | -2.18% / -27.9%/yr | $30.13 | $7.53 | $0.530 | $16.34 | 62% | 8 | $-376.08 | none |
| fee_x15 | -7.76% | -5.86% | -1.90% | -7.93% | -2.06% / -26.5%/yr | $42.28 | $10.57 | $0.530 | $16.34 | 62% | 8 | $-376.08 | none |
| fee_x2 | -7.63% | -5.86% | -1.77% | -7.79% | -1.93% / -24.8%/yr | $56.41 | $14.10 | $0.530 | $16.34 | 62% | 8 | $-376.35 | none |
| fee_x3 | -7.37% | -5.86% | -1.50% | -7.53% | -1.67% / -21.4%/yr | $84.76 | $21.19 | $0.530 | $16.34 | 62% | 8 | $-376.90 | none |
| full_range | -6.02% | -5.86% | -0.16% | -6.19% | -0.33% / -4.2%/yr | $2.14 | $0.53 | $0.000 | $16.34 | 0% | 0 | $-22.50 | none |

**Fees minus LVR minus costs (a different benchmark from holding; not subtracted from any return above)**

| run (full window) | fees to holders | LVR vs delta-matched benchmark | recenter gas | maintenance (assumed) | fees - LVR - gas | fees - LVR - gas - maintenance | maintenance if the observed average $0.1428/day: excess after all costs |
| --- | --- | --- | --- | --- | --- | --- | --- |
| single600_trig300 | $33.89 | $143.66 | $1.326 | $51.64 | $-111.09 | $-162.74 | +2.04% / +8.3%/yr |
| single600_trig300_poll300 | $23.51 | $105.93 | $1.658 | $51.64 | $-84.08 | $-135.72 | +1.97% / +8.0%/yr |
| full_range | $10.27 | $21.77 | $0.000 | $51.64 | $-11.50 | $-63.14 | -0.03% / -0.1%/yr |

**Supplier returns versus protocol-paid operating costs (full window)**. In the contract the keeper wallet pays gas and the vault owes suppliers only their claims, so recenter gas and maintenance are operator/protocol costs, not deductions from supplier principal. Supplier view = the run with recenter gas added back; protocol view = reserve-cut inflow minus gas minus maintenance.

| run | supplier return | hold | supplier excess vs hold (window / annualised) | reserve-cut inflow to the loss reserve | recenter gas (operator-paid) | maintenance (operator-paid, assumed) | protocol net (inflow - costs) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| single600_trig300 | +1.87% | -0.32% | +2.19% / +8.9%/yr | $8.47 | $1.326 | $51.64 | $-44.49 |
| single600_trig300_poll300 | +1.80% | -0.32% | +2.12% / +8.6%/yr | $5.88 | $1.658 | $51.64 | $-47.42 |
| full_range | -0.22% | -0.32% | +0.10% / +0.4%/yr | $2.57 | $0.000 | $51.64 | $-49.07 |

**Fee history and fee stress (full window; time-varying fee in force on every swap is the baseline; the stresses scale or replace it with volume unchanged, so they are upper-bound fee scenarios, not forecasts)**

| run | fees to holders | supplier excess vs hold (window / annualised) | after all assumed costs incl. maintenance (window / annualised) |
| --- | --- | --- | --- |
| actual fee in force (baseline) | $33.89 | +2.19% / +8.9%/yr | +1.66% / +6.7%/yr |
| constant 150 ppm (listed / instantaneous) | $15.06 | +2.00% / +8.1%/yr | +1.47% / +6.0%/yr |
| fee x1.5 | $51.02 | +2.35% / +9.5%/yr | +1.82% / +7.4%/yr |
| fee x2 | $68.29 | +2.52% / +10.2%/yr | +1.99% / +8.1%/yr |
| fee x3 | $103.21 | +2.87% / +11.6%/yr | +2.34% / +9.5%/yr |

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

### GLD: RESEARCH SCENARIO: $10,000 pair, ranged cap = capital (not a current cap)

Tick spacing 1, 93,803 swaps, pool head block 104,071,867, starting capital $10,000, ranged cap $10,000.

**Full window**: 2026-09-21T00:00:51Z to 2026-11-02T08:19:00Z (42.3 days)

| run | return | hold | excess before maintenance | return after all costs | excess after all costs (window / annualised) | fees to holders | reserve cut | recenter gas | maintenance (assumed) | idle capital (avg) | recenters | worst week vs hold | blocked |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| single600_trig300 | -8.79% | -5.50% | -3.29% | -9.04% | -3.54% / -30.5%/yr | $34.09 | $8.52 | $0.199 | $24.29 | 61% | 3 | $-212.55 | none |
| single600_trig300_poll300 | -8.80% | -5.50% | -3.30% | -9.05% | -3.54% / -30.6%/yr | $33.34 | $8.34 | $0.199 | $24.29 | 62% | 3 | $-212.66 | none |
| single600_trig100 | -8.70% | -5.50% | -3.20% | -8.94% | -3.44% / -29.7%/yr | $30.87 | $7.72 | $1.658 | $24.29 | 64% | 25 | $-211.38 | none |
| live2400_trig600_perday4 | -6.67% | -5.50% | -1.17% | -6.92% | -1.41% / -12.2%/yr | $17.25 | $4.31 | $0.133 | $24.29 | 16% | 2 | $-91.52 | stale 196, session 319 (stale 0.5 d, session 0.74 d) |
| single1200_trig600 | -7.66% | -5.50% | -2.16% | -7.91% | -2.40% / -20.7%/yr | $28.07 | $7.02 | $0.133 | $24.29 | 32% | 2 | $-159.05 | stale 196, session 319 (stale 0.5 d, session 0.74 d) |
| single600_trig300_noreserve | -8.71% | -5.50% | -3.21% | -8.96% | -3.45% / -29.8%/yr | $42.64 | $0.00 | $0.199 | $24.29 | 61% | 3 | $-212.70 | none |
| single600_trig300_gas040 | -8.80% | -5.50% | -3.30% | -9.05% | -3.55% / -30.6%/yr | $34.09 | $8.52 | $1.200 | $24.29 | 61% | 3 | $-212.88 | none |
| single600_trig300_cap5k | -7.79% | -5.50% | -2.29% | -8.04% | -2.54% / -21.9%/yr | $35.40 | $8.85 | $0.199 | $24.29 | 53% | 3 | $-182.31 | none |
| single600_trig300_histshare | -8.54% | -5.50% | -3.04% | -8.79% | -3.29% / -28.3%/yr | $60.61 | $15.15 | $0.199 | $24.29 | 61% | 3 | $-213.57 | none |
| fee_fixed150 | -8.90% | -5.50% | -3.40% | -9.14% | -3.64% / -31.4%/yr | $22.76 | $5.69 | $0.199 | $24.29 | 61% | 3 | $-212.13 | none |
| fee_x15 | -8.63% | -5.50% | -3.13% | -8.88% | -3.37% / -29.1%/yr | $51.20 | $12.80 | $0.199 | $24.29 | 61% | 3 | $-212.84 | none |
| fee_x2 | -8.47% | -5.50% | -2.97% | -8.71% | -3.21% / -27.7%/yr | $68.37 | $17.09 | $0.199 | $24.29 | 61% | 3 | $-213.13 | none |
| fee_x3 | -8.14% | -5.50% | -2.64% | -8.39% | -2.88% / -24.9%/yr | $102.84 | $25.71 | $0.199 | $24.29 | 61% | 3 | $-213.71 | none |
| full_range | -5.64% | -5.50% | -0.14% | -5.88% | -0.38% / -3.3%/yr | $2.27 | $0.57 | $0.000 | $24.29 | 0% | 0 | $-11.37 | none |

**Last 28 days**: 2026-10-05T00:00:32Z to 2026-11-02T08:19:00Z (28.3 days)

| run | return | hold | excess before maintenance | return after all costs | excess after all costs (window / annualised) | fees to holders | reserve cut | recenter gas | maintenance (assumed) | idle capital (avg) | recenters | worst week vs hold | blocked |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| single600_trig300 | -4.22% | -3.08% | -1.13% | -4.38% | -1.30% / -16.7%/yr | $32.39 | $8.10 | $0.133 | $16.26 | 24% | 2 | $-157.55 | none |
| single600_trig300_poll300 | -4.23% | -3.08% | -1.15% | -4.40% | -1.31% / -16.9%/yr | $32.01 | $8.00 | $0.133 | $16.26 | 25% | 2 | $-160.96 | none |
| single600_trig100 | -4.30% | -3.08% | -1.22% | -4.46% | -1.38% / -17.8%/yr | $30.51 | $7.63 | $1.061 | $16.26 | 28% | 16 | $-152.15 | none |
| live2400_trig600_perday4 | -3.41% | -3.08% | -0.33% | -3.57% | -0.49% / -6.3%/yr | $10.73 | $2.68 | $0.066 | $16.26 | 3% | 1 | $-45.87 | none |
| single1200_trig600 | -3.71% | -3.08% | -0.63% | -3.88% | -0.79% / -10.2%/yr | $20.21 | $5.05 | $0.066 | $16.26 | 5% | 1 | $-87.57 | none |
| fee_fixed150 | -4.31% | -3.08% | -1.23% | -4.47% | -1.39% / -17.9%/yr | $22.78 | $5.70 | $0.133 | $16.26 | 24% | 2 | $-157.29 | none |
| fee_x15 | -4.06% | -3.08% | -0.98% | -4.22% | -1.14% / -14.7%/yr | $48.61 | $12.15 | $0.133 | $16.26 | 24% | 2 | $-157.35 | none |
| fee_x2 | -3.90% | -3.08% | -0.82% | -4.07% | -0.98% / -12.7%/yr | $64.85 | $16.21 | $0.133 | $16.26 | 24% | 2 | $-157.13 | none |
| fee_x3 | -3.59% | -3.08% | -0.50% | -3.75% | -0.67% / -8.6%/yr | $97.35 | $24.34 | $0.133 | $16.26 | 24% | 2 | $-156.70 | none |
| full_range | -3.12% | -3.08% | -0.04% | -3.28% | -0.20% / -2.6%/yr | $1.25 | $0.31 | $0.000 | $16.26 | 0% | 0 | $-5.26 | none |

**Fees minus LVR minus costs (a different benchmark from holding; not subtracted from any return above)**

| run (full window) | fees to holders | LVR vs delta-matched benchmark | recenter gas | maintenance (assumed) | fees - LVR - gas | fees - LVR - gas - maintenance | maintenance if the observed average $0.1428/day: excess after all costs |
| --- | --- | --- | --- | --- | --- | --- | --- |
| single600_trig300 | $34.09 | $61.59 | $0.199 | $24.29 | $-27.70 | $-52.00 | -3.35% / -28.9%/yr |
| single600_trig300_poll300 | $33.34 | $60.14 | $0.199 | $24.29 | $-26.99 | $-51.29 | -3.36% / -29.0%/yr |
| full_range | $2.27 | $9.43 | $0.000 | $24.29 | $-7.16 | $-31.45 | -0.20% / -1.7%/yr |

**Supplier returns versus protocol-paid operating costs (full window)**. In the contract the keeper wallet pays gas and the vault owes suppliers only their claims, so recenter gas and maintenance are operator/protocol costs, not deductions from supplier principal. Supplier view = the run with recenter gas added back; protocol view = reserve-cut inflow minus gas minus maintenance.

| run | supplier return | hold | supplier excess vs hold (window / annualised) | reserve-cut inflow to the loss reserve | recenter gas (operator-paid) | maintenance (operator-paid, assumed) | protocol net (inflow - costs) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| single600_trig300 | -8.79% | -5.50% | -3.29% / -28.4%/yr | $8.52 | $0.199 | $24.29 | $-15.97 |
| single600_trig300_poll300 | -8.80% | -5.50% | -3.30% / -28.4%/yr | $8.34 | $0.199 | $24.29 | $-16.16 |
| full_range | -5.64% | -5.50% | -0.14% / -1.2%/yr | $0.57 | $0.000 | $24.29 | $-23.73 |

**Fee history and fee stress (full window; time-varying fee in force on every swap is the baseline; the stresses scale or replace it with volume unchanged, so they are upper-bound fee scenarios, not forecasts)**

| run | fees to holders | supplier excess vs hold (window / annualised) | after all assumed costs incl. maintenance (window / annualised) |
| --- | --- | --- | --- |
| actual fee in force (baseline) | $34.09 | -3.29% / -28.4%/yr | -3.54% / -30.5%/yr |
| constant 150 ppm (listed / instantaneous) | $22.76 | -3.40% / -29.3%/yr | -3.64% / -31.4%/yr |
| fee x1.5 | $51.20 | -3.13% / -27.0%/yr | -3.37% / -29.1%/yr |
| fee x2 | $68.37 | -2.97% / -25.6%/yr | -3.21% / -27.7%/yr |
| fee x3 | $102.84 | -2.64% / -22.7%/yr | -2.88% / -24.9%/yr |

Excess versus holding = fees to holders + inventory effect - recenter gas (baseline: $34.09 + $-363.13 - $0.199); end state idle USDG $0, idle stock $8140; RAM accrued 355.9 tokens, valued at zero.

**Windows (baseline, before maintenance) and dilution**

| window | excess (window) | annualised |
| --- | --- | --- |
| t1 2026-09-21 to 2026-10-04 | -0.84% | -23.2%/yr |
| t2 2026-10-05 to 2026-10-18 | +0.21% | +5.7%/yr |
| t3 2026-10-19 to 2026-11-02 | -1.56% | -39.6%/yr |
| full, single600_trig300 at $1,000 | -3.30% | -28.4%/yr |
| full, full_range at $1,000 | -0.14% | -1.2%/yr |
| full, single600_trig300 at $50,000 | -3.34% | -28.8%/yr |
| full, full_range at $50,000 | -0.14% | -1.2%/yr |


**Reading the research scenarios.**

* **SPY** (zero recenters: no trigger fired, so this is a static +/-6.2% range). Supplier excess over holding +0.82% (+4.0%/yr): fees to holders $89.4, reserve cut $22.3, inventory effect -$6.9. After assumed maintenance (operator-paid, $42.69) +0.40% (+2.0%/yr), +0.72% with the observed-average maintenance. Fees minus LVR minus gas is +$1.20, break-even; minus maintenance -$41.49. Last 28 days: +0.05% before maintenance, -0.11% after.
* **NVDA** (Ramses pool). Supplier excess +2.19% over holding (+8.9%/yr) is **not fee income**: fees to holders $33.9, inventory effect +$184.6. With no swap at recenter each redeploy takes only matched value, the NVDA side was used up in the rally, and the position ended as $9,564 idle USDG (88% idle on average), i.e. mostly cash while NVDA fell. Fees minus LVR minus gas is -$111.1 and -$162.7 after maintenance. The sign flips by window: last 28 days -2.03% (-26%/yr) with a worst week of -$376. At 300-second polling the baseline's fees fall to $23.5 and the last-28-day excess to -2.38%.
* **GLD** (the Ramses pool, contract-feasible baseline): supplier excess **-3.29%** against holding over 42 days (-28%/yr); fees to holders $34.1 against LVR $61.6; 61% of capital idle. **Even with every fee tripled the feasible strategy is still -2.64% against holding**, and a constant 150 ppm gives -3.40%. So on this sample GLD's loss against holding is inventory/LVR, not the fee level, and higher fees (volume held fixed, an upper bound) do not close it.
* **The policy read back from the live Uniswap vault** (+/-2400 ticks, trigger 600, at most 4 per day; the `live2400_trig600_perday4` rows) is wider and recentres less: supplier excess over holding +0.22% (SPY), +1.01% (NVDA), -1.17% (GLD) against +0.82%, +2.19% and -3.29% for the 600-tick baseline. It loses less than the baseline where the baseline loses (GLD) and earns less where the baseline gains, the same inventory mechanism in both directions; it passes the contract's rules (checked by the feasibility gate).
* **Idle capital** is structural: matched-only redeploys leave the surplus side idle. A half-capital ranged cap raised the NVDA headline (+12.4%/yr) only because it keeps both idle sides balanced longer; that is the same inventory artefact, not a better strategy.
* **Blocked maintenance.** No due recenter was blocked by a stale feed or a closed session in any run (the guard paths are exercised by unit tests, not by these windows; the SPY feed is older than 12 h about half of the time, so this is not a statement that it is always fresh). The only blocks were cooldown blocks at trigger 100 on NVDA (2,605 evaluations, 0.5 day).
* **Reserve and RAM.** The 20% reserve cost holders about 0.23% over the SPY window. RAM accrued (883 tokens SPY, 222 NVDA) is valued at zero.
* Maintenance is $0.5737/day regardless of size: 2.1%/yr of $10,000, 0.42%/yr of $50,000, 21%/yr of $1,000.

**Scoped conclusion.** For the contract-feasible single-range configuration, fee income roughly equalled adverse selection on SPY and fell well short on NVDA and GLD; NVDA's positive headline versus holding is an inventory path that reverses in the latest window; at the current caps the strategy cannot pay for its own keeper under the assumed maintenance cost. This does not pass the profitability gate and says nothing in favour of lending reserves, borrow caps or liquidation capacity. These are three short samples of a modelled strategy, not a yield estimate.

## 8. GLD

**Feed label (captured).** `gld/directory-feeds-robinhood-mainnet.json` is the raw response (SHA-256 `69056e59...1674`, fetched 2026-11-02T11:50:59Z, HTTP 200, `etag "0cf583b21207184301c0feee7492fc24"`, `last-modified 2026-11-02 08:50:42 GMT`; headers in `gld/directory-headers.txt`) from the exact URL **`https://reference-data-directory.vercel.app/feeds-robinhood-mainnet.json`**. `gld/directory-selected-feeds.json` holds the SPY, NVDA and GLD entries. For GLD (proxy `0x470A51258068043bd43dC0a56245625C9fE86eB0`, contract `0xeA3F8E77a98d7877b6F8164569a8e82454cCcDB8`): `docs.attributeType = "dex_state_price"`, `productSubType = "Reference"`, `marketHours = "Crypto"`, `assetClass = "Crypto"`, `assetSubClass = "Pegged Asset"`, `feedCategory = "new"`. SPY and NVDA are `attributeType "other"`, `productSubType "calculatedPrice"`, `marketHours "us_equities_24/5"`. Of the 58 directory entries, 11 are `dex_state_price` (BTC.B, WEETH, USDS, EURC, WSTETH, WBTC, LBTC, CBBTC, SYRUPUSDC, GLD, USDE); GLD is the only tokenized stock or commodity among them.

**What this establishes.** The directory classifies the GLD feed as a DEX-state reference price on 24/7 hours, unlike the equity feeds. **It does not identify the source pools, venues, weights or window, and it does not show the feed can be moved by trading.** Node-observation dispersion (0.12% median spread for GLD against 0.002% for SPY), the answer's closeness to the Ramses GLD pool on weekends (median 0.022%), and its non-reaction to the 28.5-minute MAX_TICK episode of 2026-09-28 (`gld/*.json`) are observations that do not identify source pools. `gld/large-file-hashes.txt` records the SHA-256 of the large local probe files.

**Scoped statement about the tested LP strategies:** section 1, item 3, plus the contract-feasible GLD baseline in section 7 (supplier excess -3.29% over 42 days, still -2.64% with fees tripled). GLD's fee history is in section 5. Almost all of the grid configurations are hypothetical (section 6). GLD exit depth (pinned depth table) is a separate input to any lending or liquidation cap, and **GLD is not activated**.

## 9. Where entries, exits and liquidations use Ramses (existing contracts and tests)

Everything below is read from the contracts on this branch (`ramses-vaults/src`); no contract was changed. "Ramses" means the CL pool, its NonfungiblePositionManager and gauge, reached only through `RamsesV3PairedAdapter` / `RamsesVaultAdapter`, plus the direct `pool.swap` in `RamsesSwapLib` used by the bounded settlement swap.

| Event | Path | Touches Ramses? | Existing tests |
| --- | --- | --- | --- |
| **Entry: supply** | `RamsesBoostedDelegate.mint` (supply cap, `ZeroSharesMinted`, timelock seed) puts the underlying in the market as local cash | No | `Lending.t.sol`: `testSupplyCapIncludesPendingInterest`, `testSeedMakesDonationAttackCostlyAndZeroSharesRevert` |
| **Entry: to the vault** | The market keeps a local-cash buffer and `_depositToVault` -> `RamsesBoostedVault.depositForPair` moves the excess into the pair's idle balance (`rebalanceVault` / `syncVault` are operator calls; failures are caught and emitted, never silent success) | No (idle only) | `Lending.t.sol`: `testBufferBothSidesAndRamsesLP`, `testAllocationPausedSwallowKeepsCashAndNoApproval` |
| **Entry: into the LP** | Keeper `rebalance` -> `RamsesRangeLib.deploy` deploys *matched* value (min of the two idle sides at oracle prices) through the adapter into the position; `recenter` removes, burns, sets the new range and redeploys; both revert on stale checkpoint / oracle / pool deviation | **Yes** (position manager mint / increase / decrease / burn; gauge) | `Adapter.t.sol` (round trip, approvals, band checks, fuzz), `Phase2.t.sol` (`testMintRecenterBothDirectionsAndEmergency`), fork tests |
| **Exit: redeem** | `redeem` / `redeemUnderlying` -> `_checkpoint(true)` -> `_withdrawFromVault` -> `RamsesBoostedVault.withdrawForSide`. Local cash is used first; only the missing part is withdrawn. If liquidity is open or idle is short, the vault checkpoints, unwinds LP liquidity, settles the shortfall (reserve, then bounded swap) and recognizes any residual loss before paying | **Yes**, only when local cash and idle do not cover it | `Lending.t.sol`: `testStaleCashOnlyExitRollbackAndRefresh`, `testLossPreviewAndCheckpointBeforeCashExit`, `testBorrowUsesWithdrawableNotCustody`; `VaultV3PostExit.t.sol`, `VaultV3Recovery.t.sol` |
| **Exit: emergency** | Guardian `emergencyDecrease` recognizes loss atomically with the removal | **Yes** | `Lending.t.sol`: `testEmergencyExitWithMarketsHoldingClaims`, `testPartialEmergencyDoesNotBrickLocalCash` |
| **Settlement swap** (a withdrawal deficit only) | `RamsesSettlementLib._settlementSwap` -> `RamsesSwapLib.execute` -> `pool.swap`, oracle-floored and size-capped; **paused in the production configuration (`swapsPaused`)** | **Yes** (direct pool swap) | `LossReserve.t.sol`, `VaultV3Recovery.t.sol` |
| **Liquidation (lending)** | `liquidateBorrow` repays the debt **in the borrowed underlying, supplied by the liquidator**, and `seize` moves collateral pTokens to the liquidator at the close factor. Neither step calls Ramses or swaps | **No** | `Lending.t.sol`: `testLiquidationUsingDecimalCorrectOracle` (repay 20e6 in-kind, receive collateral shares, close-factor revert) |
| **Liquidation (margin)** | **Not present on this branch**: the Ramses markets have "no margin wiring; flash loans paused". The live margin stack's router adapter (`RobinhoodV4RouterAdapter`) is Uniswap v4 only; **there is no Ramses router adapter**, so a margin entry, exit or liquidation that must swap has no Ramses route today | Gap | none |

**Funded in-kind liquidation need not swap.** A liquidator who already holds the borrowed asset repays in kind and receives collateral pTokens; no pool is touched, so the lending liquidation path is independent of Ramses depth. A swap is needed only (a) by a liquidator who does not hold the debt asset and wants to convert, or whose seized collateral is redeemed for the underlying and sold, or (b) by a margin flow that must close a position by swapping. For r33 collateral, (a) is where local r33 sale capacity matters (section 4), and it is the liquidator's cost, not a protocol swap. A small pilot could show an in-kind liquidation end to end without any Ramses swap; that is a later, separately approved step.

**Gaps to close before any pilot (not done here):** the in-kind liquidation followed by a redemption that needs an LP unwind now has a local-fixture proof (section 15B) but no version on a fork of the real pools; no Ramses margin / router adapter; the one-sided case is proven for redemption (15C) but not for a seized-collateral market.

## 10. Withdrawals and principal when an optional LP becomes one-sided

Principal is kept **per side, in each side's native token** (`stockPrincipal`, `usdgPrincipal`, with `stockIdle` / `usdgIdle` for what sits in the vault). The LP position holds whatever mix the price implies: inside the range both tokens, above the range only USDG, below it only the stock token.

* **Recenter does not swap.** It removes the position and redeploys only matched value; the surplus of the larger side stays idle and earns nothing (`Phase2.t.sol`: `testOneSidedInventoryFinishesIdle`, `testOutOfRangePositionRecentersToOneSidedIdleThenSettles`). A one-sided inventory therefore ends as idle balances, which is why the backtests show a large idle share after a trending move.
* **A withdrawal is single-token.** `withdrawForSide(token, requested)` can pay only that side's token, up to `min(requested, that side's idle, that side's principal)`. If no liquidity is open and the side's idle covers the request, it is paid from idle without the oracle. Otherwise the vault (1) checkpoints: any shortfall of the pair against its benchmark at the oracle reference price is recognized **pro rata across both sides' principals**; (2) unwinds LP liquidity for the requested token with a small over-unwind. If none of the requested token is in the position (one-sided the other way) and settlement swaps are paused, it does **not** churn the position (`_unwindForWithdrawal`); (3) covers the deficit from the in-kind loss reserve within its per-event, daily and coverage-ratio limits, then from the bounded oracle-floored settlement swap if enabled (paused in production); (4) recognizes whatever remains as loss.
* **What the supplier sees.** `returned` is what the vault could pay in that token; `realizedLoss` is the principal reduction beyond it. The supplier is never paid in the other token as a substitute. When the position has drifted into the other token and the swap is off, the supplier of the *missing* token side is paid from idle and reserve and the rest of the claim is written down; the supplier of the token the position holds is paid in kind. Gains are recognized only while both native claims are covered, so a one-sided gain is not credited to the side that is short.
* **In the lending market** the delegate applies `returned` and `realizedLoss` atomically (`_withdrawFromVault`, `_redeemTokensLossAware`): the pToken exchange rate falls by the recognized loss before the redeemer is paid, and an exact-output redeem burns more pTokens.
* **For an optional r33 LP**: r33 deposited as direct collateral stays a plain market balance (section 11). Only an explicitly opted-in portion would enter an LP pair, and it would be subject to every point above, including the loss write-down on the side whose token the position no longer holds.

## 11. Direct r33 collateral exposure (kept separate from the SPY/GLD/USDG collateral set and from LP participation)

r33 is not part of the SPY/GLD/USDG collateral set. The partner's stated goal (partner-supplied, unverified here) is **borrowing against r33 while retaining r33 exposure**; the intended design is **direct collateral, with LP participation separately optional**. This brief keeps them apart in three ways. (1) An LP route does not preserve exposure: matched-only recentering converts inventory (section 7; section 10 for what a one-sided position pays out), so r33 posted as collateral must be the plain asset, not an LP position. (2) The group shares one controller with cross-collateral, so **an r33 market with a collateral factor above zero would count toward SPY/GLD/USDG borrowers' liquidity and put the group's suppliers behind r33.** That is a change from handoff 01 tier 0 (collateral factor 0), and nothing here proposes it. (3) Liquidations of direct r33 collateral are funded in kind (section 9): the protocol does not swap r33, but whoever liquidates must be able to sell it, which is what section 4 measures.

**Blockers, not resolved here:** r33 has no Chainlink feed and no local price source is established (the only local r33/USDG concentrated pools are thin and one-sided, so a price built on them would be manipulable); the contract's control facts (owner, mint, pause, blacklist, transfer tax; partner-supplied claim that it is an ERC-20/OFT whose staking vault and redemption live on HyperEVM) are unverified; and HyperEVM redemption is not an atomic fallback, so liquidation economics must stand on Robinhood Chain liquidity alone. **No r33 collateral factor and no r33 activation** while pricing and executable capacity under the actual liquidation mechanism are unproven. The next capacity proof is the complete route executed atomically at one fork block with real fees, slippage limits and adapter requirements (section 4); the approximately 2% to 3% figure is relative to the CL200 spot. Handoff 01 requires `research/R33-RAM.md` (who controls the r33 contract and which price source is acceptable) before any positive collateral factor; that has not been done.

## 12. Pins, hashes, keeper receipts, input bundle, release, reproduction

`pins-and-receipts.json` holds full hashes and headers for the research pins (all `l1BlockNumber` checks pass); `pools/pool-quotes.json`, `pools/r33-routes.json` and `pools/inputs/<pool>/<block>-<hash>.json` hold each pool's own pin and the **full saved state** (sqrtPriceX96, slot0 words, liquidity, bitmap words, ticks), referenced by path and SHA-256 from the quotes and from the fork proofs. Short form of the earlier pins:

| Use | L2 block | Native (l1BlockNumber) | Block hash | Provenance |
| --- | --- | --- | --- | --- |
| Fee-entitlement fork tests (`logs/fork-final.log`, 3 pairs, 3 tests each, all pass) | 104,116,096 | 26,326,238 | `0x9211f82fbe82275c5ebd239719fa4aaef504a2fc857fc9397c55cb06de776cbb` | printed as `pin 104116096 26326238` in the log |
| Depth fork tests (`logs/fork-depth-all.log`, 3 pairs, all pass) | 104,096,178 | 26,326,064 | `0x878484914d4b76373cfb8a15e8208719b2ddf51d8373e29174464cbd1b0362f1` | **recovered**: the log prints only `DEPTH_BLOCK: 26326064`. The L2 number came from an earlier subagent report; its header's native number matches the log, but about a hundred consecutive L2 blocks share one native number, so this is consistent with, not derived from, the log |
| Keeper verification | 104,172,613 | 26,326,728 | `0xa98daed63190376b96b042414b59527f67a88fd20d53fb88ceedd1432bf8d439` | 2026-11-02T11:14:39Z |
| SPY / GLD dataset head | 104,071,867 | 26,325,854 | `0xbad7a6a33c1bec4a6f432e13b92bcf50edba64825420c41910bb666196175b0d` | `bundle/manifest-v1.json` |
| NVDA dataset head / grid anchor | 104,187,711 / 104,180,000 | 26,326,860 / 26,326,793 | see `pins-and-receipts.json` | 8 swaps after the anchor dropped |
| Pool quotes, fork replay, routes | one per pool | recorded | recorded in each inputs file | `pools/inputs/*.json`, `pools/fork-replay/*.json` |

**Keeper transactions (RPC receipts, status 1):** checkpoint `0xcad407716d6b7299784206d773d376ca75f6148d98d7c14b98cafc9f7403476d` (block 104,165,913, nonce 138, gas 523,686, effective gas price 20,094,000 wei, 0.00001052 ETH, 19 logs) and rebalance `0x7cb6988a23127109837531ecf2fdf2c3f4e95d499a4eb750ac8dac3116078031` (block 104,165,952, nonce 139, gas 735,438, 0.00001486 ETH, 31 logs), both from `0x978e2ADF18da8B846552932c0aB2B1193da1fAc4` to the production vault. Keeper nonce 140 and balance 0.005269526 ETH at block 104,193,993. **Mined reverted sends: none found** (nonce 138 equalled 138 event-emitting transactions in the PR #13 evidence; the two receipts explain 138 and 139). The `CheckpointStale` / `execution reverted` lines in the keeper log are never-mined simulation rejections: no receipt, no cost.

**Input bundle (hash-verified, versioned).** The raw pool logs (about 810 MB) and datasets are too large for Git. They are immutable chain data, so `bundle/manifest-v1.json` pins, per pool, the block range, event signatures and a SHA-256 over the canonical log set and over the dataset columns; `bundle.py` rebuilds and verifies them from the public RPC. **Verification now fails (non-zero) when a required input is missing**: the dataset, the manifest entry, either committed small input, or the raw file when one is requested; and `rebuild` verifies the small inputs *before* fetching anything (`test_bundle.py`: 22 regressions, including removing a required input from disk AND from the manifest, dropping any required event stream, a wrong signature or pool, and a non-canonical path; the required lists are fixed in code, not read from the manifest). Cold reconstruction status (empty cache, fresh header grid, every event hash and the dataset-column hash checked): GLD VERIFIED (all event hashes and the dataset-column hash match); SPY VERIFIED (all event hashes and the dataset-column hash match). **NVDA provenance, stated exactly:** (1) the first NVDA run fetched its inputs cold and matched all 12 event-stream hashes but FAILED the dataset-column hash (`logs/bundle-rebuild-NVDA.first-run-dataset-mismatch.log`), because the local dataset had interpolated timestamps with 12 off-grid header anchors that other scripts had added to a shared cache (112 timestamps, tens of milliseconds); (2) the dataset definition was changed to interpolate on the regular grid only, the manifest hash corrected, the NVDA baselines rerun, and the corrected dataset then **passed using the cache that first run populated** (`logs/bundle-rebuild-NVDA.second-pass-reused-cache.log`): that second pass is cache-assisted and is **not** a fresh cold reconstruction; (3) a **targeted fresh NVDA reconstruction with an empty cache** was then run: VERIFIED (`logs/bundle-rebuild-NVDA.log`). The regression `test_dataset_grid.py` proves that unrelated off-grid header anchors cannot change a dataset. SPY's cold run used the previous revision of `bundle.py` (same event and dataset checks, before the code-fixed required lists); GLD's used the final one.

**Release asset (prepared and verified locally; NOT published).** `make_release.py --version <v> --require-cold --full` builds two compressed, versioned archives with checksums: a *core* archive (code, evidence, the saved pinned pool-state snapshots that the RPC cannot serve again, JSON datasets, small inputs) and a *rawlogs* archive. Datasets are JSON, not pickle, so unpacking runs no downloaded code, and `unpack_datasets.py` refuses any dataset that does not match the manifest hash. It refuses to build unless the local inputs verify and all three cold rebuild logs are complete FRESH runs, judged by exact stream names: each of the 12 required streams fetched exactly once and hash-checked exactly once, each required small input once, one dataset line, no failure, and no second-pass marker (a missing stream replaced by a duplicate fails). Before anything is published it extracts the core archive into a fresh directory and runs `sh reproduce_offline.sh full` there **under a macOS sandbox that denies all network (`sandbox-exec "(deny network*)"`)**, after a probe inside the same sandbox proves a socket connect and a DNS lookup both fail (if `sandbox-exec` were unavailable the run falls back to dead proxies and is recorded as "proxy-only, NOT network isolation"). The run re-verifies the inputs, re-checks every fork proof, replays every pinned quote and reruns every baseline, comparing each number with the committed evidence; it also verifies the raw-log archive against the manifest. **Build history (the final package's own hashes are NOT in this file, because an archive built from this file cannot contain its own checksum; they are in the PR description and in `release/SHA256SUMS-<version>`).** (a) A **dry run** (version `dryrun`, commit `86dda30`) exercised the build and the sandboxed reproduction; it reported `allPassed: false` only because of a bug in `make_release.py` (it looked for the success marker on stderr); the reproduction itself passed when run by hand. It is not a result and its archives were discarded. (b) Package `v1` (commit `0df52fa`, core sha256 `70dec91db68177069eeb490c9e3934afc4454c00b4de3f305d0649a8b38907b0`, rawlogs sha256 `af29d1a0ff8949ed53b6018c698ffc8197a5be321ea911a912da4356f9f7b511`) passed its checks but its cold-rebuild gate accepted the cache-assisted NVDA second pass: **superseded, do not publish**. (c) Package `v1.1` (commit `2a4b7d8`, core 42,298,405 bytes sha256 `ce1972b32a71b0289ea87b8c1b86e063145abdaf670ef4325f7daa2b3a986aec`, rawlogs 100,273,521 bytes sha256 `3bcfcdd473d9a5567def7bb89997b5b1d7090901ea6fa53709eb61d69003e635`) passed all its checks with a fresh NVDA reconstruction; its identity is preserved unchanged. It is superseded by `v1.2` only because the packaged cold-gate checker counted lines instead of naming streams, and because `v1.2` also packages the stock proofs of section 15. (d) The final package `v1.2` (built from the commit that contains this file) uses the exact-stream-name gate and new versioned output names; a built version is never overwritten. Nothing has been uploaded.

**Offline reproduction command** (from an unpacked core archive; no network needed):

```
tar xzf ramses-research-inputs-core-v1.2.tar.gz && cd ramses-research-inputs-v1.2 && sh reproduce_offline.sh      # quick set (the archive name carries the package version)
sh reproduce_offline.sh full                                                                                        # every baseline too
```

**Commands in the repository checkout** (read-only; the public RPC keeps only minutes of state, so rerunning the pinned reads gives a new pin, and the saved inputs are the evidence for the old ones):

```
python3 -m unittest test_engine test_feasibility test_quote_math test_bundle test_replay_check test_dataset_grid test_release_gate
python3 mutate_engine.py ; python3 mutate_checks.py
python3 bundle.py rebuild SPY|GLD|NVDA [--cache DIR --out DIR]      # cold fetch + hash verification
python3 bundle.py verify SPY|GLD|NVDA [--raw FILE] [--results-dir D]
python3 run_baseline.py SPY|NVDA|GLD caps|research ; python3 render_baseline.py caps|research SPY NVDA GLD
python3 quote_offline.py --check ; python3 verify_replays.py ; python3 render_pools.py ; python3 render_routes.py ; python3 readback_live_caps.py ; python3 proposed_ramses_settings.py
python3 replay_pool.py "<pool name>"      # pin + save inputs + fork replay against the deployed pool (needs forge and the RPC)
python3 pool_quote.py --from-inputs ; python3 r33_routes.py ; python3 fee_reconcile.py ; python3 collect_evidence.py
python3 make_release.py --version v1.2 --require-cold --full ; python3 staleness_by_session.py ; python3 render_staleness.py
FOUNDRY_PROFILE=ramses forge test --match-path ramses-vaults/test/StockProofs.t.sol -vv   # local stock proofs
# fork proof (needs a fresh pin and ROBINHOOD_RPC_URL): forge test --match-path ramses-vaults/test/fork/StockMultiplierFork.t.sol
```

## 13. Open items

* The partner's original page (HTTP 403 here) and the partner-supplied claims in section 2 (r33 is an ERC-20/OFT; staking vault and redemption on HyperEVM), all unverified.
* The gateway's `hasDynamicFees: false` against the `FeeAdjustment` history; a third-party LP Collect comparison; fork tests of the exact configuration on META and r33; the profitability and liquidation-capacity gates; operator decisions in handoff 01 section 13. The $1,000 pair cap is operator-supplied pending configuration readback of the (undeployed) Ramses vault; the live Uniswap vault reads back $1,000 / $50.
* Not done: a real signed recenter; the in-kind liquidation followed by an LP-unwinding redemption on a fork of the REAL pools (section 15B is on the local fixture only); whether the feed's price per token includes `uiMultiplier` (15A proves only that the multiplier is metadata); a Ramses margin/router adapter; RAM holder entitlement, Ramses-vault gas measurements, a longer or more volatile SPY sample (its baseline never recentered).
* Wording to avoid: the baselines are short samples of a modelled strategy, not a yield estimate and not a safety claim; the route quotes are not router quotes and not atomic.

## 14. Readiness: SPY and NVDA (META next; r33 a separate candidate)

Status words: **Met**, **Partial**, **Not met**, **Unverified**. These are research statuses against the gates in handoff 01, not approvals; nothing here proposes a cap change or activation. LP economics and ordinary lending readiness are separate questions and are separated below.

### 14A. LP economics and entitlement (what the paired vault earns)

| Dimension | SPY / USDG CL1 (`0x3845...`) | NVDA / USDG CL10 (`0xdac1...`, the Ramses control pool; the live NVDA vault runs on Uniswap v4) |
| --- | --- | --- |
| **Economics** | **Not met.** $10,000 scenario: +0.82% over holding in 74 days (+4.0%/yr), +0.40% (+2.0%/yr) after assumed maintenance; fees minus LVR minus gas is about break-even (+$1.20); the range never recentred. At the current caps ($1,000 pair / $50 ranged) fees are $0.46 against $42.69 of assumed operator-paid maintenance: -4.2% over the window (-21%/yr; about -1.0% at the observed-average maintenance) | **Not met.** $10,000 scenario: +2.19% over holding in 90 days is inventory path, not fees (fees $33.9, inventory +$184.6, fees minus LVR minus gas -$111); -2.03% (-26%/yr) in the last 28 days; 88% of capital idle. At the current caps fees are $1.70 against $51.64 of maintenance: -5.1% over the window |
| **Fee / reward entitlement** | **Partial.** Fork test of the exact two-layer position: protocol share 50%, collect = (gross - protocol) x share to 0 raw units, holders get 80% after the 20% reserve; RAM only through `getReward` (883 tokens, valued at zero; holder entitlement unmodelled). Fee is dynamic (70 to 1,000 ppm; 100 now; 150 ppm for 16% of 30 days) and reconciled to `fee()`, the swaps and `feeGrowthGlobal`. Open: third-party Collect comparison | **Partial.** Same fork test passes (collect within 3 raw units). Fee dynamic (70 to 2,460 ppm; 100 at the pins, 150 minutes later; listed 100). RAM 222 tokens, valued at zero. Same open item |
| **Ramses entry / exit integration (LP)** | **Partial.** Delegate, vault, adapter and keeper logic exist with passing local tests (section 9) and the stock proofs in section 15 (dividend metadata, funded liquidation then an LP-unwinding redemption, one-sided recovery). Not deployed. Tick spacing 1 | **Partial.** Same contracts and tests; tick spacing 10 (half-widths must be multiples of 10; 600 is). The production NVDA product is on Uniswap, so a Ramses NVDA vault would be a second venue, not a replacement |
| **Next concrete blocker** | The strategy cannot pay for its own keeper at the current caps under the assumed maintenance cost. First step: measure the Ramses keeper's real gas, then re-run the economics (no cap change is proposed here) | Fee income does not cover adverse selection and inventory drift. First step: decide whether a Ramses NVDA vault adds anything over the live Uniswap vault before building more |

### 14B. Ordinary lending readiness (supply, borrow, liquidate; independent of whether the LP earns)

| Dimension | SPY | NVDA |
| --- | --- | --- |
| **Price source** | **Partial.** Chainlink RHSPY/USD (`0x3197...`, `calculatedPrice`, 24/5). The guard's 12 h bound is not relaxed. **Stale 48.7% of the time overall, and 30.7% of trading-session time** (26.0% of regular hours, 32.4% overnight, 92.2% of weekends, 83.4% of holidays): about 36 of its 82 stale hours a week fall in open sessions | **Partial.** Chainlink NVDA/USD (`0x379E...`, `calculatedPrice`, 24/5), the feed the production guard already uses. **Stale 28.1% overall but only 3.9% of trading-session time** (1.1% regular, 5.0% overnight; 86.3% of weekends, 83.4% of holidays): about 5 of its 47 stale hours a week fall in open sessions |
| **What a stale feed blocks** (section 15D; the same for both) | Supply (`mint`), borrowing, redeeming collateral while in debt, **liquidation of an underwater account**, any redemption larger than local cash (it needs the LP), checkpoint, rebalance, recenter, guardian `emergencyDecrease`. Still allowed: redemption covered by local cash, repayment, `collectFees` | Same |
| **Funded in-kind liquidation** | **Proven locally (section 15B).** Needs no swap and does not touch the Ramses position; a live price is required, so it is blocked while the feed is stale | Same |
| **Collateral redemption needing an LP unwind** | **Proven locally (15B).** Redeeming seized collateral beyond local cash unwinds the LP through `withdrawForSide`; the minimum market buffer is 50% local cash, so only redemptions above that touch the pool | Same |
| **One-sided recovery** | **Proven locally (15C).** Atomic revert, then recovery by price return, or by an explicit swap decision that books a loss; recentring alone does not create the missing token | Same |
| **Dividends (`uiMultiplier`)** | **Proven on a fork of the real token, pool and feed (15A):** the multiplier is metadata; balances, supply, pool price and transfers do not change. That the feed's price per token already includes the multiplier is **unverified** (partner/Chainlink statement) | Not applicable here (no multiplier risk assumed; not tested) |
| **Measured pool depth and execution costs** (descriptive; **liquidation capacity is unproven**: no route was executed atomically, no margin/router adapter exists, no collateral factor is proposed) | Input to move the price 1%: $53k up / $55k down; one-way cost 2.0 bp at $1,000 and 24.8 bp for a $50k sell; the most USDG the pool pays for an unbounded sell is above $93k (scan-window lower bound) | Input to move the price 1%: $2.7k up / $3.7k down; 14.8 bp at $1,000; 210 bp (buy) / 138 bp (sell) at $10,000; the most USDG the pool pays for an unbounded sell is about $20.6k (full scan) |
| **Next concrete blocker** | The feed is stale about a third of open-session time, so any lending that needs a live price is unavailable for long stretches; a staleness policy decision is needed (the guard itself is unchanged), and the lending-side collateral factor stays at zero | A collateral factor is not proposed; liquidations are limited by pool depth to low thousands of dollars until a route is proven atomically |

**META (next).** `USDG/META` CL50 (`0x960f...`): fee 150 ppm now (30-day mean 206), TVL about $112k, 24 h volume about $30k. Measured pool depth: input to move the price 1% about $1.5k, 35.7 bp at $1,000, the most USDG obtainable by selling about $5.4k; liquidation capacity unproven. A Chainlink META/USD feed exists (`0x7C38...`, `calculatedPrice`, 24/5). Not done: dataset and baseline, fork fee-entitlement test, staleness breakdown. **r33 stays a separate research candidate** with its own blockers (no feed, one-sided thin concentrated pools, unverified partner claims; sections 4 and 11).

## 15. Stock proofs (Phase 0)

Four proofs, each tied to a test that runs: a fork test against the **real** SPY token, pool, feed and gauge (`test/fork/StockMultiplierFork.t.sol`, `logs/fork-stock-multiplier.log`, pinned block and hash in the log), and local-fixture tests (`test/StockProofs.t.sol`, `logs/stock-proofs-local.log`; the local fixture uses mock pool/NFT/gauge, the real-pool behaviour is in the fork tests of section 3). Nothing here relaxes a guard.

### 15A. SPY dividends and `uiMultiplier`

The SPY token exposes `uiMultiplier()` (1.001718 now, i.e. 0.17% accumulated) and the pause flags the guard reads. On a fork of the real token, **moving the multiplier by a quarterly-size step (+0.33%, an assumed size) leaves balances, total supply, the pool's SPY balance, the pool's price and liquidity, and exact-amount transfers unchanged**: the multiplier is metadata, so raw accounting (pool reserves, vault ledgers, adapter balance-delta checks) is unaffected by the step itself. The repository's contracts never read `uiMultiplier`; they price a raw token with the Chainlink feed. Whether that feed's price per token already includes the multiplier (handoff 01 and Chainlink's description say so) is **unverified here**; if it does, a step shows up as a feed move against a pool that has not yet repriced. The real oracle guard (real token and pool, feed mocked fresh, test bounds 150 bp allocation / 300 bp removal, since the Ramses vault is not deployed) then behaves as follows for a feed step relative to the pool spot:

| step (bp) | -600 | -400 | -200 | -100 | -33 | 0 | +17 | +33 | +100 | +200 | +400 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| allocation (rebalance, recenter post-check) | blocked | blocked | blocked | ok | ok | ok | ok | ok | ok | blocked | blocked |
| removal (exits, checkpoint, unwind) | blocked | blocked | ok | ok | ok | ok | ok | ok | ok | ok | blocked |

A step the size of the accumulated multiplier (+17 bp) or a quarterly one (+33 bp) is far inside both bounds; only a move beyond the configured bound blocks, and exits keep working inside the wider removal bound. The LP effect of a pure price step on a concentrated position is second order (arbitrage trades only until the pool reaches the new price); it was not measured here. Real feed age at the pin was 2,020 s (fresh).

### 15B. Funded in-kind liquidation, then a collateral redemption that needs an LP unwind

Local test `testFundedLiquidationThenCollateralRedemptionNeedsLpUnwind`: a borrower is made liquidatable by a price fall (pool and feed move together). The liquidator repays 27 USDG **from its own balance**; the borrower's collateral pTokens are seized; **the Ramses position's liquidity is unchanged**, so the liquidation touched neither the pool nor the LP. The liquidator then redeems the seized pTokens for the underlying stock: the amount owed (1.134e18) exceeds the market's local cash (1.02e18), so the vault unwinds LP liquidity through `withdrawForSide` (position liquidity falls) and the redeemer is paid at the settled, loss-aware rate (never above the pre-redemption preview) with all seized pTokens burned. The market keeps at least 50% of its assets as local cash, so only redemptions above that touch Ramses.

### 15C. One-sided recovery

With the position holding only the stock token (price fell) and settlement swaps paused (the production configuration): (1) a USDG redemption beyond local cash **reverts atomically** (shares, ledger, cash and position unchanged, `testOneSidedRedemptionRevertsAtomicallyThenSucceedsWhenThePriceReturns`) while the stock-side supplier is paid in kind; (2) **the price returning** puts the untouched position back in range and the same redemption succeeds; (3) **recentring alone does not create the missing token**: the matched-only redeploy leaves the stock idle and the USDG redemption still reverts (`...ByRecentringAlone...`); (4) an **explicit decision to unpause the bounded settlement swap** serves the redemption through the oracle-floored swap and **books a loss** (cumulative loss rose from about 0 to 16.4 USD-wad in the fixture; the pUSDG rate only falls, never rises; `...BoundedSettlementSwapRealizesALoss`). A funded loss reserve is the other path (section 10) and was not re-tested here.

### 15D. Oracle staleness: sessions, closures, and what actually blocks

The guard's 12 h bound is unchanged. Measured from the recorded feed answers (`staleness-by-session.json`, `staleness_by_session.py`): share of time the latest update is older than 12 h, by session.

| feed | regular hours (Mon-Fri 09:30-16:00 ET) | overnight (rest of the 24/5 week) | weekend (Fri 20:00 to Sun 20:00 ET) | US holiday | all trading sessions | all closures | stale hours per week (of which in trading sessions) | stale episodes (median / max hours; began in a trading session / a closure) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| SPY | 26.0% of 611 h | 32.4% of 1633 h | 92.2% of 913 h | 83.4% of 24 h | 30.7% | 91.9% | 81.9 (36.4) | 104 (11.7 / 68.8; 87 / 17) |
| NVDA | 1.1% of 611 h | 5.0% of 1633 h | 86.3% of 913 h | 83.4% of 24 h | 3.9% | 86.2% | 47.3 (4.6) | 38 (23.6 / 66.2; 20 / 18) |
| GLD | 16.5% of 195 h | 23.4% of 525 h | 50.5% of 309 h | n/a | 21.6% | 50.5% | 50.8 (25.3) | 36 (12.0 / 12.0; 23 / 13) |

GLD's feed is labelled Crypto (24/7) by the directory, so its 'weekend' and 'holiday' rows are calendar labels, not closures of its market. Sessions use evidence_lib.session_of (US holidays through Feb 2027). The guard's 12 h bound is not relaxed by anything here.


**SPY's staleness is mostly a trading-session problem, not only weekends and holidays**: its feed updates rarely when the price is quiet, so it is older than 12 h for about a third of open-session time and 87 of its 104 stale episodes began while the market was open. NVDA's feed is stale almost only in closures (weekends and holidays) and about 4% of open-session time.

Operations while the feed is older than 12 h (`testStaleFeedOperationMatrix`, `testStaleFeedBlocksLiquidationOfAnUnderwaterAccountButNotRepayment`; `STALE,` lines in the log):

| operation | stale feed |
| --- | --- |
| lending: redemption fully covered by local cash | **allowed** |
| lending: repayment (including by an underwater borrower) | **allowed** |
| vault: `collectFees` | **allowed** |
| lending: supply (`mint`), borrow | blocked |
| lending: redeem collateral while in debt | blocked |
| lending: **liquidation of an underwater account** | blocked (needs a live price) |
| lending: redemption larger than local cash (needs the LP) | blocked (rolled back atomically) |
| keeper: `checkpoint`, `rebalance`, `recenter` | blocked |
| guardian: `emergencyDecrease` | blocked (it books loss at the oracle price) |

So a stale feed does not trap suppliers whose redemptions fit in local cash and never stops repayment, but it stops new borrowing and supply, collateral withdrawal, liquidation, and every operation that needs the LP or the oracle price. Policy options (a different bound, a session-aware bound, a fallback price) are decisions for the operator and are **not** proposed or tested here.
