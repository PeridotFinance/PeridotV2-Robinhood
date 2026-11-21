# Ramses vaults (NVDA/USDG): design handoff

**Status: Phases 0–3 complete locally; Phase 4 launch configuration and keeper are documented in section 11. Nothing here is deployed to production.** This folder is where a Ramses-backed copy of the paired vault, an
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
| Fee / tick spacing | earlier research: fee `100` (0.01%); final Phase 3 pin: `300` (0.03%); `tickSpacing` 10 |
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
| Boosted market delegate | `remediation/src/RobinhoodBoostedDelegateV2.sol` | Interface-compatible; Phase 3 uses a separate derivative for loss previews, caps and locked seed (section 10) |
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

These Phase 2 instructions supersede the earlier open design questions and recommendations above.

- Use the existing CL pool, with a new isolated vault, adapter and reserve. Lending/controller and keeper rollout are later phases.
- Accept Ramses governance risk: the observed **1-of-2 treasury Safe, zero-delay timelock and upgradeable gauge** remain upstream trust assumptions.
- Share the timelock; a dedicated keeper has KEEPER_ROLE only. CONFIG_ROLE belongs to the timelock. Guardian pause is one-way; only CONFIG can enable or disable reward forfeiture.
- Credit USDG/stock rewards to principal and idle like fees, including the configured reserve cut. RAM and any other non-native reward stay in separate per-pair/token buckets, **zero NAV**, with explicit CONFIG-only forwarding. No automatic RAM swap.
- **Stale-oracle LP exits fail closed**, including guardian exits. No weekend bypass. The markets' **50% local cash buffer** is implemented by the Phase 3 delegate (section 10), not this vault; it does not guarantee withdrawals after that cash is exhausted.

| Timelock-set launch setting | Value exercised by Phase 2 tests |
| --- | --- |
| NVDA/USD feed | `0x379EC4f7C378F34a1B47E4F3cbeBCbAC3E8E9F15`, 8 decimals |
| Token decimals | USDG 6, NVDA 18; USDG fixed at $1 |
| Feed staleness | 12 hours |
| Allocation / removal gates | 300 / 600 bps in stock USD price |
| Adapter allocation deviation / removal tolerance | 300 / 400 bps; tolerance covers half the exit gate plus the 100-bp operational buffer |
| Pair value cap | $1,000, configurable |
| Ranged-position cap | `type(uint128).max` USD-wad: effectively unlimited, non-zero for validation |
| Deploy / recenter loss bound | 10 / 10 bps; recenter deployment obeys the tighter of the two |
| Range half-width / trigger | 2,400 / 600 ticks |
| Recenter cooldown / rolling limit | 1 hour / 4 per rolling 24 hours |
| Reward forfeiture | OFF by default |

These are configuration, not hard-coded strategy defaults. Configure both loss policies before allocation: an unset deploy bound is zero (strict), not disabled. Other fixture values are a 20% reserve cut, 1% settlement slippage, $1,000 settlement-swap cap, 200-bp over-unwind, one-day checkpoint age and 300-second deadline limit. Phase 4 adopts these live NVDA values: 20% fee/reward cut, 1% settlement slippage, $1,000 swap and aggregate USDG caps, 200-bp over-unwind, one-hour maximum checkpoint age, and 300-second deadline limit. The keeper checkpoints at least hourly and on 50-bps oracle moves; see section 11. RAM stays in zero-NAV custody until a separate timelock forwarding instruction.

## 9. Phase 2 reproduction, results and boundaries

`src/RamsesBoostedVault.sol` is a **new, non-upgradeable deployment**, ported from the V3 vault. It keeps side principals and idle balances, oracle-valued pro-rata losses, gain recognition only when both native claims are covered, reserve fees, authorized-side withdrawals, bounded settlement, pause/deadline/cap rules, emergency decrease, and range policy. Initialization is one-time and restricted to the deploying account/factory; deploy vault and reserve, deploy the adapter with the vault address, then initialize both. There is no proxy upgrade entry point and no dependency on the live vault's storage layout.

`RamsesOracleGuard` implements the adapter's reference interface and checks the configured feed, completed round, positive answer, timestamps, stock `paused()` and `oraclePaused()`, and pool sqrt price. USDG remains fixed at $1. Pair identity (tokens/pool/gauge) cannot be changed after guard configuration. Feed and risk settings remain governed. The real USDG/NVDA pool uses USDG as token0; unit/fuzz tests also exercise the opposite order.

`RamsesVaultAdapter` inherits the unchanged Phase 1 `RamsesV3PairedAdapter`. Its new settlement route swaps directly through the registered Ramses pool, under the adapter's shared reentrancy lock. A transient callback context restricts the caller, input token and payment budget. Exact input/output deltas, oracle-derived minimum output in the settlement library, freshness/deviation checks before and after the swap, and revoked approvals bound the route. Keepers receive no NFT or token approval.

Linked libraries separate range orchestration, settlement, reward crediting, checkpoint arithmetic and configuration validation. The `ramses` Foundry profile uses `optimizer_runs=1` (as the original V3 vault profile does). Allocation caps compare both oracle-valued assets and claims, including unrecognized composition surplus. `RamsesLossReserve` is a separate deployment of the existing reserve behavior. No implementation under `contracts/`, `remediation/`, `keeper/` or `snapshot/` is edited.

### Rewards and exits

- Keeper `harvest` checkpoints first, then receives measured native rewards and applies the reserve cut. Keeper `recenter` harvests **before the cap check and before removing the old NFT**. The adapter also harvests before each decrease and burn.
- Automatic exit harvests remain in segregated adapter buckets until claimed; checkpoints and principal withdrawals never depend on draining them. `claimPendingRewards` can credit already-received rewards without querying a healthy gauge or fresh feed. RAM never enters side principal, idle, LP valuation or NAV.
- `forwardExternalReward` can spend only the named pair/token bucket and only through CONFIG_ROLE. It cannot forward either pair asset. Exact sender/recipient deltas are checked.
- `setAllowRewardForfeiture` is CONFIG-only, persistent and default OFF. The guardian cannot enable it. This avoids giving a hot guardian the power to destroy historical reward rights; the timelock explicitly accepts that tradeoff. In opted-in exits the adapter atomically rolls back a failed harvest and retains all principal/oracle checks. Empty-NFT burn may destroy historical reward metadata; retain the NFT if recovery is preferable.
- Emergency decrease collects fees with their reserve cut before principal removal. Its successful transaction sets all pause flags and books shared loss. Already-backed idle claims retain the inherited oracle-free withdrawal path after LP closure; native composition deficits remain guarded.

### Commands

All commands are read-only RPC or local EVM execution. No signing, broadcast, keystore/password access, push or PR is involved. Each verification subprocess has a hard ten-minute limit, including its children. Tests run with `--threads 1`.

```sh
make test-ramses-phase2
export ROBINHOOD_RPC_URL=https://rpc.mainnet.chain.robinhood.com
python3 ramses-vaults/tools/pin.py > /tmp/ramses-phase2-pin.env
source /tmp/ramses-phase2-pin.env
make fork-ramses-phase2
python3 ramses-vaults/tools/mutations_phase2.py --list
python3 ramses-vaults/tools/mutations_phase2.py --batch 1
# Repeat --batch 2 through --batch 13, one command at a time; or select names below.
# Select one to three exact names per command; repeat until all listed checks are run.
python3 ramses-vaults/tools/mutations_phase2.py \
  --only 'feed freshness' --only 'token pause' --only 'completed feed round'
```

