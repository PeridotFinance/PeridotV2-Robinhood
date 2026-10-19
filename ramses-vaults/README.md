# Ramses vaults (NVDA/USDG): design handoff

**Status: Phases 0–1 implemented locally; Phase 0 operator acceptance pending. Nothing here is deployed.** This folder is where a Ramses-backed copy of the paired vault, an
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
| Measured volume | **$776,446 over 82,013 seconds (22.78 hours)**, 2,365 `Swap` events ending at block **91,740,853**. About $77.60 gross fees; the current 50% protocol share leaves about **$38.80 net LP fees** in this window (about 4.9% annualized on $305k, before rewards). Range-specific estimates and their limitations are in `research/NOTES.md` |
| **Gauge** | `0x93c6A733FB3165D94774B3A5Fc4319D29bd7b6ce` (`GaugeV3`) |
| Gauge reward tokens (on-chain `getRewardTokens()`) | **USDG, NVDA and RAM** (`0x5173D45A1191eE33cBB7D8c7e65f21B04eD54802`). Rewards can arrive in the vault's own assets, not only in RAM |
| Position manager (NFT) | `0x2eBd7B85a4E08D5B508b04BA147976C94afE6590` ("Ramses V3 Non Fungible Position", from `gauge.nfpManager()`) |
| CL factory | `0xE0c4ceb92d08CA985bB70fe0a22fEb121A9854A8` (from `pool.factory()`) |
| Pool deployer | `0x4b37359BF291AbE8453692DB58d515a8b013Dca9` |
| Fee collector | `0x2Bef16A0081565E72100D73CBe19B1Bd2d802380` |
| AccessHub / Voter | `0x83341F891f898cb5E0cacC8a70501BBa83d9CecF` / `0x30032D41868906f0376eC4D87B3D3Ac4064e7A97` |
| WETH9 | `0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73` |
| CL router / quoter | **Universal Router** `0xbFbb2BCBc9dfFA029C27A249ae9BE031E1d83b1C`; **MixedRouteQuoter** `0x23B6EC50Fe0197FbE436717a0676BC07c54ba562`. Local-fork quote and swap verified. TickLens not required by the adapter |

**API claims (not verified, treat as marketing until reproduced):** this pool shows `feeApr` about 587%, `rewardApr` about 377%,
`lpApr` about 965% and about $785 of LP rewards in the current period. These annualise a short window and are probably computed on
*in-range* liquidity near the price, not on the $305k. The measured volume above implies far less on the whole TVL. Phase 0
rebuilt conditional estimates from on-chain events for specific range widths; see `research/NOTES.md`. The API default is a ±10-tick (approximately ±0.1%) range. Its static reward APR is reproducible; its headline combined APR is not the measured return of a fixed range in the sampled window.

Other USDG pairs (candidates for later; all CL with gauges, TVL from the API): SPY/USDG (about $501k at fee 100, $257k at fee
500), META/USDG ($128k), GLD/USDG ($107k), and more. A new stock needs its own oracle feed, decimals and risk review.

Useful API paths: `/pools`, `/all-pools`, `/stats/{poolAddress}`, `/positions`, `/cl-position-ids/{user}`, `/token-prices`.
Rate limit: 30 requests per 30 seconds per IP. Swagger: `https://gateway.kingdom.dev/robinhood/api/swagger`.

## 3. How RamsesV3 differs from Uniswap v3 (docs; function names to be re-verified on a fork)

- **Pool and positions are Uniswap-v3 style:** `mint/increaseLiquidity/decreaseLiquidity/collect/burn` through an NFT position
  manager, `slot0`, ticks, `LiquidityAmounts`, `TickMath`, `SqrtPriceMath` (a fork). **The NFT ABI uses `int24 tickSpacing`,
  not `uint24 fee`, and its `positions` getter returns ten fields, without nonce/operator.** Liquidity operations need no
  action batches or Permit2. The separately verified swap route uses a Universal Router. The old v4 adapter cannot be reused as is,
  but the vault-facing shape (and `RangeLib`) can.
- **Period accounting (weekly, new periods start Thursday 00:00 UTC):** a position earns fees and gauge rewards only while the
  current tick is inside `[tickLower, tickUpper]`. The gauge pays `weeklyEmissions * positionPeriodSecondsInsideX96 / (604800 * 2**96)`.
  Those seconds are already weighted by the position’s share of active liquidity; the denominator is a full week, not
  a separate `pool.totalPeriodSecondsInside` getter.
  Liquidity **added mid-period creates debt** (`periodSecondsInsideDebt += liquidity * secondsElapsed`), so a new NFT only earns
  from its mint time on. A fresh NFT starts its own reward accounting. Decreasing liquidity alone does not erase the old NFT’s range metadata;
  burning it does. The adapter **attempts harvest before every decrease and burn**; failure blocks exits by default.
  A vault-only emergency flag can permit removal/burn after an atomically reverted harvest (section 9).
- **Gauge (fork-verified correction):** direct `GaugeV3.getReward(tokenId, tokens[])` also requires protocol-authorized
  claimer status. NFT ownership alone is insufficient. At the verified block only the NFT manager and Voter are authorized.
  The contract owner calls **`positionManager.getReward(tokenId, tokens[])`**, which routes through
  `gauge.getRewardForOwner` and pays the NFT owner. No staking or attachment was needed to earn and receive RAM.
  USDG and NVDA are registered reward tokens but both had zero emissions in the observed period. The gauge has a
  reward validator and can silently forfeit rewards; the adapter checks expected amounts against both balance deltas.
