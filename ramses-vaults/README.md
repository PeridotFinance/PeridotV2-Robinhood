# Ramses vaults (NVDA/USDG): design handoff

**Status: not started. Nothing here is deployed.** This folder is where a Ramses-backed copy of the paired
vault, an isolated lending market on top of it, and a cloud keeper get built and tested. It is written as a handoff for
the developer (human or agent) who builds it. Facts below were read from the Ramses docs
(<https://tech.ramses.xyz>) **and verified on-chain on 2026-10-18**; where a fact is only a docs claim it says so.

> **Read this first: the premise needs a correction.** The brief for this work assumed Ramses on Robinhood Chain
> is "very close to Uniswap v3". **It is not.** On chain 4663 Ramses runs a **DLMM** (a Trader Joe "Liquidity Book" v2.1
> style market): discrete price bins, per-bin fungible shares, no ticks, no NFT positions, no `positionManager`.
> The Uniswap-v3-style `RamsesV3` pools exist elsewhere per the docs, but **no RamsesV3 contract is deployed on Robinhood
> Chain**. The existing Uniswap adapter therefore cannot be ported; a new adapter with different position maths is needed.
> The brief also assumed "a good amount of APY". On Robinhood the DLMM is **fee-only**: no gauge, no emissions, and the NVDA/USDG
> pool holds about $31. See "What is actually there".

## 1. Goal and constraints (from the operator)

- An **isolated market pair**: a *new* Peridottroller on Robinhood Chain whose pTokens ("boosted" markets) use a *Ramses*
  vault as their underlying vault. The existing Uniswap system stays as it is; the two sets of markets are distinct.
- **NVDA and USDG only** at first (NVDA `0xd0601CE157Db5bdC3162BbaC2a2C8aF5320D9EEC`, 18 decimals; USDG
  `0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168`, 6 decimals; pTokens 8 decimals).
- **Concentrated liquidity** with a **cloud keeper** that rebalances (recenters) it, like the Uniswap V3 vault.
- All mainnet transactions are signed by the operator locally. The developer never signs, never reads keystores or passwords,
  and never pushes to the default branch without CI.
- Upgrades go through the existing timelock (`0x6797FB8Ce049B42C5BC2b42Bf76c6d15C7B12498`, 1h delay, max 2h by plan).

## 2. What is actually there (verified on-chain, chain 4663)

| Item | Value |
| --- | --- |
| DLMM factory | `0xdcD5F77697914E27f56FD263EF82923C8524AbAc` (317 pairs, 5 quote assets, owner = AccessHub) |
| Router / Quoter | `0xd0019e86edB35E1fedaaB03aED5c3c60f115d28b` / `0xb722efaAbe807FAeA16068f595EaA9aa1a62CECD` |
| Fee collector | `0xcc543a9EDBa25cEB2922b165047b6A7bE861C55b` |
| AccessHub (factory owner) | `0x83341F891f898cb5E0cacC8a70501BBa83d9CecF` |
| Pool implementation (docs) / (factory) | `0x4e857A78bcE4FcF41677f21Bfaf3e77890d5042b` / `0xFeAA9e8A9fF97aA8BF792f219045C5c194dC67C1` (they differ: check which one pools clone) |
| Quote assets | WETH `0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73`, USDG, and three others |
| Governance (docs) | 1-of-1 Safe, immediate controls, **zero-delay timelock**; "migration planned". Voter exists but the factory is **not connected** to it |

**NVDA/USDG pools (tokenX = NVDA, tokenY = USDG in both):**

| Pair | binStep | Reserves | Notes |
| --- | --- | --- | --- |
| `0x337c9105eb8D682d49DB7BFBd986dcA952f89004` | 10 (0.10% per bin) | **0.137 NVDA and about $0 USDG (about $31 total)** | Created by the factory owner. Active bin 8,366,357, active price about $219.45 (stale versus the oracle). No non-empty bin below the active one. 47 `Swap` events in the last 6 million blocks (a block-time guess of 0.25s makes that about 17 days). Lifetime protocol fees about 2.3e12 raw NVDA, i.e. total fees of about a cent: lifetime volume on the order of $20 |
| `0xa754918339c93e29aA781E68f2AaCDF3FAeaC727` | 1 (0.01% per bin) | 0 / 0 | Created by a third party, abandoned |

Fee model (pair 1): base fee = `baseFactor * binStep * 1e10` = 0.05% at bin step 10, plus a variable fee that rises with
on-chain volatility; **95% of fees go to LPs, 5% to the treasury** (`protocolShare` 500). Seven presets are open: bin steps
1, 2, 5, 10, 15, 20, 25. Fees compound **inside the bin reserves** (there is no `collect`).

**Consequences for the plan**
1. There is no emissions APY to chase. Any APY is trading fees, and the existing pools have almost no volume. Our own liquidity
   would be most of the pool. Treat the Ramses market as an experiment in a second venue, not as a proven yield source.
2. The active price in the existing pool is far from the oracle. A deposit must either join that pool (and be priced by
   arbitrageurs first) or use a new pair; decide this explicitly (section 8).

## 3. DLMM mechanics (docs; function names verified on a live pair)

- **Bin price:** `price(id) = (1 + binStep/10000)^(id - 2^23)` as an unsigned 128.128 fixed-point number. Call
  `getPriceFromId(uint24)`; do not reimplement the maths. The price is "tokenY raw per tokenX raw": for NVDA/USDG that is
  about 2.2e-10 (USDG has 6 decimals, NVDA 18).
- **Composition:** bins below the active bin hold only tokenY (USDG), bins above hold only tokenX (NVDA), the active bin may hold
  both, `L = price * x + y` per bin. There is no curve inside a bin. Verified: `getBin(id)` returns `(reserveX, reserveY)`,
  `getActiveId()`, `getNextNonEmptyBin(bool swapForY, uint24 id)`, `getReserves()`, `getTokenX/Y()`, `getBinStep()`.
- **Shares:** ERC1155-like per bin: `balanceOf(owner, binId)`, `totalSupply(binId)`, `approveForAll(operator, bool)`. The
  router burns shares and therefore needs `pool.approveForAll(router, true)`. Burns are pro rata to the bin's reserves.
- **Adding liquidity** (`DLMMRouter.addLiquidity(LiquidityParameters)`): `tokenX, tokenY, binStep, amountX, amountY, amountXMin,
  amountYMin, activeIdDesired, idSlippage, int256[] deltaIds, uint256[] distributionX, uint256[] distributionY, to, refundTo,
  deadline`. Destination bin = live active id + `deltaIds[i]`; X may only be placed at or above the active bin, Y at or below;
  distributions are 1e18-scaled and must not over-allocate. **`to == msg.sender` is enforced.** Large bin arrays can exceed gas:
  chunk them. With `idSlippage = 0` the router silently shifts every destination bin if the active id moved (docs warning).
- **Composition fee:** adding an asset ratio to the **active bin** that differs from its composition performs an implicit swap
  and charges a fee. A recenter that re-adds into the live active bin pays this unless the ratio matches.
- **Removing:** `router.removeLiquidity(tokenX, tokenY, binStep, amountXMin, amountYMin, ids, amountsToBurn, to, deadline)`.
- **Swaps / quotes:** `pool.getSwapOut(amountIn, swapForY)`, router `swapExactTokensForTokens` with a `Path` of
  `pairBinSteps, versions (Version.V2_1), tokenPath`.
- **Oracle:** the pair keeps an oracle (length 120 at the time of reading). The docs say the variable fee never looks at CEX
  prices: only on-chain swaps move it. **Do not use the pool as the price reference** (see the guard section).
- **Hooks:** the factory owner can install a hook on a pool; a hook that reverts blocks that pool's operations until removed.
  Docs: "no general administrator withdrawal function for user liquidity", but a hook or a future implementation can still
  freeze exits. This is a trust assumption about Ramses governance (1-of-1 Safe, zero-delay timelock).
- **Token issues** (docs): fee-on-transfer tokens are unsupported; **rebasing tokens make LPs eat negative rebases with no
  recovery.** The tokenized NVDA emits `TransferWithScaledUI` and exposes `paused()` and `isBlocked(address)`: check whether a
  corporate-action multiplier can ever change raw balances. If it can, DLMM and our balance-delta accounting are both exposed.

## 4. What exists in this repository (reuse, do not re-derive)

Everything below lives on `main`. Read `remediation/README.md` and the section "Concentrated liquidity" before designing.

| Piece | Where | Reuse |
| --- | --- | --- |
| Paired vault V3 (accounting, loss waterfall, range policy, recenter) | `remediation/src/RobinhoodBoostedVaultV3.sol` | Fork it into a **new, separate deployment** (no proxy upgrade, so no storage-layout constraints). Keep the accounting (principal, idle, checkpoint, pro-rata loss, gain only when both sides are covered) and the role model. Replace pool/tick/sqrt-price plumbing |
| Range and deployment library | `remediation/src/libraries/RangeLib.sol`, `RangeTypes.sol` | Same pattern (linked library, rolling budget ring buffer, cooldown, loss bound, `allowEmpty` soft exit that only swallows the dust refusal). Range is expressed in **bins**, not ticks |
| Uniswap adapter (the only component that talks to the DEX) | `remediation/src/UniswapV4PairedAdapterV3.sol`, interface `contracts/robinhood-vaults/src/interfaces/IUniswapV4PairedAdapter.sol` | Write `DlmmPairedAdapter` with the same vault-facing shape: `registerPair`, `positionState`, `positionStateAt(pair, refPrice)`, `addLiquidity`, `decreaseLiquidity(.., referencePrice, ..)`, `collectFees`, `burnEmptyPosition`, plus range get/set. Pool-facing calls: router add/remove, `getBin`, `balanceOf`, `getPriceFromId` |
| Oracle guard | `contracts/robinhood-vaults/src/StockOracleGuard.sol` | New `DlmmOracleGuard`: Chainlink NVDA/USD feed (`0x379EC4f7C378F34a1B47E4F3cbeBCbAC3E8E9F15`, 12h staleness, stale on weekends), compare the **active-bin price** with the oracle, output a reference price. Same fail-closed behaviour, same asymmetric allocation (tight) vs removal (wide) gates |
| Loss reserve | `contracts/robinhood-vaults/src/StrategyLossReserve.sol` | New instance |
| Boosted market delegate | `remediation/src/RobinhoodBoostedDelegateV2.sol` | New pTokens use it, pointed at the new vault |
| Lending controller and price adapter | `contracts/peridot-contracts-2-5/.../Peridottroller*.sol`, `remediation/src/RobinhoodLendingPriceAdapter.sol` | **New Unitroller + Peridottroller + pTokens + interest model + price adapter.** Read `deployments/README.md` for how the live system was assembled |
| Cloud keeper | `keeper/worker.py`, `remediation/tools/range_keeper.py`, `keeper/provision.py`, `keeper/Dockerfile`, `remediation/script/GrantRangeKeeperRole.s.sol` | Copy the pattern (dedicated wallet with `KEEPER_ROLE` only, preflight pins the implementation, monitor-only by default, receipt status decides success, `--enable` uploads the signer) |
| Timelock rollout | `remediation/script/UpgradeConcentratedLiquidity.s.sol`, `tools/verify_concentrated_liquidity.py` | Same two-stage queue / execute pattern and an independent deployed-code verifier |

### The design problems that are new (the developer must decide, with evidence)

1. **Fees compound inside bin reserves, so there is nothing to `collectFees`.** The V3 vault credits collected fees to principal
   immediately, and credits price gains only when both native sides are at or above principal. With compounding, fee income
   shows up as value growth of the position. Work out how checkpoint should recognise it without crediting phantom gains
   (composition shifts with price) and without losing fee income. Prove it with tests where fees and a price move interact.
2. **`positionStateAt(refPrice)`**: composition of the held bins valued at the oracle price, not the pool price. Per bin the
   value is `L = p*x + y` and the held fraction is `shares / totalSupply`. Define the reference bin from the oracle price
   (`getIdFromPrice`) and compute what the position would hold if the pool were there.
3. **Removal floors and manipulation.** Concentrated bins are cheap to manipulate. Keep the guard's oracle anchor, the loss
   bounds on every add and every recenter, and `idSlippage`/`amountMin` set from oracle values, not from the pool.
4. **Recenter in bins.** Choose the bin step and a bin count (gas!). At step 10 (0.1% per bin), +/-12% is about 120 bins per side;
   at step 25 about 48. Measure gas per bin for add and remove on a fork and pick a bound the Robinhood block gas limit and
   the keeper's gas cap allow. Decide the shape (uniform vs weighted toward the centre), and how to avoid the active-bin
   composition fee on every recenter.
5. **Which pool.** Join `0x337c...` (bin step 10, about $31, stale price, owner-created, no bids below) or create a new pair at an
   open preset. The factory's `createLBPair` for an open preset may be permissionless: verify, and verify the activation price
   rules. Joining a stale pool means the first deposit is exposed to arbitrage against our own oracle gate.
6. **Isolation.** A second controller shares tokens but not markets. Decide what is shared (timelock, keeper wallet?) and what is
   not (vault, reserve, pTokens, controller, guard, adapter). Keep margin out of scope at first.

## 5. Risks to carry into every review

- **Ramses governance** is a 1-of-1 Safe with zero-delay controls, hooks, preset changes and a replaceable future implementation.
  Our vault can be frozen (not drained) by their configuration. Document it; consider an emergency exit that works without the
  router (call the pool directly) and a guardian pause.
- **Oracle gaps:** the NVDA feed is stale outside US hours (weekends 40h+); everything that needs the oracle fails closed. Same
  as the Uniswap vault; tests must include the stale-feed paths.
- **Tiny pool:** a few dollars of TVL means our own deposits move the active bin. Price impact and composition fees are real.
- **Two venues, one oracle:** both vaults read the same feed. A feed problem hits both.

## 6. Workflow and standards (lessons from the Uniswap build; follow them)

- Foundry, Solidity 0.8.26, `via_ir`. Keep contract runtime under EIP-170 and check `forge build --sizes`; move orchestration
  into linked libraries (as `RangeLib`) when needed. Stack-too-deep: split functions, use memory structs.
- **Tests, in this order:** unit tests with mocks of the DLMM pair (a faithful `LBPair` double: bins, shares, composition
  fees); fuzz tests for bin maths and distributions; **mainnet-fork tests** against the real factory, pair, router and quoter
  at a pinned block (`REMEDIATION_FORK_BLOCK` and the header's `l1BlockNumber` as `REMEDIATION_NATIVE_BLOCK`; mind that
  interest accrues on the L1-derived block number); invariant tests for the accounting; port the existing vault suites
  (`remediation/test/VaultV3*.t.sol`) to the new vault so inherited behaviour is covered.
- **Mutation-check every important fix:** revert the fix and confirm the test fails.
- **Review loop:** after each stage, run an adversarial review (a second model, read-only) and fix, with regression tests. Do not
  declare done after the first pass; three rounds found two high and several medium issues last time.
- **Forge traps:** `vm.expectRevert` is consumed by the next external call, including a `new` or a getter evaluated in the
  arguments; cache values first. Parallel tests share the process environment (`vm.setEnv`). `block.timestamp` can be cached
  across `vm.warp` under via-ir: use `vm.getBlockTimestamp()`. Public RPC prunes history: pin recent blocks and run with
  `--threads 1`. Never embed `type(X).runtimeCode` of a linked contract in a script.
- **Silent-failure traps:** `cast send` exits 0 for a mined revert: check the receipt status. Swallowing an inner revert
  hides failures: only swallow a named, narrow error. Send keeper transactions with an explicit gas limit.
- **Rollout:** deploy scripts verify preconditions and print what they will do; a Python verifier compares deployed runtimes
  with the reviewed build byte for byte; a local-fork rehearsal runs the real `forge script --broadcast` path before the
  operator signs. Never run anything that signs, never read `~/.foundry/keystores` or `.range-keeper-signer`.
- Publish through pull requests with green CI. Docs stay local except READMEs.

## 7. Phases and exit gates

| Phase | Deliverable | Gate |
| --- | --- | --- |
| 0 | `research/` notes: ABI of the live pair, router and quoter confirmed by fork calls; gas per bin; composition-fee behaviour; which implementation pools clone; whether `createLBPair` is open to us; a decision on problems 1 to 6 | The operator reads and approves the decisions |
| 1 | `DlmmPairedAdapter` plus a faithful DLMM double, unit and fork tests | Fork deposits and withdrawals round-trip within a stated tolerance, both token orders and the one-sided bins |
| 2 | `DlmmOracleGuard`, new vault (forked from V3), reserve, range library in bins | Ported V3 suites green, new invariants green, size under EIP-170 |
| 3 | Isolated lending: new controller, pTokens, interest model, price adapter, deployment scripts | Fork rehearsal of the full deployment and a supplier round trip |
| 4 | Cloud keeper (copy of `keeper/`), dedicated wallet, role-grant script | Local-fork rehearsal sends a real signed recenter; role least-privilege test |
| 5 | Adversarial review rounds, deployment verifier, runbook | No open high or medium findings; operator signs the queue |

## 8. Decisions the operator still has to make

1. Go or no-go on a venue with about $31 of TVL, no emissions and a 1-of-1 governance Safe, knowing the Uniswap vault
   itself earns cents at this size.
2. Join the existing bin-step-10 pool or create a fresh pair.
3. Whether the isolated controller should list only the two boosted markets or also plain (non-vault) markets.
4. Whether to share the timelock and the keeper-wallet design with the Uniswap system (recommended: share the timelock, use a
   new dedicated keeper wallet).