The public RPC prunes state. A missing historical state or stale initial real feed is a failed rehearsal, never a skipped/pass result. Fork recenter scenarios move prices with real pool swaps and mock **subsequent** feed updates to exercise the 600-tick oracle trigger; initial minting uses the pinned real feed. The reward-failure scenario injects a manager revert, not a claim that the live gauge is failing.

### Verification results

The results in this section are historical Phase 2 evidence at `51c0327`; the broad Make targets now also include Phase 3.

Final local run (2026-10-19), `make test-ramses-phase2`: **276 passed, 0 failed, 0 skipped**, across 16 suites. This includes 13 fuzz entries at 256 runs each and 12 invariants at 64 runs / depth 32; each invariant reports 2,048 calls and zero uncaught handler reverts. Some invariant actions deliberately catch business-rule reverts. Counts include inherited assertions repeated in the derived recovery suites and both token-order suites.

| Production contract | Runtime bytes | EIP-170 margin |
| --- | ---: | ---: |
| RamsesBoostedVault | 24,469 | 107 |
| RamsesVaultAdapter | 21,846 | 2,730 |
| RamsesOracleGuard | 9,801 | 14,775 |
| RamsesLossReserve | 7,370 | 17,206 |

Final `make fork-ramses-phase2`: **19 passed, 0 failed, 0 skipped** (12 inherited adapter/research tests plus 7 new vault tests), at block **92,597,455**, native **26,226,470**, hash `0x5bfc7dc96ee61752fe02d627c0d4f20f7c42826ff82a986fa8dfb88a93b5b660`, timestamp **1792411114**. The new vault received **70,019,247,849,683,618 raw RAM** through the real manager/owner route; the direct gauge route rejected the unauthorized contract owner. Settlement redemptions assert actual vault `SettlementSwap` events for both assets. Subsequent feed updates and the reward-manager failure are simulated as described above.

All linked libraries also pass the size gate. The vault has little bytecode headroom; the pinned compiler/profile and size check are mandatory for later edits.

**38/38 Phase 2 mutations killed**, with source SHA256 restored byte-for-byte after every check. Fifteen unique inherited adapter safeguards are retained from the earlier 10-case and 7-case reports (two overlap). Two initial mutation survivors exposed redundant test checks, not an accepted production bypass: invalid timestamps and idle callback authorization. More specific regressions killed both; their original logs remain in `research/`.

Detailed logs and the adversarial self-review are retained in `research/NOTES.md`, `research/mutations.json` and `research/*phase2*.log`. Those research artifacts remain ignored/local, matching the package's existing policy. Formatting, Python syntax and `git diff --check` pass. No verification command hit the ten-minute cutoff.

The inherited accounting suites are ported assertion-for-assertion using accounting fault-injection doubles. Separate integration suites use the real guard, adapter, vault and reserve with the pool/NFT/gauge double, including a multi-pair NFT manager. Additional real-adapter accounting scenarios check pro-rata loss, fees with the reserve cut, and an out-of-range position completing as one-sided idle inventory before settlement. That double models rounded CL amount math, fee growth and weekly reward accounting; its swaps use a fixed slot0 price. Mainnet-fork tests exercise the actual tick-crossing engine and owner/claimer route.

Tests are scenario evidence, not proof against all principal loss. Fixed-price invariants cover randomized deposit, withdraw, rebalance, checkpoint and harvest sequences; they do not model arbitrary governance upgrades, adversarial liquidity changes, future stock corporate actions or long-horizon markets. Per-action 10-bp loss bounds do not bound total strategy loss, repeated deployment costs, oracle error or subsequent price movement. The approximately $305k pool TVL is the earlier research snapshot, not a minimum guaranteed depth.

Phase 4 resolves the deposit, reserve, caps and interest settings below using the operator’s live NVDA decisions. Monitoring and incident procedures are in section 11. RAM forwarding remains a separate future timelock action; launch requires no destination. No production transaction authorization is inferred from local test results.

## 10. Phase 3: separate Ramses lending markets

**Local implementation and rehearsal only. No production deployment, push, PR, wallet access or margin integration.** The operator-approved launch configuration enables supply and borrowing on both markets at **80% collateral factor**, after timelocked configuration and seed. This supersedes the historical Phase 3 CF-0/paused proposal. The shared timelock, underlying tokens and Ramses infrastructure are deliberate; controller storage, market proxies, delegate/module, vault, reserve, guard, price source/adapter and libraries are separate instances.

### Delegate decision and accounting

`OriginalDelegateCompatibility.t.sol` imports the corrected `remediation/src/RobinhoodBoostedDelegateV2.sol` unchanged. Its `depositForPair`, `withdrawForSide`, `accountedAssets`, `liquidAssets` and `withdrawableAssets` calls work against Ramses (the actual deposit selector is `depositForPair`, not `depositForSide`). Its existing `vaultBufferMantissa` is the **market's local cash target**, distinct from the vault's LP allocation policy. Tests reproduce allocation-paused deposits being swallowed, revoked approvals, stale cash reports, and an explicitly gas-budgeted cash redemption.

It cannot satisfy the full Phase 3 requirements unchanged. A regression demonstrates that a cash-only redeemer can exit at the old exchange rate after a price move, before a later checkpoint assigns the loss to remaining holders. It also lacks supply caps and a mandatory locked seed. `src/lending/RamsesBoostedDelegate.sol` is therefore a separate, minimal derivative of the corrected delegate, importing the captured `PErc20Delegate` lending logic. It is **not storage-compatible with the old boosted delegate and must never upgrade the Uniswap markets**. It retains zero-share rejection and ceiling-rounded exact-underlying burns.

The linked `RamsesMarketAccounting` library previews **losses only**, using the vault's oracle-priced pro-rata formula in controller snapshots, including the other market's collateral. Gains remain limited to the vault's booked claims. Fresh mint, redeem, explicit rebalance and outgoing transfers checkpoint before proceeding; borrowing additionally rechecks account liquidity after checkpointing. Repayment does not depend on checkpointing or redeployment. The operator's `syncVault` reverts if a nonzero withdrawal was caught as a failure; its caller must supply sufficient gas and check both receipt status and actual return/balance changes.

The buffer is timelock-set in `[50%, 100%]`, rounded **up** in raw units. Protocol reserves are retained before applying the percentage to the remaining managed underlying, so reserve donations cannot increase LP exposure borne by suppliers. Explicit rebalance checks the resulting cash floor and reverts if it cannot restore it. Market `setVaultPaused` blocks new vault deposits; it does not promise to unwind an existing LP. Use explicit sync or the vault's guarded emergency path for withdrawal. Automatic mint/transfer rebalancing retains the original optional-deposit behavior: allocation pause or strategy rejection leaves funds in local cash and revokes approval. After a withdrawal or borrowing, cash can fall below the target; the parameter is a deployment target, **not a ring-fenced guarantee against borrower usage or a guarantee that a stale feed permits replenishment**. Borrow/utilization cash is local tokens plus `withdrawableAssets`, never custody-only `liquidAssets`.

### Freshness and weekend boundary

The unchanged decimal-correct `RobinhoodLendingPriceAdapter` is deployed over a new `RamsesLendingPriceSource`. Both NVDA and USDG price reads validate the Ramses feed's complete round, timestamp, positivity, stock pause flags and 12-hour freshness. USDG is `1e30` for the controller and `1e18` through `assetPrices`; NVDA uses USD18. No manual/cached legacy price source is reused.

