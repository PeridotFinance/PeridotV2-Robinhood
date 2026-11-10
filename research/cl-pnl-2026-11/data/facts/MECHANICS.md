# MECHANICS: what the contracts and keepers do (facts for the CL P&L backtest)

Read-only fact file, written 2026-11-10. No transaction was sent or signed, no fork job was run, no test was run.
Facts only; no recommendations.

**Status marks.** `CODE` = VERIFIED-IN-CODE (source read at the cited file:line). `CHAIN` = VERIFIED-ON-CHAIN (read
with `cast call` at the stated block). `DOC` = DOCUMENTED-ONLY (stated in a document, log or evidence file; not
re-derived here). `NOT FOUND` = searched, absent. A row with no mark takes its status from its source column: a
`.sol`, `.py`, `.ts` or config file:line is `CODE`; a `.md`, `.json`, `.log` or `.txt` evidence file is `DOC`. Fork and
anvil measurements are `DOC` (read from their logs; no fork was run for this file).

**Trees and heads cited**

| Tag | Path | Branch @ head |
| --- | --- | --- |
| `CL` | `/Users/joshua/Peridot/wt-cl-research` | `research/cl-pnl-2026-11` @ `d0162f7` (main as of 6 Nov, includes PR #15, #16, #17) |
| `KE` | `/Users/joshua/Peridot/wt-keeper-efficiency` | `ops/keeper-efficiency` @ `adedd35` (merged to main as PR #16) |
| `IK` | `/Users/joshua/Peridot/RobinhoodVaults/.wt-inkind` | `feat/ramses-inkind-production` @ `4e509dd` |
| `README` | `IK/ramses-vaults/research/dual-range-backtest/evidence/README.md` | same tree, 1,542 lines |
| `F247` | `/Users/joshua/Peridot/RobinhoodVaults/.wt-feed247` | `ops/nvda-247-feed` @ `1642684` |
| `SOLV` | `/Users/joshua/Peridot/RobinhoodVaults/.wt-solvency` | `ops/solvency-sequential` @ `22eb7da` |
| `P24` | `/Users/joshua/Peridot/RobinhoodVaults/PeridotV2-Robinhood` | `feat/range-policy-2400` @ `81036aa` |
| `DEP` | `/Users/joshua/Peridot/RobinhoodVaults/deployments` | untracked evidence |
| `AV` | `/Users/joshua/Peridot/peridot-contracts-2-5` | `avalanche-pharaoh-tracks-abcd` @ `d639d382` (`contracts/range-tp/`, `range-tp-keeper/`, `docs/deployment-handoffs/avalanche-range-take-profit.md`, `contracts/ceremony/margin-addresses.env` are untracked) |
| `DOCS` | `AV/docs/deployment-handoffs` | same tree |
| `RG` | `/Users/joshua/Peridot/.wt-range` | `proto/range-take-profit` @ `b573064a` (the Robinhood PR #7 code) |

**On-chain readbacks (saved next to this file)**

| File | Chain, block(s) |
| --- | --- |
| `robinhood-readback-2026-11-10.json` | Robinhood Chain, public RPC. Six batches, each pinned: L2 110,765,614 (ts 1794301632, 2026-11-10 09:07:12 UTC, hash `0x90a3c880...414e`), 110,765,893, 110,766,857, 110,767,543, 110,768,308, 110,775,357. The public RPC prunes state within minutes, so one pin for everything was not possible. Scripts and raw outputs are inside the file. |
| `avalanche-readback-vaults-block-99924000.json`, `avalanche-readback-lending-margin-block-99924000.json` | Avalanche C-Chain, public RPC, block 99,924,000 (ts 1794301565). Three network calls in total (one `block-number`, two Multicall3 `aggregate3`). |

**Read this first: three things the brief assumed that the chain does not show today**

1. **The live Uniswap v4 NVDA/USDG vault holds no LP position.** It was fully unwound on 2026-11-09 and allocation is
   paused. `CHAIN` @110,765,614: position NFT 4238052 liquidity 0; pair flags allocationPaused true, swapsPaused true,
   emergencyMode false; ledger stockPrincipal 0.019752 NVDA, usdgPrincipal 0, stockIdle 0.019202 NVDA, usdgIdle
   0.096123 USDG, cumulativeLossUSDG $1.931525. `DOC`: `SOLV/remediation/RECOVERY_SESSION.md:157-185` (sync tx
   `0x81447d27...d517`, block 110,130,582; "Allocation stays paused (flags 110). The operator decided not to reopen it").
   The range policy (2400 / 600) is still stored.
2. **The Ramses SPY/USDG contracts are deployed but the pair is not configured.** `CHAIN` @110,765,893: vault proxy
   `0x67e7742922f4CC88C545e5aB5Df402b22DB4830b` and adapter `0x0C6e8a9f43aAB3D6Be780Ac3778a0EDE525da6bE` have code;
   `pairConfig`, `ledger`, `rangePolicy` are all zero; adapter reads revert `0xfee00694`; `allowRewardForfeiture` false;
   `maxRewardShortfallBps` 0. Addresses from `IK/ramses-vaults/deployments/spy-usdg-mainnet.json`. So every Ramses
   parameter below is source or deployment-script, not live config.
3. **The 120 s reward wait is not in `ops/keeper-efficiency`.** It is in `IK/ramses-vaults/keeper/ramses_keeper.py:39`.
   `KE` holds only the rebalance value gate (section 1.5).

---

## 1. Range recentre mechanics: Robinhood Uniswap v4 vault (NVDA/USDG)

Addresses (`CL/remediation/tools/range_keeper.py:28-32`): vault `0x280825b2d856706Ff7E0d6351CcB2e935E1a9A2f`, adapter
`0xadA73211711e4790bc83B5d6B39f47fE04D276f3`, pair id `0xe2050352...6b06`. Guard `0xaE4D4DdB8dD646951d54fE9B13BE23DcB61C6741`
(`CHAIN`). Pool key (`CHAIN`): currency0 USDG `0x5fc5...d168`, currency1 NVDA `0xd060...9EEC`, fee 3000, tickSpacing 60,
no hooks.

### 1.1 Live parameters

| Parameter | Value | Status and source |
| --- | --- | --- |
| Range policy `enabled / halfWidthTicks / triggerTicks / minInterval / maxPerDay / maxLossBps / maxRangedValueUsd` | true / 2400 / 600 / 3600 s / 4 / 10 / 50e18 ($50) | `CHAIN` @110,765,614 `rangePolicy(bytes32)`. Same values in `P24/remediation/script/SetRangePolicy.s.sol:25-30` and `README:206` |
| Range state `centerTick / initialized / lastRecenter / head` | 224493 / true / 1794205323 (2026-11-09 06:22:03 UTC) / 6 | `CHAIN` |
| Position ticks | 222060 .. 226920 (width 4860 = 2 x 2400 + 60), ranged true | `CHAIN` adapter `positionTicks` |
| `maxPairValueUSDG` | $1,000 | `CHAIN` `pairConfig` |
| `maxSettlementSwapUSDG` | $25 | `CHAIN` |
| `maxCheckpointAge` | 3600 s | `CHAIN` |
| `maxDeadlineDelay` | 300 s | `CHAIN` |
| `reserveFeeBps` | 2000 (20%) | `CHAIN` |
| `maxSwapSlippageBps` / `withdrawOverUnwindBps` | 100 / 200 | `CHAIN` |
| Adapter `removalToleranceBps` | 400 | `CHAIN` `removalTolerance` |
| Guard `maxStaleness` | 43,200 s (12 h) | `CHAIN` `feedConfig` |
| Guard `maxPriceDeviationBps` (allocation) / `maxRemovalDeviationBps` | 300 / 600 | `CHAIN` |
| Guard stock feed / USDG | `0x379EC4f7C378F34a1B47E4F3cbeBCbAC3E8E9F15` ("RHNVDA / USD", 8 decimals) / fixed $1 | `CHAIN` |
| Guard sequencer feed | none (address 0) | `CHAIN` |
| Loss reserve | `0x806b182B050f7EcF908758dD6bBF91DB8B2212aF`, available 0.000340503 NVDA for the pair | `CHAIN` @110,768,308 |

Two sources disagree on an earlier policy: the rollout policy was 1200 / 300 until the October change
(`P24/remediation/script/SetRangePolicy.s.sol:11,47`); the live value is 2400 / 600.

### 1.2 Bounds the contract allows (`CODE`: `CL/remediation/src/libraries/RangeLib.sol:24-28, 68-81`)

| Field | Bound |
| --- | --- |
| `halfWidthTicks` | 600 .. 4800, a multiple of the pool tick spacing |
| `triggerTicks` | tickSpacing .. halfWidthTicks / 2 (integer division) |
| `minInterval` | 1 hour .. 7 days |
| `maxPerDay` | 1 .. 24 |
| `maxLossBps` | 0 .. 100 |
| `maxRangedValueUsd` | > 0 |
| Pair config | `maxDeadlineDelay` <= 30 min, `reserveFeeBps` <= 5000, `maxSwapSlippageBps` 1..500, `withdrawOverUnwindBps` <= 2000 (`CL/remediation/src/RobinhoodBoostedVaultV3.sol:1018-1028`) |
| Guard | `maxRemovalDeviationBps` >= allocation bound and <= 2000 (`CL/contracts/robinhood-vaults/src/StockOracleGuard.sol:33, 103-105`). This source has no upper limit on `maxStaleness` or on the allocation bound other than <= 10,000 (`:96-97`). `README:189-190` states "at most 12 h" and "at most 5%"; those limits are in the Ramses guard (section 2), not in this file. |
| Policy can only be set if the adapter's removal tolerance covers the guard's removal gate: `2 x (tolerance - 100) >= maxRemovalDeviationBps` (`RobinhoodBoostedVaultV3.sol:485-493`). Live: 2 x (400 - 100) = 600 >= 600. |

### 1.3 What `recenter(pairId, deadline)` does, in order (`CODE`)

| Step | Behaviour | Source |
| --- | --- | --- |
| Access and gates | `KEEPER_ROLE` only. Reverts `RangePolicyDisabled`, `InvalidConfiguration` (band no longer covers the gate), `AllocationPaused`, `EmergencyMode`, `InvalidDeadline` (deadline in the past or more than `maxDeadlineDelay` ahead) | `RobinhoodBoostedVaultV3.sol:506-520, 1007-1011` |
| 1. Checkpoint | `validateRemovalPrice` (pool vs oracle within the removal bound, else revert `PriceDeviation`; oracle older than `maxStaleness` reverts `StaleOracle`; token `oraclePaused()` reverts `StockOraclePaused`). Collects fees. Marks pair assets at the **oracle reference price** and books loss or gain | `:522-530, 726-779`; `StockOracleGuard.sol:157-175, 193-235, 237-255` |
| 2. Pair-cap breaker | If principal value > `maxPairValueUSDG`: sets `allocationPaused = true` and returns without recentring (no revert) | `:531-536` |
| 3. Eligibility | Cooldown: reverts `RecenterCooldown` if `now < lastRecenter + minInterval`. Trigger: the **oracle** tick (`getTickAtSqrtPrice(oracle reference)`) must have moved >= `triggerTicks` from the centre stored at the last recentre, **or** the oracle tick must be at or outside the old range (`<= oldLower` or `>= oldUpper`); otherwise revert `RecenterNotNeeded`. The pool tick is not used for the trigger | `RangeLib.sol:236-238, 283-303` |
| 4. Daily budget | Rolling 24 h: the `maxPerDay`-th most recent recentre must be at least 1 day old, else revert `RecenterRateLimited` | `RangeLib.sol:305-314` |
| 5. Value before | Idle + position amounts **computed at the oracle reference sqrt price**, valued at oracle prices | `RangeLib.sol:241-242, 348-368` |
| 6. Remove and burn | All liquidity removed with minimum amounts from the oracle band (lowest payout of each token over ref x (1 +/- d), d = 2 x (tolerance - 100) + 20 bp = 620 bp live, times 99%); the empty NFT is burned | `RangeLib.sol:316-346`; `CL/remediation/src/UniswapV4PairedAdapterV3.sol:572-626` |
| 7. New range | `lower = floor(oracleTick / spacing) x spacing - half`, `upper = floor + half + spacing`. **Centred on the oracle price, not the pool price** | `RangeLib.sol:246-248, 270-281`; `RangeTypes.sol:7` |
| 8. Redeploy | **No swap.** Matched value only: `min(idle stock value, idle USDG value, maxRangedValueUsd / 2)` per side at oracle prices; both token amounts are offered to the adapter, which computes liquidity at the **pool** spot price and refunds what the range does not take. The surplus of the heavier token stays idle in the vault. With nothing to pair the recentre still completes with everything idle (`allowEmpty`) | `RangeLib.sol:93-126, 249-253`; `UniswapV4PairedAdapterV3.sol:266-272, 298-309` |
| 9. Deploy loss bound | If (oracle-valued LP value gained) + usedValue x `maxLossBps` < usedValue: revert `DeployLossTooHigh` | `RangeLib.sol:158-167` |
| 10. Recentre loss bound | If valueAfter + valueBefore x `maxLossBps` / 10,000 < valueBefore: revert `RecenterLossTooHigh` | `RangeLib.sol:255-259` |
| 11. Post-check | `validatePoolPrice` with the allocation bound (300 bp live): revert `PriceDeviation` | `RangeLib.sol:260` |
| 12. Second checkpoint | Books the recentre's own bounded loss at the same oracle reference | `RobinhoodBoostedVaultV3.sol:544-545` |

**`maxLossBps` semantics (10 bp live).** What is measured: total pair assets (idle + LP amounts at the oracle reference
price), valued at oracle prices, before versus after the remove / burn / redeploy. Against what: the value before.
When exceeded: the whole transaction **reverts** (`RecenterLossTooHigh`, or `DeployLossTooHigh` for the add leg). The
keeper only sends after a successful simulation, so in practice the recentre is not sent and is retried at the next
poll; both names are classed "not waiting", so the worker logs `ATTENTION` (`CL/remediation/tools/range_keeper.py:50-51,
305-316`). Nothing is skipped or partially applied.

**Swap at recentre: none.** The only swap path in the vault is the settlement swap on a withdrawal, gated by
`swapsPaused` (true live) and `maxSettlementSwapUSDG`. `README:178`: "recenter has no swap (matched value only)".
`README:788-790` (15C): "recentring alone does not create the missing token: the matched-only redeploy leaves the stock
idle".

### 1.4 `rebalance` and `checkpoint` (`CODE`)

| Call | Behaviour | Source |
| --- | --- | --- |
| `rebalance` | Requires a checkpoint no older than `maxCheckpointAge` (revert `CheckpointStale`). Order of checks: `AllocationPaused`, `EmergencyMode`, checkpoint freshness, deadline. Then `validatePoolPrice` (allocation bound), collect fees, pair-cap breaker, deploy matched idle value up to the remaining ranged cap per side `(maxRangedValueUsd - LP value) / 2`. For a ranged position the deploy loss bound is the policy's `maxLossBps`; for full range there is no bound. Ends with a checkpoint when ranged | `RobinhoodBoostedVaultV3.sol:399-443`; `RangeLib.sol:205-223` |
| `checkpoint` | Collects fees (reserve cut applied), marks at the oracle reference. If assets < principal benchmark: both principals are scaled by gross / benchmark and `cumulativeLossUSDG` rises. Gain branch only if **both** token balances are at or above their principals | `:560-570, 726-779` |
| Fees | `collectFees` gross amounts; `reserveFeeBps` of each token goes to the loss reserve; the rest is added to idle **and** to principal (so it compounds to suppliers via the pToken exchange rate) | `:687-724` |

### 1.5 Keeper (`CODE`: `CL/remediation/tools/range_keeper.py`, `CL/keeper/worker.py`, `CL/keeper/app.json`)

| Item | Value | Source |
| --- | --- | --- |
| Poll interval | 300 s (`INTERVAL_SECONDS`, floor 60) | `worker.py:127`; `app.json:17` |
| Per poll | Simulate `recenter`; if it would succeed, send it. Otherwise, if the revert is a "waiting" reason, try `rebalance`; if that reverts `CheckpointStale`, send `checkpoint` and re-simulate `rebalance` | `range_keeper.py:305-333, 250-302` |
| Hourly checkpoint | Emergent, not a timer: a checkpoint is sent on the first poll after the last one is older than `maxCheckpointAge` (3600 s) and the contract accepts it | `range_keeper.py:257-274`; `CL/keeper/README.md:20` |
| Rebalance spacing | At most one `rebalance` per 3600 s (`REBALANCE_MIN_INTERVAL`) | `range_keeper.py:191, 291-292` |
| Rebalance value gate (PR #16) | Send only if matched idle value >= 10 x estimated gas cost (750,000 gas x gas price x ETH/USD feed `0x78F3...d3A9`, feed age <= 86,400 s). Env override `REBALANCE_MIN_VALUE_MULTIPLE` | `range_keeper.py:200-247`; identical in `KE/remediation/tools/range_keeper.py:202` |
| Gas price cap | 0.1 gwei default, never above 2 gwei | `range_keeper.py:37`; `worker.py:131` |
| Gas floor | Refuses to send below 0.0003 ETH (`awaiting_gas`) | `worker.py:35, 143-147` |
| Deadline | now + 120 s in the worker (290 s in the manual tool) | `worker.py:132`; `range_keeper.py:36` |
| Gas limit | 3,000,000 | `range_keeper.py:33` |
| Stale feed, weekend, holiday | No calendar logic. `StaleOracle` and `PriceDeviation` are "waiting": nothing is sent and the next poll retries. With a stale feed `checkpoint`, `rebalance` and `recenter` all revert | `range_keeper.py:41-57`; `README:816-828` |
| Allocation paused (today) | `recenter` and `rebalance` both revert `AllocationPaused` before the freshness check, so the keeper never sees `CheckpointStale` and sends no checkpoint. `CHAIN` @110,765,614: `lastCheckpoint` 1794234882 (2026-11-09 14:34:42 UTC), 66,750 s old. **Disagrees with** `SOLV/remediation/RECOVERY_SESSION.md:36` ("it sends checkpoints only") | `RobinhoodBoostedVaultV3.sol:405-407, 514`; `range_keeper.py:253-262` |
| Branch-only, not on main | Extra checkpoint when the oracle price moved >= 50 bp since the last checkpoint, at most every 300 s | `P24/remediation/tools/range_keeper.py:204-251` (commit `81036aa`); absent from `CL` |

Feed staleness measured (12 h bound, `README:805-809`, `staleness-by-session.json`): NVDA feed older than 12 h for
1.1% of regular hours, 5.0% of overnight, 86.3% of weekends, 83.4% of holidays (3.9% of all trading-session time); SPY
26.0% / 32.4% / 92.2% / 83.4% (30.7% of trading-session time).

### 1.6 Pool fee and protocol share on this venue

| Fact | Value | Source |
| --- | --- | --- |
| LP fee | 0.30% (pool key fee 3000) | `CHAIN` |
| Protocol fee | Charged on top of the LP fee, not out of it: swappers paid $8,393.87 on $2,398,934.85 of volume, LP fees $7,196.80 (30.00 bp), protocol take $1,197.07 (4.99 bp), window 2026-10-09 to 2026-11-02 | `DOC` `CL/remediation/evidence/strategy-analysis/strategy-analysis-2026-11-02-r3.json` keys `uniswapFeesAndVolume.pool` |
| Reserve cut on collected fees | 20% | `CHAIN` `reserveFeeBps` 2000 |
| Holder share of LP fees | 80% of the position's pro-rata LP fee | `CODE` `RobinhoodBoostedVaultV3.sol:698-721` |
| Rewards | None on Uniswap | `DEP/hedged-lp-2026-11-06/HEDGED_LP.md:65` |

---

## 2. Ramses vault (`IK/ramses-vaults/src`): differences from the v4 vault

Status: contract code is present on Robinhood Chain at block 110,765,893 and the pair is not registered (see the top
of this file). The deployment date is not read from the chain; the journal file is dated 2026-11-09. `README` and
`IK/ramses-vaults/PRODUCTION_DECISION.md:9` still say "Nothing Ramses is deployed"; the journal
`IK/ramses-vaults/deployments/spy-usdg-mainnet.json` and the chain show code at the addresses. Both statements are shown.

| Topic | Ramses vault | Source |
| --- | --- | --- |
| Range library | `RamsesRangeLib` is `RangeLib` with one change: recentre takes `maxDeployLossBps` and the redeploy bound is `min(policy.maxLossBps, deployLossBps[pair])`. Same policy bounds, same trigger (oracle tick), same oracle-centred range, same matched-only redeploy, same `RecenterLossTooHigh` | `CODE` diff of `IK/ramses-vaults/src/libraries/RamsesRangeLib.sol` against `CL/.../RangeLib.sol` (only lines 236-237, 257-262 differ in logic) |
| Deploy loss bound | Separate `deployLossBps[pair]`, <= 100, zero (strict) until set; applies to full range too. Script sets 10 | `CODE` `IK/ramses-vaults/src/RamsesBoostedVault.sol:52, 207-212, 437`; `IK/ramses-vaults/script/DeployRamsesInKind.s.sol:353` (`setDeployLossBps(PAIR, 10)`) |
| Add liquidity | Expected liquidity and minimum amounts are computed at the **oracle** reference (v4 adapter uses the pool spot). Pool must be within `allocationDeviation + 20 bp` of the reference; added liquidity must be >= expected x (1 - tolerance) else `SlippageExceeded` | `CODE` `IK/ramses-vaults/src/RamsesV3PairedAdapter.sol:289-302, 339-341` |
| Recentre also harvests | `_recenter` runs `_receiveRewards(..., fresh = true)` after the first checkpoint; every non-zero decrease and the NFT burn call `_harvestBeforeExit` first | `CODE` `RamsesBoostedVault.sol:532-534`; `RamsesV3PairedAdapter.sol:372, 412` |
| Guard bounds | `maxStaleness` <= 12 h, allocation deviation <= 500 bp, removal deviation <= 2000 bp | `CODE` `IK/ramses-vaults/src/RamsesOracleGuard.sol:60-62` |
| Script configuration for SPY/USDG | Policy `(true, 2400, 600, 1 hours, 4, 10, $1,000)`; guard 12 h, 300 bp allocation, 600 bp removal; adapter removal tolerance 400, allocation deviation 300; `PairConfig` pair cap $1,000, settlement swap cap $1,000, `maxCheckpointAge` 1 day, `maxDeadlineDelay` 300, `reserveFeeBps` 2000, slippage 100, over-unwind 200; shortfall ceiling 2000 bp; forfeiture on; in-kind limit 0; coverage gate 10% USDG / 5% stock of ranged exposure | `CODE` `DeployRamsesInKind.s.sol:60-63, 303, 321-340, 348, 353-371` (not live: pair unregistered on chain) |
| Settlement swap | Script `PairConfig`: `allocationPaused` false, **`swapsPaused` false**, `emergencyMode` false, `maxSettlementSwapUSDG` $1,000; no later `setPairPause` in the script. The live v4 pair has `swapsPaused` true and a $25 cap (`CHAIN`), and `README:790` calls swaps paused "the production configuration". Both shown | `CODE` `DeployRamsesInKind.s.sol:321-340` |
| Coverage gate | Router refuses `recenter` and `rebalance` (`CoverageInsufficient`) unless the reserve holds >= 10% of ranged exposure in USDG and 5% in SPY | `DOC` `IK/ramses-vaults/DEPLOY_SPY_USDG.md:141-149` |
| Staked in the gauge | **No staking.** The NFT stays with the adapter (`recipient: address(this)`); the position earns RAM automatically for in-range time; an out-of-range layer earns 0 | `CODE` `RamsesV3PairedAdapter.sol:313-327`; `IK/ramses-vaults/test/fork/GroupFeeEntitlement.t.sol:314-316, 352` |
| How swap fees reach the position | Through the position manager's `collect`, **not** through the gauge. Fork result: `collect` = (gross fee - protocol share) x our share of active liquidity, to 0 to 3 raw units; a second collect pays zero; gauge `rewardRate` for both pool tokens is 0 | `IK/ramses-vaults/test/fork/GroupFeeEntitlement.t.sol:266-311, 360-363`; log `IK/.../evidence/logs/fork-fee-entitlement-all.log:9-19, 35-37, 115-125`; `README:158` |
| Protocol fee share | 50% of the swap fee (`slot0.feeProtocol` 500,000 ppm) on SPY, NVDA, GLD, META; 5% on r33; changed from 5% at blocks 76,901,418 (SPY) and 76,907,586 (NVDA). `CHAIN` @110,767,543: SPY/USDG pool `slot0` feeProtocol 500,000 | `README:158`; log `:9, 62, 115` |
| Where the protocol share goes | To the Ramses treasury `0x74F1e8Be68Ae7bf24Bc387Ee1a900d5bC23317a3`; the gauge receives no pool tokens | `GroupFeeEntitlement.t.sol:399-403`; log `:41-52` |
| Holder share | 80% of collect after the 20% reserve cut, i.e. 40% of the gross swap fee pro rata | `CODE` `IK/ramses-vaults/src/libraries/RamsesRewardLib.sol:25-44`; `README:159` |
| Pool fee | Dynamic. SPY/USDG CL1 `0x38453c115607463Ac284820Ce959831042f3Df4E`: 100 ppm at read (`CHAIN`), 70 to 1,000 ppm range, 150 ppm for 16.2% of 30 days. NVDA/USDG CL10 `0xdac1904D823F7d6bcAaF431ceEe40c934fEd321b`: **300 ppm at read (`CHAIN` @110,768,308)**, 70 to 2,460 ppm range, 100 at the earlier pins | `README:10, 748` |
| Tick spacing | SPY 1, NVDA 10 (`CHAIN`) | |
| Ramses NVDA pool volume and fees, 2026-10-09 to 11-02 | 103,784 swaps, $30,904,813 volume, swap fees $4,163.36, LP half $2,081.68 (0.67 bp of volume), fee ppm median 100 | `DOC` strategy-analysis JSON key `ramsesFeesAndVolume.summary` |

---

## 3. Ramses gauge reward (RAM) mechanics

No Peridot position exists on Ramses on mainnet, so **there are no live RAM amounts and no mainnet transaction
hashes**. Every number below is from a mainnet fork, an anvil rehearsal or a backtest.

### 3.1 Rules

| Rule | Statement | Status and source |
| --- | --- | --- |
| 99 s withholding | A claim within 99 s of the position's last change pays 0 while `earned()` > 0; from 100 s it pays `earned()` exactly | Fork-measured 2026-11-06 on both gauges, pins L2 107,558,030 and 107,566,558: `IK/.../evidence/logs/reward-shortfall-fork.log` GRID lines; `IK/ramses-vaults/DEPLOY_SPY_USDG.md:165-178`; `IK/ramses-vaults/keeper/ramses_keeper.py:37-38`. The gauge source is not in the repo (`NOT FOUND`). **`CHAIN` @110,775,357: `gauge.voter()` = `0x30032D41868906f0376eC4D87B3D3Ac4064e7A97`; `voter.timeThresholdForGauge(gauge)` = 99 for the SPY gauge and 99 for the NVDA gauge** |
| Treasury slice | Two position changes less than 99 s apart: the RAM accrued between them is paid to the Ramses treasury `0x74F1...17a3` inside the next claim; `earned()` does not include it. Size = gap between the changes as a share of the claim's accrual time | `DEPLOY_SPY_USDG.md:180-194`; test `testTreasurySliceAfterRecenterThenRebalance` in `IK/ramses-vaults/test/fork/RewardShortfallFork.t.sol:141-182` |
| How the claim is made | Adapter `_harvest`: `pool._advancePeriod()`, read `gauge.earned(token, tokenId)` per token, then `positionManager.getReward(tokenId, tokens)`. Direct `gauge.getReward` is restricted to protocol-authorised claimers | `CODE` `RamsesV3PairedAdapter.sol:500-514` |
| Who triggers it | Vault `harvest(pairId, deadline)` (`KEEPER_ROLE`; checkpoints first), every `recenter`, every liquidity decrease and NFT burn | `CODE` `RamsesBoostedVault.sol:532-534, 742-747`; adapter `:372, 412` |
| Shortfall booking | Entitlement = max(`earned()`, what left the gauge for the position). `received > paid` or `received > earned()` reverts `RewardClaimMismatch`. If received < entitlement: with forfeiture off, revert `RewardClaimMismatch`; with forfeiture on, book `rewardShortfall[pair][token] += shortfall`, emit `RewardShortfallBooked`, credit only what arrived; above `maxRewardShortfallBps` of the entitlement, revert `RewardShortfallTooHigh` | `CODE` `RamsesV3PairedAdapter.sol:92-98, 515-544` |
| Ceiling | 20% (`MAX_REWARD_SHORTFALL_BPS = 2000`), forfeiture on, in the deployment batch | `CODE` `DeployRamsesInKind.s.sol:63, 356-363`; not live (`CHAIN`: 0 and false) |
| Exits | With forfeiture on, a failed exit harvest is caught (`ExitHarvestFailed`) and the exit proceeds, forfeiting the claim | `CODE` adapter `:489-498` |
| Reward periods | The adapter advances the pool period before reading `earned()`. Period length: `NOT FOUND` in the repo. Indirect: 3 `NotifyReward` events on the NVDA gauge in 23.98 days (45,793.488897 RAM, strategy-analysis JSON key `ramsesFeesAndVolume.gaugeNotifyRewardsInWindow`); `CHAIN` @110,768,308 `rewardRate(RAM)` x 604,800 s = 20,000.00 RAM (SPY gauge `0x739E...7337`, rate 33,068,783,068,783,068 wei/s) and 6,011.25 RAM (NVDA gauge `0x93c6...b6ce`, rate 9,939,231,213,624,338 wei/s) |

### 3.2 What the keeper does (`CODE`: `IK/ramses-vaults/keeper/ramses_keeper.py`)

| Item | Value | Line |
| --- | --- | --- |
| Wait after any position change before harvest, recentre or rebalance | `ANTI_SYBIL_WAIT` = 120 s of **block time** (env override) | 39, 213-218, 239-242, 258-259 |
| Harvest cadence | `HARVEST_INTERVAL` = 6 h (env override) | 36, 260-270 |
| Alert | Every receipt is scanned for `RewardShortfallBooked` (topic `0x2d781398...d8db`); if present the log line carries `alert: RewardShortfallBooked` and the event count | 40, 129-133 |
| `RewardShortfallTooHigh` | A wait (retry later) | 70-71 |
| `RewardClaimMismatch` | `ATTENTION` (a person must decide) | 68-69 |
| Poll interval / rebalance spacing / value gate / gas cap | 300 s / 3600 s / 10 x gas / 0.1 gwei, as the v4 keeper | 34-35, 41-42, 296 |
| Weekend rule | Exists, **off by default** (`WEEKEND_RULE=on`), and `park` is unsupported: it logs `park_unsupported` and does nothing | 175-200 |

### 3.3 Measured RAM numbers (all of them)

| Date, surface | Measurement | Source |
| --- | --- | --- |
| Fork, fee-entitlement test, 1 h of in-range time (warp 3600 s), two layers (70% core / 30% wide) | RAM earned = paid by `getReward`: GLD pool 8.866383 + 0.788663 = 9.655046; NVDA pool 28.308076 + 2.599377 = 30.907453; SPY pool 18.461883 + 1.636869 = 20.098752. Gauge `rewardRate(RAM)` at that pin: 5.410e15, 1.285e16, 4.324e16 wei/s | `IK/.../evidence/logs/fork-fee-entitlement-all.log:23-37, 76-90, 129-142`; `GroupFeeEntitlement.t.sol:227-228, 323` |
| 2026-11-06 fork, NVDA gauge grid (L2 107,566,558) | wait 30 s: earned 0.000618008, paid 0. 60 s: 0.001236015, 0. 99 s: 0.002039425, 0. 100 s: 0.002060025 paid in full. 200 s: 0.004120051. 600 s: 0.012360153. 1800 s: 0.037080458. 3600 s: 0.074160916. 1 day: 1.779861976. 7 days: 9.778631736 (all paid in full from 100 s) | `reward-shortfall-fork.log` GRID lines 50-60 |
| 2026-11-06 fork, SPY gauge grid | 30 s: 0.000144038, 0. 60 s: 0.000288076, 0. 99 s: 0.000475325, 0. 100 s: 0.000480126 paid. 200 s: 0.000960252. 600 s: 0.002880757. 1800 s: 0.008642271. 3600 s: 0.017284543. 1 day: 0.414829029. 7 days: 2.279087009 | same log lines 64-74 |
| 2026-11-06 fork, treasury slice (recentre, rebalance 5 s later, claim 200 s after) | Suite `NvdaRewardShortfallForkTest`: gauge paid 0.004375888, received 0.004273317, booked 0.000102571 (234 bp). Suite `SpyRewardShortfallForkTest`: paid 0.001125619, received 0.001101613, booked 0.000024006 (213 bp). **Two sources disagree on which gauge is which:** `DEPLOY_SPY_USDG.md:186-190` puts 0.004376 / 2.3% under "SPY gauge" and 0.001126 / 2.1% under "NVDA gauge"; the log has them the other way round (and the grid rates agree with the log) | `reward-shortfall-fork.log` SLICE lines |
| 2026-11-06 full-stack fork (L2 107,567,951) | +130 s harvest: treasury slice 0.0000071 RAM booked, alert raised | `DEPLOY_SPY_USDG.md:235-249` |
| 2026-11-06 rerun with the rebalance also spaced (L2 107,573,092) | Booked shortfall 0 RAM, no alert | `DEPLOY_SPY_USDG.md:253-259` |
| 2026-11-09 anvil ceremony rehearsal | Gap about 5 s, claim about 2 h later: 0.0000088 RAM, 0.07%. With the spacing keeper: RAM shortfall booked 0 | `DEPLOY_SPY_USDG.md:192-193, 261-272` |
| Size statement | "about 0.0005 RAM per change on SPY at today's size" is the most the 99 s window can lose | `DEPLOY_SPY_USDG.md:176-177` |
| Backtest accrual | 883.1 RAM (SPY, $10,000 scenario, 74.4 days) and 222 RAM (NVDA, 90 days), "valued at zero" | `README:463, 642, 748` |
| NVDA gauge funding | 3 `NotifyReward` events, 45,793.488897 RAM, 2026-10-09 to 2026-11-02 | strategy-analysis JSON |

Earned, claimed, forfeited or sent to treasury **on mainnet by a Peridot position: none exists.**

### 3.4 Form of RAM, and what the vault can do with it

| Fact | Status and source |
| --- | --- |
| The reward token paid to the adapter is the ERC-20 `0x5173D45A1191eE33cBB7D8c7e65f21B04eD54802`, `symbol()` "RAM", `name()` "Ramses" | `CHAIN` @110,767,543; `GroupFeeEntitlement.t.sol:48`; the tests measure a plain `balanceOf` increase |
| The adapter is not an authorised direct claimer: `gauge.isAuthorizedClaimer(adapter)` = false; gauge `rewardValidator()` = `0x29F5302C1bA4AD049c9D4D41915E7079fc8A1d3A` | `CHAIN` @110,775,357 |
| Gauge reward token lists: SPY gauge `[SPY, USDG, RAM]`, NVDA gauge `[USDG, NVDA, RAM]`; pool-token reward rates are 0 | `CHAIN` @110,767,543 / 110,768,308; log `:35-36` |
| Share of emissions paid in a locked form | `CHAIN` @110,775,357: Ramses `voter.xRatio()` = 0. On Pharaoh the same getter is documented as the share of emissions converted to xPHAR (section 4.5); that the Ramses voter uses it the same way is an inference from the shared interface, not read from Ramses source |
| xRAM, vesting or an exit penalty on the reward | `NOT FOUND` anywhere in the repos or evidence. (Hits for "x33" are hex strings. r33 is described as "reportedly a transferable ERC-20/OFT", partner-supplied and unverified: `README:26, 734`) |
| RAM price, RAM liquidity or a RAM/USDG quote used for valuation | `NOT FOUND` as a valuation; `README:26` mentions quoted two-hop routes "via r33/RAM" for r33 liquidation only. RAM is "valued at zero" throughout |
| Custody | RAM claimed lands in `vault.externalRewards[pair][token]`, "segregated custody, always zero NAV"; it is not in principal, not in the pToken exchange rate | `CODE` `RamsesBoostedVault.sol:53-54`; `RamsesRewardLib.sol:107-115` |
| Can the vault sell it | No. "RAM is not valued or swapped here"; no swap path takes a reward token | `CODE` `RamsesV3PairedAdapter.sol:423` |
| Can the vault distribute it | Only `forwardExternalReward(pair, token, receiver, amount)`, `CONFIG_ROLE` (the timelock): a transfer of the whole or part of the balance to one address. There is no per-holder accumulator, claim function or reward-per-share in `src/` (the phrase "claim-first accumulator" appears only in a comment, adapter `:532`). `src/lending` contains no reward code at all | `CODE` `RamsesBoostedVault.sol:727-740`; grep of `IK/ramses-vaults/src` for `accumulat`, `rewardIndex`, `rewardPer`, `userReward`, `claimable`, `perShare`, `distribut`, and for `reward` in `src/lending` and `src/inkind` |
| Statement of record | "RAM is not valued here: without an enforceable distribution mechanism for suppliers its value to them is zero" | strategy-analysis JSON key `ramsesFeesAndVolume.rewardNote`; `README:748` "holder entitlement unmodelled" |

---

## 4. Pharaoh (Avalanche) vaults

`CHAIN@B` = Avalanche block 99,924,000.

### 4.1 Pools: neither vault is an AVAX/USDC pool

| | USDC vault `0x855bF832f26a294d28500db59eE941dE3d654129` | WAVAX vault `0xe9a53f0077f9cf767a95Ce75Da483E906eE190E8` | Source |
| --- | --- | --- | --- |
| Pool | `0x9bFE3108Cc16D17a9Ec65545a0f50B2CA1C970c0`, **USDC/USDt** | `0x65B9016c376604Fe0aF38c1E336Ffcec0F8ecBbD`, **sAVAX/WAVAX** | `CHAIN@B` `vault.pool()`; `DOCS/avalanche-track-a-launch-gate.md:54` |
| Tick spacing | 1 | 1 | `CHAIN@B` |
| Pool fee | 50 ppm | 100 ppm | `CHAIN@B` `fee()` |
| Protocol share | `slot0.feeProtocol` 250,000; measured as 25% (LPs keep 75%) | 250,000 | `CHAIN@B`; `DOCS/avalanche-vault-entry-fairness.md:148` |
| Spot tick / pool liquidity | -11 / 1.487e12 | 2569 / 5.411e24 | `CHAIN@B` |
| Gauge | `0xf51B4EB0f6801729170d18BEe67C7F5d98985572` | `0xCB348abA8D8CE40a45353955ad63ca72095E152d` | `CHAIN@B` |
| Vault price oracle | `ChainlinkRatioOracle` (USDC/USD, USDT/USD), maxStaleness 93,600 s | `SAVAXRateOracle` (BENQI `getPooledAvaxByShares`), no staleness input | `DOC` only, launch-gate:57; oracle source not in the repo |

The Pharaoh **WAVAX/USDC** pool `0xf01449C0bA930B6e2CaCA3DEF3CCBd7a3E589534` (spacing 10) is used only by margin and
the range take-profit prototype (`AV/contracts/script/DeployAvalancheMarginMainnet.s.sol:90-91`). `CHAIN@B`: `fee()`
1000 ppm, `slot0.feeProtocol` 1,000,000. The repo reads that as 100% to the protocol, so LPs earn no swap fee there
(`DOCS/avalanche-range-take-profit.md:163`); `DOCS/avalanche-section5-fork-results.md:39` flags the units as needing a
check. **Two sources disagree on its fee:** 490 ppm at the 99.2M pin (`DOCS/avalanche-capacity-matrix.md:19`), 1000 ppm
`CHAIN@B`.

### 4.2 Range policy as deployed (`CHAIN@B`; code `AV/contracts/vault-r3/src/r3/PharaohLiquidityVaultR3.sol`)

| Parameter | USDC vault | WAVAX vault | Meaning |
| --- | --- | --- | --- |
| `tickRange` (half-width) | 100 ticks | 600 ticks | New range = floor-to-spacing(TWAP tick) +/- tickRange (`:817-822`) |
| Current position | NFT 485334, -109..91 | NFT 485416, 1854..3054 | Same as the pre-launch pin (launch-gate:56) |
| `twapPeriod` | 1800 s | 1800 s | `:503-509` |
| `maxTwapDeviationTicks` (spot vs TWAP) | 30 | 100 | Revert `Vault__PriceDeviation` (`:488-489`) |
| `maxOracleDeviationBps` (TWAP vs oracle) | 30 bp | 100 bp | Revert `Vault__OracleDeviation` (`:495-500`) |
| `slippageBps` | 100 | 300 | Per swap and per liquidity leg (`:835-837`) |
| `valuationHaircutBps` | 100 | 300 | On the paired leg in `totalAssets` (`:215-219`) |
| Rebalance loss bound = 2 x slippage + haircut | **300 bp** | **900 bp** | `:460, 843-850` |
| `depositCap` | 200 USDC | 25.0409 WAVAX | `AV/contracts/ceremony/pilot-sizing.json:15-16` |
| `totalAssets` / `totalSupply` | 39.815868 / 39.795988 | 2.976660 / 2.948443 | |
| `rebalancer()` | `0x70f341B85222cF2E590E40d596DdBD7bF0a3ddB9` | same | |
| `paused` / `owner` | false / Safe `0x80f4...fB12` | same | |

- **Two sources disagree on the loss bound.** Docs: "`ExcessiveLoss` of about 3% per call"
  (`DOCS/avalanche-pilot-r3-upgrade.md:99`, `AV/operator/src/rebalancer.ts:8`). Code formula with on-chain parameters:
  3% (USDC vault), 9% (WAVAX vault).
- **Two sources disagree on the rebalancer.** `DOCS/avalanche-pilot-addresses.json:57`: "rebalancer role still the
  Ledger". `CHAIN@B`: already the hot wallet named at `:74`.
- Parameter bounds (`CODE` `:795-803`): tickRange > spacing, a multiple of spacing, <= 50,000; twapPeriod >= 300;
  TWAP deviation 1..250 ticks; oracle deviation 1..2000 bp; slippage <= 1000 bp and >= oracleDev + twapDev + 25;
  haircut <= 2000 bp and >= slippage. Settable only at initialize or the one-time `initializeV2RiskParameters`
  (`:772-785`).

### 4.3 `rebalance()` in the contract (`CODE` `PharaohLiquidityVaultR3.sol:450-464`)

- **Trigger:** none on chain. Callable any time by `rebalancer` or `owner` while unpaused. No cooldown, no minimum
  interval, no daily limit.
- **Sequence:** collect fees; price check; record `totalAssets`; exit the whole position (decrease all, collect, burn
  NFT, `:647-661`); `_deployIdle()`.
- **The recentre includes a swap.** `_balanceIdle()` swaps half the value difference between the two idle legs, at
  oracle value, to reach 50/50 (`:535-556`); `exactInputSingle` in the same pool with minOut = oracle quote x
  (1 - slippageBps) (`:727-745`).
- **Centre is the pool's 30-minute TWAP tick**, not the oracle and not spot (`:566-567, 817-822`). Liquidity is added
  at the spot ratio; what does not fit stays idle (`:558-622`).
- **Loss limit:** `totalAssets` after versus before (haircut NAV at the TWAP tick, paired leg at oracle). Below
  (1 - (2 x slippage + haircut)) of before: **revert** `Vault__ExcessiveLoss` (`:460-462`).
- **Price gates** run first and revert the call (`:480-501`). They also gate `deposit`, `withdraw`, `redeem`,
  `deployIdle`; not `redeemInKind` (`:373-400`).
- **Out-of-range position:** `deposit` and `deployIdle` revert `Vault__PositionNeedsRebalance` (`:407, 446, 568-570`).
- **Fees:** collected into the vault on every deposit / withdraw / redeem / redeemInKind / deployIdle / rebalance
  (`:685-692`). Uncollected fees are excluded from `totalAssets` (`:213-214`). Collected fees sit idle until the next
  add.
- **Reserve or performance cut on fees: none.** No fee variable or fee transfer exists in the base contract or the
  reward extension (`CODE`, by absence: a grep of both files for `fee`, `treasury`, `performance`, `reserve` finds only
  the pool-fee collection code).
- **Entry and exit cost (fork):** immediate deposit-and-redeem returns 99.46 to 99.48% (USDC) and 98.73 to 98.78%
  (WAVAX) (`DOCS/avalanche-pilot-r3-upgrade.md:38, 92`). `previewDeposit` floor = assets x (1 - 3% / 9%) (`:284-287`).

### 4.4 Rebalancer module (`CODE` `AV/operator/src/rebalancer.ts`; config `AV/operator/config/hosts/rebalancer.json`)

| Rule | Value | Source |
| --- | --- | --- |
| Rebalance trigger | Pool **spot** tick <= lower or >= upper, or within `edgeTicks` of an edge | `rebalancer.ts:66-70` |
| `edgeTicks` | 5 | `hosts/rebalancer.json:108` |
| Minimum interval between rebalances per vault | 3600 s | `:109`; `rebalancer.ts:69` |
| Max actions per rolling 24 h (deployIdle + rebalance, both vaults) | 24 | `:112`; `rebalancer.ts:160-164` |
| Halt threshold | 50 bp drop in `totalAssets` on a rebalance or deployIdle, measured **after** the transaction is mined; halts the module until a person clears the state file (it does not revert) | `:110-111`; `rebalancer.ts:197-203` |
| `deployIdle` trigger | Idle asset >= 1 USDC / 0.1 WAVAX | `:100, 105`; `rebalancer.ts:72` |
| Poll interval | 120 s in the host config; 15 s in `pilot.example.json:56` | `hosts/rebalancer.json:56`; `AV/operator/src/main.ts:126` |
| Fee cap / gas floor / gas limit | 100 gwei / 0.04 AVAX / estimate x 1.5 | `:113-114`; `rebalancer.ts:170` |
| Stale feed or failed price gate | Simulation reverts, nothing is sent, a warn alert is raised, retry next cycle. No weekend or holiday logic. The module does not read the oracle for its trigger | `rebalancer.ts:165-175` |

`rebalance()` "is not exercised on the fork" (`DOCS/avalanche-pilot-r3-upgrade.md:101`). Whether the module is running
on its host, and whether a mainnet rebalance ever happened: `NOT FOUND` (current NFT ids and ticks equal the pre-launch
pin).

### 4.5 Gauge, PHAR and xPHAR

| Fact | Status | Source |
| --- | --- | --- |
| NFT held by the vault, **not staked**; rewards accrue to the NFT via the gauge | `CODE` + `CHAIN@B` | mint `recipient: address(this)` (`PharaohLiquidityVaultR3.sol:600`); `NFP.ownerOf` = the vaults; `gauge.earned(PHAR, id)` > 0 |
| Only reward function: `harvestRewards(bool claimPosition, uint256 minimumPharFromXPhar)`, **owner (Safe) only** | `CODE` | `AV/contracts/vault-r3/src/r3/PharaohRewardExtensionR3.sol:58-91` |
| It calls `positionManager.getReward(tokenId, [PHAR, xPHAR])`, optionally exits all xPHAR against a minimum, then sends the vault's whole PHAR balance to `owner()` | `CODE` | `:71, 81, 89` |
| The rebalancer cannot harvest; no public harvest | `CODE` | `:60` (`onlyOwner`) |
| Every `increaseLiquidity` / `decreaseLiquidity` auto-claims PHAR into the vault | `DOC` (verified source + fork probe) | `DOCS/avalanche-vault-source-references.md:97, 113` |
| Reward tokens are outside `totalAssets` (not in the share price) | `CODE` | `PharaohLiquidityVaultR3.sol:213-214` |
| No holder distribution or sale mechanism is deployed; the "swap-and-donation Safe batch" is an intention in a comment | `CODE` (comment `:51-52`) + `DOC` | "EXPERIMENTAL. Nothing here is implemented" (`DOCS/avalanche-vault-phar-decisions.md:3`); "PHAR is never sold in these tests" (launch-gate:178) |
| Form paid today: liquid **PHAR only**; `voter.xRatio()` = 0 | `CHAIN@B` | same at pin 99,216,397 (source-references:111) |
| xPHAR: not transferable unless exempt (the vaults are not); `exit` is instant and burns 50%; no vesting. Governance can turn the xPHAR ratio on without notice | `DOC` only (reading of XPhar / Voter source and probe P6; source not in the repo) | source-references:111 |
| Rewards are per weekly period; claims run across period boundaries | `DOC` only | `DOCS/avalanche-vault-phar-entitlement-proposal.md:80, 83` |

**Forfeiture rule (the analogue of the Ramses 99 s rule)**

- **The window is 111 s.** `voter.timeThresholdForGauge(gauge)` = 111 for both vault gauges (`CHAIN@B`).
- A claim within the threshold of the position's `lastModified` returns normally, pays 0 and records a forfeit; the
  forfeited amount goes to **r33** `0x26e9dbe7...6Ca9` on a later claim (`DOC`, source-references:110: probe P2, a claim
  50 s after a change paid 0 and forfeited 3,454,986,920,120 wei).
- Scope is "all accrual since the last successful claim", not 111 s of accrual: a slashed `getReward` loses every
  earlier period's unpaid amount; `getPeriodReward` forfeits only the named period (entitlement-proposal:86, 276).
- The first liquidity change after a quiet span is lossless; a second change inside the window loses
  (entitlement-proposal:18).
- A blacklisted owner, receiver, `tx.origin` or position hash, or a reverting validator, is also slashed
  (entitlement-proposal:85).
- Burning an NFT does not look at rewards, so it orphans unclaimed ones (source-references:97, probe P5). `rebalance()`
  burns the old NFT (`:657`) after the decrease's auto-claim.
- A ~2%-to-treasury slice like Ramses': `NOT FOUND` in the Pharaoh material.

**Measured PHAR**

| When / where | Amount | Source |
| --- | --- | --- |
| Block 99,216,397, USDC vault, `earned` unclaimed over 11 weekly periods | 2.320900114408409301 PHAR | entitlement-proposal:55; launch-gate:76 |
| Block 99,216,312, WAVAX vault | 0.821631199989894334 PHAR | source-references:112 |
| Block 99,924,000, PHAR sitting in the vaults, unharvested | 2.515225784504450905 (USDC vault), 0.947349684620629539 (WAVAX vault); xPHAR 0 in both | `CHAIN@B` |
| Block 99,924,000, `gauge.earned(PHAR)` since `positionLastModified` (68,946 s / 68,899 s earlier) | 0.000145711973904730 (USDC vault), 0.000027327801165927 (WAVAX vault) | `CHAIN@B` |
| Fork probe P9: backlog forfeited by a blacklisted origin | 2.320900114408409301 PHAR to r33 | source-references:110 |
| Range take-profit probe: WAVAX/USDC band of a 90 USDC-margin position, half-filled 3 days, pin 99,859,291 | "about 349 PHAR" (logged, not asserted) | `DOCS/avalanche-range-take-profit.md:111, 211` |

PHAR USD price, weekly emissions, vote share, vault fee APR: `NOT FOUND` ("not measured", launch-gate:88, 170). The only
APY figure is a 20% assumption in a design tool (`DOCS/avalanche-pilot-frontend-handoff.md:291`).

---

## 5. Range take-profit / one-sided band

**Status: prototype on both chains; nothing deployed.** Avalanche: "Nothing in this document is deployed, signed, sent
or merged" (`DOCS/avalanche-range-take-profit.md:3`). Robinhood PR #7: "fork-tested, NOT for deployment"
(`RG/docs/margin-orders/RANGE_TAKE_PROFIT.md:1`).

| Item | Avalanche (`AV/contracts/range-tp/src/RangeTakeProfit.sol`) | Robinhood PR #7 (`RG/contracts/contracts/margin/orders/range/RangeTakeProfit.sol`) |
| --- | --- | --- |
| Pool / asset | Pharaoh WAVAX/USDC, spacing 10, WAVAX must be token0 (`:254`); on a 2x long | Ramses NVDA/USDG (spacing 10, NVDA token1) and SPY/USDG (spacing 1, SPY token0) (`RANGE_TAKE_PROFIT.md:45-46`) |
| Placement | Single-sided, entirely above the current tick. Near edge = `_ceil(tick + distance)`, snapped away from the market; far edge = near + width (`:382-386, 558-561`) | Same; a token1 stock uses `_floor(tick - distance)` (`:450-455`) |
| Who chooses the range | Admin presets only. Distance and width > 0 and multiples of spacing; 1..3 levels; weights sum to 10,000 (`:294-305`). No other minimum or maximum; no minimum size (`RangeTooSmall` only if liquidity is 0, `:405`) | Admin presets only (`:265-271`); "no arbitrary user ranges" (doc `:53`) |
| Presets | 1 / 2 / 3: distance 100 / 300 / 490 ticks, width 200. Preset 4: three rungs at 100 / 300 / 490, 200 wide, weights 70 / 20 / 10% of collateral (`:263-266`) | 1 / 2 / 3: 100 / 300 / 490 ticks, 200 wide (`RG/docs/margin-orders/RANGE_DEPLOY.md:17`); one band per order |
| Pre-conditions | Owner only; ACTIVE long; one order per position; no open TAKE_PROFIT; pool within the pair's `oracleDeviationBps` (100 bp) of the oracle; not liquidatable after placement (`:336-349, 376, 666-674`) | Same pattern; a stale feed or issuer pause refuses (doc `:39-43`) |
| Swap-fee recipient | Fees = collected - principal, split user / protocol / reserve by bps fixed at creation (`:586-587, 608-614`). **Default 100 / 0 / 0** (`:257`). On the live pool LP fees are zero (`feeProtocol` 1,000,000 `CHAIN@B`; a crossed rung "returned exactly the principal", doc `:163`) | Code default 100 / 0 / 0 (`:256`); decided **70 / 20 / 10** for the pilot (`RANGE_TAKE_PROFIT.md:165`; `RANGE_DEPLOY.md:18`). Measured 157 / 78 USDG raw to protocol / reserve (doc `:65`) |
| Close condition | Permissionless `finalize(positionId)`, pays the caller nothing (`:419-423`). Requires spot tick >= the rung's upper tick (`:432`), else `BandNotFullyConverted`. Then the oracle-deviation check (`:438`) and a slippage floor = max(liquidity-maths USDC, committed WAVAX x oracle price at creation) x (1 - 100 bp) (`:599-604`) | `tick >= tickUpper` (token0) or `tick < tickLower` (token1) (`:379`); oracle-deviation check; removal minimums 0, no floor (doc `:160-161`) |
| Wait or confirmation | None in the contract: one spot-tick read | Same |
| Keeper | `AV/range-tp-keeper`: poll 20 s, fee cap 60 gwei, 3 sends per step, 300 s cooldown after a non-wait refusal, gas margin 130% (`config/keeper.example.json:8-12`). Reads `finalizable(id)`, simulates, sends only if it passes. `BandNotFullyConverted`, `OracleUnavailable`, `PoolOracleDeviation` are waits (`src/keeper.ts:59, 150, 165`). Not provisioned (doc `:195`) | `RG/contracts/tools/orders/orders_keeper.py`: interval default 30 s (`:241`), max gas price 0.1 gwei (`:34`). Not provisioned (doc `:140`) |
| Price returns before the keeper acts | Not final: the stock converts back, `finalize` refuses, the order stays ACTIVE and converts again if the market returns (doc `:98-99, 219`): "no loss, but the take-profit did not happen" | Same (doc `:29-30, 63`). `F247/remediation/NVDA_247_FEED.md:147-153`: a band that fills over a weekend cannot be finalised until the 24/5 feed updates, "and a reversal before then undoes the fill" |
| After finalize | Proceeds go to the margin account; `executor.rangeReturn` repays debt first; the position closes when no rung remains; equity to the owner (`:447-454`) | Same, single band (`:381-382`) |
| Cancel and risk paths | `unwind`: owner, liquidator, order adapter; no price read, no floor (`:461-477`) | Same (doc `:51-52`) |
| Costs to the user | No fee on principal. Margin open and close fees 0. Fee split on swap fees only. Slippage floor 100 bp. The user pays gas for create and cancel; the keeper pays for finalize | Fee split on swap fees only |
| Gauge staking | **Not staked**; the NFT is held by the custodian (`:399`). Rewards claimed via `positionManager.getReward` before each removal, non-blocking (`:577`). PHAR goes whole to the order owner, outside the fee split; xPHAR or a failed transfer is recorded in `rewardClaimable` (`:637-650`) | Not staked. Reward tokens other than stock / USDG go whole to the owner (`:529-552`) |
| Anti-sybil | A removal within **111 s** of the last liquidity change forfeits that window's PHAR (doc `:221`) | "a removal within **99 s** of placement forfeits that window's RAM" (doc `:162-163`) |

- **Two sources disagree on RAM in PR #7.** `RANGE_TAKE_PROFIT.md:50`: "Gauge rewards follow the same split". Code
  comment (`:529-530`) and doc `:165`: RAM goes whole to the owner (operator decision 2026-11-07).
- **Measured gas, Avalanche** (fork pin 99,859,291; doc `:113, 118`): `createRangeOrder` 972k (1 rung) / 1.91M (3);
  `finalize` 1.02M (1 rung, closes) / 0.71M (rung 1 of a ladder) / 0.81M (last rung) / 1.64M (3 at once) / 1,854,827
  (anvil, cold); `unwind` 1.09M (3 rungs); `liquidateFunded` with a 3-rung band 1.67M; stop-loss close with a 3-rung
  band 2.53M. "At 25 gwei, 1.85M gas is 0.046 AVAX."
- **Measured gas, Robinhood** (`RANGE_TAKE_PROFIT.md:136, 138`): finalize 2,089,736; stop-loss with a band 2,905,729.
  Gas price and ETH price are not stated there.
- **Measured outcomes, Avalanche** (doc `:88, 95-96`): 90 USDC margin at 2x = 20.1005 WAVAX, 88.218 USDC debt. Owner
  equity after finalize: 93.24 / 96.91 / 100.46 USDC (presets 1 / 2 / 3); ladder 94.70 USDC.
- **Not tested** (doc `:209-213`): real feed lag (the oracle is mocked after the first read); how often
  `PoolOracleDeviation` blocks a real pump; whether a 20 s poll catches a spike.

---

## 6. Margin market used as the hedge

### 6A. Robinhood (short NVDA against USDG)

**Markets** (`CHAIN` @110,766,857 unless noted; controller `0x6148183676e304dbe63a85c350c208da3ceac39c`)

| | pNVDA `0xa155ccCB986774AE818b3F10F07d01D1b7A47b26` ("bpNVDA") | pUSDG `0x55aEd0569c8f0D166D71facE57B57C2f2624a563` ("bpUSDG") |
| --- | --- | --- |
| `getCash()` | 0.032312565590810770 NVDA (about $5.86 at $181.24). Includes the vault's withdrawable 0.019202 NVDA; the token balance held by the market itself is 0.013110498743128914 NVDA | 26.165909 USDG (equal to its own USDG balance) |
| `totalBorrows` | 0.000014615404196690 NVDA | 0.968775 USDG |
| `totalReserves` | 0.000000000547680177 NVDA | 0.000579 USDG |
| Utilisation (borrows / (cash + borrows - reserves)) | 0.045% | 3.57% |
| `borrowRatePerBlock` | 7,631,854,492 (2.006% a year at 2,628,000 blocks) | 9,308,562,734 (2.446%) |
| `supplyRatePerBlock` | 3,105,386 (0.0008%) | 299,111,121 (0.0786%) |
| `reserveFactorMantissa` | 0.10 | 0.10 |
| Borrow cap | 4.35 NVDA | 1,000 USDG |
| Collateral factor | 0.80 | 0.80 |
| Borrow / mint paused | false / false | false / false |
| `accrualBlockNumber` | 26,148,189 | 26,349,062 |
| `exchangeRateStored` | 1.90814814489e26 | 1.90313909127875e14 |
| `totalSupply` (raw pToken units) | 172,300,825 | 142,575,522,327 |

**Rate model** `0x0987154fB5676a8Ea545AAf41F8ef2492F785d22`, shared by both markets (`CHAIN` @110,767,543)

| Parameter | Per block | Per year at the model's `blocksPerYear` |
| --- | --- | --- |
| `blocksPerYear()` | 2,628,000 | (12 s blocks) |
| `baseRatePerBlock()` | 7,610,350,076 | 2.00% |
| `multiplierPerBlock()` | 47,564,687,975 | 12.5% per unit of utilisation (10% reached at the kink) |
| `jumpMultiplierPerBlock()` | 380,517,503,805 | 100% per unit of utilisation above the kink |
| `kink()` | 0.8e18 | 80% |

- Borrow rate = base + util x multiplier for util <= 0.8; above, + (util - 0.8) x jump: 2% at 0, 12% at 80%, 32% at 100%
  (arithmetic from the read values). Supply rate = util x borrow x (1 - 0.10).
- **The block the model counts is the L1-derived block number, not the L2 block.** `accrualBlockNumber` is about
  26.1M to 26.3M while the L2 height is 110.77M; the L2 header's `l1BlockNumber` at the read was `0x192949d`
  (26,383,517). `IK/ramses-vaults/src/lending/RamsesInterestRateModel.sol:6-8` says the same of the Ramses model
  ("per native (L1-derived) block ... assumed 12 seconds").
- pNVDA last accrued at 26,148,189, about 235,000 L1 blocks before the read: stored rates and balances exclude interest
  since then.
- Cash definition (`README:1116`): the model's cash counts the local balance plus the vault's withdrawable, so
  utilisation moves when withdrawable changes.
- **pSPY: no such market on this controller.** `getAllMarkets()` = [pNVDA, pUSDG] (`CHAIN`). A SPY market exists only
  in the new Ramses stack: `rpSPY` `0x14CC6fE0AE856B736fAb6678619cEd3d4738EA8B` under controller
  `0xA322CB665B69732F3Ab6A0136bc636EAF81927a3` (`IK/ramses-vaults/deployments/spy-usdg-mainnet.json`), `totalSupply` 0
  and cash 0 (`CHAIN` @110,767,543); its script pauses borrowing and sets collateral factor 0
  (`IK/ramses-vaults/DEPLOY_SPY_USDG.md:125`; `DeployRamsesInKind.s.sol:397`). Its model constants: base 2%, slope 10%,
  jump 100%, kink 80%, 2,628,000 blocks (`RamsesInterestRateModel.sol:8-12`). So SPY cannot be borrowed anywhere today.

**Margin pair risk** (`CHAIN` @110,766,857, `getPairRisk(margin, position, debt)` on config
`0x09F94fe0B79E000c8a26617c63E3427fdECB528b`; field order from
`P24/contracts/peridot-contracts-2-5/contracts/contracts/margin/IsolatedMarginTypes.sol:20-33`)

| Field | Short pair (pUSDG, pUSDG, pNVDA) | Long pair (pUSDG, pNVDA, pUSDG) |
| --- | --- | --- |
| `enabled` | true | true |
| `maxLeverageX100` | 500 (5x, position value / equity) | 500 |
| `initialMarginBps` | 2000 | 2000 |
| `maintenanceMarginBps` | 1000 | 1000 |
| `liquidationTargetBps` | 12,500 | 12,500 |
| `fullLiquidationHealthBps` / `maxLiquidationBps` | 5000 / 5000 | 5000 / 5000 |
| `liquidationBonusBps` | 500 | 500 |
| `maxSlippageBps` | 100 | 100 |
| `oracleDeviationBps` | 100 | 100 |
| `maxPositionValueUsd` | **$2** (2e18) | $2 |
| `maxDebtValueUsd` | **$1** (1e18) | $1 |

| Other margin fact | Value | Source |
| --- | --- | --- |
| "Per-position caps are $2 gross / $1 debt" | **Confirmed** | `CHAIN`; `DEP/hedged-lp-2026-11-06/CAPS_FOR_1000.md:18-19`; `HEDGED_LP.md:54` |
| "NVDA lendable is about 0.033 NVDA" | **Confirmed as `getCash()`**: 0.0323 NVDA now (0.033 on 6 Nov). Of that, 0.0131 NVDA is in the market and 0.0192 NVDA is idle in the vault | `CHAIN`; `CAPS_FOR_1000.md:44` |
| Open / close fee | 0 / 0 bp (`openFeeBps()`, `closeFeeBps()`); code maximum 100 bp each | `CHAIN` @110,767,543; `.../margin/IsolatedMarginConfigUpgradeable.sol:13-14, 27-28` |
| Flash vault `0x79d33c9BbC1D0711e88C5602f86135Ab4C088b06` | 0.025000858740850569 NVDA and 105.222116 USDG; `feeBps()` = 5. The executor flash-borrows the debt asset on both open and close | `CHAIN` @110,767,543 / 110,768,308; `CAPS_FOR_1000.md:45`; `.../margin/SimpleFlashLoanVault.sol:16, 74-76` |
| Config action delay | >= 3,600 s between `queuePairRisk` and `setPairRisk` | `CAPS_FOR_1000.md:18, 27-30`; `IsolatedMarginConfigUpgradeable.sol:22` |
| Execution venue and gate | Opens, closes and liquidations swap through the Uniswap v4 NVDA/USDG pool; the swap module refuses when pool and oracle differ by more than 1% | `F247/remediation/NVDA_247_FEED.md:134-136, 140-143` |
| Price availability | The margin oracle returns 0 whenever the 24/5 NVDA answer is older than 12 h (about a quarter of the time, every weekend): opens, closes, liquidations and TP/SL all refuse then | `NVDA_247_FEED.md:129-131`; `README:1119` |
| Liquidation boundary measured (fork, $0.20 margin at 4.9x) | Short: first liquidatable at +12.9% from entry; long: -11.8% | `DEP/robinhood-mainnet.margin-5x-measurements.json` |
| Live debt on 3 Nov | About $1.94: two isolated positions (gross $1.27 / $1.68, debt $0.97 / $0.96) | `README:1097` |
| What a $1,000 hedge would need | `maxPositionValueUsd` $1,500, `maxDebtValueUsd` $1,000, pNVDA borrow cap 7 NVDA, >= 5.4 NVDA lendable and >= 5.4 NVDA in the flash vault; "prepared, NOT executed" | `CAPS_FOR_1000.md:1, 18-20, 44-45` |
| One-window hedged-LP run (2026-10-19 to 11-06, 17.75 days) | Inputs: NVDA borrow 2.006% APR, USDG supply 0.182% APR, swap cost 0.30% of each adjustment's notional, $0.087 gas per adjustment, margin = half the notional, rebalance when drift > 5% of LP value, 10 adjustments. Result: hedged -4.16%, unhedged -7.19%, hold -4.89%; hedge leg +$0.706 = price +$1.680, swap -$0.095, gas -$0.870, borrow interest -$0.009, supply interest +$0.001 | `DEP/hedged-lp-2026-11-06/hedged-lp.json`; `HEDGED_LP.md:9-37` |
| Costs that run does not include | (a) The flash fee: 5 bp of the flash-borrowed debt asset on every open and close; the executor repays `amount + fee` and on an open the fee is part of the position's debt. (b) The v4 protocol fee: swappers paid 34.99 bp of volume on this pool (30.00 LP + 4.99 protocol, section 1.6); the run uses 0.30% | `CODE` `.../margin/IsolatedMarginExecutorUpgradeable.sol:541-563, 572-598`; `CHAIN` `feeBps()` 5; strategy-analysis JSON |

### 6B. Avalanche (PW30 lending, 2x margin)

**Lending** (`CHAIN@B` 99,924,000; live since block 99,846,691)

| Item | pWAVAX `0x12f1F4Fc057a76f473B781ce2Fa88E55ef8581B1` | pUSDC `0x0f7849CD9E54ffb027aacaEec82007cca9470098` | Source |
| --- | --- | --- | --- |
| **Borrow paused** | **true** | false | `CHAIN@B` `borrowGuardianPaused` on controller `0xbE938cD7C25E0E4d0604Fa3F9f0a8b5b67b3D51A`; `AV/contracts/ceremony/pilot-pw30.env:10`. A paused borrow reverts "borrow is paused" (`AV/contracts/contracts/Peridottroller.sol:384`) |
| Borrow cap | 5.008172856 WAVAX | 200 USDC | `CHAIN@B`; `pilot-pw30.env:10-12` |
| Borrow cash floor | 0 | 50 USDC | `CHAIN@B`; `pilot-pw30.env:24-26` |
| Collateral factor | 0.30 | 0 | `CHAIN@B` |
| Cash / borrows / reserves | 1.252043214 WAVAX / 0 / 0 | 270.000000 USDC / 0 / 0 | `CHAIN@B` |
| `borrowRatePerBlock` / `supplyRatePerBlock` | 597,014,925 / 0 | 597,014,925 / 0 | `CHAIN@B` (utilisation 0) |
| Reserve factor | 0.10 | 0.10 | `CHAIN@B` |
| Close factor / liquidation incentive | 0.50 / 1.08, controller-wide | | `CHAIN@B`; `pilot-pw30.env:14-15` |

**Rate model** `ConfigurableJumpRateModelV2` `0xe5a82bF40b493cf37C06851A934370F3f0896263`, shared

| Parameter | Per year (`pilot-pw30.env:17-21`) | Per block `CHAIN@B` |
| --- | --- | --- |
| `blocksPerYear` | 33,500,000 (0.936 s blocks) | 33,500,000 |
| base | 2% | 597,014,925 |
| multiplier (to the kink) | 10% | 3,731,343,283 |
| jump multiplier | 100% | 29,850,746,268 |
| kink | 80% | 0.8e18 |

- Formulas (`AV/contracts/contracts/ConfigurableJumpRateModelV2.sol:54-107`): util = borrows / (cash + borrows -
  reserves); borrow = base + util x multPerBlock up to the kink, above it + (util - kink) x jumpPerBlock; supply = util x
  borrow x (1 - reserveFactor); `multiplierPerBlock = multiplierPerYear / (blocksPerYear x kink)`. Simple APR: 2% at 0,
  12% at 80%, 32% at 100%.
- Operating rule: team lending borrows <= 0.4 x CAP (80 USDC) so about 0.49 x CAP of margin debt fits
  (`DOCS/avalanche-margin-ceremony.md:95`; `DOCS/avalanche-pilot-sizing-table.md:45`). Margin debt shares the pUSDC cap
  and cash floor (`DOCS/avalanche-margin-2x-scope-and-rehearsal-plan.md:16`).

**Margin 2x: deployed and wired, not enabled at block 99,924,000**

| Item | Value | Source |
| --- | --- | --- |
| Product | Long WAVAX, USDC margin, USDC debt, pool `0xf01449C0...9534`; **long only** | `CODE`: the script requires WAVAX borrowing paused at deploy (`AV/contracts/script/DeployAvalancheMarginMainnet.s.sol:151`) and at enable (`:489`) |
| Short pair (pUSDC, pUSDC, pWAVAX) | `enabled = false`, all zeros | `CHAIN@B`. "Shorts ... Not proposed" (scope doc `:38`) |
| Long pair risk on chain | `enabled = false`, all zeros (stage 3 not executed) | `CHAIN@B` |
| Long pair risk in the script | 2.00x; initial margin 50%; maintenance 25%; liquidation target 125%; full-liquidation health 50%; max liquidation 50%; bonus 5%; max slippage 100 bp; oracle deviation 100 bp; position cap = `MARGIN_CAP_USD`; debt cap = half of it | `CODE` `:100-111` |
| Caps at pilot size | Position $200, debt $100 | `DOC` `AV/contracts/ceremony/margin-sizing.env:1-2`; sizing-table:41 |
| `opensPaused` | true | `CHAIN@B` |
| Open / close fee | 0 / 0 | `CHAIN@B`; script `:236` |
| `actionDelay` | 86,400 s | `CHAIN@B` |
| Flash vault `0x73B06542...475b` | 100 USDC, fee 0 bp, USDC only | `CHAIN@B`; `margin-sizing.env:3`; script `:212` |
| Insurance fund | 5e10 pUSDC (10 USDC) | `CHAIN@B` |
| `executor.nextPositionId` | 1 (no position opened) | `CHAIN@B` |
| Liquidation trigger at 2x / 25% | About -33% | `DOC` scope `:17` |
| Round-trip cost at the pool | 19 bp of margin at 500 / 1,500 / 2,500 USDC margin | Fork pin 99,833,032: `DOCS/avalanche-margin-evidence/rehearsal-fork-suite-run2-pin-99833032.txt:853-864` |

- **Two sources disagree on margin status.** `DOCS/avalanche-pilot-addresses.json:45-55` has `margin: null`;
  `AV/contracts/ceremony/margin-addresses.env` (untracked) lists 17 addresses and they answer on chain.
- Stage 1 ran out of gas on 10 Nov after 8 of 22 transactions (`DOCS/avalanche-margin-ceremony.md:70`); the stage-2
  wiring is visible on chain at the read.
- **Hedge facts:** WAVAX cannot be borrowed (paused on chain; total pWAVAX cash 1.252 WAVAX). No short pair exists. The
  two vault pools are USDC/USDt and sAVAX/WAVAX.

### 6C. Measured gas

**Robinhood** (gas price read `CHAIN` @110,768,308: 0.022284 gwei; ETH/USD feed `0x78F3...d3A9`: $2,097.79)

| Operation | Gas | Gas price | USD as stated | Source |
| --- | --- | --- | --- | --- |
| Margin open, long (the open transaction itself) | 2,988,817 | 0.022134 gwei | not stated | `CL/remediation/evidence/margin-acceptance-long-open-verified.json` (receipt `0xff13ffc9...`) |
| Margin open, short | 3,095,677 | 0.020026 gwei | not stated | `margin-acceptance-short-open-verified.json` (`0xfcfcf14c...`) |
| Margin open, preparatory transactions (three per open) | 51,734 + 309,270 (long) or 272,215 (short) + 31,798 | 0.0200 to 0.0225 gwei | | same files |
| Margin close, long | 2,056,575 | 0.022096 gwei | | `margin-acceptance-long-close-verified.json` (`0xbec99fcb...`) |
| Margin close, short | 2,320,389 | 0.020012 gwei | | `margin-acceptance-short-close-verified.json` (`0x4e8eafbe...`) |
| Margin withdraw | 148,647 | 0.0201 to 0.0222 gwei | | `margin-acceptance-*-withdraw-verified.json` |
| Margin adjust (hedged-LP assumption) | 2.23M ("the measured margin close") | "the live keepers' gas price" | $0.087 at ETH $2,260 | `HEDGED_LP.md:21-22`. The receipts above give 2.06M and 2.32M; 2.23M is not either of them |
| Margin adjust as its own operation | `NOT FOUND` | | | |
| Liquidation (5x fork) | 1,928,296 | | | `DEP/robinhood-mainnet.margin-5x.md:115` |
| Vault checkpoint | median 516,851, mean 483,191, max 523,422 (79 txs) | effective, from receipts | $0.0233 each | strategy-analysis JSON `keeperCosts.byAction`; `README:1371` |
| Vault rebalance | median 721,202, mean 761,547, max 926,037 (63 txs) | | $0.0370 to $0.0376 each | same; `range_keeper.py:203` (79 rebalances: median 721k, mean about 770k) |
| Vault recentre | median 1,318,906, mean 1,324,534, max 1,343,131 (3 txs) | | $0.0615 to $0.0663 each | same; `README:200, 1371` |
| Recovery `syncVault` (full unwind + settlement swap) | 1,029,233 | | | `SOLV/remediation/RECOVERY_SESSION.md:168` |
| Ramses recentre / harvest (anvil rehearsal) | 1,565,929 / 1,298,911 | | rebalance "about $1.68 of gas at the rehearsal's gas cap" | `IK/ramses-vaults/DEPLOY_SPY_USDG.md:250, 269-271` |
| Range take-profit finalize / stop-loss with band (PR #7) | 2,089,736 / 2,905,729 | not stated | | `RG/docs/margin-orders/RANGE_TAKE_PROFIT.md:136, 138` |

ETH price used in the evidence: $2,254 (`README:1111, 1237, 1371`), $2,254 to $2,266 (`README:1439`), $2,260
(`HEDGED_LP.md:22`), $2,283.34 at the strategy-analysis end block (JSON `keeperCosts.ethUsdAtEnd`).

**Avalanche**

| Context | Gas price | AVAX/USD | Source |
| --- | --- | --- | --- |
| Cost estimate, 2026-11-03 | 2.5 gwei | 8.43; 1M gas = 0.0025 AVAX, about $0.021 | `DOCS/avalanche-operating-cost-estimate.md:8-10` |
| Pilot sizing | 2.5 gwei | 7.98694477 | `DOCS/avalanche-pilot-sizing-table.md:1`; `pilot-sizing.json:3` |
| PW30 live deploy, 9 Nov | 3.5875 gwei effective; 30 txs, 20,158,862 gas, 0.072319919 AVAX | | `DOCS/avalanche-pilot-deploy-evidence/pw30-live-broadcast-summary.md:5-10` |
| Margin stage 1 live, 10 Nov | forge estimated 17.8 gwei (base fee 7.5); budget reset to 25 gwei | | `DOCS/avalanche-margin-ceremony.md:70` |
| Range take-profit doc | 25 gwei | | `DOCS/avalanche-range-take-profit.md:113` |
| `CHAIN@B` | not read | 8.39778360 | Multicall readback |

| Operation (fork unless marked) | Gas | Source |
| --- | --- | --- |
| Margin open (2x long) | 2,579,951 / 2,480,781 / 2,471,876 | run2 file `:855, 859, 863` |
| Margin close | 1,372,644 / 1,326,712 / 1,326,744 | run2 file `:856, 860, 864` |
| Margin open at an older pin | long 2.62M, short 2.75M; flash long liquidation 1.57M | `DOCS/avalanche-section5-fork-results.md:45` |
| Margin adjust | `NOT FOUND` | |
| Funded liquidation | 570,325; range 0.55 to 0.70M | run2 `:513`; `DOCS/avalanche-funded-fallback.md:57` |
| TP/SL `executeOrder` full close | 1,474,800 (forge); 1,656,376 / 1,673,882 / 1,676,222 (anvil) | run2 `:601`; `DOCS/avalanche-order-keeper.md:64-66, 74` |
| Vault deposit into an existing position | 951,767 (USDC), 829,973 (WAVAX) | `DOCS/avalanche-pilot-deploy-evidence/r3-upgrade-rehearsal-cap200.txt:10, 17` |
| Vault first deposit (mints the NFT) | 2.31M / 2.28M | `DOCS/avalanche-pilot-vault-opening.md:3, 54` |
| Vault `deployIdle` | 0.72M / 0.67M | vault-opening:3, 54 |
| Vault `rebalance()` (recentre) | **NOT MEASURED** | operating-cost-estimate:21, 37; r3-upgrade:101 |
| Vault `harvestRewards` | NOT MEASURED. Probe unit costs: `getReward` over 12 periods 1,053,520; `getPeriodReward` 178,344; NFP `mint` 1,014,864; `increaseLiquidity` 279,436 | entitlement-proposal:263 |
| Vault checkpoint | No such function in the Pharaoh vault | code |
| Lending mint / borrow / liquidateBorrow / repay / redeem | 112k / 332k / 244k / 42k / 43k (warm; cold up to about 1.6x) | operating-cost-estimate:11 |
| Range take-profit | section 5 | |

---

## 7. Oracle feeds (Robinhood Chain)

| Feed | Address | Description (`CHAIN`) | Triggers as documented | Latest at read (`CHAIN`) |
| --- | --- | --- | --- | --- |
| NVDA 24/5 (the one every live contract uses) | `0x379EC4f7C378F34a1B47E4F3cbeBCbAC3E8E9F15` | "RHNVDA / USD", 8 decimals | 0.5% deviation, no off-hours heartbeat (`F247/remediation/NVDA_247_FEED.md:41`); "24/5" (`README:756`) | 181.24038132, updated 1794301298 (334 s before block 110,765,614) |
| NVDA 24/7 | `0x321A36e441DfBadA01Aa6f9884F14a344E133D1F` | "Robinhood NVDA (24/7)", 8 decimals | 0.5% deviation, 86,400 s heartbeat "as stated" (`NVDA_247_FEED.md:41`) | 181.00019528, updated 1794301289 |
| SPY 24/5 | `0x319724394D3A0e3669269846abE664Cd621f9f6A` | "RHSPY / USD" | "24/5", `calculatedPrice` (`README:756`) | 754.16843261, updated 1794245641 (2026-11-09 17:34 UTC, 15.6 h before block 110,767,543, i.e. older than 12 h on a Tuesday overnight session) |
| ETH/USD (keeper gas gate) | `0x78F3556b67E17Df817D51Ef5a990cDaF09E8d3A9` | | | 2,097.7865 |

Heartbeat and deviation for the two 24/5 feeds from Chainlink's own directory: `DOC` only as quoted above; not read on
chain.

**The 24/7 NVDA feed**

| Fact | Source |
| --- | --- |
| Live since Fri 2026-11-06 20:21:23 UTC (round 1) | `DEP/nvda-247-feed-2026-11-07/feed247-rounds.json`; `NVDA_247_FEED.md:40` |
| How it derives weekend prices: `NOT FOUND` (no statement of its sources). Observed behaviour only: it posts on a 0.5% move or the 24 h heartbeat; on Sunday 8 Nov from 20:41 UTC it "walked down in 0.5% steps, from 186.34 to 181.70 by 00:09" while the 24/5 feed stayed at Friday's 188.24; 23 answers from launch to Mon 07:39 UTC | `NVDA_247_FEED.md:12-15` |
| Used by any vault or market today: **no.** Guard `feedConfig.stockFeed` is still the 24/5 feed (`CHAIN` @110,765,614). The switch is prepared, not executed, planned for the 15 to 17 Nov ceremony with `maxStaleness` 90,000 s | `NVDA_247_FEED.md:3-8, 74-88, 111` |
| Agreement with the 24/5 feed while both update | within 0.001% to 0.092% (first four answers); 0.03% to 0.09% after the overnight open, with two 24/5 lags of about 0.5% | `DEP/nvda-247-feed-2026-11-07/feed247.json`; `NVDA_247_FEED.md:25, 51-56` |
| At the Sunday overnight open (Mon 01:00 UTC) | 24/7 181.70 (3,025 s old) against 24/5 188.24 (181,766 s old): gap -3.48% | `NVDA_247_FEED.md:22-24` |

**Pool versus feed gaps (Uniswap v4 NVDA/USDG pool price from each `Swap` event)**

| Window | Swaps | Pool vs feed in force (absolute) | Source |
| --- | --- | --- | --- |
| Weekend Sat 7 Nov 01:00 to Mon 9 Nov 01:00 UTC, vs the 24/7 feed | 835 | **median 0.802%, p90 2.155%, max 3.236%** (7 answers of the 24/7 feed and 0 of the 24/5 feed inside the weekend) | `F247/remediation/evidence/feed247/feed247-2026-11-09-0738.json` key `weekends[1]` (read 2026-11-09 07:39:08 UTC); quoted as 0.80 / 2.16 / 3.24 in `NVDA_247_FEED.md:16-18, 136, 143` |
| Same weekend, vs the frozen 24/5 feed | 835 | median 2.964%, p90 3.889%, max 5.078% | same JSON; `NVDA_247_FEED.md:18` |
| Sunday 20:41 UTC | | pool 182.60 vs 24/7 186.34: 2.05% (the pool moved first) | `NVDA_247_FEED.md:19` |
| Weekend 31 Oct to 2 Nov, vs the 24/5 feed (24/7 did not exist) | 370 in the 7 Nov run, 378 in the 9 Nov run | median 0.444% / 0.443%, p90 0.945% / 0.944%, max 1.026% (the two runs) | `DEP/nvda-247-feed-2026-11-07/feed247.json`; `F247/.../feed247-2026-11-09-0738.json`; `NVDA_247_FEED.md:64` |
| Sat 7 Nov 01:00 to 12:01 UTC (partial) | 31 | vs 24/7: median 0.06%, p90 0.17%, max 0.18% | same |
| SPY pool-over-feed basis at feed updates (about 90 observations) | | +0.056% (standard error 0.022%) | `README:773` |

**Staleness limits by consumer**

| Consumer | Limit | Behaviour when exceeded | Source |
| --- | --- | --- | --- |
| Vault guard (v4 NVDA vault; also the margin guarded source) | 43,200 s | Reverts `StaleOracle`: checkpoint, rebalance, recentre, LP-needing withdrawals, guardian `emergencyDecrease` all blocked; `collectFees`, repayment and redemption from local cash still work | `CHAIN` `feedConfig`; `StockOracleGuard.sol:250-252`; `README:816-828` |
| Token flags | `oraclePaused()` true reverts `StockOraclePaused` | | `StockOracleGuard.sol:168` |
| Margin price (`GuardedMarginPriceSource` `0x25E0...`) | 12 h via the guard, and the guard price must equal the lending source's price exactly | Returns 0: opens, closes, liquidations, TP/SL refuse | `NVDA_247_FEED.md:76-79, 129-131`; `README:1119` |
| Lending oracle (`StockSimplePriceOracle` `0x266F...`, controller path) | 259,200 s (72 h) for NVDA | Between 12 h and 72 h it prices at the stale answer with no revert; beyond 72 h `getUnderlyingPrice` returns 0 (PRICE_ERROR) | `README:1083, 1119` |
| Ramses guard (not live) | <= 12 h by code; script sets 12 h | Reverts `StaleOracle` for every action | `RamsesOracleGuard.sol:60, 123`; `DeployRamsesInKind.s.sol:303` |
| NVDA feed older than 12 h, last 30 days to 3 Nov | 26.45% of the time (10 gaps, longest 56.9 h); 0% beyond 72 h | | `README:1119` |

---

## 8. Stock-token quirks that affect P&L

| Fact | Status and source |
| --- | --- |
| SPY token `uiMultiplier()` = 1.001717991187472003 | `CHAIN` @110,767,543; `README:773` (1.001718, "0.17% accumulated") |
| NVDA token `uiMultiplier()` = 1.000775159164630595 | `CHAIN` @110,767,543; `README:1117` (1.000775) |
| The multiplier is metadata: a step of +0.33% (an assumed quarterly size) leaves balances, total supply, the pool's token balance, the pool price and liquidity, and exact-amount transfers unchanged | Fork of the real SPY token, pool and feed: `IK/ramses-vaults/test/fork/StockMultiplierFork.t.sol`; `README:773` |
| No contract in the repos reads `uiMultiplier`; they price the raw token with the Chainlink feed | `README:773, 1117` |
| Token price versus share price: Chainlink's documentation states token price = underlying equity price x `uiMultiplier`. The SPY feed checked against the pool is closest to "the feed already includes the multiplier" (basis +0.056%, s.e. 0.022%) and rejects "bare equity price" (+0.17%, 5.3 s.e.) and "applied twice" (-0.17%, 10.4 s.e.). A statistical check on about 90 observations, not a proof | `README:773`; "unverified (partner/Chainlink statement)" at `README:761` |
| Effect of a multiplier step: it shows up as a feed move against a pool that has not repriced. Guard table: steps of +17 bp and +33 bp are inside both bounds; allocation blocks beyond about +/-150 bp and removal beyond about +/-300 bp **in that test's bounds** (the live v4 bounds are 300 / 600 bp) | `README:775-779` |
| LP effect of a pure price step: "second order ... it was not measured here" | `README:779` |
| Token pause flags (`paused()`, `oraclePaused()`): read false for NVDA on 7 Nov; the guard and margin path enforce `oraclePaused`, the lending oracle path does not | `DEP/nvda-247-feed-2026-11-07/feed247.json`; `README:1119` |
| NVDA multiplier risk in the Ramses proofs: "Not applicable here (no multiplier risk assumed; not tested)" | `README:761` |
| Dividend amounts, dates or a schedule of multiplier steps: `NOT FOUND` | |

---

## 9. Costs observed

| Item | Value | Source |
| --- | --- | --- |
| Per-transaction keeper gas in USD (ETH $2,254) | checkpoint $0.0233, rebalance $0.0370, recentre $0.0615 | `README:1371` (18E); `README:1237` (17F: "about $0.023 / $0.037 / $0.06") |
| Same, strategy-analysis r3 (per-row USD as given in the JSON; the rows imply about $2,436 to $2,476 per ETH, against $2,283.34 at the end block) | recentre $0.0663 (3 txs, $0.1989 total), rebalance $0.0376 (63 txs, $2.3715), checkpoint $0.0239 average (79 txs, $1.8885) | `CL/remediation/evidence/strategy-analysis/strategy-analysis-2026-11-02-r3.json` keys `keeperCosts.byAction`, `operatingCostAssumptions` |
| Keeper total, window 2026-10-09 to 11-02 | 0.0018348 ETH, $4.1895 at the end-block ETH price $2,283.34; 13.23 active days; average burn 0.0000765 ETH per active day. The ETH total equals the sum of the `byAction` rows, which cover 147 transactions: 138 from the keeper wallet `0x978e...fac4` and 9 from the governor `0x9469...e5d2` (`perWalletTransactionsWithEvents`). The `byAction` USD figures sum to $4.472 (a different ETH price per row than the end-block total) | same JSON `keeperCosts` |
| Keeper total to 3 Nov | 176 transactions $5.244 (96 checkpoints, 78 rebalances, 2 recentres); corrected count 178 (97 / 79 / 2), $5.30 of gas against $0.185 of fees on about $18.7 of pair value | `README:1107, 1315, 1330` |
| Cost per day | Hourly checkpoint cadence the vault requires: **$0.5737/day** (assumed). Observed average including the out-of-gas period: **$0.1428/day**. Measured burn at the 3 Nov cadence: **about $0.85/day wall-clock, $310/year**; 0.000465 ETH per active day | `README:200, 1237, 1241, 1326, 1330`; JSON `operatingCostAssumptions` |
| Waste breakdown | 78 of 79 rebalances deployed less than their own gas (median $0.00038 deployed; $2.92 gas for $0.104 deployed); 41 checkpoints sent only to discover dust | `README:1330` |
| Protocol cost per year by checkpoint cadence (gas only) | hourly 24/5 $145.44; every 4 h $36.36; daily $8.51; weekly $1.21; plus recentres ($147.83 hourly for NVDA +/-2400 with 24 recentres a year; $153.42 for +/-600 with 81) | `README:1373-1382` |
| Recentre counts used | NVDA: 6 to 20 recentres per 90 days ($1.6 to $5.4 a year); +/-2400 24 a year, +/-1200 24, +/-600 81; SPY 0 | `README:1237, 1375-1382` |
| Hosting | Range keeper app $5/month; margin keeper $12/month ($5 worker + $7 database); together $17/month ($204/year); a Ramses keeper $0 to $60/year incremental | `README:1231, 1235, 1315` |
| RPC | $0 (public endpoint, rate limited, minutes of state) | `README:1236` |
| Who pays | The keeper wallet (protocol), not supplier principal: "gas and maintenance are not deductions from supplier principal" | `README:202` |
| Gas shortage | 0.002 ETH lasted 3.68 active days; the keeper sent nothing from 10-22 16:29 to 11-02 11:03 (10.77 days) and the next checkpoint booked -$0.157 | `README:1326, 1384` |
| After PR #16 (value gate, merged) | Dust rebalances are no longer sent; the hourly checkpoint is kept. Projected runway "about 19 [days] with hourly checkpoints and no dust rebalances". A measured post-change daily cost: `NOT FOUND` | `range_keeper.py:200-247`; `README:1384` |
| Live LP income for scale, 2026-10-09 to 11-02 (23.98 days) | Gross fees collected $0.1015 + uncollected $0.0819; estimate from liquidity share $0.1842; average pair value $19.31; LP in range 77.2% of strategy capital, idle 22.8%; time with liquidity in range 100% | JSON `uniswapFeesAndVolume`, `capitalPlacement` |
| Live result since seed (59 days to 3 Nov) | Gross fees $0.196, net of reserve $0.157, recognised strategy loss $0.546; pNVDA exchange rate -0.573%, pUSDG -1.180% | `README:1085, 1099-1108` |
| Backtest cost assumptions used so far | Recentre $0.0663, maintenance $0.5737/day, sensitivity $0.1428/day; poll 300 s; 150 bp pool-versus-oracle gate (the live gate is 300 bp) | `README:195, 200`; `CHAIN` |
| Avalanche | 1M gas = 0.0025 AVAX, about $0.021 at 2.5 gwei and $8.43; vault `rebalance()` gas not measured | `DOCS/avalanche-operating-cost-estimate.md:8-10, 21, 37` |

---

## Could not determine

1. Ramses gauge source rules. The 99 s threshold is now read on chain (`voter.timeThresholdForGauge` = 99 for both
   gauges, a per-gauge value held by the voter), but the gauge and validator source is not in the repos, so the exact
   slashing scope (the window's accrual only, or everything since the last successful claim as documented for Pharaoh),
   the treasury-slice rule and the period length rest on fork measurements and comments.
2. Whether RAM has any vesting, lock or exit-penalty form (xRAM). Nothing in the repos mentions one; the token paid in
   the fork tests is the plain ERC-20 "RAM" and `voter.xRatio()` reads 0. What a non-zero ratio would pay, and its exit
   terms, are not established. RAM's USD price and liquidity are not measured anywhere.
3. Which gauge the two treasury-slice figures belong to (the log and `DEPLOY_SPY_USDG.md` disagree).
4. How the 24/7 NVDA feed derives its weekend price (sources, method).
5. Heartbeat and deviation thresholds of the 24/5 NVDA and SPY feeds from a primary source (only quoted in a doc).
6. The Uniswap v4 protocol fee as a configured number (only inferred as 4.99 bp of volume from the evidence JSON).
7. Measured keeper cost per day after the value gate (PR #16) went live.
8. Gas for a margin "adjust" on either chain (only open, close, withdraw and liquidation are measured); the source of
   the 2.23M figure in `HEDGED_LP.md`.
9. Whether the Ramses SPY/USDG timelock batch has been scheduled, and when the pair will be configured.
10. Why the live keeper's last checkpoint is 18.5 h old beyond the code path described in 1.5 (no keeper log was read).
11. Avalanche: whether `rebalance()` or `harvestRewards` ever executed on mainnet and their gas; whether the rebalancer
    process is running; PHAR price, emissions and vault fee APR; the staleness behaviour of the two vault oracles in
    code; the meaning of `feeProtocol` = 1,000,000 on the WAVAX/USDC pool; the xPHAR and 111 s semantics beyond the
    three values read on chain (they rest on `avalanche-vault-source-references.md`); whether margin stage 1 completed
    and when stages 3 to 4 run.
12. Dividend dates or a schedule of `uiMultiplier` steps for SPY and NVDA.
13. Robinhood PR #7 gas price and ETH price for its finalize figures.
