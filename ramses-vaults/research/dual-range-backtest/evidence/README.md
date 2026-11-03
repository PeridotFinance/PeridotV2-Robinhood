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

**Gaps to close before any pilot (not done here):** the in-kind liquidation followed by a redemption that needs an LP unwind has a local-fixture proof and real-pool proofs on NVDA and SPY (section 15B); no Ramses margin / router adapter; the one-sided case is proven for redemption (15C, local and NVDA and SPY real-pool) but not for a seized-collateral market.

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

**Release asset (prepared and verified locally; NOT published).** `make_release.py --version <v> --require-cold --full` builds two compressed, versioned archives with checksums: a *core* archive (code, evidence, the saved pinned pool-state snapshots that the RPC cannot serve again, JSON datasets, small inputs) and a *rawlogs* archive. Datasets are JSON, not pickle, so unpacking runs no downloaded code, and `unpack_datasets.py` refuses any dataset that does not match the manifest hash. It refuses to build unless the local inputs verify and all three cold rebuild logs are complete FRESH runs, judged by exact stream names: each of the 12 required streams fetched exactly once and hash-checked exactly once, each required small input once, one dataset line, no failure, and no second-pass marker (a missing stream replaced by a duplicate fails). Before anything is published it extracts the core archive into a fresh directory and runs `sh reproduce_offline.sh full` there **under a macOS sandbox that denies all network (`sandbox-exec "(deny network*)"`)**, after a probe inside the same sandbox proves a socket connect and a DNS lookup both fail (if `sandbox-exec` were unavailable the run falls back to dead proxies and is recorded as "proxy-only, NOT network isolation"). The run re-verifies the inputs, re-checks every fork proof, replays every pinned quote and reruns every baseline, comparing each number with the committed evidence; it also verifies the raw-log archive against the manifest. **Build history (the final package's own hashes are NOT in this file, because an archive built from this file cannot contain its own checksum; they are in the PR description and in `release/SHA256SUMS-<version>`).** (a) A **dry run** (version `dryrun`, commit `86dda30`) exercised the build and the sandboxed reproduction; it reported `allPassed: false` only because of a bug in `make_release.py` (it looked for the success marker on stderr); the reproduction itself passed when run by hand. It is not a result and its archives were discarded. (b) Package `v1` (commit `0df52fa`, core sha256 `70dec91db68177069eeb490c9e3934afc4454c00b4de3f305d0649a8b38907b0`, rawlogs sha256 `af29d1a0ff8949ed53b6018c698ffc8197a5be321ea911a912da4356f9f7b511`) passed its checks but its cold-rebuild gate accepted the cache-assisted NVDA second pass: **superseded, do not publish**. (c) Package `v1.1` (commit `2a4b7d8`, core 42,298,405 bytes sha256 `ce1972b32a71b0289ea87b8c1b86e063145abdaf670ef4325f7daa2b3a986aec`, rawlogs 100,273,521 bytes sha256 `3bcfcdd473d9a5567def7bb89997b5b1d7090901ea6fa53709eb61d69003e635`) passed all its checks with a fresh NVDA reconstruction; its identity is preserved unchanged. It is superseded by `v1.2` only because the packaged cold-gate checker counted lines instead of naming streams, and because `v1.2` also packages the stock proofs of section 15. (d) The final package `v1.2` was built from commit `9ef8680370b4dfdab5a9565d69ab1f351dfdd053` (not from the commit that contains this file); it uses the exact-stream-name gate and new versioned output names, and a built version is never overwritten. It contains section 15 as of that commit. **Everything after that commit exists only in the repository and is NOT in the v1.2 package:** the real-pool NVDA and SPY proofs and the reserve measurements added to 15B and 15C, the fresh-control and specific-reason assertions, the multiplier evidence in 15A, and all of section 16 with its replay, economics tests, fork integration, oracle research (`evidence/oracle-research/`), study, prototype and logs. No new package has been built and nothing has been uploaded.

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

The SPY token exposes `uiMultiplier()` (1.001718 now, i.e. 0.17% accumulated) and the pause flags the guard reads. On a fork of the real token, **moving the multiplier by a quarterly-size step (+0.33%, an assumed size) leaves balances, total supply, the pool's SPY balance, the pool's price and liquidity, and exact-amount transfers unchanged**: the multiplier is metadata, so raw accounting (pool reserves, vault ledgers, adapter balance-delta checks) is unaffected by the step itself. The repository's contracts never read `uiMultiplier`; they price a raw token with the Chainlink feed. Chainlink's documentation states that the token price equals the underlying equity price times `uiMultiplier`; the SPY feed was checked against the pool (section 16, `multiplierCheckSPY`): the pool-over-feed basis at feed updates is +0.056% (standard error 0.022%), which is closest to "the feed already includes the multiplier" (though 2.6 standard errors from exactly zero, a residual of about 0.06% unexplained) and rejects "bare equity price" (+0.17%, 5.3 standard errors) and "multiplier applied twice" (-0.17%, 10.4 standard errors). The multiplier must therefore NOT be applied again. This is a statistical consistency check on about 90 observations with the multiplier read once at the pin, not a proof of the feed's internals. A multiplier step then shows up as a feed move against a pool that has not yet repriced. The real oracle guard (real token and pool, feed mocked fresh, test bounds 150 bp allocation / 300 bp removal, since the Ramses vault is not deployed) then behaves as follows for a feed step relative to the pool spot:

| step (bp) | -600 | -400 | -200 | -100 | -33 | 0 | +17 | +33 | +100 | +200 | +400 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| allocation (rebalance, recenter post-check) | blocked | blocked | blocked | ok | ok | ok | ok | ok | ok | blocked | blocked |
| removal (exits, checkpoint, unwind) | blocked | blocked | ok | ok | ok | ok | ok | ok | ok | ok | blocked |

A step the size of the accumulated multiplier (+17 bp) or a quarterly one (+33 bp) is far inside both bounds; only a move beyond the configured bound blocks, and exits keep working inside the wider removal bound. The LP effect of a pure price step on a concentrated position is second order (arbitrage trades only until the pool reaches the new price); it was not measured here. Real feed age at the pin was 2,020 s (fresh).

### 15B. Funded in-kind liquidation, then a collateral redemption that needs an LP unwind

Local test `testFundedLiquidationThenCollateralRedemptionNeedsLpUnwind`: a borrower is made liquidatable by a price fall (pool and feed move together). The liquidator repays 27 USDG **from its own balance**; the borrower's collateral pTokens are seized; **the Ramses position's liquidity is unchanged**, so the liquidation touched neither the pool nor the LP. The liquidator then redeems the seized pTokens for the underlying stock: the amount owed (1.134e18) exceeds the market's local cash (1.02e18), so the vault unwinds LP liquidity through `withdrawForSide` (position liquidity falls) and the redeemer is paid at the settled, loss-aware rate (never above the pre-redemption preview) with all seized pTokens burned. The ledger saved before the liquidation is byte-identical to the ledger immediately after it, the borrower's debt falls by exactly the repayment, the liquidator's USDG falls by exactly the repayment, and total supply falls by exactly the redeemed shares. The market keeps at least 50% of its assets as local cash, so only redemptions above that touch Ramses.

**On the real NVDA/USDG and SPY/USDG pools** (`test/fork/LendingForkProofs.t.sol`, contracts `LendingForkProofsTest` (NVDA) and `SpyForkProofsTest`, test `testForkProofFundedLiquidationThenRedemptionUnwindsRealLp`, `logs/fork-lending-proofs-spy-nvda.log`, pin header in the log). **What is real and what is simulated, precisely:** the pool, the nonfungible position manager and the gauge are the deployed contracts on a mainnet fork; the vault, adapter, loss reserve, markets and controller are this repository's contracts deployed on the fork; the price is moved by **real swaps** from a funded test trader; the first feed read (pair registration) is the real pinned Chainlink feed; **every later feed update is simulated** by `vm.mockCall`, which re-stamps the answer to the new pool price (a fork has no real subsequent update). The sequence: a borrower becomes liquidatable after a real-swap price fall of about 75%; two funded liquidation rounds (31 USDG repaid from the liquidator's own balance); the position's liquidity and the vault ledger are byte-identical immediately after each round; the liquidator then redeems the seized collateral: NVDA owed 0.648393 against 0.511397 of local cash, SPY owed 0.175434 against 0.152938, so the redemption unwinds the real LP (position liquidity and stock principal fall), balances and total supply change by exactly the redeemed shares. **These are mechanism proofs at one pinned block and a size of tens of USDG, not liquidation capacity**: measured depth (section 3) and validated liquidation capacity remain separate statements, and a larger second round was rejected by the market (`LIQUIDATE_SEIZE_TOO_MUCH`). The SPY pool lists the stock as token0, so the fixture was parameterized by token order (the NVDA arithmetic is unchanged).

### 15C. One-sided recovery

With the position holding only the stock token (price fell) and settlement swaps paused (the production configuration): (1) a USDG redemption beyond local cash **reverts atomically** (shares, ledger, cash and position unchanged, `testOneSidedRedemptionRevertsAtomicallyThenSucceedsWhenThePriceReturns`) while the stock-side supplier is paid in kind; (2) **the price returning** puts the untouched position back in range and the same redemption succeeds; (3) **recentring alone does not create the missing token**: the matched-only redeploy leaves the stock idle and the USDG redemption still reverts (`...ByRecentringAlone...`); (4) an **explicit decision to unpause the bounded settlement swap** serves the redemption through the oracle-floored swap and **books a loss** (cumulative loss rose from about 0 to 16.4 USD-wad in the fixture; the pUSDG rate only falls, never rises; `...BoundedSettlementSwapRealizesALoss`). A funded loss reserve is the other path (section 10) and was not re-tested here.

**On the real NVDA/USDG and SPY/USDG pools** (`testForkProofPersistentOneSidedStateAndReserveRecovery`, `testForkProofReserveExhaustedWhatHappens`, same real/simulated split as 15B): with swaps paused and the price shocked until the position holds only the stock token, the USDG redemption beyond local cash reverts atomically; a keeper recentre, the price returning and three days later still leave it reverting, because the pair has no USDG available (raw 0 on both pools at these pins) against the 5 USDG needed beyond local cash. **A permissionless reserve deposit proves that recovery is possible when someone supplies funds; it does not establish a funded recovery programme.** Measured, identically on both pools:

* reserve balance at the start: **0** (a fresh fixture has accrued no fees; in the design the standing sources are only the 20% fee cut and voluntary deposits);
* funding deposited by the test: 60 USDG; the redemption of 105.02 USDG (local cash plus 5) then **consumed exactly 5.000000 USDG of reserve** and left 55 USDG; the redeemer burned about 550k raw pTokens at the pre-redemption rate and was paid in full;
* **who bears the cost:** the reserve funders. No principal write-down was recognized (the cumulative recognized loss, 16.45 USD-wad from the earlier price shock on both pools, did not change) and neither market's exchange rate fell (USDG market rate +311 raw units from rounding; stock market rate unchanged);
* **reserve exhausted** (1 USDG funded against a 5 USDG deficit): the same redemption **reverts atomically**: shares, ledger and reserve unchanged, nothing paid, no loss pushed onto the remaining suppliers, exchange rates and cumulative loss unchanged. The exiting supplier is blocked until the price returns, more funding arrives, or a governance decision enables the (paused) bounded settlement swap, which books a loss (local test `...BoundedSettlementSwapRealizesALoss`). The reserve's per-event, daily and coverage-ratio limits were not binding in this fixture (they are set high); with the production-style limits the covered amount would be capped the same way.

