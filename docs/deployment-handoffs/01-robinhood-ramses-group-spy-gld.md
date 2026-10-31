# Handoff 01: One Ramses isolated group on Robinhood Chain (SPY, GLD, USDG; r33/RAM later) with dual-range CL keeper and leveraged margin

Audience: the implementation LLM working in `PeridotFinance/PeridotV2-Robinhood`.
Written: 2026-10-30 (revision 2, after operator feedback). Goal: mainnet deployment as soon as the gates below are green.

Read `00-shared-risk-model-and-rollout.md` first; it overrides this file where they differ. Then read this whole file before writing code. Then read, in this order:

1. `ramses-vaults/README.md` on branch **`feat/ramses-vaults`** (newest branch, last commit 2026-10-19 `8d061ee`). It is the NVDA/USDG design handoff plus Phase 0 to 3 results. Almost everything you need already exists there.
2. `README.md` and `keeper/README.md` on `main`.
3. `contracts/robinhood-vaults/margin-mainnet/RobinhoodMainnetMarginBase.sol` (how the live NVDA margin stack was assembled).
4. `contracts/peridot-contracts-2-5/contracts/contracts/margin/` (`IMarginRouterAdapter.sol`, `RobinhoodV4RouterAdapter.sol`, `IsolatedMarginTypes.sol`, `RobinhoodMarginPriceOracle.sol`).

Branch from `feat/ramses-vaults`, not from `main`. Name it `feat/ramses-group`.

## 0. What the operator decided

- **One isolated group for all Ramses assets**, separate from the Uniswap v4 stock system. SPY and GLD are **in the same group** (shared controller, cross-collateral allowed). Later Ramses assets join this same group.
- Assets: **SPY, GLD, USDG** now. **r33 and RAM** possibly later (the project wants them listed; scope depends, see section 10). r33/USDG is dropped.
- Underlying vaults use **Ramses** concentrated liquidity on the **CL1** pools (tick spacing 1): SPY/USDG at **0.01%**, GLD/USDG at **0.015%**.
- Full range earns too little. Use **narrow ranges (about 1% to 5%)** run by a more efficient off-chain keeper, with a **dual-range model**: most liquidity in a tight core range (about ±1%), a smaller share in a wider backup range (about ±5%) so the vault keeps earning when price moves faster than the keeper can recenter. SPY and gold are slow movers, which is why this can work.
- **Leveraged margin** on the group.
- Deploy to Robinhood mainnet (chain 4663) as soon as possible.

## 1. Read this first

**1a. Fee tier and fee entitlement must be verified on-chain.** The API says fee 100 (SPY) and 150 (GLD). For NVDA the API said 100 while on-chain pins read 175 and then 300 ppm; Ramses CL fees are governance-adjustable. Identify pools **by address**, assert `tickSpacing() == 1`, read `fee()` at your pin, and stop and report if it differs. Monitoring must not assume it stays fixed.

Who receives the swap fees is a separate question. Ramses' general docs describe gauged pools sending swap fees to voters by default, with pool-specific configurations allowed. On the NVDA pool our fork test saw net LP fees reach the vault with a 50% protocol share, but that does not prove the SPY and GLD settings. **Gate:** a fork test per pool that mints the exact position configuration we will use (both layers), executes real swaps through it, calls `collect` and the manager reward route, and measures what actually reaches the vault. The backtest uses the measured split, not the API's `feeProtocol`.

**1b. GLD margin size is capped by the pool's thin USDG side.** About $110.8k GLD versus $4.7k USDG at reading. Every close or liquidation of a GLD long sells GLD into that USDG. Caps come from measured depth. SPY is healthier ($132k SPY, $63.7k USDG).

**1c. Feeds are unknown.** Chainlink publishes Robinhood tokenized-equity feeds (price = equity price × `uiMultiplier`, 24/5 sessions, no heartbeat off-hours), but neither Chainlink's nor Robinhood's docs exposed the SPY or GLD proxy addresses to us. Phase 0 finds them and proves each with `description()`, `decimals()`, `latestRoundData()` on a fork. NVDA's `0x379EC4f7C378F34a1B47E4F3cbeBCbAC3E8E9F15` (8 dec) is the pattern. **No feed, no market.** Never substitute pool spot for SPY or GLD.

## 2. Target pools (Ramses API `https://gateway.kingdom.dev/robinhood/api/pools`, read 2026-10-30; verify on-chain)

