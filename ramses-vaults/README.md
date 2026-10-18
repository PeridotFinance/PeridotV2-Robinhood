# Ramses vaults (NVDA/USDG): design handoff

**Status: not started. Nothing here is deployed.** This folder is where a Ramses-backed copy of the paired vault, an
isolated lending market on top of it, and a cloud keeper get built and tested. It is written for the developer (human or
agent) who builds it. Facts were read from the Ramses docs (<https://tech.ramses.xyz>), from Ramses' public API
(`https://gateway.kingdom.dev/robinhood/api`) and **verified on-chain on 2026-10-18**. Anything that is only a docs or API
claim says so.

> **Correction history (read this).** An earlier version of this file said Ramses on Robinhood Chain is DLMM-only, with
> no Uniswap-v3-style pools. **That was wrong.** The docs' Robinhood page lists only the DLMM deployment, but the Ramses API
> and the chain show a large **concentrated-liquidity (RamsesV3, Uniswap-v3-style) deployment with gauges**: 580 CL pools,
> about $5.6M TVL, versus 317 DLMM pools with about $0.45M. The operator was right. **The target is the CL pool below.**
> The DLMM NVDA/USDG pair (about $30 TVL) is irrelevant and is not part of this work.

## 1. Goal and constraints (from the operator)

- An **isolated market pair**: a *new* Peridottroller on Robinhood Chain whose pTokens ("boosted" markets) use a *Ramses* vault
  as their underlying vault. The existing Uniswap system stays untouched; the two sets of markets are distinct.
- **NVDA and USDG first**, then **more tokenized stocks paired with USDG later**. Design for many pairs from day one (the
  existing vault already keys everything by `pairId`).
- **Concentrated liquidity** with a **cloud keeper** that recenters (like the Uniswap V3 vault).
- Start with a **small test deposit**, scale later.
- Use the **existing Ramses pool** (our vault mints its own NFT positions in it). We do not create a pool.
- Share the existing **timelock**; use a **new dedicated keeper wallet** (KEEPER_ROLE only).
- Operator signs every mainnet transaction locally. The developer never signs, never reads keystores or password files, and never
  pushes to the default branch without green CI.

## 2. The target pool and the Ramses CL deployment (verified on-chain, chain 4663)

| Item | Value |
| --- | --- |
| **Pool** NVDA/USDG "CL10" | `0xdac1904D823F7d6bcAaF431ceEe40c934fEd321b` (token0 = **USDG**, token1 = **NVDA**: the same order as our Uniswap v4 pool) |
| Fee / tick spacing | fee `100` (0.01%), `tickSpacing` 10 |
| State at reading | tick 222,684, liquidity 7.42e17, observation cardinality 1000 |
| TVL (API) | about **$305k** |
| Measured volume | **about $784k in the last 22.8 hours** (230 swaps per 3 hours; summed from `Swap` events). At 0.01% fee that is about $80 per day of fees pool-wide, or about 10% a year on the whole TVL. Concentrated ranges earn more per dollar |
| **Gauge** | `0x93c6A733FB3165D94774B3A5Fc4319D29bd7b6ce` (`GaugeV3`) |
| Gauge reward tokens (on-chain `getRewardTokens()`) | **USDG, NVDA and RAM** (`0x5173D45A1191eE33cBB7D8c7e65f21B04eD54802`). Rewards can arrive in the vault's own assets, not only in RAM |
| Position manager (NFT) | `0x2eBd7B85a4E08D5B508b04BA147976C94afE6590` ("Ramses V3 Non Fungible Position", from `gauge.nfpManager()`) |
| CL factory | `0xE0c4ceb92d08CA985bB70fe0a22fEb121A9854A8` (from `pool.factory()`) |
| Pool deployer | `0x4b37359BF291AbE8453692DB58d515a8b013Dca9` |
| Fee collector | `0x2Bef16A0081565E72100D73CBe19B1Bd2d802380` |
| AccessHub / Voter | `0x83341F891f898cb5E0cacC8a70501BBa83d9CecF` / `0x30032D41868906f0376eC4D87B3D3Ac4064e7A97` |
| WETH9 | `0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73` |
| SwapRouter, Quoter, TickLens | **not yet found**: discover on-chain or via the API; verify by fork call before use |

**API claims (not verified, treat as marketing until reproduced):** this pool shows `feeApr` about 587%, `rewardApr` about 377%,
`lpApr` about 965% and about $785 of LP rewards in the current period. These annualise a short window and are probably computed on
*in-range* liquidity near the price, not on the $305k. The measured volume above implies far less on the whole TVL. Phase 0
must rebuild the yield from on-chain events for specific range widths.

Other USDG pairs (candidates for later; all CL with gauges, TVL from the API): SPY/USDG (about $501k at fee 100, $257k at fee
500), META/USDG ($128k), GLD/USDG ($107k), and more. A new stock needs its own oracle feed, decimals and risk review.

Useful API paths: `/pools`, `/all-pools`, `/stats/{poolAddress}`, `/positions`, `/cl-position-ids/{user}`, `/token-prices`.
Rate limit: 30 requests per 30 seconds per IP. Swagger: `https://gateway.kingdom.dev/robinhood/api/swagger`.

## 3. How RamsesV3 differs from Uniswap v3 (docs; function names to be re-verified on a fork)

- **Pool and positions are Uniswap-v3 style:** `mint/increaseLiquidity/decreaseLiquidity/collect/burn` through an NFT position
  manager, `slot0`, ticks, `LiquidityAmounts`, `TickMath`, `SqrtPriceMath` (a fork). This is **simpler than our current v4
  integration**: no `PositionManager` action batches, no Permit2, no Universal Router. The old v4 adapter cannot be reused as is,
  but the vault-facing shape (and `RangeLib`) can.
- **Period accounting (weekly, new periods start Thursday 00:00 UTC):** a position earns fees and gauge rewards only while the
  current tick is inside `[tickLower, tickUpper]`. Reward = `periodSecondsInside / pool.totalPeriodSecondsInside * weeklyEmissions`.
  Liquidity **added mid-period creates debt** (`periodSecondsInsideDebt += liquidity * secondsElapsed`), so a new NFT only earns
  from its mint time on. A recenter that mints a fresh NFT forfeits nothing already earned **only if rewards are claimed first**.
- **Gauge:** `GaugeV3.getReward(tokenId, tokens[])` (also an array form) is callable by the position's owner, operator or approved
  address; `earned(token, tokenId)` and `periodEarned(period, token, tokenId)` are views; `getRewardForOwner` is for the Voter.
  The docs do not say whether a position must be staked or attached: **verify on a fork** (mint, accrue a period, call `earned`).
  Whitelisted reward tokens are managed by AccessHub.
- **Fees:** governance-adjustable and not tied to tick spacing; a fee collector and a protocol share exist (`slot0.feeProtocol`
  is non-zero). Verify the split by calling `collect` on a fork.
- **Possible restrictions on contracts as position owners:** the docs are silent; the vault is a contract owner, so test it.

## 4. What exists in this repository (reuse, do not re-derive)

Everything below is on `main`. Read `remediation/README.md` ("Concentrated liquidity (V3)") and `keeper/README.md` first.

| Piece | Where | Reuse |
| --- | --- | --- |
| Paired vault V3 (accounting, loss waterfall, range policy, recenter) | `remediation/src/RobinhoodBoostedVaultV3.sol` | Fork into a **new separate deployment** (no proxy upgrade, so no storage-layout constraint). Keep principal/idle accounting, checkpoint, pro-rata loss, "gain only when both native sides are covered", roles, pause model, the recenter and rebalance entry points |
| Range and deployment library | `remediation/src/libraries/RangeLib.sol`, `RangeTypes.sol` | Same pattern (linked library, rolling budget ring buffer, cooldown, loss bound, soft idle-exit that only swallows the named dust error, deploy loss bound) |
| DEX adapter (the only component that talks to the DEX) | `remediation/src/UniswapV4PairedAdapterV3.sol`, interface `contracts/robinhood-vaults/src/interfaces/IUniswapV4PairedAdapter.sol` | New `RamsesV3PairedAdapter` with the same vault-facing shape (`registerPair`, `positionState`, `positionStateAt(pair, refSqrtPrice)`, `addLiquidity`, `decreaseLiquidity(.., ref, ..)` with oracle-anchored minimums, `collectFees`, `burnEmptyPosition`, range get/set) calling the V3-style position manager. Keep: exact approvals revoked after use, balance-delta checks, the removal-band maths by token order (USDG is currency0, the stock is currency1, and the guard bounds the **stock price in USD**, so the pool band is `[ref/(1+d), ref/(1-d)]`), the 20 bp quantisation widening |
| Oracle guard | `contracts/robinhood-vaults/src/StockOracleGuard.sol` | A V3-pool variant: Chainlink NVDA/USD feed `0x379EC4f7C378F34a1B47E4F3cbeBCbAC3E8E9F15` (12h staleness; stale on weekends), compare against the Ramses pool's `slot0`, output the oracle-derived reference sqrt price. Same fail-closed behaviour and asymmetric gates (tight to allocate, wider to exit) |
| Loss reserve | `contracts/robinhood-vaults/src/StrategyLossReserve.sol` | New instance |
| Boosted market delegate | `remediation/src/RobinhoodBoostedDelegateV2.sol` | New pTokens use it, pointed at the new vault |
| Lending controller and price adapter | `contracts/peridot-contracts-2-5/.../Peridottroller*.sol`, `remediation/src/RobinhoodLendingPriceAdapter.sol` | **New Unitroller + Peridottroller + pTokens + interest model + price adapter.** `deployments/README.md` records how the live system was assembled |
| Cloud keeper | `keeper/worker.py`, `remediation/tools/range_keeper.py`, `keeper/provision.py`, `keeper/Dockerfile`, `remediation/script/GrantRangeKeeperRole.s.sol` | Same pattern (dedicated wallet with `KEEPER_ROLE` only, preflight pins the implementation, monitor-only by default, receipt status decides success, operator-run `--enable`). The keeper additionally has to **harvest gauge rewards** (below) |
| Timelock rollout and verifier | `remediation/script/UpgradeConcentratedLiquidity.s.sol`, `remediation/tools/verify_concentrated_liquidity.py` | Same two-stage queue/execute pattern and independent deployed-code verifier |

## 5. New design problems the developer must solve (with evidence)

1. **Reward harvesting and accounting.** Rewards are paid in USDG, NVDA and RAM through `getReward`. Decide, with fork evidence:
   who triggers it (the vault owns the NFT, so the vault must be a permitted caller or approve the keeper); how USDG/NVDA rewards
   enter the ledger (like fees: credited to principal and idle, with the reserve cut?); and what to do with **RAM** (swap through a
   Ramses pool with an oracle-anchored minimum, hold in a separate bucket, or forward). RAM is thin and volatile: do not mark it
   into NAV without a bound. **Always harvest before `decreaseLiquidity`/burn**, or the rewards of a position that is recentered
   away are lost.
2. **Recenter versus rewards.** Each recenter mints a new NFT and restarts its reward debt; out-of-range time earns nothing. Model
   expected fees + rewards against the loss from recentering and choose band width, trigger and cooldown from data, not from the
   Uniswap defaults.
3. **Yield model from on-chain data** (Phase 0 deliverable): in-range liquidity distribution of the pool, volume and fee growth per
   unit liquidity, gauge `rewardRate` and the RAM price, for a few band widths. Reproduce or refute the API's 587/377/965% numbers.
4. **Pool versus oracle.** The NVDA token trades on-chain 24/7 while the Chainlink feed is stale on weekends. On 2026-10-18 the
   pool was about 5% below the last feed price. The guard must fail closed (as now); test the weekend paths.
5. **Manipulation in a $305k pool.** Our deposits start small and grow. Keep the allocation deviation gate, the deploy loss bound
   and oracle-anchored `amountMin`s on every mint, increase and decrease.
6. **Isolation and multi-pair.** A second controller shares tokens but not markets. Decide what is shared (timelock, role design)
   and what is not (vault, reserve, pTokens, controller, guard, adapter). Keep margin out of scope. Design the per-pair
   parameters (feed, decimals, tolerance, fee tier, gauge) so adding SPY, META or GLD is configuration plus review, not new code.
7. **Governance and token risks.** Ramses' controls (AccessHub, Voter, gauge reward whitelist, fee changes) are theirs. The docs
   say the DLMM side runs a 1-of-1 Safe with a zero-delay timelock; check what controls the CL pool and gauge. The NVDA token has
   `paused()`/`isBlocked()` and emits `TransferWithScaledUI`: if a corporate-action multiplier can change raw balances, both
   Ramses accounting and our balance-delta checks are exposed.

## 6. Workflow and standards (lessons from the Uniswap build; follow them)

- Foundry, Solidity 0.8.26, `via_ir`. Keep runtime under EIP-170 (`forge build --sizes`); move orchestration into linked libraries
  when needed. Stack-too-deep: split functions, use memory structs.
- **Tests, in this order:** unit tests with a faithful V3-style pool/position-manager/gauge double; fuzz tests for range and amount
  maths in both token orders and both decimals; **mainnet-fork tests** against the real pool, position manager and gauge at a pinned
  block (`REMEDIATION_FORK_BLOCK` and the header's `l1BlockNumber` as `REMEDIATION_NATIVE_BLOCK`; interest accrues on the
  L1-derived block number); invariant tests for the accounting; port the existing vault suites (`remediation/test/VaultV3*.t.sol`)
  to the new vault so inherited behaviour stays covered.
- **Mutation-check every important fix** (revert it, see the test fail).
- **Review loop:** after each stage run an adversarial review by a second model (read-only), fix, add regression tests. Last time
  three rounds found two high and several medium issues.
- **Forge traps:** `vm.expectRevert` is consumed by the next external call, including a `new` or a getter in the arguments (cache
  values first). Parallel tests share the process environment (`vm.setEnv`). `block.timestamp` can be cached across `vm.warp`
  under via-ir (use `vm.getBlockTimestamp()`). Public RPC prunes history: pin recent blocks and use `--threads 1`. Never embed
  `type(X).runtimeCode` of a linked contract in a script.
- **Silent-failure traps:** `cast send` exits 0 for a mined revert (check receipt status). Only swallow a named, narrow error.
  Send keeper transactions with an explicit gas limit and a deadline that survives the password prompt or node lag.
- **Rollout:** scripts verify preconditions and print what they will do; a Python verifier compares deployed runtimes with the
  reviewed build byte for byte; a local-fork rehearsal runs the real `forge script --broadcast` path before the operator signs.
  Never sign, never read `~/.foundry/keystores` or `.range-keeper-signer`.
- Publish through pull requests with green CI. Docs stay local except READMEs.

## 7. Phases and exit gates

| Phase | Deliverable | Gate |
| --- | --- | --- |
| 0 | `research/NOTES.md`: live ABI of pool, position manager, gauge, router and quoter confirmed by fork calls; whether a contract can own a position and claim rewards; reward tokens and who pays what; fee split; the yield model (problem 3); decisions on problems 1 to 7 | The operator reads and approves |
| 1 | `RamsesV3PairedAdapter` + a faithful pool/NFT/gauge double, unit, fuzz and fork tests including harvest | Fork round trip (mint, increase, decrease, collect, harvest, burn) conserves value within a stated tolerance, both token sides, in and out of range |
| 2 | Oracle guard, new vault (forked from V3), reserve, range library, reward handling | Ported V3 suites green, new invariants green, size under EIP-170 |
| 3 | Isolated lending: new controller, pTokens, interest model, price adapter, deployment scripts | Fork rehearsal of the full deployment and a supplier round trip |
| 4 | Cloud keeper (copy of `keeper/` plus harvest), dedicated wallet, role-grant script | Local-fork rehearsal sends a real signed recenter and harvest; least-privilege test |
| 5 | Adversarial review rounds, deployment verifier, runbook | No open high or medium findings; operator signs the queue |

## 8. Decisions already made by the operator

1. Use the existing CL pool; start with a small test deposit and scale later.
2. NVDA/USDG first; more stock/USDG pairs later.
3. Isolated controller lists the two boosted markets first.
4. Share the timelock; new dedicated keeper wallet.

Open: how to treat RAM rewards (problem 1) and the first-deposit size.
