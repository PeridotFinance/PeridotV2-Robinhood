# Ramses vaults (NVDA/USDG): design handoff

**Status: Phase 2 implemented locally; verification results are in section 9. Nothing here is deployed.** This folder is where a Ramses-backed copy of the paired vault, an
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

These Phase 2 instructions supersede the earlier open design questions and recommendations above.

- Use the existing CL pool, with a new isolated vault, adapter and reserve. Lending/controller and keeper rollout are later phases.
- Accept Ramses governance risk: the observed **1-of-2 treasury Safe, zero-delay timelock and upgradeable gauge** remain upstream trust assumptions.
- Share the timelock; a dedicated keeper has KEEPER_ROLE only. CONFIG_ROLE belongs to the timelock. Guardian pause is one-way; only CONFIG can enable or disable reward forfeiture.
- Credit USDG/stock rewards to principal and idle like fees, including the configured reserve cut. RAM and any other non-native reward stay in separate per-pair/token buckets, **zero NAV**, with explicit CONFIG-only forwarding. No automatic RAM swap.
- **Stale-oracle LP exits fail closed**, including guardian exits. No weekend bypass. The intended markets' **50% local cash buffer** is an availability measure for Phase 3; it is not implemented by this vault or a guarantee of withdrawals after that cash is exhausted.

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

These are configuration, not hard-coded strategy defaults. Configure both loss policies before allocation: an unset deploy bound is zero (strict), not disabled. Other fixture values are a 20% reserve cut, 1% settlement slippage, $1,000 settlement-swap cap, 200-bp over-unwind, one-day checkpoint age and 300-second deadline limit. These are not additional operator decisions: choose their launch values, reserve funding/coverage, aggregate USDG cap, first deposit and RAM forwarding destination before deployment.

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

Unresolved deployment decisions, in priority order: select the initial deposit and reserve funding/coverage/cut; choose the aggregate USDG cap and external-reward destination; establish monitoring and incident handling for upstream fee/gauge/token changes. Phase 3 must wire the separate lending markets and their 50% local cash buffer; Phase 4 must implement the dedicated keeper. No production deployment approval is inferred from these local results.