An unencumbered supplier who has **not entered the market as collateral** may redeem up to local cash while the stock feed is stale. The narrowly recognized `StaleOracle` error permits the last booked vault claim for that cash exit. A larger redemption reverts atomically, without accepting a loss or changing the ledger/shares. Freshness restoration returns the guarded settlement route to normal. Collateral-member redemption, borrowing and liquidation still require fresh prices; repay stays possible. Other oracle/checkpoint failures are not treated as stale or as zero-valued claims. Partial emergency decreases also preserve local-cash redemptions, using the live loss preview when fresh and the same stale boundary when stale; new borrowing remains blocked while that LP is in emergency mode. Vault withdrawals still enforce their own emergency and freshness guards. Closed positions with fully backed native idle retain the vault's oracle-free exit behavior from section 9.

**This availability choice has a limit:** a stale feed cannot prove current LP losses. Cash-only stale exits use the last booked claim and can precede a loss discovered after reopening. The no-early-exit loss tests cover **fresh oracle price moves**, not unknown weekend moves. Eliminating this residual priority risk would require suspending those cash exits or a separately reviewed haircut/reserve/settlement policy. Neither is silently introduced here. A token pause can also prevent cash transfers independently of the oracle.

### Approved lending launch settings (Phase 4)

| Setting | Script behavior / rationale |
| --- | --- |
| Market proxies | Fresh `PErc20Delegator` instances; `rpNVDA` and `rpUSDG`, 8 decimals; initial exchange rates `2e21` and `2e11` |
| Controller | Captured chain-4663 `Peridottroller` logic, fresh `RamsesUnitroller`; implementation and proxy admin set to the supplied timelock at construction |
| Timelock | Contract with `getMinDelay() >= 1 hour`; sole CONFIG/DEFAULT_ADMIN on vault, guard and reserve; vault guardian also timelock |
| Keeper | Separate address; only KEEPER_ROLE on the vault and market rebalance operator; market proxies additionally hold KEEPER_ROLE solely to checkpoint their fixed pair |
| Buffer | 50% minimum, timelock may raise it |
| Interest | Constructor-configured kink model, replaced through timelock-only market setter: per native block base `7610350076`, slope `47564687975`, jump `380517503805`, kink `0.8e18`. Approximately 2% base, 12% APR at kink and 32% at full utilization; the slope is 12.5% APR per unit utilization |
| Interest clock | Captured controller/market use EVM `block.number`; chain 4663 exposes L1-derived block numbers there. Model assumes 2,628,000 native blocks/year (12 seconds); realized annual rates depend on that cadence |
| Lending reserves | 10% of borrower interest; separate from the vault's 20% fee/reward reserve cut |
| Collateral factors | **80% / 80%, borrowing enabled** after seed. Tests exercise liquidation at this factor; they do not prove weekend or tail-risk solvency |
| Close factor / incentive | 50% / 1.08; scenario-tested, still subject to stale-feed liquidation unavailability and actual execution depth |
| Supply caps | 4.35 NVDA / 1,000 USDG, including seed and accrued supplier interest. **Zero closes supply.** At the 50% buffer this targets approximately $1,000 combined vault exposure near $230/NVDA; the independent $1,000 pair cap still applies when price moves |
| Borrow caps | 4.35 NVDA / 1,000 USDG; controller zero means unlimited and is rejected. Both markets open for supply/borrowing; ordinary seizure enabled |
| First deposit | Timelock-only first mint: $20 NVDA at the deployment feed price plus 20 USDG, leaving approximately $20 matched pair value in the vault at the 50% buffer (approximately $40 total market deposits). `1e8` raw shares remain locked at `0xdead`: 0.0000002 NVDA and 0.000020 USDG at the initial exchange rates. Fund the timelock with both seeds plus 0.0002 NVDA for reserve before execution |
| Donations | Direct vault/adapter transfers do not become booked claims. Direct market cash donations are real backing and can raise rates; locked shares and zero-share rejection bound the first-depositor rounding attack. Supply-cap saturation can still require timelock intervention |
| Margin / flash / rewards | No margin wiring; flash loans paused; controller PERIDOT reward speeds zero |

The interest curve uses the exact per-block parameters supplied by the operator for live NVDA. No equivalence to every edge case of Peridot JumpRateModel is claimed: the Ramses model retains its existing utilization clamp at 100%. Governance can replace the model through the markets’ timelock-only setter.

The reserve uses the recorded live limits: **$10/event, $25/UTC day, 50% coverage**, with **0.0002 NVDA** funded in the launch batch. The live deployment README records that NVDA funding and a subsequent 0.0000165 NVDA payout; it records no production USDG funding amount. Accordingly this plan does not invent a USDG balance: it funds zero USDG initially and uses the documented limits. Coverage remains limited by in-kind available balances. All risk values are set by timelock payloads, not strategy constants; interest parameters are constructor inputs and the installed model is timelock-replaceable.

### Reproduction and deployment tooling

All verification commands are bounded to ten minutes. Solidity tests use `--threads 1`. Never run a real-network broadcast from this development workflow.

```sh
make test-ramses-phase3
export ROBINHOOD_RPC_URL=https://rpc.mainnet.chain.robinhood.com
python3 ramses-vaults/tools/pin.py > /tmp/ramses-phase3-pin.env
source /tmp/ramses-phase3-pin.env
make fork-ramses-phase3
python3 ramses-vaults/tools/mutations_phase3.py --list
python3 ramses-vaults/tools/mutations_phase3.py --batch 1
# Run batches 2 through 8 separately. Each batch restores every source byte.
```

`script/DeployMarkets.s.sol:DeployRamsesMarkets` deploys guard, adapter, reserve, vault, controller implementation/proxy, delegate/module, rate model, both market proxies, price source and adapter. It initializes ownership but does not execute governance configuration. It prints every target/calldata pair and complete timelock `scheduleBatch`/`executeBatch` payloads for guard/reserve policy, pair registration, limits, role grants, market listing/configuration, pauses and first deposits. A fresh journal path is mandatory; existing journals refuse redeployment. An interrupted broadcast must be reconciled against receipts and Foundry's `--resume`, not repeated with a new journal to hide a partial deployment.

Required **public configuration** inputs are `RAMSES_PUBLIC_SENDER`, `RAMSES_TIMELOCK`, `RAMSES_KEEPER`, and `RAMSES_JOURNAL` (under `ramses-vaults/deployments/`). Raw caps default to the approved values; seed defaults to $20 of NVDA at the current fresh feed and 20 USDG. Optional `RAMSES_STOCK_SUPPLY_CAP`, `RAMSES_USDG_SUPPLY_CAP`, `RAMSES_STOCK_BORROW_CAP`, `RAMSES_USDG_BORROW_CAP`, `RAMSES_STOCK_SEED`, and `RAMSES_USDG_SEED` are explicit plan overrides; the production launch verifier rejects caps that differ from the approved values. Review any seed price drift between scheduling and execution. A simulation uses:

```sh
FOUNDRY_PROFILE=ramses forge script \
  ramses-vaults/script/DeployMarkets.s.sol:DeployRamsesMarkets \
  --rpc-url "$ROBINHOOD_RPC_URL" --sender "$RAMSES_PUBLIC_SENDER" --threads 1
```