| Item | SPY/USDG | GLD/USDG |
| --- | --- | --- |
| Pool | `0x38453c115607463ac284820ce959831042f3df4e` | `0x9896af2f6fed9939acc87637b973b6484e6619d8` |
| token0 / token1 | **SPY / USDG** (stock is token0, opposite of NVDA) | USDG / GLD (same order as NVDA) |
| Stock token | SPY `0x117cc2133c37b721f49de2a7a74833232b3b4c0c`, 18 dec | GLD `0xc9a981fee1f9dec688bb123ccdecc63d0debfc4e`, 18 dec |
| USDG | `0x5fc5360d0400a0fd4f2af552add042d716f1d168`, 6 dec | same |
| API fee / tick spacing | 100 (0.01%) / **1** | 150 (0.015%) / **1** |
| Protocol fee share (API) | 50% | 50% |
| TVL | ~$196.0k | ~$115.5k |
| Volume 24h / 7d | ~$1.53M / ~$8.88M | ~$352k / ~$1.61M |
| Gauge | `0x739e31798efe8cff3ef4c613d8ae751e179c7337` (RAM) | `0x7724fa91cd3bbf4bf1eee4e8383dbb74e08a975c` (RAM) |
| Share of TVL in range | ~63% | ~61% |
| Price at read | ~$763.79 | ~$366.72 |

The API's per-range figures (for SPY: about 13.5% fee + 43.9% reward APR at ±1%, 2.7% + 8.8% at ±5%; for GLD: 32.0% + 30.5% at ±1%, 6.4% + 6.1% at ±5%) assume the position sits in range all the time and annualize a short window. They show the right direction (narrow beats full range by an order of magnitude) but are not a forecast. Phase 0 rebuilds them from chain events for our actual dual-range policy, including recenter costs and out-of-range time.

Shared Ramses infrastructure (verified for NVDA; re-verify both new gauges report the same manager):

| Item | Address |
| --- | --- |
| NFT position manager | `0x2eBd7B85a4E08D5B508b04BA147976C94afE6590` |
| CL factory / pool deployer | `0xE0c4ceb92d08CA985bB70fe0a22fEb121A9854A8` / `0x4b37359BF291AbE8453692DB58d515a8b013Dca9` |
| Universal Router / MixedRouteQuoter | `0xbFbb2BCBc9dfFA029C27A249ae9BE031E1d83b1C` / `0x23B6EC50Fe0197FbE436717a0676BC07c54ba562` |
| RAM | `0x5173D45A1191eE33cBB7D8c7e65f21B04eD54802` |
| Existing Peridot timelock (shared) | `0x6797FB8Ce049B42C5BC2b42Bf76c6d15C7B12498` |

## 3. Group architecture

One controller for the whole Ramses group. Nothing is shared with the live Uniswap v4 system or its controller.

```
RamsesUnitroller + RamsesPeridottroller (one, isolated from Uniswap stocks)
 ├── rpSPY           -> RamsesBoostedVault pair "SPY/USDG"
 ├── rpGLD           -> RamsesBoostedVault pair "GLD/USDG"
 ├── rpUSDG          -> USDG side of the pairs (see 3a)
 └── later: rpR33, rpRAM (section 10)
One RamsesBoostedVault, one RamsesVaultAdapter, one RamsesOracleGuard, one RamsesLossReserve,
all multi-pair (keyed by pairId; `MultiPair.t.sol` already covers two pairs incl. a shared USDG cap).
One margin stack for the group (section 7).
```

### 3a. The USDG market problem and how to solve it

`RamsesBoostedDelegate` binds a market to exactly one `(vault, pairId)` (`robinhoodVault`, `robinhoodPairId`). A single USDG market therefore cannot today supply the USDG side of both SPY and GLD. Two ways:

| Option | What | Contract work | Time to mainnet |
| --- | --- | --- | --- |
| **A (launch)** | Two USDG markets in the same controller: `rpUSDG-SPY` (side of SPY/USDG) and `rpUSDG-GLD` (side of GLD/USDG). Both are normal collateral and borrowable in the one controller. The frontend can show them as one "USDG" row with a split | None. Same delegate, two proxies | Fastest |
| **B (follow-up)** | One `rpUSDG` with a multi-pair delegate: allocates to several pairs by timelock-set weights, `accountedAssets` sums all pairs, checkpoints and the loss preview iterate all pairs, withdrawals pull pro-rata | New delegate derivative plus a linked library. `RamsesBoostedDelegate` has only **254 bytes** of EIP-170 headroom, so the multi-pair logic must live in a library | Needs its own review round |