- **Fees:** governance-adjustable and not tied to tick spacing; a fee collector and a protocol share exist (`slot0.feeProtocol`
  is **500000 / 1000000**, i.e. **50%**). A fork swap verified the split. `collect` returns actual net LP
  fees plus any already-decreased principal; it does not include separately claimed gauge rewards. The fee collector’s
  `voter` is zero at the snapshot, so protocol fees go to its treasury, not automatically back to this gauge.
- **Contract owners:** verified on a local fork, including the adapter owning its NFT and claiming through the manager.

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
   who triggers it (the **adapter** owns the NFT and only the vault may invoke it; the future keeper should call the vault,
   not receive NFT approval); how USDG/NVDA rewards
   enter the ledger (like fees: credited to principal and idle, with the reserve cut?); and what to do with **RAM** (swap through a
   Ramses pool with an oracle-anchored minimum, hold in a separate bucket, or forward). RAM is thin and volatile: do not mark it
   into NAV without a bound. **Always harvest before `decreaseLiquidity`/burn**. The Phase 1 adapter keeps claimed amounts in separate per-pair
   reward buckets until the vault explicitly receives and accounts for them. This does not decide the reserve cut or RAM policy.
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
   say the DLMM side runs a 1-of-1 Safe with a zero-delay timelock; CL calls here instead found a **1-of-2 treasury Safe and a zero-delay timelock** (see research evidence), with the CL factory
   controlled by AccessHub and the gauge using an upgradeable implementation. The NVDA token’s `paused()` is callable;
   `isBlocked(address)` and `isBlocked()` reverted and must not be relied on as known ABI. It exposes `uiMultiplier()` and
   emits `TransferWithScaledUI`. [Robinhood’s documentation](https://docs.robinhood.com/chain/building-with-stock-tokens/)
   specifies **UI scaling, not raw-balance rebasing**, and says the Chainlink token price already includes the multiplier.
   Current raw transfers are fork-checked; the automated suite does not establish the UI balance formula,
   future upgrades or corporate-action transitions.

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

## 9. Phase 0–1 reproduction and integration boundary

The implementation is `src/RamsesV3PairedAdapter.sol`. It is non-upgradeable, with immutable vault, NFT manager and
reference-oracle addresses. Pool/gauge/range/risk configuration is per pair. The vault-facing liquidity/state/range methods
retain the paired adapter shape; registration uses Ramses pool/gauge addresses instead of a v4 `PoolKey`. There is no
settlement swap method in this Phase 1 liquidity adapter. The router is verified separately in the fork suite; settlement
swap integration, the production feed guard and vault accounting remain Phase 2 work. `IRamsesReferenceOracle` is a
required fail-closed dependency; the test oracle is **not** a production implementation or approval to allocate at spot.

`harvest` returns token and amount arrays. Automatic pre-removal/burn claims accumulate in `pendingRewards`; they are
excluded from liquidity/fee return values. `claimPendingRewards` can drain amounts already credited even if the gauge later
fails. A failed new harvest blocks liquidity removal/burn by default. The vault may explicitly set
`setAllowRewardForfeiture(pairId, true)` to permit exits after a failed harvest. The entire harvest runs in a
self-only subcall: a revert or wrong amount rolls back all reward transfers, claim state and pending-bucket writes before
the exit continues. `ExitHarvestFailed` records the affected NFT. Healthy claims still accrue normally, and explicit
`harvest` stays fail-closed. Oracle, price-band, principal, balance-delta and custody checks remain mandatory.

This flag is persistent and per pair; the future vault must restrict its setter to emergency governance and reset it
after an incident. It can forfeit **historical as well as current-period rewards**, especially on burn. Prefer removing
liquidity and retaining the empty NFT while rewards may be recoverable. No proof excludes all principal loss under
upstream upgrades, token failures or market movement, so the default remains fail-closed. Stale-oracle exits remain
blocked independently of this flag and need a separate Phase 2 decision.

```sh
make test-ramses
export ROBINHOOD_RPC_URL=https://rpc.mainnet.chain.robinhood.com
python3 ramses-vaults/tools/pin.py > /tmp/ramses-pin.env
source /tmp/ramses-pin.env
make fork-ramses
python3 ramses-vaults/tools/research.py --block "$REMEDIATION_FORK_BLOCK"
python3 ramses-vaults/tools/mutations.py
```

The public endpoint prunes state quickly. Save the printed pin and run promptly; a missing historical state is a failed
reproduction, not a skipped or passing test. Research snapshots and detailed notes stay local under `research/`. No deployment,
key access, signing, broadcast, push or PR is part of these commands. For exact tested pins, counts, tolerances, limitations and
operator decisions, read `research/NOTES.md`.

Latest local verification (2026-10-19): `make test-ramses` passed **76 tests** (including six fuzz cases at
256 runs each and four invariants at 64 runs / 2048 calls each); `make fork-ramses` passed **12 tests** at
block **92,544,029**, native block **26,226,016**. Both commands use `--threads 1`; no tests were skipped.
Adapter runtime is **20,428 bytes**. The fork includes ordinary in/out-of-range round trips, two price shocks,
fee split, real owner/claimer routing, week-boundary rewards, router/quoter and a simulated reward-failure exit.
These are scenario checks, not proof against all principal loss or approval of production parameters.
Seven targeted mutations of changed/new exit safeguards were killed; the earlier 10/10 report is retained locally.
Use repeated `tools/mutations.py --only 'mutation name'` options to rerun selected safeguards; source is restored
after each run and compiler errors do not count as kills. Detailed commands and limitations are in the local notes.