This has **no `--broadcast`**. Preserve its journal as the reviewed simulation plan, then use a distinct empty journal path for any separately authorized broadcast; simulation output is not a receipt. If the operator later authorizes production, they sign locally using **`--account <their-keystore-alias>` only**, with the explicit public sender. Never use `--private-key`, wallet environment variables, `--unlocked`, or the rehearsal helper against production. The developer does not read any keystore, password or private key. The local helper is the sole exception to the `--account` signer rule: it uses anvil's unlocked impersonated public addresses, checks a literal loopback HTTP endpoint, checks `web3_clientVersion` is anvil, and checks chain 4663 before any write.

For a disposable fork, start anvil in a separate terminal and stop it after the bounded rehearsal:

```sh
python3 ramses-vaults/tools/check_phase2.py anvil \
  --host 127.0.0.1 --port 8547 --chain-id 4663 --silent \
  --fork-url "$ROBINHOOD_RPC_URL" --fork-block-number "$REMEDIATION_FORK_BLOCK"
# In another terminal:
python3 ramses-vaults/tools/check_phase2.py python3 ramses-vaults/tools/rehearse_markets.py
python3 ramses-vaults/tools/check_phase2.py python3 ramses-vaults/tools/rehearse_markets.py
```

The helper runs the **real forge `--broadcast` path** twice (a local timelock, then the stack), checks receipts, funds the tiny seed through a local impersonated pool transfer, schedules the batch, advances local time by one hour, executes, then independently verifies. The second helper invocation verifies and sends nothing. `--finish-local` is for a fully deployed but not configured local rehearsal, after unconfigured runtime verification; it is not a production resume mechanism. Local fork token transfers/impersonation never affect the remote chain.

`tools/verify_deployment.py` is read-only. It checks full reviewed runtime bytes and hashes, each linked library and approved immutable, Compound proxy slots (including packed offsets), admin/pending-admin/implementation state, role-event history and current roles, keeper privileges, pair and launch settings, controller markets/caps/collateral/pauses, oracle scaling and freshness, zero margin/reward wiring, locked seed shares and distinct addresses. It rejects known Uniswap deployment addresses from the frozen snapshot. For a journal created outside the local helper, add `deploymentBlock` from the earliest actual deployment receipt before verification; a simulation's addresses alone are not deployment evidence.

```sh
python3 ramses-vaults/tools/verify_deployment.py \
  --rpc-url http://127.0.0.1:8547 \
  --journal ramses-vaults/deployments/local-phase3.json
# --unconfigured verifies deployed code and initial ownership before timelock configuration.
python3 ramses-vaults/tools/check_phase2.py python3 \
  ramses-vaults/tools/test_local_deployment.py \
  --rpc-url http://127.0.0.1:8547 \
  --journal ramses-vaults/deployments/local-phase3.json
```

### Results, review and open decisions

Historical Phase 3 results at `8d061ee` (2026-10-19); Phase 4 results are in section 11:

| Check | Result |
| --- | --- |
| `make test-ramses-phase3` | **306 Solidity tests passed**, 0 failed/skipped, 18 suites: 276 inherited + 28 Ramses lending + 2 unchanged-delegate compatibility tests; **5 Python tests passed** |
| Fuzz/invariants | 14 fuzz entries at 256 runs; inherited 12 invariants at 64 runs / depth 32 |
| `make fork-ramses-phase3` | **29 passed**, 0 failed/skipped: 19 inherited + 10 lending; `--threads 1` |
| Source mutations | **22/22 killed**, original source SHA256 restored after each; batches 1–8 |
| Local broadcast rehearsal | Real forge broadcast to loopback anvil, timelock delay/configuration/seed, independent verifier covering 20 runtimes, then idempotent rerun with no transactions |
| Verifier fault injection | **9/9 rejected**: runtime, proxy admin/implementation, supply/borrow caps, buffer, pause, CONFIG role escalation, stale prices; restored state verified |
| Size/format/scope | `forge build --sizes`, targeted `forge fmt --check`, Python syntax, `git diff --check` pass; frozen source directories unchanged |

Final fork and anvil pin: block **92,661,841**, native **26,227,020**, hash `0x19c23bd071b83bbf29ae43178f510e10be10e712536641356aa03372dc53d769`, timestamp **1792417722**. The earlier block 92,651,549 became unavailable at the public RPC; that failed historical-state run was retained and replaced by a fresh pin, not skipped. The final pin reports pool fee **300 ppm** and protocol share **500,000 ppm** (50%); the earlier Phase 3 pin reported 175 ppm. The fork assertion derives expected fees from actual parameters. Monitoring cannot assume the original 100-ppm fee persists.

| New production contract | Runtime bytes | EIP-170 margin |
| --- | ---: | ---: |
| RamsesBoostedDelegate | 24,322 | 254 |
| RamsesMarketAccounting | 3,405 | 21,171 |
| RamsesPeridottroller | 23,441 | 1,135 |
| RamsesUnitroller | 1,507 | 23,069 |
| RamsesInterestRateModel | 939 | 23,637 |
| RamsesLendingPriceSource | 1,094 | 23,482 |
| RobinhoodLendingPriceAdapter (unchanged import) | 1,472 | 23,104 |

The unchanged vault remains **24,469 bytes** (107 bytes of headroom). All deployed dependencies/libraries pass the size gate; keep the pinned compiler/profile. The final journal is `deployments/local-phase3-release.json`; logs are `research/tests-phase3-final.log`, `fork-phase3-final.log`, `rehearsal-phase3-release*.log`, `verifier-faults-phase3-release.log` and `verified-phase3-release.json`. Mutation details remain in `research/mutations.json`. These generated research/journal files remain ignored and local. Intermediate unsuccessful runs are retained and do not count as passing evidence. One earlier anvil service reached its ten-minute watchdog and was stopped; no test/verification command exceeded the limit. The final service was stopped explicitly after verification.