Build A for launch. Write B as a separate PR with its own test and mutation set, and migrate by opening `rpUSDG` and letting the two pair-specific USDG markets wind down (no in-place upgrade; the delegate is not storage-compatible).

### 3b. Prices for a multi-asset controller

`RamsesLendingPriceSource` is single-pair (one immutable `pair`) and deliberately prices USDG only when the stock feed is fresh. Write `RamsesGroupLendingPriceSource`: `asset -> pairId` map set once by the timelock; a stock asset returns its own pair's guarded price.

**Do not make USDG depend on every stock feed.** That would let a GLD feed interruption disable SPY/USDG operations. Instead (shared risk model section 7):

- USDG unit price is $1 under its own policy, with a timelock/guardian **group emergency pause** that returns 0.
- Each **boosted USDG market's exchange rate validity** depends on the feed of the pair it backs: `rpUSDG-SPY` is invalid for collateral checks when the SPY feed is invalid, `rpUSDG-GLD` when GLD is. Implement this in the group price source by pricing the *market* (pToken) through its pair, not only the asset, or by a validity check in the Ramses delegate's account snapshot. Choose the smaller reviewed change and document it.
- An account that only touches SPY-side markets must keep working while GLD is stale.

Keep the unchanged `RobinhoodLendingPriceAdapter` on top for decimal scaling. Tests: each asset, every stale/paused combination per pair, SPY operations live while GLD is stale, unknown asset reverts, pair-map cannot be changed after first set (or only by timelock with an event).

### 3c. Cross-collateral consequences

Because SPY and GLD share a controller, a user can post SPY to borrow GLD or USDG, and so on. That is what the operator wants. It also means a GLD pool or GLD feed problem can affect accounts holding SPY. Set collateral factors per asset with that in mind and keep GLD's borrow cap tied to its thin USDG depth (1b).

## 4. What already exists and what changes

Everything below is on `feat/ramses-vaults` and passed 306 Solidity tests, 29 fork tests and 60 mutations (Phase 2 + 3). Reuse; do not re-derive.

| Component | File | Change needed |
| --- | --- | --- |
| CL adapter | `src/RamsesV3PairedAdapter.sol`, `src/RamsesVaultAdapter.sol` | **Dual-range layering (section 6).** Also real-pool fork tests for SPY (stock = token0; only USDG-as-token0 was fork-tested) |
| Oracle guard | `src/RamsesOracleGuard.sol` | None expected; already multi-pair. Staleness capped at 12h in `configurePair` |
| Vault | `src/RamsesBoostedVault.sol` | **Avoid changes.** Only **107 bytes** of headroom. The dual-range design keeps the vault ABI unchanged; range and cadence settings are configuration |
| Range library | `src/libraries/RamsesRangeLib.sol` | Spacing-1 fuzz cases; trigger semantics for the layered model (section 6) |
| Lending | `src/lending/*` | New `RamsesGroupLendingPriceSource` (3b). Option B delegate later (3a) |
| Deploy script | `script/DeployMarkets.s.sol` | **Hardcodes NVDA** (`PAIR`, `POOL`, `GAUGE`, `FEED`, lines 35 to 39) and deploys exactly two markets. Rewrite to deploy one group from `pairs.json`: N stock markets + N USDG side markets (USDG option A, section 3a), one vault/adapter/guard/reserve, all pairs registered |
| Verifier / rehearsal | `tools/verify_deployment.py`, `tools/rehearse_markets.py`, `tools/pin.py` | Read `pairs.json`; verify all markets of the group; reject reuse of Uniswap system addresses |
| Keeper | `keeper/`, `remediation/tools/range_keeper.py` | Phase 4 was never built for Ramses. New, faster, multi-pair (section 8) |
| Margin | `margin/*`, `margin-mainnet/*` | Ramses router adapter, multi-asset guarded price source, one margin stack for the group (section 7) |

## 5. Per-pair configuration file

`ramses-vaults/config/pairs.json`; every script, fixture, verifier and keeper reads it. A new pair must be configuration plus review, not new code.