Assertions on idle and liquidity are relaxed to be pin-independent. Same scope limits as 15B.

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

Each stale outcome above is asserted with its specific revert reason (the oracle's `StaleOracle(address,uint256)`, or `RedeemTransferOutNotPossible()` for the redemption beyond local cash) and each is paired with a fresh-feed control that succeeds (`testStaleFeedOperationMatrixWithFreshControlsAndSpecificReasons`).

So a stale feed does not trap suppliers whose redemptions fit in local cash and never stops repayment, but it stops new borrowing and supply, collateral withdrawal, liquidation, and every operation that needs the LP or the oracle price. Section 16 studies a bounded fallback price as an isolated, test-only prototype; the guard, its 12 h bound and every deployed or configured value are unchanged, and adopting any option is a decision for the operator.

## 16. Bounded oracle fallback for SPY and NVDA (Phase 0 investigation; test-only prototype, nothing deployed or configured)

> **Deferred by the operator (2026-11-03).** Further fallback-oracle, API-publisher and paid-provider integration work is paused. This section, its replay, its economics tests, its fork integration and its prototype are **preserved as research**; the "cheapest credible option" in 16J is evidence, not a plan, and nothing here is wired to a guard, vault or market. The primary oracle is the existing Chainlink feed with the guard's freshness bound, issuer-pause checks and execution-price checks, all unchanged.
>
> **Remaining blocker, stated once.** With Chainlink alone, a stock-collateral or stock-debt lending action that reads the price reverts `StaleOracle` whenever the feed is older than the guard's 12 h bound: in the study window that was about 51% of wall-clock time for SPY and about 29% for NVDA, with single gaps of up to 80.8 h (SPY) and 78.2 h (NVDA). **During that time an underwater borrower cannot be liquidated**, and no mitigation is in place or now planned. **Two different things are not to be confused:** Robinhood describes the issuer's `oraclePaused()` flag as advisory (the issuer's own contracts do not enforce it), whereas **our guard enforces it**: `RamsesOracleGuard.pricesUSD18` (and the live `StockOracleGuard`) revert `StockOraclePaused` when `paused()` or `oraclePaused()` is true. That check only helps when the issuer sets the flag; it does not protect against a silent feed, so the 12 h staleness bound remains the control for an outage. This blocks the decision to enable **borrowing** on any stock market and is separate from deployment readiness (nothing is deployed) and from the permission to activate borrowing (not granted by anything in this repository). Repayment, within-cash redemption and `collectFees` keep working while the feed is stale.

**Scope.** Research and an isolated prototype (`test/fallback/AnchoredFallbackOracle.sol`, test-only, not under `src/`, not wired to any guard, vault or market). The guard's 12 h bound, pause checks, USDG-at-$1 valuation and every deployed or configured value are unchanged. Borrowing, leverage and allocation are excluded from the fallback in every variant, and **liquidation stays disabled**. Data: `evidence/oracle/fallback-replay.json` (`fallback_replay.py`, the executable policy replayed step by step, 30-minute TWAP like the prototype), `evidence/oracle/fallback-study.json` (`fallback_study.py`: basis and error statistics, TWAP window 30 min), `evidence/oracle/twap-probe.json` (`twap_probe.py`, read-only at one pin) and `evidence/oracle-research/` (independent oracle and API research, section 16H). About 10.4 weeks of SPY and 12.7 weeks of NVDA feed and swap history.

**What this section does NOT claim.** It does not claim that the fallback price is close to the market price, that a move limit bounds a loss, or that any D or L is the right one. Corrections to the first version of this section are listed in 16J.

### 16A. Three different situations, three different answers

| situation | how it looks on chain | evidence | fallback? |
| --- | --- | --- | --- |
| **Scheduled closure** (weekend, exchange holiday) | feed frozen at its last value, flags clear | NVDA's stale time is almost all closure; the longest gaps between feed answers in the study window are 80.8 h (SPY) and 78.2 h (NVDA), measured from the saved feed series | bounded by a closure duration; **needs an operator-maintained calendar** |
| **Quiet market or feed outage in a trading session** | feed older than 12 h, flags clear, **indistinguishable from each other on chain** | SPY: older than 12 h for about a third of open-session time (its feed updates only on a 0.5% move, our measured figure); NVDA about 4% | only a shorter duration; a real outage is caught only by the pool's move |
| **Issuer pause / corporate action** | `paused()` or `oraclePaused()` true; the multiplier changes | our guard and the prototype both revert on either flag (the issuer calls `oraclePaused()` advisory for its own contracts; our check is enforced, but only as good as the issuer's use of the flag) | **no**; also halt if `uiMultiplier()` differs from its value at the anchor |

There is no generic catch-all: every refusal is a named error or a bubbled revert (16G lists them).

### 16B. The multiplier is not applied again

Chainlink's methodology page for the Robinhood tokenized-equity feeds, which is accessible now and saved with its hash (`evidence/oracle-research/raw/chainlink-robinhood-tokenized-equity.*`, `manifest.json`), documents the token price as the underlying equity price times `uiMultiplier`. **The feed already includes it and it is not multiplied again anywhere.** The earlier version of this section took that statement from a search summary because the page returned 404 to the tooling; that caveat is withdrawn. The pool-basis statistics below remain a **consistency check**, not a proof of the feed's internals. SPY feed against the pool at every open-session feed update (1.001718 read at the pin):

Measured over 90 feed updates in open sessions: mean basis +0.056% (median +0.031%, standard error 0.022%).

| hypothesis for SPY | expected pool-over-feed basis | distance from the measured mean |
| --- | --- | --- |
| feedIncludesMultiplier (expect ~0) | +0.000% | 2.6 standard errors |
| feedIsBareEquityPrice (expect +mult-1) | +0.172% | 5.3 standard errors |
| multiplierAppliedTwice (expect -(mult-1)) | -0.172% | 10.4 standard errors |

The two alternatives are rejected (5.3 and 10.4 standard errors); the measured basis is closest to "the feed already includes the multiplier" but is 2.6 standard errors from exactly zero, a residual of about 0.06% that is unexplained here (fee, latency, feed-update timing, and Chainlink's documented overnight smoothing are candidates, none tested). The prototype only **reads** `uiMultiplier()`, to halt when it differs from the value at the anchor; it never multiplies by it. A residual hole is demonstrated on the fork (16G): a step that lands between an answer and a permitted late poke cannot be seen on chain, because the multiplier can only be read now.

### 16C. What the Ramses pools can support

| pool | observation cardinality | longest TWAP window the pool serves | USD to move the price 2% (buy / sell) |
| --- | --- | --- | --- |
| SPY/USDG CL1 | 1801 | 86400 s | $89,819 / $84,109 |
| NVDA/USDG CL10 | 1000 | 43200 s | $3,910 / $6,999 |
| META/USDG CL50 | 121 | 3600 s | $2,917 / $2,973 |
| GLD/USDG CL1 | 1 | 60 s | $15,205 / $15,742 |
| r33/USDG CL200 | 1 | 3600 s | $222 / $12 |
| r33/USDG CL500 | 1 | 43200 s | $198 / $202 |

* **History is a per-pool property and differs by orders of magnitude.** SPY serves a 24 h TWAP, NVDA 12 h, META only 1 h, GLD and the r33 pools effectively none (cardinality 1). Only SPY and NVDA can support the design; META could only with a very frequent poke; GLD and r33 cannot.
* **The anchor must be taken while the feed is fresh.** NVDA's 12 h history cannot reach back to a 60 h-old answer, so the pool's TWAP at the answer time is recorded at poke time (permissionless) and stored; a missed poke means no fallback for that answer (`AnchorMissing`), never a recomputation from a later price. A poke is refused after `maxPokeDelay` (1 h in the tests): `PokeTooLate`.
* **Depth, measured on the real pools (fork, 16G).** Holding a push for the 30-minute window needs capital: raising the NVDA price about 1% took about $3.6k of USDG input (17.6 NVDA bought) and a further 2% about $5.2k; for SPY about 1% took about $39k and a further 2% about $101k. These are **capital required, not net cost**: the pushed inventory is recoverable, so the loss is price impact plus fees plus holding risk, which was not measured. A closed market has no arbitrage to push back, and **the cost of holding a push through a closure is not quantified**. The Ramses vault is not deployed, so today none of this liquidity is ours; if it were, removing it would make the pool thinner (the operator's $1,000 pair cap is about 25% of NVDA's 2% depth and about 1% of SPY's).
* **What L bounds, and what it does not.** The move limit L bounds the change of the **pool's** TWAP since the anchor. It is not a bound on the error against the current market price (a stationary or manipulated pool can sit still across an arbitrarily large real gap, 16I), and it is not a bound on cumulative extraction (repeated exits and refills across gaps, 16I). The earlier statement that L = 2% limits mispricing to about $20 on a $1,000 cap is **withdrawn**.

### 16D. Candidate options: availability, remaining failures, trust and implementation cost

| option | recovers | remains broken | trust added | implementation |
| --- | --- | --- | --- | --- |
| **0. Chainlink alone** (today) | nothing; in this window SPY's feed was older than 12 h about 51% of the time and NVDA's about 29% | everything that needs the oracle during stale time | none new | none |
| **1. Independent on-chain oracle** | would recover staleness only if it updates while Chainlink does not | **On Robinhood Chain (verified by contract calls, 16H): Pyth Core and Pyth Pro verifier contracts exist, the Pyth SPY/NVDA feeds do not exist in the Core contract, nothing is sponsored or pushed; Stork exists and holds no equity feed.** Pyth equity data needs a paid plan (Pro from $2,500/month, confirmed); RedStone, API3, Chronicle, Switchboard, DIA and Supra are **unverified**, not absent | a paid data provider; a second pause/units/multiplier story; Pyth's terms bar re-publishing its data on chain through our own publisher | an adapter plus a keeper that pays for and submits signed updates; per-share price needs one multiplication by `uiMultiplier` |
| **2. Ramses TWAP as the price** | availability | the pool's absolute basis to the feed (median 0.1% to 0.4%, p99 up to 3.5% on NVDA overnight, 30-minute TWAP), no anchor, thin-pool manipulation; GLD/r33/META lack history | none new, but the weakest price | small |
| **3. Anchored hybrid (prototype)**: last fresh answer x pool's relative move since it | exit and settlement valuation during closures and quiet sessions, within D and L | gaps longer than D; real moves the pool does not show; missed pokes; wide-basis answers; **liquidation, borrowing, allocation (excluded)** | an operator-maintained closure calendar, a poke keeper, pool honesty over the window | about 220 lines, 100 local Solidity tests (76 unit, 24 economics), 20 fork tests, mutation-tested; **the guard and lending price source would need the operation class plumbed through (today one price entry point), which the economics tests stand in for with a shim** |
| **4. Hold-and-bound** (`Mode.HOLD`): anchor price flat; the pool only refuses | the same availability as 3 | the same, plus no tracking of a real closure move; the vault's removal guard compares pool spot with a flat price, so completion is lower at tight removal bounds | same as 3 | simpler than 3 |
| **5. Off-chain shadow monitor** (Alpaca / Twelve Data free tiers, docs only) | nothing on chain; an alert when Chainlink is stale and the market has moved | no on-chain effect | none on chain; vendor terms limit use to internal, non-display | hosting only, no gas; provider quotes are not signed, so a contract could only trust our own publisher key |

**What the error data says about 3 versus 4.** The estimate is compared with the next Chainlink answer, a proxy for the market price that exists only because an update (a 0.5% move) happened. Keeping the last price is wrong by about 0.5% in an open-session gap by construction. The pool's relative move is not clearly an improvement:

| pair | gap between feed answers | pairs | keep the last price: median / p90 / max | anchored 30 min TWAP: median / p90 / max |
| --- | --- | --- | --- | --- |
| SPY | <1h | 2 | 0.51% / 0.51% / 0.51% | 0.45% / 0.45% / 0.45% |
| SPY | 1-6h | 19 | 0.51% / 0.55% / 0.56% | 0.25% / 0.60% / 0.69% |
| SPY | 6-12h | 12 | 0.51% / 0.53% / 0.55% | 0.20% / 0.33% / 0.74% |
| SPY | 12-24h | 28 | 0.52% / 0.55% / 0.60% | 0.21% / 0.33% / 0.40% |
| SPY | 24-48h | 19 | 0.17% / 0.43% / 0.45% | 0.17% / 0.42% / 1.15% |
| SPY | >48h | 11 | 0.18% / 0.69% / 0.69% | 0.22% / 0.61% / 1.09% |
| SPY | gap starts and ends in open sessions | 32 | 0.51% / 0.53% / 0.56% | 0.20% / 0.45% / 0.74% |
| SPY | gap spans a weekend or holiday | 13 | 0.19% / 0.69% / 0.69% | 0.26% / 0.61% / 1.09% |
| NVDA | <1h | 443 | 0.54% / 0.64% / 1.43% | 0.53% / 0.94% / 2.16% |
| NVDA | 1-6h | 257 | 0.52% / 0.58% / 1.41% | 0.37% / 0.83% / 2.17% |
| NVDA | 6-12h | 42 | 0.52% / 0.57% / 0.62% | 0.29% / 0.66% / 1.11% |
| NVDA | 12-24h | 18 | 0.51% / 0.55% / 0.58% | 0.22% / 0.59% / 0.89% |
| NVDA | >48h | 13 | 0.35% / 1.13% / 1.16% | 0.47% / 0.95% / 0.98% |
| NVDA | gap starts and ends in open sessions | 740 | 0.53% / 0.62% / 1.43% | 0.46% / 0.88% / 2.17% |
| NVDA | gap spans a weekend or holiday | 15 | 0.37% / 1.13% / 1.16% | 0.31% / 0.95% / 0.98% |

On the replay (16E) the held anchor price shows a smaller error against the next answer than the pool-adjusted price (SPY p99 0.69% against 1.27%; NVDA 1.16% against 1.90%). There are only 13 SPY and 15 NVDA closure gaps and the proxy is conditional on an update, so the defensible reading is that **the pool is a move detector, not a precision upgrade**, and that hold-and-bound is at least as good on this evidence. Both are candidates and both need the economics proofs of 16I; neither is selected here.

| pair | session | feed updates | 30 min TWAP vs feed: median | p90 | p99 | max |
| --- | --- | --- | --- | --- | --- | --- |
| SPY | regular | 44 | 0.18% | 0.33% | 0.62% | 0.62% |
| SPY | overnight | 46 | 0.10% | 0.31% | 1.25% | 1.25% |
| NVDA | regular | 446 | 0.40% | 0.82% | 1.54% | 1.98% |
| NVDA | overnight | 326 | 0.30% | 0.96% | 3.50% | 4.39% |

(The anchored design uses the pool's relative move, so this static basis cancels; it is shown because option 2 would have to absorb it, because the anchor is refused when it exceeds `maxBasisBps`, and because Chainlink's documentation says the overnight price is an exponential moving average, which is itself a source of basis. This is a consistency statistic, not the multiplier proof.)

### 16E. What the executable policy does on the recorded history (replay)

`fallback_replay.py` replays `poke()` and `price()` minute by minute: an answer is anchored only if a poke follows it within `maxPokeDelay` while it is the latest and fresh, the pool's observation history reaches back far enough (SPY 24 h, NVDA 12 h), and the pool-over-answer basis is within `maxBasisBps`; the age limit is D closed in a calendar closure and D open otherwise (the prototype's rule, **as written**: a closure-session anchor is over the open limit the moment the market reopens); the pool's move since the anchor must be within L. Two outcomes are counted separately: **the oracle returns a price**, and **the exit would complete**, which additionally requires the vault's removal guard to pass (pool spot within R of the returned price). Exits can still be blocked by what this replay cannot see: the withdrawable balance of the requested token (one-sided positions, 15C: the SPY and NVDA fork proofs revert atomically with no USDG available), the reserve, swap settings, caps and the checkpoint. The reference row is the prototype's test configuration (TWAP 30 min, D open 24 h, D closed 72 h, L 2%, basis 1%, R 6%, ideal pokes), **not a recommendation**.

| pair | weeks | oracle returns a price: trading / closure | exit completes (price AND removal guard at 6%): trading / closure | refusals (stale minutes) | one multiplier step per window loses | error vs next answer p99 / max |
| --- | --- | --- | --- | --- | --- | --- |
| SPY | 10.4 | 95.9% / 98.3% | 95.9% / 98.3% | FallbackExpired 780 min; no anchor: basis too wide 720 min | 1.5% of recovered minutes | 1.27% / 1.51% |
| NVDA | 12.7 | 93.7% / 91.5% | 93.7% / 91.5% | no anchor: basis too wide 2405 min; FallbackExpired 621 min; MoveExceedsBound 68 min | 1.4% of recovered minutes | 1.90% / 2.22% |

**Neither D nor L is chosen for availability.** The tables below are the trade-off, not a ranking: a larger D raises availability and the time a possibly wrong price can be used; the open-session duration must exceed the 12 h staleness bound to apply at all (the constructor requires it), and SPY's quiet stretches mostly last 12 h to 24 h, so 24 h is where SPY's open-session recovery starts, which is a property of the feed rather than a preference; longer closure durations add only the tail of the longest runs.

| pair | D open | D closed | L | trading time: price returned | closure time: price returned | error vs next answer p99 / max | refusals |
| --- | --- | --- | --- | --- | --- | --- | --- |
| SPY | 18 h | 48 h | 2% | 57.9% | 78.4% | 1.06% / 1.33% | FallbackExpired 15673 min; no anchor: basis too wide 720 min |
| SPY | 18 h | 72 h | 2% | 57.9% | 98.3% | 1.33% / 1.51% | FallbackExpired 9672 min; no anchor: basis too wide 720 min |
| SPY | 18 h | 96 h | 2% | 57.9% | 100.0% | 1.33% / 1.51% | FallbackExpired 9144 min; no anchor: basis too wide 720 min |
| SPY | 24 h | 48 h | 2% | 95.9% | 78.4% | 1.05% / 1.33% | FallbackExpired 6781 min; no anchor: basis too wide 720 min |
| SPY | 24 h | 72 h | 2% | 95.9% | 98.3% | 1.27% / 1.51% | FallbackExpired 780 min; no anchor: basis too wide 720 min |
| SPY | 24 h | 96 h | 2% | 95.9% | 100.0% | 1.27% / 1.51% | no anchor: basis too wide 720 min; FallbackExpired 252 min |
| SPY | 36 h | 48 h | 2% | 95.9% | 78.4% | 1.05% / 1.33% | FallbackExpired 6777 min; no anchor: basis too wide 720 min |
| SPY | 36 h | 72 h | 2% | 95.9% | 98.3% | 1.27% / 1.51% | FallbackExpired 776 min; no anchor: basis too wide 720 min |
| SPY | 36 h | 96 h | 2% | 95.9% | 100.0% | 1.27% / 1.51% | no anchor: basis too wide 720 min; FallbackExpired 248 min |
| NVDA | 18 h | 48 h | 2% | 83.2% | 77.5% | 1.74% / 1.98% | FallbackExpired 5782 min; no anchor: basis too wide 2405 min |
| NVDA | 18 h | 72 h | 2% | 83.2% | 91.5% | 1.90% / 2.22% | no anchor: basis too wide 2405 min; FallbackExpired 1031 min; MoveExceedsBound 68 min |
| NVDA | 18 h | 96 h | 2% | 83.2% | 92.6% | 1.90% / 2.22% | no anchor: basis too wide 2405 min; FallbackExpired 656 min; MoveExceedsBound 68 min |
| NVDA | 24 h | 48 h | 2% | 93.7% | 77.5% | 1.74% / 1.98% | FallbackExpired 5372 min; no anchor: basis too wide 2405 min |
| NVDA | 24 h | 72 h | 2% | 93.7% | 91.5% | 1.90% / 2.22% | no anchor: basis too wide 2405 min; FallbackExpired 621 min; MoveExceedsBound 68 min |
| NVDA | 24 h | 96 h | 2% | 93.7% | 92.6% | 1.90% / 2.22% | no anchor: basis too wide 2405 min; FallbackExpired 246 min; MoveExceedsBound 68 min |
| NVDA | 36 h | 48 h | 2% | 93.7% | 77.5% | 1.74% / 1.98% | FallbackExpired 5372 min; no anchor: basis too wide 2405 min |
| NVDA | 36 h | 72 h | 2% | 93.7% | 91.5% | 1.90% / 2.22% | no anchor: basis too wide 2405 min; FallbackExpired 621 min; MoveExceedsBound 68 min |
| NVDA | 36 h | 96 h | 2% | 93.7% | 92.6% | 1.90% / 2.22% | no anchor: basis too wide 2405 min; FallbackExpired 246 min; MoveExceedsBound 68 min |

Move limit (D open 24 h, D closed 72 h):

| pair | L (D open 24 h, D closed 72 h) | trading time | closure time | error p99 / max |
| --- | --- | --- | --- | --- |
| SPY | 1% | 95.9% | 98.3% | 1.27% / 1.51% |
| SPY | 2% | 95.9% | 98.3% | 1.27% / 1.51% |
| SPY | 3% | 95.9% | 98.3% | 1.27% / 1.51% |
| NVDA | 1% | 93.7% | 83.1% | 1.66% / 1.74% |
| NVDA | 2% | 93.7% | 91.5% | 1.90% / 2.22% |
| NVDA | 3% | 93.7% | 91.7% | 1.90% / 2.22% |

Poke reliability and the basis limit at poke time (reference policy otherwise; a keeper later than `maxPokeDelay` = 1 h anchors almost nothing, and an unreliable keeper loses availability in proportion):

| pair | poke model | max basis accepted at poke | answers anchored | trading time | closure time | why answers were not anchored |
| --- | --- | --- | --- | --- | --- | --- |
| SPY | ideal | 1.0% | 99% | 95.9% | 98.3% | basis too wide 1 |
| SPY | ideal | 2.0% | 100% | 98.9% | 98.3% | - |
| SPY | keeper15m | 1.0% | 99% | 95.9% | 98.3% | basis too wide 1 |
| SPY | keeper15m | 2.0% | 100% | 98.9% | 98.3% | - |
| SPY | keeper1h | 1.0% | 98% | 95.9% | 98.3% | basis too wide 1, superseded before the poke 1 |
| SPY | keeper1h | 2.0% | 99% | 98.9% | 98.3% | superseded before the poke 1 |
| SPY | keeper2h | 1.0% | 30% | 29.4% | 20.6% | poke too late (maxPokeDelay) 62, superseded before the poke 2 |
| SPY | keeper2h | 2.0% | 30% | 29.4% | 20.6% | poke too late (maxPokeDelay) 62, superseded before the poke 2 |
| SPY | miss10pct | 1.0% | 86% | 85.6% | 89.6% | missed poke 13 |
| SPY | miss10pct | 2.0% | 86% | 85.6% | 89.6% | missed poke 13 |
| SPY | miss30pct | 1.0% | 66% | 64.4% | 63.2% | missed poke 31 |
| SPY | miss30pct | 2.0% | 66% | 64.4% | 63.2% | missed poke 31 |
| NVDA | ideal | 1.0% | 93% | 93.7% | 91.5% | basis too wide 43, superseded before the poke 11 |
| NVDA | ideal | 2.0% | 98% | 93.7% | 98.7% | basis too wide 7, superseded before the poke 11 |
| NVDA | keeper15m | 1.0% | 82% | 93.7% | 91.5% | basis too wide 26, superseded before the poke 114 |
| NVDA | keeper15m | 2.0% | 85% | 93.7% | 98.7% | basis too wide 1, superseded before the poke 114 |
| NVDA | keeper1h | 1.0% | 61% | 93.7% | 91.5% | basis too wide 12, superseded before the poke 286 |
| NVDA | keeper1h | 2.0% | 63% | 93.7% | 98.7% | superseded before the poke 286 |
| NVDA | keeper2h | 1.0% | 31% | 53.0% | 69.5% | poke too late (maxPokeDelay) 125, basis too wide 8, superseded before the poke 400 |
| NVDA | keeper2h | 2.0% | 32% | 53.0% | 76.7% | poke too late (maxPokeDelay) 125, superseded before the poke 400 |
| NVDA | miss10pct | 1.0% | 83% | 80.2% | 91.5% | missed poke 86, basis too wide 36, superseded before the poke 10 |
| NVDA | miss10pct | 2.0% | 87% | 80.2% | 98.7% | missed poke 86, basis too wide 7, superseded before the poke 10 |
| NVDA | miss30pct | 1.0% | 62% | 50.5% | 68.9% | missed poke 262, basis too wide 23, superseded before the poke 6 |
| NVDA | miss30pct | 2.0% | 65% | 50.5% | 68.9% | missed poke 262, basis too wide 4, superseded before the poke 6 |

Hold-and-bound against anchored, and the removal bound R (reference policy otherwise):

| pair | variant | removal bound R | price returned: trading / closure | exit completes: trading / closure | error vs next answer p99 / max |
| --- | --- | --- | --- | --- | --- |
| SPY | anchored | 1% | 95.9% / 98.3% | 95.9% / 98.3% | 1.27% / 1.51% |
| SPY | hold | 1% | 95.9% / 98.3% | 95.9% / 97.0% | 0.69% / 0.69% |
| SPY | anchored | 3% | 95.9% / 98.3% | 95.9% / 98.3% | 1.27% / 1.51% |
| SPY | hold | 3% | 95.9% / 98.3% | 95.9% / 98.3% | 0.69% / 0.69% |
| SPY | anchored | 6% | 95.9% / 98.3% | 95.9% / 98.3% | 1.27% / 1.51% |
| SPY | hold | 6% | 95.9% / 98.3% | 95.9% / 98.3% | 0.69% / 0.69% |
| NVDA | anchored | 1% | 93.7% / 91.5% | 93.7% / 91.5% | 1.90% / 2.22% |
| NVDA | hold | 1% | 93.7% / 91.5% | 93.7% / 83.1% | 1.16% / 1.16% |
| NVDA | anchored | 3% | 93.7% / 91.5% | 93.7% / 91.5% | 1.90% / 2.22% |
| NVDA | hold | 3% | 93.7% / 91.5% | 93.7% / 91.5% | 1.16% / 1.16% |
| NVDA | anchored | 6% | 93.7% / 91.5% | 93.7% / 91.5% | 1.90% / 2.22% |
| NVDA | hold | 6% | 93.7% / 91.5% | 93.7% / 91.5% | 1.16% / 1.16% |

Not covered by the history: **issuer pauses** (none occurred in the window; the prototype halts for their whole duration), **multiplier steps** (a step at a random time per window halts the fallback for the rest of that anchor, about 1.4% to 1.5% of recovered minutes per step in the replay; SPY pays quarterly), and the **cost of the calendar being wrong**.

### 16F. Exits, settlement, liquidation, new borrowing, evaluated separately

| use | with fallback? | reason |
| --- | --- | --- |
| New borrowing, leverage, allocation (`rebalance`, `recenter` post-check) | **never** | a wrong price here creates risk rather than preserving access |
| Collateral redemption or any liquidity check of an account **with debt** | **no (classify as BORROW)** | 16I: priced at the stale value, it admits a redemption that leaves the account under water after the gap, in both borrowing directions |
| Supplier exit and settlement valuation for **debt-free** positions | candidate, within D and L, **with a transfer of loss** | 16I: an exit at the stale value avoids the loss that the remaining suppliers then bear |
| Liquidation | **disabled; keep it disabled** | see below |
| Repayment, within-cash redemption, `collectFees` | unchanged (already allowed while stale) | |

**Liquidation.** If it were enabled, flooring the collateral price at the anchor (`max(anchor, fallback)`) is not a general cure for false liquidation: it protects a borrower whose **collateral** is the stock from a downward estimate, but it does nothing for a borrower whose **debt** is the stock (an upward estimate makes a healthy borrower liquidatable; 16I measures both), and a true price above the anchor with a lagging pool still prices collateral below the truth. A 1% to 2% estimate error also wrongly liquidates borrowers within that margin of the limit (NVDA: 1.0% / 6.4% of sampled minutes at 98% / 99% of the limit in the first study). **When the duration limit D expires the fallback becomes unavailable (`FallbackExpired`); that does not restore liquidation**, it restores the existing `StaleOracle` behaviour.

**Bad debt.** For the *assumed* position (collateral factor 0.3 and 8% liquidation incentive, carried from the earlier readiness work, **not re-read from a deployment**), collateral would have to fall about two thirds from the borrowing limit before a position is under water. That is arithmetic for those inputs, **not a demonstrated bad-debt bound**: it does not cover other factors, an account that redeems collateral at a stale price (16I), interest, or a seizure that exceeds the close factor.

### 16G. Prototype, tests and fail-closed classes

`test/fallback/AnchoredFallbackOracle.t.sol`, run for the stock as token0 and as token1 and for both modes (4 contracts x 19 tests, `logs/fallback-prototype-tests.log`): fresh feed returned untouched and multiplier not applied; anchor stored at the answer's own timestamp with the TWAP ending then; anchor never written by the fallback and refused from a stale feed; poke refused after `maxPokeDelay`; fallback equals anchor x relative move (hold mode: the anchor); closure versus open durations; move limit both directions; basis refusal; pool history too short; every pause flag blocks everything including a fresh feed; multiplier change halts; borrow and allocation never use the fallback; liquidation off by default; guardian can only disable; short spike versus sustained push; invalid feed rounds fail closed. **Mutation check of the prototype's safeguards: 18 of 18 killed** (survivors: 0; `logs/fallback-prototype-mutations.log`). A mutant counts as killed when any test of `AnchoredFallbackOracle.t.sol` (the unit suite, 4 contracts) fails or the mutated source no longer compiles; the economics and fork tests were not part of the mutation loop.

**Fork integration against the real SPY and NVDA feed, token and pool** (`test/fork/FallbackFork.t.sol`, `logs/fallback-fork.log`, 10 tests per pair, header line gives the pin): anchoring reads the real answer at its own timestamp and equals the pool's own `observe()` arithmetic; the closure calendar is a test double; time is moved with `vm.warp`, so the real pool's `observe()` extrapolates its last tick as in a closed market; a funded trader pushes the real pool by real swaps (there is no Ramses vault liquidity in it, which is the post-removal state); multiplier steps are written into the token's storage slot. Fail-closed classes, none of them a generic catch-all:

| failure | result |
| --- | --- |
| feed call reverts | the feed's revert bubbles (not caught), in `price()` and `poke()` |
| feed answer zero or negative, timestamp in the future, answered round older than the round | `InvalidAnswer` |
| a newer answer that was never anchored | `AnchorMissing` |
| token `paused()` / `oraclePaused()` true | `IssuerPaused`, **even with a fresh feed**, for every operation class |
| token pause call reverts | bubbles |
| `uiMultiplier()` differs from the anchor | `MultiplierChanged` |
| pool `observe()` cannot serve the window | `NoPoolHistory` (the one `try/catch`, which re-reverts with a specific error) |
| calendar call reverts | bubbles |
| age beyond D, pool move beyond L, class not allowed, fallback disabled | `FallbackExpired`, `MoveExceedsBound`, `FallbackNotAllowedFor`, `FallbackDisabled` |

Delayed pokes across a multiplier step: a step **after** the anchor halts the fallback; a step that lands **between the answer and a permitted late poke is invisible** (the anchor records the post-step multiplier against a pre-step answer and the fallback proceeds); `maxPokeDelay` bounds that window (1 h) and does not close it, and a poke beyond it is refused. A held push on the real pool moves the estimate up to L (after a push of about 1% the estimate was 101.00% of the anchor for SPY and 101.47% for NVDA at the pin) and a larger held push fails closed with `MoveExceedsBound`.

### 16H. Independent oracles and stock-price APIs (verified conclusion)

Full report and raw evidence: `evidence/oracle-research/REPORT.txt`, `manifest.json` (URLs, retrieval times, hashes), `*.requests.json` (exact calls). **Independent oracle contracts exist on Robinhood Chain, but none holds a SPY or NVDA price and none is sponsored.** Three statements are kept apart: *contract exists* (Pyth Core `0x8250f4aF...487a` and the Pyth Pro verifier `0xACeA761c...F481` have code; Stork `0xacC0a0cF...4fd62` has code), *feed exists* (no: the four Pyth equity ids revert `PriceFeedNotFound` in Core; all Stork ids tested revert; a full-chain log scan found 860 Pyth Core events, none for an equity), *feed stays fresh* (not applicable: never stored; the BTC control feed was 466,619 s stale). Pyth's own list of sponsored/pushed EVM chains does not include Robinhood Chain and the 25 distinct senders in a sample are ad-hoc pulls. Cost: the free plan has no API access; equity symbols need Pro from $2,500/month (confirmed from the pricing page; the $500 Starter plan is crypto only); Pyth's terms bar re-publishing its data on chain outside its contracts. Hermes returned 401 without a key, so SPY and NVDA prices and publish times could not be read: **a failed read, not a skip**. Free stock APIs (Alpaca Basic: IEX-only real time, 200 historical calls/min; Twelve Data Basic: 8 credits/min, internal non-display use only) fit an off-chain shadow monitor, not publication; neither documents provider signatures, so a contract could not verify their quotes: an API publisher would add trust in OUR signer (technically possible, and a licensing question for the free tiers). Unverified, not absent: RedStone, API3, Chronicle, Switchboard, DIA, Supra, and Chainlink Data Streams for this chain. New facts from the Chainlink page: overnight prices are smoothed (an EMA), the issuer describes `oraclePaused()` as advisory for its own contracts (ours enforces it, see the blocker note), and a feed can report a zero price with a fresh timestamp.

### 16I. Economics on the real vault, guard, markets and controller (local doubles for pool, position manager, gauge, token)

`test/fallback/FallbackEconomics.t.sol` (6 tests x 4 contracts: stock token0/token1 x anchored/hold; `logs/fallback-economics.log`). A test-only shim presents the prototype's price for the selected operation class to the real guard; production would need the class plumbed through the guard and lending price source, which is an implementation cost of options 3 and 4. All numbers are from the log.

* **A stationary pool hides any real gap from L.** With the pool unmoved, the estimate is the anchor whatever the market did: the error against a market that gapped -6%, -20%, -50% is 638, 2,500 and 10,000 bp against L = 200 bp. L bounds the pool's move, not the market's.
* **Loss moves from the exiting supplier to the remaining ones.** A -20% gap that the pool did not show: an exiting USDG supplier is paid 199.999999 USDG at the stale estimate, against 196.541515 for the same position had it stayed (+3.458 USDG, 1.73% of 200). The remaining USDG supplier's claim falls from 196.541515 to 193.083724 USDG, i.e. by the amount the exiter avoided; the pair's total recognized loss is identical in both worlds (9.7487 USD-wad), and the stock supplier's claim is unchanged. The exit changed who bears the loss, not how much there is. This holds for both token orders and both modes.
* **Repeated exits and refills accumulate.** Three closures, each ending with a -6% gap the pool did not show; the same supplier exits during the closure and refills after the gap is visible: loss avoided 0.530, 1.693 and 3.088 USDG (5.311 cumulative), while the remaining supplier's claim falls from 200 to 194.728 USDG. Nothing in L or D bounds this sum; pair caps and withdrawable balances do.
* **Both borrowing directions.** Stock collateral and USDG debt: a 0.33-stock collateral redemption classed as BORROW fails closed; classed as an exit it is admitted at the stale price and the account has a shortfall of 10.62 USD after a -20% gap. USDG collateral and stock debt: the same, 4.59 USD shortfall after a +25% gap. A liquidity check on an account with debt must therefore never use the fallback.
* **Flooring is one-directional.** With a 1.9% downward push held for the window, a stock-collateral borrower near the limit is liquidatable at the unfloored estimate (shortfall 0.137 USD) and not at `max(anchor, fallback)`; a stock-**debt** borrower near the limit with the market unchanged becomes liquidatable under the floored price when the pool is pushed up (shortfall 0.140 USD). Hold mode never moves the price, so neither case triggers there, and it cannot see a real move either.

### 16J. Corrections to earlier statements, remaining failures, and what was not done

* **Withdrawn:** "L = 2% limits mispricing to about $20 on a $1,000 cap"; "max(anchor, fallback) removes false liquidations" and "a real fall is not acted on until the feed returns or D expires" as a safety property (expiry makes the fallback unavailable; it does not restore liquidation); the "none found on Robinhood Chain" oracle statement (replaced by 16H); the Chainlink "search summary only" caveat; availability figures that assumed every answer was anchored, one duration for all sessions and a one-hour TWAP (superseded by 16E; the old `fallback-study.json` policy table is kept as the idealized upper bound).
* **Remaining failures even with option 3 or 4:** closures longer than D; real moves the pool does not show (the stationary-pool gap) and the loss they transfer; a missed or late poke; wide-basis answers (NVDA overnight); a multiplier step between answer and poke; META, GLD and r33; every issuer pause and corporate action (by design); a wrong calendar; and any exit blocked by the missing token (15C).
* **CI coverage, stated exactly.** The repository's workflow (`.github/workflows/verify.yml`) runs `make verify`, `make build`, `make test`, `make test-remediation` and `make reproduce`, which cover the snapshot and the `remediation` folder. **It does not run the `ramses` profile (`make test-ramses`, `make fork-ramses`) or the Python research tests.** The Solidity and Python results above were run locally, the fork tests need a fresh pin on the public RPC (about 9 minutes of state), and a green CI check says nothing about them. Adding them to CI is a recommendation, not done here.
* **Not done:** SPY real-pool cost of holding a push through a closure; META dataset/fork tests; r33 (stays blocked: no collateral factor or activation until a complete route executes atomically at one fork block with real fees, slippage limits and adapter requirements and a defensible price source exists); the calendar, the poke keeper and the guard/lending plumbing; a mainnet-fork test of the guard consuming the fallback; the choice between options 3 and 4, D and L, which is the operator's.
* **Cheapest credible option, with its limit.** On this evidence it is hold-and-bound (option 4) restricted to **debt-free** supplier exits and settlement valuation, liquidation, borrowing, leverage and allocation excluded, with an off-chain shadow monitor for alerts and an independent pause. It is the cheapest because it has no price-adjustment logic, the same availability as option 3 and the smaller measured error; it is credible only if the loss transfer in 16I is acceptable (it is a transfer from remaining to exiting suppliers of up to roughly the real gap on the exited amount, bounded in practice by caps and withdrawable balances), which needs an operator decision and, likely, an exit throttle that does not exist. Doing nothing (keeping `StaleOracle` behaviour) is the other credible option and carries no new trust.

## 17. Robinhood priorities after the oracle deferral (live baseline, simple strategies, reserve, prototypes, costs)

Research, read-only verification and test-only prototypes. No `src/` change, no merge, signing, broadcast, deployment, cap change, borrowing activation or handoff 00 edit. The v1.2 package is preserved and not rebuilt. The operator's approximate 1% APY is **an observation stated in conversation, not an evidence artifact**; it is neither used as an input nor as a target below.

### 17A. Reconciled live baseline (Uniswap NVDA/USDG vault, read fresh)

Read-only reconstruction by a delegated agent from chain state and event logs (`evidence/live-baseline/`: scripts `lb_*.py`, `run_all.sh`, raw request log `raw/rpc-log.jsonl`, results `results.json`, `decomposition.json`, `costs.json`, `state-table.json`, `stale-oracle-simulation.json`); the lead re-read the following **independently with `cast`** and they match: the ledger (stock principal 21,137,493,913,601,450 raw, stock idle about 2.962e16, USDG idle 0, cumulative loss 546,235,985,148,367,630 to the wei), both pToken exchange rates (pNVDA 198853415317349043453506389 identical; pUSDG within a fraction of a percent of a percent of the agent's value, it moves with each fee collection), pUSDG total borrows 1,936,152, the controller's oracle address, and the live oracle's NVDA staleness threshold (259,200 s) with an empty cached-price slot. Main pin: L2 block 104,960,794, hash `0x93a7902a...1772`, native block 26,333,444, 2026-11-03T09:42:13Z. State is pruned within minutes, so each group of state reads has its own pin (listed in `raw/snapshot.json`); everything historical is rebuilt from logs plus end-state checks.

**Bottom line.** The operator's approximate 1% APY is not reproduced. **Suppliers have lost money since the market was seeded (2026-09-05, 59 days):** exchange-rate growth pNVDA **-0.573%**, pUSDG **-1.180% stored (-1.165% with interest accrued)**, about -3.5% and -7.2% annualised (simple). The cause is the LP: about **$0.196 gross fees** ($0.157 net of the 20% reserve cut) against **$0.546 recognized strategy loss**, net about **-$0.385** on a time-weighted principal of $9.8 to $19.4. **Borrowing interest to suppliers is about $0.005** (utilisation 0.04% on pNVDA, about 6.6% on pUSDG; realised average borrow rate 2.0% and 2.7%). The nearest figures to "1%" are the pUSDG instantaneous supply rate (0.43%) and the size of the negative exchange-rate moves; none is a positive realised yield. All of this is on a TVL of about **$36** (pNVDA $7.01, pUSDG $29.18), mostly protocol or team wallets. Protocol-paid costs exceed all income (below). Short-window annualisations are meaningless: the 7-day and 24-hour figures reflect one loss burst booked on 2026-11-02.

**The repository CLAUDE.md is stale on LP state:** allocation was unpaused on 2026-10-06 (block 81,683,197), the ranged vault V3 and its adapter were installed on 2026-10-09, and a live ranged position (token 4148743) holds liquidity. Swaps remain paused. (The `deployments/README.md` read by the agent has no record of the 10-09 install; the bytecode was not compared to the V3 source.)

| item (fresh) | value |
| --- | --- |
| position | ticks 220,800 to 225,660, pool tick 223,113 (in range); liquidity 3.903e12 (0.00136% of the active pool); 0.029811 NVDA + 6.673570 USDG = **$12.77** at pool spot and at the oracle reference (pool 204.5549, oracle 204.5234) |
| ledger | principal 0.021137 NVDA and 14.4229 USDG; idle 0.029622 NVDA and **0 USDG**; cumulative loss $0.546236 (equals the sum of the 19 negative checkpoint results); last checkpoint 2,304 s old (limit 3,600) |
| caps and policy | pair cap $1,000; ranged cap **$50**; half-width 2,400 ticks, trigger 600, one hour minimum between recentres, at most 4 per day, loss bound 10 bp; reserveFeeBps 2,000; **allocationPaused false, swapsPaused true, emergencyMode false**; aggregate USDG principal 14.42 of $1,000 |
| reserve | 0.000286618 NVDA + 0.017193 USDG, about **$0.076** (equals reserve funded minus covered to the unit); per-event $10, daily $25, coverage 50%, not paused |
| guard | feed answer 204.5234, age 2,233 s, 12 h bound, no sequencer feed; NVDA `oraclePaused()` and `paused()` false |
| pNVDA / pUSDG | NAV 0.034263 NVDA ($7.01) / 29.1774 USDG; borrow rates 2.005% / 3.640%; supply rate 0.00077% / 0.430%; collateral factor 0.8; mint, borrow, seize **not paused**; last interest accrual 2026-10-08 (stored rates exclude about 26 days of interest, a difference of 0.012% for pUSDG) |
| live debt | about **$1.94**: two isolated margin positions (gross $1.27 / $1.68, debt $0.97 / $0.96, health 23,832 / 42,906 bp, not liquidatable) and dust on the controller path ($0.009 against $0.011 of collateral) |

| return decomposition since seed (59 days) | pNVDA | pUSDG | USD |
| --- | --- | --- | --- |
| borrowing interest to suppliers | 2.35e10 raw | 4,853 raw | +0.0049 |
| LP fees gross (pair-wide) | 5.158e14 raw NVDA | 86,133 raw USDG | 0.1958 |
| reserve cut (20%) | 1.03e14 raw | 17,193 raw | 0.0391 |
| LP income net (credited to principals) | +4.13e14 raw | +68,940 raw | **+0.1567** |
| strategy loss (principal scale-down) | -6.10e14 raw | -418,176 raw | **-0.5462** (NVDA side 0.128, USDG side 0.418) |
| NAV result excluding flows | -1.98e14 raw | -344,383 raw | **-0.3848** |
| protocol-paid: keeper `0x978e...` 176 transactions (96 checkpoints, 78 rebalances, 2 recentres) | | | **$5.244** |
| protocol-paid: governor LP operations since 2026-09-15 | | | $0.604 (all time $3.385; one-off admin/upgrade about $12.05) |
| result net of ongoing operating cost | | | **about -$6.23** |

(Gas is gasUsed x effective gas price from receipts at the on-chain ETH price of $2,254; the keeper list is complete (nonce 176), the governor list is a lower bound; 18 test-wallet transactions ($1.24) are excluded.) The "unreconciled residual = 0" lines in the agent's files are an algebraic identity of its timeline model, not an independent check. The independent checks that do hold: log-rebuilt supply, borrows, reserves and balances equal the chain's; rebuilt vault principals equal the ledger; the recomputed exchange rates equal the chain's to the wei; reserve funded minus covered equals the balances; adapter fee events equal vault fee events; the sum of negative checkpoint results equals the cumulative loss.

**Findings that matter for activation**
1. **A live one-sided shortfall of exactly the kind modeled in 15C and 17E.** The vault holds 6.67 USDG against 14.42 owed (a **7.75 USDG shortfall**) and 0.0383 NVDA (about **$7.84**) more than its stock principal; swaps are paused and the reserve holds $0.076. Only 12.82 of pUSDG's 29.18 USDG NAV is instantly redeemable. The stock surplus value ($7.84) is the USDG shortfall plus the $0.084 by which assets exceed the benchmark, which is the premise the in-kind rule uses (17E). The gain branch never fired: 0 of 190 checkpoints booked a gain while 19 booked a loss.
2. **The keeper ran only 10-19 to 10-22 and 11-02 to 11-03:** no checkpoint for 10.8 days (earlier gaps 5.5, 2.9 and 20 days); losses are recognized lazily at the next checkpoint, so the 11-02 burst followed 10.8 unchecked days. The cause of the gap is not established.
3. **Cash definition:** the interest-rate model's cash counts local balance plus the vault's *withdrawable*, while the exchange rate counts full principal, so utilisation (13.1% vs 6.6% NAV-based for pUSDG) jumps when withdrawable changes.
4. The token's `uiMultiplier` is 1.000775 and the lending oracle and guard ignore it (consistent with the feed already including it; not investigated further).

**Does the stale-price liquidation issue affect the existing live debt? Yes, in two different ways, and not the way the undeployed Ramses markets behave.** (a) The controller path is **not** fresh-gated: `StockSimplePriceOracle` uses a **72 h** staleness threshold for NVDA (259,200 s) and its cached-price slot is empty (`lastValidChainlinkPrice` = 0, `updateChainlinkPrices` has never been called). Verified by `eth_call` with a state override of the threshold: with the feed between 12 h and 72 h old the lending oracle **silently prices at the stale answer** (no revert; borrow, redeem and liquidate proceed); beyond 72 h `getUnderlyingPrice` returns 0 and borrow, redeem and liquidate fail with PRICE_ERROR. Feed history (1,426 on-chain rounds): older than 12 h for **26.45% of the last 30 days** (10 gaps, longest 56.9 h), 0% beyond 72 h in the last 30 days, 0.32% since the feed began (gaps of 76.1 h and 78.2 h, both before borrowing opened). (b) The two isolated margin positions that hold the $1.94 are liquidated through `GuardedMarginPriceSource`, which **returns 0 when the 12 h guard fails**, so their liquidation (and new opens, and vault exits) are blocked for about a quarter of wall-clock time; both positions are currently healthy (not liquidatable), at 2.3x and 4.2x. The token pause flag is enforced by the guard (and so by the margin path) and **not** by the lending oracle path. This differs from the undeployed Ramses markets, whose price source reverts `StaleOracle` at 12 h for every action.

**Not verified or failed:** historical state rereads are unavailable on the public RPC; pending uncollected LP fees were not read (LP income is collected fees only); `0xdc48fd39...` (10.3% of pUSDG supply, received 11-03 from a team wallet) is unidentified; liquidation levels of the open margin accounts were not independently computed; `isLiquidatable()` returns true for closed margin accounts (probably a view quirk, whether the liquidator requires an active status was not checked); the Ramses price-source code was not read by the agent (the lead read `RamsesOracleGuard`).


### 17B. The simplest supported strategies, side by side

Reused evidence (`baseline_*_caps.json`, `baseline_*.json`; `strategy_comparison.py`, `evidence/strategy-comparison.json`), no new simulation. Windows are 74 days (SPY) and 90 days (NVDA) and are **not annualised**: the LP income is a few dollars, so an annualised figure would overstate how well it is measured. "Supplier return" includes the price move of the held inventory; **supplier excess vs hold is the comparable column**. LVR is never subtracted from a return versus holding.

**At the CURRENT CAPS ($1,000 pair, $50 ranged cap; the Ramses caps are operator-supplied and the live Uniswap vault reads back the same two numbers).** The $50 ranged cap applies to the recentring policies; full range is limited by the pair cap and deploys about the whole pair.

| pair | configuration | window (days) | supplier return | hold | supplier excess vs hold | fees to holders | reserve inflow | LVR (cost of being the counterparty) | worst week vs hold | idle capital (avg) | deployed (avg) | recentres | recentre gas | maintenance (protocol-paid, assumed) | protocol net |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| SPY | full range | 74 | -0.59% | -0.62% | +0.03% | $0.28 | $0.07 | $0.29 | $-0.06 | 0% | 100% | 0 | $0.00 | $42.69 | $-42.62 |
| SPY | ±600 ticks | 74 | -0.58% | -0.62% | +0.04% | $0.46 | $0.12 | $0.47 | $-0.11 | 95% | 5% | 0 | $0.00 | $42.69 | $-42.57 |
| SPY | ±1200 ticks | 74 | -0.60% | -0.62% | +0.02% | $0.24 | $0.06 | $0.25 | $-0.05 | 95% | 5% | 0 | $0.00 | $42.69 | $-42.63 |
| SPY | ±2400 ticks (live policy) | 74 | -0.61% | -0.62% | +0.01% | $0.12 | $0.03 | $0.14 | $-0.03 | 95% | 5% | 0 | $0.00 | $42.69 | $-42.66 |
| NVDA | full range | 90 | -0.21% | -0.32% | +0.10% | $1.08 | $0.27 | $2.18 | $-2.02 | 0% | 100% | 0 | $0.00 | $51.64 | $-51.37 |
| NVDA | ±600 ticks | 90 | -0.14% | -0.32% | +0.17% | $1.70 | $0.43 | $3.69 | $-3.35 | 95% | 5% | 20 | $1.33 | $51.64 | $-52.54 |
| NVDA | ±1200 ticks | 90 | -0.23% | -0.32% | +0.09% | $0.89 | $0.22 | $1.73 | $-1.76 | 95% | 5% | 6 | $0.40 | $51.64 | $-51.82 |
| NVDA | ±2400 ticks (live policy) | 90 | -0.27% | -0.32% | +0.05% | $0.46 | $0.12 | $0.73 | $-0.95 | 95% | 5% | 6 | $0.40 | $51.64 | $-51.92 |

**COUNTERFACTUAL, NOT APPROVED: ranged cap equal to capital.** The $10,000 research results scaled by 1/10 to $1,000. A 75% to 90% allocation to a ranged position is **incompatible with the $50 ranged cap** and needs a higher `maxRangedValueUsd`; the deployed shares below show it is not even reached by the narrower ranges in practice (one-sided inventory stays idle: 88% idle for NVDA ±600 ticks, 61% for ±1200, 29% for the live ±2400 policy). Only full range (about 100%) and the wide ±2400 policy on SPY (99%) get there.

| pair | configuration | window (days) | supplier return | hold | supplier excess vs hold | fees to holders | reserve inflow | LVR (cost of being the counterparty) | worst week vs hold | idle capital (avg) | deployed (avg) | recentres | recentre gas | maintenance (protocol-paid, assumed) | protocol net |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| SPY | full range | 74 | -0.59% | -0.62% | +0.03% | $0.28 | $0.07 | $0.29 | $-0.06 | 0% | 100% | 0 | $0.00 | $42.69 | $-41.99 |
| SPY | ±600 ticks | 74 | +0.20% | -0.62% | +0.82% | $8.94 | $2.23 | $8.82 | $-2.10 | 2% | 98% | 0 | $0.00 | $42.69 | $-20.34 |
| SPY | ±1200 ticks | 74 | -0.19% | -0.62% | +0.43% | $4.65 | $1.16 | $4.53 | $-1.08 | 1% | 99% | 0 | $0.00 | $42.69 | $-31.06 |
| SPY | ±2400 ticks (live policy) | 74 | -0.40% | -0.62% | +0.22% | $2.43 | $0.61 | $2.36 | $-0.56 | 1% | 99% | 0 | $0.00 | $42.69 | $-36.62 |
| NVDA | full range | 90 | -0.22% | -0.32% | +0.10% | $1.03 | $0.26 | $2.18 | $-2.02 | 0% | 100% | 0 | $0.00 | $51.64 | $-49.07 |
| NVDA | ±600 ticks | 90 | +1.87% | -0.32% | +2.19% | $3.39 | $0.85 | $14.37 | $-39.86 | 88% | 12% | 20 | $0.13 | $51.64 | $-44.49 |
| NVDA | ±1200 ticks | 90 | +1.65% | -0.32% | +1.97% | $5.94 | $1.48 | $18.92 | $-28.78 | 61% | 39% | 6 | $0.04 | $51.64 | $-37.20 |
| NVDA | ±2400 ticks (live policy) | 90 | +0.70% | -0.32% | +1.01% | $5.64 | $1.41 | $16.19 | $-16.96 | 29% | 71% | 6 | $0.04 | $51.64 | $-37.95 |

What the comparison says, without choosing: at the current caps the LP contributes **dollars of income over a quarter** (fees to holders $0.12 to $1.70, reserve inflow $0.03 to $0.43) against **assumed protocol-paid maintenance of $42.69 to $51.64** over the same window, so the protocol net is about -$42 to -$53 per quarter in every configuration; supplier excess versus holding is +0.01% to +0.17%. The wider configurations pay less fee and lose less to LVR; none changes the order of magnitude. A stronger APR is not a substitute for correct accounting and safe exits (17D to 17E), and none of these results show the LP, on its own, producing a visible supplier yield at the current caps.

### 17C. Reserve funding records, sizing and retention

**Can existing events and records tell a dedicated seed from retained earnings? Yes, without new storage.** `RamsesLossReserve.deposit` emits `ReserveFunded(pairId, token, funder, amount)`. The vault's retained fee cut reaches the reserve through the same function with **the vault as funder** (`RamsesRewardLib.credit` -> `reserve.deposit`); an operator seed has the operator address as funder; supplier deposits never enter the reserve; a plain token transfer to the reserve is **not** protection (it is surplus, invisible to `available`, recoverable only by `sweepSurplus`). Tested in `test/prototypes/ReserveProtocol.t.sol::testSeedRetainedEarningsAndStrayTransfersAreDistinguishable`. What does not exist: separate **balances** (seed and retained earnings are fungible inside `available(pair, token)` and are both withdrawable by the admin once the reserve is paused). No enforcement requirement was found that needs a separate seed balance: the coverage restriction in 17D keys on total available coverage. **No contract storage was added.**

**Stress sizing at the current caps** (`reserve_sizing.py`, `evidence/reserve-sizing.json`). Target: 100% of the modeled aggregate strategy shortfall, with +25% and +50% contingency shown separately. Shortfall S is the vault's own measure (principals valued at the oracle price minus what the pair holds) for a **gap** move with no recentring and **no future income counted** ("day-one backing"); concurrent withdrawals add a **swap-path cost** (maxSwapSlippageBps 100 plus the pool fee on the converted surplus) and **recovery gas once** (5 transactions at the baselines' $0.0663). The alternative column is the **no-swap cash** the scarce token would need so that every supplier of that side can exit at once without any swap (a liquidity need that overlaps the shortfall, so the two are never added). The reserve is held per token: a price fall leaves USDG scarce, a price rise leaves the stock token scarce. Historical moves (SPY worst 28-day -3.4% / +6.6%; NVDA -16.9% / +20.1%, about 10 and 13 weeks of feed answers) are **a baseline, not proof of a sufficient reserve**; the synthetic 30% and 50% gaps are not forecasts.

| configuration (deployed value) | modeled stress | price falls: USDG seed, loss cover 100% / +25% / +50% | price rises: stock seed (USD), 100% / +25% / +50% | no-swap cash instead (USDG / stock) | shortfall S down / up |
| --- | --- | --- | --- | --- | --- |
| A. current caps: ±600 ticks, $50 ranged (cap-compatible) | NVDA historical worst within 28d (x0.831 / x1.201) | $4.17 / $5.21 / $6.25 | $4.87 / $6.09 / $7.31 | $23.36 / $28.03 | $3.60 / $4.26 |
| A. current caps: ±600 ticks, $50 ranged (cap-compatible) | SPY historical worst within 28d (x0.966 / x1.066) | $0.73 / $0.91 / $1.10 | $1.49 / $1.87 / $2.24 | $14.83 / $26.53 | $0.25 / $0.90 |
| A. current caps: ±600 ticks, $50 ranged (cap-compatible) | NVDA synthetic gap 30% (x0.700 / x1.429) | $7.51 / $9.39 / $11.27 | $10.59 / $13.23 / $15.88 | $21.23 / $30.19 | $6.97 / $9.95 |
| A. current caps: ±600 ticks, $50 ranged (cap-compatible) | NVDA synthetic gap 50% (x0.500 / x2.000) | $12.62 / $15.78 / $18.93 | $24.91 / $31.14 / $37.37 | $17.25 / $34.17 | $12.12 / $24.24 |
| B. current caps: live policy ±2400 ticks, $50 ranged (cap-compatible) | NVDA historical worst within 28d (x0.831 / x1.201) | $2.26 / $2.82 / $3.39 | $2.59 / $3.23 / $3.88 | $18.98 / $22.44 | $1.74 / $2.03 |
| B. current caps: live policy ±2400 ticks, $50 ranged (cap-compatible) | SPY historical worst within 28d (x0.966 / x1.066) | $0.44 / $0.55 / $0.65 | $0.64 / $0.80 / $0.96 | $4.12 / $7.65 | $0.07 / $0.24 |
| B. current caps: live policy ±2400 ticks, $50 ranged (cap-compatible) | NVDA synthetic gap 30% (x0.700 / x1.429) | $5.82 / $7.28 / $8.73 | $8.18 / $10.22 / $12.26 | $22.23 / $31.62 | $5.27 / $7.53 |
| B. current caps: live policy ±2400 ticks, $50 ranged (cap-compatible) | NVDA synthetic gap 50% (x0.500 / x2.000) | $11.42 / $14.27 / $17.13 | $22.50 / $28.13 / $33.76 | $18.06 / $35.79 | $10.91 / $21.81 |
| C. current caps: full range, whole $1,000 pair (cap-compatible; the ranged cap applies to ranged policies) | NVDA historical worst within 28d (x0.831 / x1.201) | $4.69 / $5.86 / $7.03 | $5.43 / $6.79 / $8.14 | $42.50 / $50.32 | $3.93 / $4.59 |
| C. current caps: full range, whole $1,000 pair (cap-compatible; the ranged cap applies to ranged policies) | SPY historical worst within 28d (x0.966 / x1.066) | $0.57 / $0.71 / $0.85 | $1.03 / $1.29 / $1.55 | $8.90 / $16.89 | $0.15 / $0.53 |
| C. current caps: full range, whole $1,000 pair (cap-compatible; the ranged cap applies to ranged policies) | NVDA synthetic gap 30% (x0.700 / x1.429) | $14.42 / $18.03 / $21.63 | $20.46 / $25.57 / $30.69 | $74.15 / $105.79 | $13.34 / $19.06 |
| C. current caps: full range, whole $1,000 pair (cap-compatible; the ranged cap applies to ranged policies) | NVDA synthetic gap 50% (x0.500 / x2.000) | $44.42 / $55.53 / $66.63 | $88.51 / $110.64 / $132.77 | $118.18 / $236.03 | $42.89 / $85.79 |
| D. COUNTERFACTUAL, needs a higher ranged cap: ±2400 ticks, $750 ranged (75%) | NVDA historical worst within 28d (x0.831 / x1.201) | $29.22 / $36.52 / $43.83 | $34.14 / $42.67 / $51.21 | $280.02 / $331.90 | $26.05 / $30.44 |
| D. COUNTERFACTUAL, needs a higher ranged cap: ±2400 ticks, $750 ranged (75%) | SPY historical worst within 28d (x0.966 / x1.066) | $1.90 / $2.38 / $2.85 | $4.97 / $6.21 / $7.46 | $57.18 / $110.16 | $0.99 / $3.53 |
| D. COUNTERFACTUAL, needs a higher ranged cap: ±2400 ticks, $750 ranged (75%) | NVDA synthetic gap 30% (x0.700 / x1.429) | $82.70 / $103.37 / $124.05 | $118.00 / $147.50 / $177.00 | $328.84 / $469.63 | $79.03 / $112.91 |
| D. COUNTERFACTUAL, needs a higher ranged cap: ±2400 ticks, $750 ranged (75%) | NVDA synthetic gap 50% (x0.500 / x2.000) | $166.63 / $208.28 / $249.94 | $332.92 / $416.15 / $499.38 | $266.27 / $532.20 | $163.60 / $327.19 |
| E. COUNTERFACTUAL, needs a higher ranged cap: ±2400 ticks, $900 ranged (90%) | NVDA historical worst within 28d (x0.831 / x1.201) | $35.00 / $43.74 / $52.49 | $40.90 / $51.13 / $61.35 | $335.95 / $398.21 | $31.26 / $36.53 |
| E. COUNTERFACTUAL, needs a higher ranged cap: ±2400 ticks, $900 ranged (90%) | SPY historical worst within 28d (x0.966 / x1.066) | $2.21 / $2.77 / $3.32 | $5.90 / $7.37 / $8.85 | $68.55 / $132.13 | $1.19 / $4.23 |
| E. COUNTERFACTUAL, needs a higher ranged cap: ±2400 ticks, $900 ranged (90%) | NVDA synthetic gap 30% (x0.700 / x1.429) | $99.17 / $123.97 / $148.76 | $141.53 / $176.92 / $212.30 | $394.54 / $563.49 | $94.84 / $135.49 |
| E. COUNTERFACTUAL, needs a higher ranged cap: ±2400 ticks, $900 ranged (90%) | NVDA synthetic gap 50% (x0.500 / x2.000) | $199.89 / $249.86 / $299.83 | $399.44 / $499.30 / $599.16 | $319.46 / $638.58 | $196.31 / $392.63 |
| F. COUNTERFACTUAL, needs a higher ranged cap: ±600 ticks, $750 ranged (75%) | NVDA historical worst within 28d (x0.831 / x1.201) | $57.88 / $72.34 / $86.81 | $68.43 / $85.53 / $102.64 | $345.81 / $415.78 | $54.04 / $63.88 |
| F. COUNTERFACTUAL, needs a higher ranged cap: ±600 ticks, $750 ranged (75%) | SPY historical worst within 28d (x0.966 / x1.066) | $6.33 / $7.92 / $9.50 | $17.75 / $22.19 / $26.63 | $217.83 / $393.25 | $3.79 / $13.43 |
| F. COUNTERFACTUAL, needs a higher ranged cap: ±600 ticks, $750 ranged (75%) | NVDA synthetic gap 30% (x0.700 / x1.429) | $108.02 / $135.02 / $162.03 | $154.17 / $192.72 / $231.26 | $313.86 / $448.23 | $104.51 / $149.29 |
| F. COUNTERFACTUAL, needs a higher ranged cap: ±600 ticks, $750 ranged (75%) | NVDA synthetic gap 50% (x0.500 / x2.000) | $184.70 / $230.87 / $277.05 | $369.06 / $461.33 / $553.60 | $254.14 / $507.94 | $181.79 / $363.58 |

Reading it at the current caps: the cap-compatible configurations (A to C) need, at 100% coverage, on the order of **$2 to $5 per token for the NVDA 28-day-worst move, $6 to $20 for a 30% gap and $11 to $89 for a 50% gap** (+50% contingency: up to $8, $31 and $133), with the full-range configuration the largest because it deploys the whole $1,000. The counterfactual high-allocation configurations need **tens to hundreds of dollars** (a 90% ±2400 allocation: $35 to $41 for the NVDA 28-day worst, $99 to $142 for a 30% gap, $200 to $399 for a 50% gap) and are not approved.

**Earnings retention, modeled separately, nothing counted on day one.** The contract allows `reserveFeeBps` up to 5,000 (the live Uniswap vault uses 2,000). At the current caps the LP fees are a few dollars a year, so retention cannot build the seed in a useful time and cannot cover the operating cost:

| strategy at current caps | LP fees before the reserve cut, per year | retained at 50% (contract maximum 5,000 bp), per year | at the live 20% | protocol-paid operating cost per year (assumed / observed-average basis) | years of 50% retention to build the NVDA 28-day-worst seed (full range, 100%) |
| --- | --- | --- | --- | --- | --- |
| SPY/full_range | $1.71 | $0.85 | $0.34 | $209 / $52 | 6.4 |
| SPY/single600_trig300 | $2.84 | $1.42 | $0.57 | $209 / $52 | 3.8 |
| SPY/live2400_trig600_perday4 | $0.75 | $0.38 | $0.15 | $209 / $52 | 14.4 |
| NVDA/full_range | $5.46 | $2.73 | $1.09 | $209 / $52 | 2.0 |
| NVDA/single600_trig300 | $8.63 | $4.32 | $1.73 | $215 / $53 | 1.3 |
| NVDA/live2400_trig600_perday4 | $2.34 | $1.17 | $0.47 | $211 / $53 | 4.6 |

(The last column builds the full-range NVDA 28-day-worst seed from the configuration's own retained fees; protocol-paid cost is shown under both the assumed $0.5737/day and the observed-average $0.1428/day basis of the baselines.) **The seed has to come from the operator's capital on day one; retained earnings are a very slow top-up.**

**Four funding purposes, kept separate** (nothing is counted twice):

| purpose | where it lives | funded by | counted toward |
| --- | --- | --- | --- |
| supplier withdrawal cash | market local cash plus the vault's idle and LP balances (principals per side) | supplier deposits (pTokens) | supplier claims only; never reserve, never opex |
| loss reserve / protection | `RamsesLossReserve.available(pair, token)` | operator seed (funder = operator) and the retained fee cut (funder = vault) | strategy shortfall and the scarce-token cash gap (17C) |
| operating expenditure | keeper wallet gas, hosting | operator | not a claim on any pool; 17F |
| funded-liquidator capital | the liquidator's own balance (in-kind repayment, no swap) | the liquidator | the liquidator's repayment; **not** protocol capital, so no seed is sized for it |

### 17D. Restrictions on new risk: what exists and what is missing

Already enforced in the repository's contracts: `allocationPaused` and `emergencyMode` (admin, or the one-way guardian); the pair cap `maxPairValueUSDG` on deposits and at allocation, which also auto-sets `allocationPaused` at `rebalance` and `recenter`; `aggregateUsdgDepositCap`; the ranged-position cap `maxRangedValueUsd`; per-recentre loss bounds (`maxLossBps` at most 100 bp, `deployLossBps`), `minInterval` and `maxPerDay` (at most 24); checkpoint freshness (`CheckpointStale`); oracle freshness, pause flags and the tighter allocation deviation bound; market supply and borrow caps and collateral factors. The reserve's per-event, daily and coverage-ratio limits bound **payouts**, not new risk.

**Missing:** any restriction keyed to **reserve coverage** (the vault never reads the reserve before allocating); any automatic restriction after a **cover** event or a recognized loss; a requirement that a seed exist before allocation; a **mixed-token exit** (exits are single-token per side and revert when the side's token is scarce, 15C); and a way to resume allocation that checks coverage (resuming is a bare admin call).

### 17E. Local prototypes (test-only, `test/prototypes/`, nothing in `src/`)

1. **`CoverageGuard`** holds only the vault's existing one-way guardian role. It requires, per token, `reserve available >= requiredBps x oracle-valued liquidity in the ranged position`; `enforce()` (permissionless, idempotent) pauses allocation and swaps if short; `allowedAfter(extra)` and `headroomUsd()` say how much more liquidity the reserve supports. Tests: an adequately protected pair is left alone; a depleted reserve pauses **new risk only** (rebalance, recentre and `depositForPair` revert `AllocationPaused`) while **checkpoints and redemptions still work**; the guard **cannot unpause** (`GuardianCannotUnpause`); re-seeding does not resume by itself (admin decision); with a **stale feed `enforce()` reverts instead of pausing**, so staleness cannot be used to force a sticky pause.
2. **`InKindRedemptionModel`** is an isolated accounting model of an optional mixed-token redemption for **debt-free** suppliers (it moves no tokens and is not connected to a vault). A supplier receives its own token in the proportion the pair can pay it and the rest in the other token **at the fresh oracle price** (`pricesUSD18` reverts when stale, so **no stale price is ever used to make an exit pass**). Measured on the real vault fixture after a recognized loss: the stock surplus value equals the USDG deficit (90.6587 USD both, to six digits), so the surplus pays the scarce side exactly and nobody else is charged; with the position fully one-sided the whole USDG claim is paid as 1.2237 stock. Fuzzed (5,000 runs per property, both sides): the mix does not depend on the order of exits; the payout never exceeds the claim at the oracle price and is within rounding of it; the other side's claim stays covered; the remaining holders keep the same assets-to-principal ratio; a depleted reserve is neither needed nor touched; an account with debt in either market is refused; a stale oracle is refused; claims fully covered are paid in their own token. The recipient gets a mixture and bears the price risk of the other token; **immediate conversion into the originally deposited asset is not promised.**
3. **Funding-record tests** (17C).

**Test results:** 14 tests, fuzz properties at 5,000 runs (`logs/prototypes-tests.log`). **Mutation check** of the two prototype contracts (`logs/prototypes-mutations.log`): 10 mutants (guard satisfied by either token, guard not pausing swaps, ignored new exposure, enforce never pausing, debt check removed, first-mover mix, surplus check removed, a substituted price, rounding against the remaining holders, stock-side assets not reduced); **8 were killed on the first pass and 2 survived** (either-token coverage, removed surplus check); two tests were added and both were then killed: 10 of 10.

**Requires production integration later (not authorized here):** a vault entry point that releases the other side's surplus to the redeeming market, the pToken delegate path (burn, exchange-rate preview, `redeemAllowed`/liquidity and debt checks for the "permitted collateral claims" case, which the model deliberately excludes), the vault itself consulting `allowedAfter` inside `rebalance`, `recenter` and `depositForPair` and after every `cover()` (the guard is reactive, not atomic), storage for the coverage parameters, and an audit.

### 17F. Operating cost, itemized (nothing to subscribe to or migrate)

Known hosting baseline: the margin keeper on DigitalOcean App Platform, Frankfurt, **$12/month** ($5 worker plus $7 development PostgreSQL, observed). AWS capacity is **unverified and not assumed**; nothing here depends on it and no migration is requested.

| item | basis | per year |
| --- | --- | --- |
| hosting | existing $12/month app; a Ramses keeper job added to the same worker and database: $0 incremental; if run as a separate worker: +$5/month | $144 existing; $0 to $60 incremental |
| RPC | the public endpoint, rate limited (429s were hit), about nine minutes of archive state, browser-style User-Agent required; no paid plan proposed | $0 (risk: availability, no deep history) |
| keeper transactions | **measured on the live keeper** (17A, ETH at $2,254): checkpoint about $0.023, rebalance about $0.037, recentre about $0.06; 176 transactions cost $5.24 in total; the baselines assume $0.0663 per recentre and a maintenance of $0.1428/day (observed average) to $0.5737/day (assumed) | $52 (observed average) to $209 (assumed) per year; recentres add $1.6 to $5.4 per year (NVDA, 6 to 20 recentres per 90 days) |
| monitoring | the keeper's own status/health output; external heartbeat alerts and Telegram are deferred, not installed | $0 |
| seed capital | not an expense; it is protection capital (17C) | n/a |
| total incremental for the Ramses strategy | gas plus at most one extra worker | about **$52 to $270 per year**, against LP fees of $0.75 to $8.63 per year before the reserve cut |

### 17G. Wording corrections and CI

* **Oracle pause wording (corrected in 16 as well).** The issuer describes `oraclePaused()` as advisory in the issuer's own contracts; **our guard enforces it**: `RamsesOracleGuard.pricesUSD18` and the live `StockOracleGuard` revert `StockOraclePaused` when `paused()` or `oraclePaused()` is true. The check only helps if the issuer sets the flag; the 12 h staleness bound is what protects against a silent feed. Both are kept. Fallback work stays deferred; the stale-liquidation blocker stays (16, and 17H).
* **API publisher.** The absence of provider signatures means a publisher adds trust in **our** signer; it does not make that architecture technically impossible. No further provider research or fallback implementation was done.
* **CI.** `.github/workflows/verify.yml` now has a deterministic job `ramses-deterministic` (the `ramses` profile without fork tests: 425 tests passed locally in `logs/ramses-deterministic-suite.log`; the `src/` size check with tests and scripts skipped; and the Python research tests). `make size-ramses` as written **fails today** because a test imports the deployed-snapshot `RobinhoodBoostedDelegateV2` (24,625 bytes, 49 over EIP-170; unrelated to this work), so the job runs the size check without test imports and a **separate, manual-only** job `ramses-fork` (`workflow_dispatch`) that pins a fresh block per fork test file, writes the pin into each log and uploads the logs as an artifact. Fork results never gate the deterministic job and are not reported as its results. The new job has not run in CI at the time of writing.

### 17H. Exact remaining activation blockers (Robinhood / Ramses)

1. **Stale-price liquidation (16, unchanged):** on the Ramses markets the guard reverts `StaleOracle` while the feed is older than 12 h (about 51% of the time for SPY, 29% for NVDA in the study window), so an underwater borrower cannot be liquidated; blocks the decision to enable borrowing. Distinct from the live markets (17A).
2. **No reserve exists or is funded for the Ramses vault** and no on-chain restriction on new risk ties to its coverage (prototype only); the minimum day-one seed at the current caps is in 17C.
3. **Exits are single-token and revert when the side's token is scarce** (15C); the in-kind alternative exists only as a model.
4. **Economics:** at the current caps the LP's income is orders of magnitude below the operating cost; supplier excess versus hold is within +0.01% to +0.17% over the windows.
5. **Not deployed / not built:** the Ramses vault, a Ramses margin or router adapter, the DLMM adapter and an r33 price source (r33 stays blocked); META dataset and fork tests not done.
6. **Live Uniswap vault, separate from the Ramses activation but unresolved:** a 7.75 USDG one-sided shortfall against a $0.076 reserve with swaps paused (only 12.82 of pUSDG's 29.18 USDG NAV instantly redeemable); negative realised supplier return; keeper gaps of up to 20 days with an unestablished cause; lending priced on feed answers up to 72 h old while the guard refuses after 12 h; the 17A items not verified.
7. **CI:** the new jobs are unproven until they run.