The tests establish the listed scenarios with the faithful pool double and the real pinned Ramses pool/NFT/gauge: both lending directions, interest, guarded liquidation, losses shared before fresh-price exits, booked fee gains, pause/full and partial emergency exits, donations and the locked initial seed. Native-token reward gains and the two-sided coverage condition are exercised by the double; the real fork demonstrates swap-fee gains and RAM excluded from NAV (the live gauge's native reward rates were zero). Subsequent price-feed changes are simulated; initial feed readings and real pool execution are not. These are scenario results, not exhaustive economic or governance safety proofs.

Self-review fixes include fresh-loss preview/checkpoint ordering, strict oracle error handling and USDG scaling, reserve cash retention, cap accrual ordering, ceiling-rounded buffers, first-deposit protection, silent sync failure, partial-emergency cash exits, packed proxy-slot verification and keeper/config separation. No known code finding is intentionally deferred. Remaining boundaries are the stale cash-exit priority risk, cash consumption between rebalances, token/upstream pauses and governance changes, inherited insolvency/bad-debt behavior, and the accepted launch policy below. The review is an adversarial self-review, not an independent audit. The supplied timelock and its governance membership must be independently approved; the scripts validate its contract interface/delay and keeper separation, not the integrity of arbitrary timelock implementations. A timelock-only guardian also implies emergency-action latency. USDG is valued at $1, so depeg risk is outside the tested oracle model.

Phase 4 resolves the earlier launch decisions. Enabling 80% collateral and borrowing explicitly accepts that a stale stock feed blocks new borrowing, collateral redemption and liquidation, while debt and market prices may move; bad debt can accumulate before reopening. Unencumbered local-cash redeemers can exit at the last booked claim before unknown losses are recognized, leaving remaining holders exposed. The 50% buffer is a deployment target and borrowers may consume it. A 50-bps checkpoint trigger reduces the fresh-price recognition window; it cannot observe unknown weekend prices or make liquidation available with a stale feed. These boundaries are accepted by the specified launch configuration and are not fixed by the keeper. Independent pre-deployment review remains separate from this self-review.

## 11. Phase 4: approved launch and Ramses keeper

**Local implementation and validation only. No real-network signing/broadcast, credential access, cloud API, push or PR.** The operator’s live NVDA configuration now replaces the earlier CF-0/paused launch proposal in the deployment plan and verifier. See section 10 and [`deployments/launch.env.example`](deployments/launch.env.example). All risk policy remains timelock-set; the interest model takes constructor parameters and is replaced through the timelock-only market setter. No existing Uniswap source or deployment is changed.

The initial markets receive $20 of NVDA at the fresh deployment feed and 20 USDG: approximately $20 stays local and approximately $20 enters the paired vault. Both raw supply caps and borrow caps are 4.35 NVDA / 1,000 USDG. Supply caps include seed and accrued supplier interest; the vault independently enforces its $1,000 pair cap. The immutable initial exchange rates are smaller than Phase 3’s so the existing `1e8` locked shares cost only 0.0000002 NVDA / 0.000020 USDG. Tests cover donation/rounding protection at these amounts; this is not a claim about every adversarial donation or an absolute mathematical minimum seed.

The timelock batch configures the guard, reserve, pair, controller, market policies and caps; seeds both markets; funds **0.0002 NVDA** reserve; explicitly enables ordinary seizure; then activates **80% CF and borrowing** on both markets. No unseeded market is publicly usable: the first mint is admin-only and the batch is atomic. The reference deployment README records that NVDA funding, but no production USDG funding amount. This plan uses zero initial USDG reserve funding and the documented **$10/event, $25/day, 50% deficit-coverage** limits. It does not promise unfunded cover.

### Keeper behavior and safety

[`keeper/README.md`](keeper/README.md) contains operator commands and the hosting/journal boundary; [`keeper/OPERATIONS.md`](keeper/OPERATIONS.md) contains alerts and incident handling. The copied wallet/provisioning tools were adapted, not executed. The container entry point defaults to monitor-only, requires chain 4663, checks all keeper privileges before loading signer material, pins runtime hashes/dependency addresses and proxy/beacon state, and repeats preflight before every send. The final local manifest covered **49 runtime hashes**, including linked upstream libraries, gauge validator and beacons, as well as the new stack.

Each maintenance pass harvests, confirms a checkpoint, drives both market delegates, and considers vault rebalance followed by recenter. **A due recenter retains idle assets for its new range:** the keeper previews it after checkpoint/market maintenance and skips increasing the old NFT. The real gauge rehearsal demonstrated why: an immediate mint/increase followed by recenter can produce positive `earned` but no reward payout, causing the adapter’s strict `RewardClaimMismatch`. The worker does not weaken that check, enable forfeiture or classify it as benign. This conditional skip also avoids creating and immediately burning an intermediate full-range NFT on startup. Normal rebalance runs only after the successful checkpoint; every submitted recenter follows harvest.

The new trigger compares the current oracle price with the last confirmed checkpoint price, in either direction, using `abs(new-old) * 10000 >= old * 50`. It supplements hourly maintenance, with 60-second attempt rate limiting and the inherited 300-second default polling interval (minimum 60). A cooldown wait persists its expiry for a timely retry after restart, without needing another price move. Receipt-block prices and pending intent survive in the journal; failed submissions never advance the confirmed baseline. Gas price, per-action gas and the aggregate cycle budget are bounded. Named dust errors use the freshly read **matched** native-side idle budget, not total idle value: unmatched stock alone cannot fund a two-sided deployment. Material matched-balance failures still alert.

Unknown simulation failures, mined reverts, caught delegate-failure events, code/role changes and ambiguous submissions stop signing. Known waits remain observable. Pending hashes recover without resubmission. Losing the journal with a used signer refuses execution. The OS lock is per signer, independent of state-file path; it is **not a distributed lease**. One cloud instance and a stop/drain handover are required. The App Platform template does not provide durable storage: replacement containers need a preserved/restored journal, or a persistent single-worker host. Automatic cloud restart continuity is not claimed.

### Reproduction

Run from the repository root. Each child verification command and each disposable anvil lifetime is capped at ten minutes; Solidity uses `--threads 1`. Compile before starting the bounded anvil service. No account or key is needed for these commands.

```sh
make test-ramses-phase4
python3 -m unittest discover -s ramses-vaults/keeper -p 'test_*.py'
export ROBINHOOD_RPC_URL=https://rpc.mainnet.chain.robinhood.com
python3 ramses-vaults/tools/pin.py > /tmp/ramses-phase4-pin.env
source /tmp/ramses-phase4-pin.env
make fork-ramses-phase4

python3 ramses-vaults/tools/check_phase2.py python3 ramses-vaults/tools/mutations_phase4.py
# Each plan mutation is a separate bounded command, not one long shell batch:
python3 ramses-vaults/tools/mutations_phase4.py --plan --only 'launch CF'
python3 ramses-vaults/tools/mutations_phase4.py --plan --only 'launch supply caps'
python3 ramses-vaults/tools/mutations_phase4.py --plan --only 'launch borrow caps'
python3 ramses-vaults/tools/mutations_phase4.py --plan --only 'launch borrowing enabled'
# Rebuild restored sources before rehearsal.
python3 ramses-vaults/tools/check_phase2.py forge build --threads 1

# Repin immediately before anvil; public historical state can be pruned.
python3 ramses-vaults/tools/pin.py > /tmp/ramses-phase4-keeper-pin.env
source /tmp/ramses-phase4-keeper-pin.env
python3 ramses-vaults/tools/local_anvil.py  # loopback :8557, --accounts 0, no generated keys
# In another terminal, with the same pin environment and a NEW journal/state path:
source /tmp/ramses-phase4-keeper-pin.env
python3 ramses-vaults/tools/check_phase2.py python3 ramses-vaults/keeper/rehearse.py \
  --rpc-url http://127.0.0.1:8557 \
  --deployment ramses-vaults/deployments/local-phase4.json \
  --state /tmp/ramses-keeper-phase4 --deploy
```

For the independent verifier fault suite, use `tools/rehearse_markets.py` to deploy/configure on a fresh local anvil, then run `tools/test_local_deployment.py --rpc-url http://127.0.0.1:8557 --journal <that local journal>` through `tools/check_phase2.py`. Run it while the local feed is fresh. The keeper rehearsal deliberately ends with a 13-hour stale-feed scenario; an explicit simulated local feed refresh is needed to run freshness-enforcing verification afterward. The final run did this refresh before its verifier fault checks. Stop anvil when finished.

### Final results and adversarial self-review

Phase 4 validation on **2026-10-19**:

| Check | Result |
| --- | --- |
| Solidity local suite / size gate (`make test-ramses-phase4`) | **311 passed**, 0 failed/skipped, 20 suites; 306 inherited + 4 launch tests + 1 actual-timelock-plan test. Existing fuzz/invariant campaigns remain green |
| Python | **51 keeper/tooling tests**, **5 deployment-tool tests**, **12 unchanged original-keeper tests** passed. The last three keeper regressions were run with the separate unittest command above after the broad Make run; the final one-hour payload assertion was rerun with `forge test --threads 1 --match-path ramses-vaults/test/LaunchPlan.t.sol` through the bounded wrapper |
| Solidity fork (`make fork-ramses-phase4`) | **29 passed**, 0 failed/skipped, including 80%-CF lending/repayment/stale-feed/liquidation scenarios |
| Real worker on anvil | **18 checks passed, 26 keeper transactions mined successfully**: real gauge rewards, checkpoint, delegate/vault maintenance, both recenter directions, no-op, 50-bps trigger, pending recovery, code/role/timelock refusal and stale-feed wait |
| Phase 4 source mutations | **36/36 killed**: 32 worker safeguards and 4 actual deployment-plan CF/cap/activation mutations; source bytes restored after every case. The plan mutations preceded the final one-hour checkpoint-bound correction; its positive payload assertion and complete local deployment were rerun afterward. Compiler errors, timeouts and zero-test runs do not count |
| Independent deployment verifier | **13/13 corruptions rejected**, snapshots restored and positive verification repeated: code, proxy admin/implementation, supply/borrow caps, CF, borrow activation, ordinary seizure, reserve factor, cash buffer, market pause, CONFIG escalation, stale prices |
| Role-grant tool | Foundry payload-only simulation passed against the local deployed stack; no signer loaded and no grant broadcast |

Solidity fork pin: **92,695,425**, native **26,227,304**, hash `0xcf81f81499092d4be468c84dc98554309ff6d0226b57140d2157d2c68de07c7b`, timestamp **1792421140**. Worker anvil fork pin: **92,710,941**, native **26,227,435**, hash `0x872e22acd06d2b218ef9cdd966e5ecb72244f00d9d929f62b46edf4af4d91081`, timestamp **1792422723**. The manifest was captured at local block 92,710,970 after deployment/setup. The final worker deployment includes the live one-hour checkpoint bound. Initial deployment uses the pinned real feed. Subsequent feed updates are simulated; recenter price moves execute actual swaps through the real pool. The swap driver alone receives local ERC20 balance overrides, analogous to Foundry `deal`; pool liquidity and balances are preserved. Local transactions use anvil unlocked impersonation, not a production keystore or a real-network signature. The local worker uses a 2-gwei test ceiling because anvil’s suggested gas price exceeds the production 0.1-gwei default.

The vault/delegate runtimes remain **24,469 / 24,322 bytes**; the parameterized interest model is **1,153 bytes**. All libraries/dependencies pass EIP-170. The small vault bytecode margin is unchanged. Detailed local logs are `research/tests-phase4-final.log`, `fork-phase4-final.log`, `keeper-phase4-92710941.log`, `verifier-phase4-92710941.log`, `grant-phase4-simulation.log` and `mutations-phase4.json`. Generated logs/journals remain ignored; this README is the tracked reproduction record. No command exceeded ten minutes. All disposable anvil services were stopped explicitly.

Self-review produced regressions for initial and due-recenter ordering under the real reward validator, matched-side dust classification, receipt-block checkpoint baselines, caught delegate failures despite successful receipts, URL-prefix bypasses, missing journals, ambiguous submission and signer locks. The first gas-cap refusal, reward-mismatch receipts and the earlier faulty swap-funding fixture were retained as failed experiments; none is counted as a pass. The corrected production keeper preserves every on-chain reward/loss/oracle check. This is an adversarial self-review, not an independent audit or Phase 5 sign-off.

The tests prove these bounded scenarios, not long-horizon solvency or all upstream validator/governance behavior. At **80% CF**, the accepted stale-feed boundary in section 10 still permits unavailable liquidation and unknown cash-exit loss priority; borrowers can consume the 50% cash target. A 50-bps trigger cannot bypass a stale/missing feed, gas cap, reward validator failure or RPC outage. USDG remains fixed at $1; future stock actions, upstream upgrades, arbitrary bad debt and actual cloud/keystore operation remain outside the demonstrated results. Cloud/API and wallet-creation flows were tested only with mocked boundaries; no cloud provision, real signer test, image deployment or production activation was performed.

### Operator steps still required

1. Create the dedicated wallet locally with `keeper/create_wallet.py`; retain the public record and protect its generated encrypted files. Choose the explicit public governance sender. No credentials need to be shared with the developer.
2. Review the local commits and publish/build the reviewed release through the operator’s normal process. The developer made local commits only; the cloud branch must contain the reviewed keeper before provisioning.
3. Simulate deployment with `--account <encrypted-keystore-alias>` and the explicit sender, then operator-sign the reviewed deployment. Fund the timelock with both seed amounts plus 0.0002 NVDA. Schedule/review/wait/execute the configuration-and-seed batch during fresh feed hours. Its final actions enable supply, ordinary seizure and borrowing at 80% CF. Check actual receipts and the independent verifier.
4. Verify the wallet’s vault role and both market-operator assignments. For a later grant/rotation, use `keeper/grant_keeper.sh` to print payloads, simulate queue/execute and then explicitly operator-broadcast through the timelock. Revoke any replaced keeper separately after handover.
5. Build/review the runtime manifest, fund keeper gas, establish single-instance journal persistence/restoration and alerts, provision monitor-only, inspect heartbeats, then explicitly enable signing with `keeper/provision.py --enable`. Preserve and reconcile the journal before every restart/migration. Perform the real keystore/cloud end-to-end check as the operator; it was intentionally not performed here.

## 12. Phase 5a: adapter, rewards and lending availability

Work is isolated on `phase5/adapter-lending`, starting from `4c4ce34` plus the existing `b4ee2b3` non-owner NFT-increase regression. No vault-core, settlement, accounting, range or reserve source was changed. Tests were committed first in `aacb3b8`; the initial run demonstrated the failures below. Launch policy remains **80% collateral factor, 50% cash target, 10 bps deployment/recenter bounds and 2,400/600 ticks**. No real-network transaction, signer, keystore or cloud API was used.

### Review checks and evidence

Test names below are in `test/Phase5aAdapter.t.sol`, `test/Phase5aLending.t.sol` and `test/fork/Phase5aAdapterFork.t.sol` / `test/fork/Phase5aMarketFork.t.sol`, unless otherwise noted. Adapter unit scenarios run in both token orders. Faults injected into fork dependencies model future failures; they are not claims that the live token currently has those restrictions.

| Check | Verdict | Evidence (test name) | Commit |
| --- | --- | --- | --- |
| 1 | changed | `testPhase5aRewardLifetimeLimitExits`, `testPhase5aOversizedLiveListExits`, `testPhase5aBrokenBalanceOfExits`, `testPhase5aRecenterForfeitureSkipsBrokenRewardBookkeeping`, `testPhase5aForkRewardListAndBrokenRamPrincipalExit`, `testPhase5aForkRecenterBrokenRamWithForfeiture` | tests `aacb3b8`; implementation recorded below |
| 2 | changed | `testPhase5aRewardViewRoundingFailsAtomicallyButExits`, `testPhase5aRestrictedTransferRecoveryAndPrincipalExclusion`, `testPhase5aAlternatePayoutTokenClaimRollsBackAndExits`, `testPhase5aRecoveryCannotSpendAnotherPairsPendingRewards`, `testPhase5aForkRealRamExactClaimAndRestrictedRecipientRecovery`, `testPhase5aForkEarnedOneWeiDifferenceRollsBack` | tests `aacb3b8`; implementation recorded below |
| 3 | changed | `testPhase5aAllocation{Wide,Narrow}{Upper,Lower}`, `testPhase5aAllocationNarrowSafe`, `testPhase5aFork{Wide,Narrow}Allocation{Upper,Lower}` | tests `aacb3b8`; implementation recorded below |
| 4 | no change needed | `testPhase5aRemoval{Lower,Upper}GateEdge`, `testPhase5aForkRemoval{Lower,Upper}GateEdge`; existing `test_inverseRemovalBandFarEndpoint` | tests `aacb3b8` and final regression commit |
| 5 | changed | `testPhase5aRegistrationRejectsGuardPoolMismatch`, `testPhase5aRegistrationRejectsGaugePoolMismatch`, `testPhase5aForkGuardIdentityAndGaugeMismatch` | tests `aacb3b8`; implementation recorded below |
| 6 | changed | `testPhase5aBorrowChecksHealthAfterWithdrawalLoss` | tests `aacb3b8`; implementation recorded below |
| 7 | documented | `testPhase5aDepositMismatchIsAtomicTripwire`, `testPhase5aRealDepositFailureDoesNotBlockMintBorrowRedeems`, `testPhase5aTaxedVaultDepositCaughtWithoutBlockingMint` | tests `aacb3b8` and final regression commit |
| 8 | no change needed | `testPhase5aExternalShareAndUnderlyingPaths`, `testPhase5aSharesCannotBypassCollateralGate`; existing `testRoleBufferFloorAndPauseGates`, `testOperatorSyncDoesNotReportSilentWithdrawSuccess`, fork lending tests | tests `aacb3b8` and final regression commit |
| 9 | documented | `testPhase5aUncheckpointedDirectDepositRedistributesPreview`, `testPhase5aSideRebalanceCheckpointsLossBeforeDeposit`, `testPhase5aForkDepositCheckpointBeforePairChange` | tests `aacb3b8` and final regression commit |
| 10 | changed (verifier); rate logic unchanged | `testPhase5aWeekendSixtyHoursAtCashLimit`, `testPhase5aForkWeekendCashLimit`, Python `Phase5aRateTest.test_phase5a_maximum_rate_guard` | tests `aacb3b8`; implementation recorded below |

### Rewards, exits and recovery

The eight-token limit is a **lifetime union**, not just the current gauge list. Removed tokens remain tracked to preserve historical claims. A ninth unique token, nine current entries, broken `balanceOf`, failed claim or inconsistent receipt makes strict harvest revert atomically. One raw unit of `earned` mismatch also reverts; this deliberately continues to detect silent validator forfeiture. The real gauge pays RAM exactly in the tested hourly claim; no escrow variant was observed. The double additionally pays an unlisted escrow variant and proves that all transfers/claim state roll back on mismatch. Future gauge behavior is not inferred from today's payout.

The narrow availability mechanism remains the existing **CONFIG_ROLE/timelock opt-in** `vault.setAllowRewardForfeiture(pair,true)`. No new keeper or guardian authority is granted. Decrease and burn already isolate their claim in a self-call, roll back a failed reward frame, emit `ExitHarvestFailed`, and continue principal operations. The gap was recenter's earlier reward-library call. In forfeiture mode, fresh reward processing now emits `RewardsDeferred` and returns before any list or balance reads. Recenter can then remove/burn through the existing isolated claim route. This mode suspends explicit fresh harvest too; use `claimPendingRewards` for healthy credited rewards, then restore strict mode after remediation. Historical rewards can become unclaimable when the NFT is burned. The switch does not bypass price freshness, slippage, principal balance checks, or restrictions on an underlying token itself.

`adapter.recoverReward(pair,token,receiver,amount)` permits only the configured vault's CONFIG_ROLE holders. It recovers credited external rewards individually, or uncredited external-token surplus (including an escrow variant sent separately). It cannot transfer **any token registered as principal for any pair**, the NFT manager, or another pair's credited rewards. Aggregate pending liabilities are debited consistently. Exact sender/recipient deltas and the adapter lock remain enforced. Recovery to the vault itself is forbidden because it would bypass the vault's reward ledger. An external-token recovery has zero effect on market NAV. A token that refuses every transfer or balance read cannot be forced to move; it stays quarantined while principal exits use forfeiture mode. Recovery does not claim or erase a reward on an upstream gauge.

### Range loss and removal floors

At the unit fixture's near-300-bps price edges, returning to the unchanged oracle price loses **9.61 / 10.20 bps** for half-width 2,400 and **36.67 / 38.93 bps** for half-width 600. The fork at block **93,376,820** measured **9.64 / 10.24 bps** and **36.82 / 39.08 bps**, respectively. These figures mark the deposited amounts and resulting LP principal at the same oracle price; fees, rewards, idle balances and gas are excluded. The permitted wide upper edge also executes a real swap back to the reference and checks the resulting principal. The larger losses were accepted by the old adapter and are rejected by the new one.

A fixed spot-deviation band alone is therefore insufficient. The adapter now values the **actual minted liquidity** at the oracle price and requires at least 99.9% of the oracle-marked amounts spent, with two raw units per asset for mint-up/valuation-down rounding. This automatically tightens the accepted displacement with range width and asymmetry, while retaining the configured price band and launch policy. It is an atomic post-mint check; failure rolls back mint, transfers and approvals. For example, a 100-tick displacement in the 600-tick range loses about 4.22 bps and remains accepted.

Removal has a different purpose: it releases already-held LP inventory at an allowed current price and bounds each token's amount, rather than promising a 10-bps limit against the historical deposit value. The registration rule inverts `tolerance >= gate/2 + 100`; at launch `tolerance=400`, `gate=600`, so `d=2*(400-100)+20=620` bps. `_minimums` evaluates token0 at the high price endpoint and token1 at the low endpoint, then applies the 100-bps operational buffer. Inverse stock ordering inverts both endpoints. The guard independently rejects prices outside 600 bps; the extra 20 bps absorbs quantisation. Both fork directions succeed just inside the gate. One could derive the envelope as `gate+20`, but changing the adapter interface is unnecessary: the vault already requires its fixed envelope to cover the current gate. The floor is not a substitute for the guard or the vault's settlement-loss bound.

### Lending ordering, tripwire and entry-point inventory

A withdrawal can book an execution loss after a checkpoint. The borrow regression places Alice exactly at the 80% limit and uses a vault double that recognizes collateral loss only when USDG is withdrawn. Before the fix the borrow succeeded; now `doTransferOut` prepares liquidity first, then checks the borrower's updated controller liquidity before sending funds. A failing health check rolls back debt and withdrawal together. The redeem paths already price/check collateral after preparation.

`DepositBalanceMismatch` remains a deliberate fail-closed tripwire. A fake vault can return success without moving the reported amount and trigger it; the entire mint rolls back. The configured real vault checks both sender and recipient deltas before returning `received==amount`, so a successful mismatch is not reachable under supported exact-transfer tokens. Reverting vault deposits (paused allocation and taxed vault transfers tested) are caught, approvals cleared, and mint retains local cash. Ordinary borrow/redeem paths also remain available while vault deposits are paused. Converting a successful inconsistent transfer into a soft failure without a rollback frame would silently accept corrupted asset accounting.

The reviewed base files are `PErc20.sol`, `PToken.sol`, and `PErc20Delegate.sol`, including proxy dispatch. There is no additional mint/redeem/borrow-behalf implementation to override.

| External path | Underlying/share movement and guard |
| --- | --- |
| `mint` | Delegate checkpoint, cap/seed checks, shared lock, `doTransferIn`; optional exact vault deposit; zero-share rejection |
| `redeem`, `redeemUnderlying` | Delegate loss-aware loops; controller check and burn use the settled rate; exact-output burn rounds up |
| `borrow` | Base controller/debt checks and lock, then delegate `doTransferOut` with final post-withdrawal health check |
| `repayBorrow`, `repayBorrowBehalf` | Base lock/debt accounting and delegate transfer-in; no side-effect vault deposit for repayments |
| `liquidateBorrow` | Base repayment and controller liquidation/seize checks; live market exchange-rate preview; repayment transfer-in remains independent of vault deposits |
| `seize` | Shared lock and `seizeAllowed`; only listed market/controller-approved seizure; public arbitrary seizure fails |
| `transfer`, `transferFrom` | Shared lock and controller `transferAllowed`/collateral check using market snapshots; cannot move encumbered collateral away |
| `_addReserves`, `_reduceReserves` | Base reserve checks/lock; transfer-in independent of vault deposit; admin-only reduction uses delegate transfer-out |
| `sweepToken` | Admin-only, underlying expressly forbidden; unrelated ERC20 tokens go to admin |
| `flashLoan` | Launch-paused and independently verified; `maxFlashLoan` returns zero. If governance unpauses it, base implementation still routes through delegate transfer-out and verifies repayment |
| `rebalanceVault`, `syncVault` | Operator/admin only and locked; maintenance checkpoints before deposits; withdrawal validates returned balances and reports losses |
| `setVaultBuffer` | Admin only; checkpoint, rebalance and final buffer check; minimum remains 50% |
| `activateBorrowAccounting` | Admin migration of debt shares, no underlying transfer or supplier-share mint; launch markets already activate the base module |
| `_becomeImplementation`, `_resignImplementation`, `initialize`, other setters | Configuration/lifecycle only; base initialization/admin constraints; no separate underlying-transfer route |

`approve` changes allowances only. Accrual and `*Current` queries change debt/reserve accounting but do not transfer assets. Unknown `mintBehalf`, `redeemBehalf`, and `borrowBehalf` selectors revert. Existing controller isolation, liquidation, buffer, cap and access tests remain part of the complete suite.

### Unbooked loss and the stale weekend

The proportional loss preview depends on the **current** pair denominator. A direct deposit into the vault while a loss is unbooked redistributes that estimated loss: newly deposited capital bears part of it and the other side's preview rises. The diagnostic deliberately impersonates a market to demonstrate this: adding 0.5 NVDA (about $47.56) to a roughly $197 pair raises the other side's preview by **0.053007 USDG** (about **5.3 bps** of its $100.02 vault claim) and adds **0.000557243781487791 NVDA** of haircut to the depositing side. Public market mint, maintenance and buffer-change paths already checkpoint before depositing, and withdrawals checkpoint/settle before changing principal. The market-route and real-fork regressions confirm that the other side's exchange rate stays stable within rounding through these operations. No accounting formula is changed.

**Request to the vault-core owner:** if the vault will support side accounts other than these controlled delegates, require a checkpoint before principal-changing deposits (and review native-reward credits), or freeze each side's loss allocation at its last checkpoint. `depositForPair` itself currently has no such requirement. Do not represent the current ratio as invariant under arbitrary privileged direct deposits. This request is documented here instead of modifying the other developer's files.

After borrowing all **100.020000 USDG** of local cash, 60 hours at the assumed 12-second native block cadence is **18,000 blocks**, with withdrawable cash zero and utilisation **100%**. The launch curve gives **121,765,601,217 / 1e18 per block**. One accrual after the interval yields **100.239221 USDG** of debt, a **0.219177%** increase before raw-unit rounding. It is below the base market's `5e12` maximum rate by about 41 times. Repayment of all debt succeeds without a feed; a subsequent unencumbered USDG redemption succeeds from returned local cash. Before repayment, a redemption needing vault cash reverts; an encumbered collateral redemption and liquidation revert on the stale oracle. These are the existing availability boundaries, not rate-guard failures. The verifier now explicitly checks the maximum-utilisation rate against `PToken.borrowRateMaxMantissa` in addition to checking the approved model parameters/runtime.

### Reproduction and validation record

```sh
make test-ramses-phase5a
export ROBINHOOD_RPC_URL=https://rpc.mainnet.chain.robinhood.com
python3 ramses-vaults/tools/pin.py > /tmp/ramses-phase5a-pin.env
source /tmp/ramses-phase5a-pin.env
make fork-ramses-phase5a
# One reversible, bounded mutation per invocation; do not run source mutations concurrently.
python3 ramses-vaults/tools/mutations_phase5a.py allocation
# Other cases: recovery, recovery-authority, recovery-principal, forfeiture, identity, borrow-order, rate.
```

Each command uses a ten-minute child-process bound. Over-limit compilation attempts were stopped (two manually before the wall-clock watchdog was added, then by that watchdog). A recovery-authority mutation compilation also timed out before succeeding on retry; a zero-test filter experiment was discarded. None is counted as passing evidence. On macOS the runner holds a temporary idle-sleep assertion only for the bounded child command. Logs are ignored under `research/phase5a-*.log`, with mutation results in `research/mutations-phase5a.json`. Final test counts, runtime sizes, local rehearsal results and implementation commits are recorded at completion below.

### Applicability to the deployed Uniswap markets

Read-only comparison used the requested `RobinhoodBoostedDelegate.sol` and `UniswapV4PairedAdapterV3.sol` from `/Users/joshua/Peridot/RobinhoodVaults/PeridotV2-Robinhood`; neither was modified. Source comparison is not a fresh deployed-bytecode attestation.

- **1–2:** Ramses gauge/list/earned issues do not apply to that Uniswap adapter, which has no equivalent gauge reward route.
- **3:** Concentrated-range sensitivity to price displacement also applies to Uniswap V3-style range amounts used by V4. Its adapter should receive the same oracle-marked allocation-loss review; the exact measured numbers above are Ramses fixture results.
- **4:** The Uniswap adapter uses the same inverted tolerance/buffer/quantisation construction; the interpretation and range-endpoint tests apply.
- **5:** The exact Ramses guard-pool/gauge identity gap is specific to this composition. Uniswap stores a `PoolKey`/pool ID and checks that identity.
- **6:** The supplied Uniswap delegate prepares liquidity but has **no final borrower health check** in `doTransferOut`, so the withdrawal-loss concern applies there too.
- **7:** It contains the same successful-return `DepositBalanceMismatch` tripwire. Its transfer-in also rebalances on repayment, unlike Ramses; repayment independence deserves a separate review.
- **8:** The common base entry-point inventory applies. Launch flash-loan pause and controller policy must be verified independently for the deployed markets.
- **9:** Ramses' live proportional preview is package-specific. The supplied Uniswap delegate has no Ramses-style checkpoint-before-deposit protection; the broader unbooked-loss/claim-change issue requires review of its vault/accounting as well.
- **10:** Withdrawable-cash utilisation and the base maximum-rate guard apply to both. The 60-hour numbers only apply if the deployed interest model uses the same curve and block cadence.