```json
{
  "SPY/USDG": {
    "pool": "0x38453c115607463ac284820ce959831042f3df4e",
    "gauge": "0x739e31798efe8cff3ef4c613d8ae751e179c7337",
    "stock": "0x117cc2133c37b721f49de2a7a74833232b3b4c0c",
    "usdg": "0x5fc5360d0400a0fd4f2af552add042d716f1d168",
    "stockIsToken0": true,
    "expectedTickSpacing": 1,
    "expectedFeePpm": 100,
    "feed": "<PHASE 0>",
    "feedDescription": "<exact description()>",
    "maxStalenessSec": "<<= 43200>",
    "allocationGateBps": 150,
    "removalGateBps": 400,
    "coreHalfWidthTicks": 100,
    "wideHalfWidthTicks": 490,
    "coreWeightBps": 7000,
    "coreTriggerTicks": 70,
    "wideTriggerTicks": 295,
    "wideDriftTriggerTicks": 140,
    "wideDriftDwellSec": 21600,
    "hysteresisBps": 2500,
    "recenterCooldownSec": "<from backtest>",
    "recentersPer24h": "<from backtest>",
    "deployLossBps": 10,
    "recenterLossBps": 10,
    "pairValueCapUsd": 1000
  }
}
```

GLD: same shape, `stockIsToken0: false`, `expectedFeePpm: 150`, gates proposed 200 / 500 bps. Tick values are starting points (100 ticks ≈ ±1.0%, 490 ticks ≈ ±5.0% at spacing 1); the backtest decides.

## 6. Dual-range liquidity (core + backup)

### 6a. Model

Per pair the vault holds **two NFT positions in the same pool, both centered on the oracle price**:

| Layer | Range | Capital share | Job |
| --- | --- | --- | --- |
| Core | ±`coreHalfWidthTicks` (start ±1%) | `coreWeightBps` (start 70%) | Earns most fees and RAM while the keeper keeps it centered |
| Backup | ±`wideHalfWidthTicks` (start ±5%) | remainder (start 30%) | Keeps earning when price outruns the keeper, during weekends when the stale feed blocks recentering, and during cooldowns |

Per dollar, the ±1% layer is roughly 5x as dense as the ±5% layer, so even at 70/30 the core dominates in-range fees. The backup is cheap insurance: when the core goes out of range the vault does not drop to zero yield, and the backup's composition limits how one-sided the whole pair becomes.

Recenter rules, **independent per layer with hysteresis**:

- **Core:** recenter when the oracle tick is more than `coreTrigger` from the core center (start 70% of the core half-width, so 70 ticks at ±100).
- **Backup:** recenter only when the oracle tick is more than `wideTrigger` from the **backup's own center** (start 60% of the backup half-width, about 295 ticks at ±490), **or** when the core center has sat more than `wideDriftTrigger` from the backup center for longer than `wideDriftDwell` (start 2x `coreTrigger` for at least 6 hours).
- After any recenter, a layer may not recenter again until price has moved at least `hysteresisTicks` (start 25% of that layer's trigger) back inside and out again, or its cooldown has passed, whichever is later. This stops flapping around a threshold.

With these defaults a normal core recenter (about 70 ticks) does **not** move the backup. An earlier draft used a 50-tick center-drift rule that fired on every core recenter; that is removed. Unit-test the separation: a sequence of core recenters within the backup trigger must leave the backup NFT untouched.

This keeps gas, swap cost and the gauge's reward-debt reset (every new NFT restarts its period accounting) mostly on the small, frequently moved layer.

### 6b. Where the code goes

Keep the vault unchanged. Put the layering in the **adapter**, which is the only component that talks to Ramses:

- Per pair store `tokenIdCore`, `tokenIdWide`, and the layer config (`wideHalfWidthTicks`, `coreWeightBps`), set by CONFIG (timelock).
- `addLiquidity` splits by value at the oracle reference price into the two layers; oracle-anchored minimums on both mints.
- `positionState` / `positionStateAt(pair, ref)` return the **sum** of both NFTs' amounts and liquidity, so vault accounting, the loss waterfall, caps and checkpoints work unchanged.
- `decreaseLiquidity` / `burnEmptyPosition` act on both layers pro-rata, harvesting both first (existing rule: harvest before every decrease and burn).
- Add `recenterLayer(pair, layer)` reachable only through the vault's existing `recenter` path. If the vault ABI cannot express "core only" without new bytes, encode the layer choice inside the adapter (core by default; backup when its own trigger fires) so the vault still calls one function.
- Adapter headroom is **2,730 bytes**. Move layer math into a linked library (`RamsesLayerLib`). Size gate on every build.

If this turns out to need vault changes, stop and report with a byte budget before touching the vault.

### 6c. What the contract still enforces

The keeper decides timing; the contract decides limits. With narrow ranges these need new values, chosen from the backtest and set by the timelock:

- **Cooldown and rolling limit.** NVDA used 1h and 4 per 24h. A ±1% core on SPY or gold may need several recenters on a normal day and more on CPI or FOMC days. Propose 10 to 15 minutes and 16 to 24 per 24h, then tune.
- **Per-recenter loss bound** stays tight (10 bps). Each recenter of a one-sided core swaps through the same pool; at 0.01% / 0.015% fee that is cheap, but the realized divergence loss is real and is the main cost of narrow ranges. The backtest must count it.
- **Oracle gates.** Allocation gate tight, exit gate wider (existing asymmetric design). Recentering is blocked while the feed is stale or the pool drifts beyond the gate, which on weekends is most of the time. The backup layer is what earns then.

### 6d. Backtest (Phase 0 deliverable, gates the parameters)

`research/dual-range-backtest/`: replay 60 to 90 days of each pool's swaps and the feed, simulate the exact contract policy (independent layer triggers, hysteresis, cooldown, rolling limit, loss bound, weekend and session-closed blocks), and keep **one portfolio P&L ledger** per configuration (shared risk model section 3). Report: time in range per layer, fees at the **fork-measured** entitlement (1a), recenter count and gas, net return versus holding and versus a 50/50 rebalanced portfolio, our capital at several sizes (dilution), and a **profitable-capacity sentence** (shared risk model section 2).

RAM: resolve the entitlement **before** validating profitability. The deployed paired vault keeps RAM in a segregated zero-NAV bucket with timelock-only forwarding, which today gives suppliers no enforceable claim to it. Either (a) the operator approves a distribution mechanism (guarded sale and compounding, or pro-rata distribution) that is implemented and tested in this build, and the backtest models exactly that, or (b) the backtest counts RAM as zero for users. Report both cases side by side.

Grid at least core ±0.5%, ±1%, ±2%; backup ±3%, ±5%, ±8%; core weight 50%, 70%, 85%. Compare against single-range ±1%, ±5% and full range. Pick the configuration with the best net return that also survives the worst week in the window.

## 7. Leveraged margin (one margin stack for the group)

The live NVDA margin stack (executor, liquidator, risk engine, config, vault, account factory, flash vault, insurance fund, fee distributor, quoter, swap module) is DEX-agnostic except two seams. Margin pairs are keyed by `(marginPToken, positionPToken, debtPToken)`, so one stack serves SPY and GLD.

**7a. `RamsesV3MarginRouterAdapter` (new, `IMarginRouterAdapter`).** Swap directly through the registered Ramses pool, not the Universal Router and Permit2. Copy `RamsesVaultAdapter.swapExactInput` + `RamsesSwapLib`: direct `pool.swap`, pay in `uniswapV3SwapCallback` (fork-verified on Ramses), transient callback context (only the registered pool, only during a swap, only the expected token, at most `amountIn`), `sqrtPriceLimitX96` from `minAmountOut`, measured balance-delta check, exact approvals, zero residuals, `onlyOperator`, owner = timelock, pools registered by address with `factory`, tokens and `tickSpacing` asserted against `pairs.json`. Tests: unit with `RamsesV3Double`, fork on both pools in both directions, callback reentrancy, wrong-pool callback.

Note that our own vault is an LP in the same pools. Margin swaps trade against our layers, which is fee income for suppliers. For **liquidation capacity**, follow shared risk model section 4: simulate correlated liquidations, remove a stressed share of third-party liquidity, and count our own layers only where they demonstrably remain at that step (they may be out of range, emergency-exited or being withdrawn in the same scenario). A wide range does not make liquidity permanent.

**7b. `RamsesGroupMarginPriceSource` (new).** Same logic as `margin-mainnet/GuardedMarginPriceSource.sol` (price only when the guard and the lending source agree exactly; else 0) but multi-asset: `asset -> pairId` map over the single group guard. USDG and the boosted USDG markets follow **3b's market-specific validity rules** (USDG unit price under its own policy and the group emergency pause; `rpUSDG-SPY` valid only while the SPY feed and session checks pass, `rpUSDG-GLD` only while GLD's do). Minimal local interface with just `pricesUSD18`; do not import the v4 `IStockOracleGuard`.

Invalid prices here mean "block", not "worth zero": the margin risk engine and the controller must **revert** risk-increasing actions for an account holding invalid collateral, and ordinary seizure of that collateral is disabled until a trustworthy price returns or the recovery policy applies (shared risk model section 6). Test that a zero from this source can never feed a seize calculation.

**7c. Prove on a fork, do not assume.** `RobinhoodMarginPriceOracle._vaultClaimReadable` reads `robinhoodVault()`, `robinhoodPairId()`, `underlying()` and `accountedAssets(pairId, token)`: register all group markets and price them. `activateBorrowAccounting(...)` must exist and work on the Ramses delegate. `IsolatedMarginAccount` does plain `mint`, `enterMarkets`, `borrow`, so borrowing needs **positive CFs, positive borrow caps, unpaused borrowing**: a separate timelocked activation batch after the supply-only launch has run cleanly.

**7d. Risk settings.**

| Setting | Live NVDA | Proposal for first Ramses activation |
| --- | --- | --- |
| Max **opening** leverage | Canary 2x (50% / 25%); live **5x (20% / 10%)** | Set in the margin pair config (`maxLeverageX100`, `initialMarginBps`, `maintenanceMarginBps`); margin accounts bypass the controller CF (shared risk model section 5a). Proposal: **2x with 50% / 25%** at activation. Raise only after stressed liquidation rehearsals pass on a fork |
| Initial / maintenance margin | Live 5x profile: **20% / 10%** (the 2x canary used 50% / 25%) | Candidate for the first Ramses pair: 50% / 25% at 2x |
| Liquidation bonus | 5% | 5% |
| Max slippage / oracle deviation | 1% / 1% | 1% / 1% SPY; GLD checked against depth |
| Per-position and aggregate caps | $2 / $1, no aggregate | From `tools/liquidation_capacity.py` (shared risk model section 4): aggregate notional that correlated liquidations can clear below max slippage after collateral withdrawal and a 50% third-party liquidity haircut, ÷ 2. Per-position cap a fraction of that. GLD limits must come from its measured USDG-side exit depth |
| Collateral factors (ordinary lending) | n/a | Bound **ordinary** borrowing and manual looping only (shared risk model section 5.0). Proposal: **50%** for SPY and GLD, **66.7%** for the USDG markets, checked against worst closed-session gaps. Margin leverage is not derived from these |
| Pairs | NVDA long/short vs pUSDG | SPY long/short vs `rpUSDG-SPY`, GLD long/short vs `rpUSDG-GLD` (option A); cross pairs (SPY margin for GLD) not at launch |

**7e. Weekends.** Feeds hold the last value off-hours or when `oraclePaused()`. Opens, swap-based closes and liquidations fail closed then (keep this). Fallback: repay with underlying, exit debt-free into pTokens. Size and state the insurance-fund seed; a Monday gap can exceed it.

## 8. Keeper (Phase 4): faster, multi-pair, dual-range

Build `ramses-vaults/keeper/` from `keeper/` (cloud worker) and `remediation/tools/range_keeper.py` (logic). "More efficient" means:

1. **Event-driven, not slow polling.** Subscribe to each pool's `Swap` events (websocket) and new blocks; recompute the oracle tick and layer distances on every swap; fall back to short polling if the socket drops. Robinhood Chain blocks are fast, so react within seconds.
2. **Pre-checked transactions.** Before sending, simulate `recenter` with `eth_call` against the latest block, check every named eligibility error (cooldown, rolling limit, stale feed, gate, dust) and only send when it would succeed. Keep a pre-built nonce and gas strategy so a send costs one round trip.
3. **Layer logic** from 6a: core on its trigger; backup on its own trigger or sustained center drift; hysteresis on both; never both in one block unless both fire independently.
4. **Harvest** both layers' gauge rewards when value exceeds gas by a configured multiple, and always as part of recenter (the contract already harvests first). RAM stays in the segregated zero-NAV bucket; no RAM sale by the keeper (existing operator decision).
5. **Session awareness.** Know the 24/5 feed sessions; stop attempting recenters when the feed is stale (they fail closed) and log the weekend drift between pool and last feed so the operator sees how much the backup layer is carrying.
6. **Metrics** per pair: time in range per layer, recenters per day, cost per recenter, fees and RAM accrued; expose them to the existing health log for tuning.
7. Same safety rules as the live keeper: dedicated wallet with `KEEPER_ROLE` only (refuse if it holds admin, config or guardian roles), implementation code hashes pinned in preflight, `EXECUTE=false` by default, receipt status decides success, explicit gas limit and deadline, JSON logs without secrets, named-error soft waits.
8. The margin liquidation keeper (`margin-mainnet/keeper-service`, `tools/keeper.py`) runs for the group's margin stack, separate process, separate key.

Gate: local-fork rehearsal with real signed recenters of core and backup on both pairs, a harvest, a simulated weekend stale-feed period, and a least-privilege test.

## 9. Oracle and corporate actions

- Measure real SPY and GLD feed update intervals for two weeks before choosing staleness (max 12h in the guard). If quiet-session gaps exceed it, the guard blocks allocation and recentering; that is intended.
- **Closed session is not the same as a late update.** Chainlink's Robinhood feeds keep their last value off-hours without heartbeats, so with a 12h age limit a Friday-close price still passes until Saturday morning while the token trades 24/7 on-chain. Add a session check: `oraclePaused()`, the token's `paused()`, and a session calendar (or a measured "no update for N minutes during what should be a session" rule) as separate fail-closed conditions for margin opens, liquidations based on the feed, recentering and new borrows. Document the exact behavior for: regular close, weekend, US holiday, early close, and a corporate-action pause.
- Pool vs feed drift on weekends: NVDA's pool sat about 5% off the last feed. With ±1% cores the core will be out of range for most weekends. That is expected; the backtest must include it.
- SPY pays quarterly distributions, so its `uiMultiplier` moves and Chainlink's price includes it. Our earlier finding: boosted markets on dividend-paying tokens leak value to arbitrageurs at ex-dividend dates, and a narrow range concentrates that. Add a fork test that moves the multiplier and shows what the vault, guard, adapter layers and margin do. Document it as a known boundary.

## 10. r33 and RAM (instead of r33/USDG; scope depends, stop and ask before building)

Ramses data 2026-10-30:

| Pool | Type | TVL | Composition | 24h volume | Gauge |
| --- | --- | --- | --- | --- | --- |
| r33/RAM `0x865ce837dd246e4059f3d435eec9eab9a78fa8e6` | **DLMM**, bin step 200 | ~$274k | r33 ~$234k / RAM ~$40k | ~$8.1k | none |
| r33/RAM `0xcf507f940d7b58c46efaaa7d307e98c1be27f12f` | **DLMM**, bin step 25 | ~$50k | r33 ~$48k / RAM ~$2.1k | ~$8.5k | none |
| WETH/RAM `0x5860dd468b4fc1740eaf80a3b7f22e78d70a076a` | CL, fee 1.75%, spacing 100 | ~$432k | | ~$171k | yes (RAM) |

RAM ~$0.0355, r33 ~$0.047. Neither has a Chainlink feed. There is **no r33/RAM CL pool**; both are DLMM (bins), which our adapter does not support. The vault is also built for stock/USDG pairs (`FeedConfig` has `stockToken` and `usdg`, USDG fixed at $1); r33/RAM has no USD leg.

Listing tiers, from cheapest to most work:

| Tier | What users get | Work | Risk to SPY/GLD/USDG suppliers |
| --- | --- | --- | --- |
| 0 | `rpR33` and `rpRAM` as **plain supply-only** markets in the group: collateral factor 0, borrowing paused, no vault, no margin. Listed, no yield | Deploy two plain delegators; no oracle needed because nobody can enter them as collateral (verify `redeemAllowed` never prices an un-entered market) | Essentially none |
| 1 | r33/RAM **LP yield** for those two markets, still CF 0, no borrowing, no margin | New DLMM adapter (bins, not ticks), a vault variant for a non-USD pair, and a manipulation-resistant price for r33 and RAM (e.g. long-window TWAPs via WETH/RAM CL and ETH/USD, if a Robinhood ETH feed exists). Weeks of work plus review | Low if CF stays 0 and borrowing stays off |
| 2 | Borrowing or margin on r33/RAM | Everything in tier 1 plus depth caps on pools with ~$40k or less on the RAM side | **Not recommended.** Shorts on a thin memecoin can squeeze into bad debt that lands on whoever lent the asset, and liquidators cannot exit size |

Recommendation: tier 0 now if the projects want to be listed, tier 1 only after SPY/GLD are live and the operator has confirmed what r33 is, who controls its contract (owner, mint, pause, blacklist, transfer tax: read bytecode and storage) and which price source is acceptable. Write `research/R33-RAM.md` with those facts first.

## 11. Phases and gates

| Phase | Deliverable | Gate |
| --- | --- | --- |
| 0 | `research/NOTES-RAMSES-GROUP.md`: verified feeds; on-chain `fee()`, `tickSpacing()`, token order, gauge reward tokens and `nfpManager()` per pool; two weeks of feed intervals and weekend drift; **dual-range backtest (6d)**; depth curves for margin caps | Operator approves feeds, layer config, cadence limits |
| 1 | `pairs.json`, group deploy script (USDG option A), `RamsesGroupLendingPriceSource`, adapter layering + `RamsesLayerLib`, spacing-1 fuzz, SPY token-order fork tests, multiplier fork test | All Phase 2/3 suites green with both pairs and both layers; new layer invariants (sum of layers equals reported position; pro-rata exits; harvest before every decrease); size gate green |
| 2 | Supply-only group rehearsal on a local fork via real `forge script --broadcast`; verifier; fault injection | Rehearsal + verifier green twice (second run sends nothing) |
| 3 | Keeper (section 8) | Fork rehearsal with signed core and backup recenters on both pairs plus harvest |
| 4 | Margin: router adapter, group price source, margin stack script, depth caps script, activation batch | Fork: long/short open and close, liquidation, stale-feed fail-closed, repay-to-exit, on both pairs |
| 5 | Adversarial review (second model, read-only), fixes, regression tests, mutations for every new safeguard | No open high/medium; operator signs |
| later | Option B multi-pair USDG market; r33/RAM tier 0 or 1 | Own review |

Mainnet order: supply-only group → keeper monitor mode → keeper `--enable` → margin stack deploy → margin activation batch after the timelock delay. Small caps first. Never combine steps. **Activate only the pairs that pass** the fee-entitlement fork test, the backtest's profitable-capacity gate and the liquidation-capacity gate; deploying the group with both pairs registered is fine, enabling allocation and margin is per pair. SPY is the likely first; GLD may stay supply-only or tightly capped.

## 12. Working rules (unchanged from the NVDA build)

- Foundry, Solidity 0.8.26, `via_ir`, `FOUNDRY_PROFILE=ramses` (optimizer_runs 1). EIP-170 on every production contract.
- Fork tests pin `REMEDIATION_FORK_BLOCK` / `REMEDIATION_NATIVE_BLOCK` via `tools/pin.py`; `--threads 1`; a failed historical read is a failed test, never a skip.
- Mutation-check every important fix. Ten-minute bound on every verification command.
- Forge traps: `vm.expectRevert` is consumed by the next external call including getters in arguments; `vm.getBlockTimestamp()` under via-ir; never embed `type(X).runtimeCode` of a linked contract in a script.
- You never sign, never read keystores, password files or `.range-keeper-signer`, never use `--private-key`. The operator signs with `--account <alias>`. Simulations have no `--broadcast`. Fresh journal per broadcast.
- Pull requests with green CI. Only READMEs are published; research logs stay local.
- READMEs in plain language: what is live, what was only tested, what is open. No "safe" or "guaranteed" claims.

## 13. Open operator decisions

1. SPY and GLD feed addresses once found, accepted staleness.
2. Dual-range parameters from the backtest: core and backup widths, core weight, trigger, cooldown, rolling limit.
3. USDG option A (two USDG markets) at launch, USDG option B (one multi-pair market) later: confirm.
4. Supply caps, first deposits and locked seed per market; reserve funding; aggregate USDG cap.
5. Collateral factors for SPY, GLD and both USDG markets given cross-collateral.
6. Margin: leverage at activation, borrow caps, depth-derived caps, insurance fund seed.
7. r33/RAM: tier 0, tier 1, or not at all; confirm r33's contract and controller.
8. Whether the Ramses NVDA pair from `feat/ramses-vaults` joins this group now (it fits the same group as a third pair).
