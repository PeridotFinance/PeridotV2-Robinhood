# Lending delegate rounding correction: INSTALLED on both markets

**Status: INSTALLED AND INDEPENDENTLY VERIFIED on pNVDA and pUSDG, October 2, 2026.** The governor signed three transactions locally; this repository's tooling signed and broadcast nothing. Both markets now run the corrected delegate `0x0C6F6962d80390F6104f80811a150e8233bd8FF1`. The min-shares router described below is **not** deployed, the wider Almanax scan is only partially triaged, and the residual risks listed below remain.

## Installation record

| Step | Transaction | Block (UTC) |
| --- | --- | --- |
| Deploy delegate (also creates its accounting module `0xbE99A699E81AB667C0Be517E6AAe4d92EA6C4557`) | `0xc23e437298c6c0430f4e0a0749167875b934859f5cfd48ccd8478067cb812edd` | 78,172,768 (10:50:15) |
| Install on pNVDA `0xa155…7b26` | `0xce623cdbcc51d2c7d0b4b631e3f0e01c54760f3d0c27f77a357bb189c588fa95` | 78,176,131 (10:55:56) |
| Install on pUSDG `0x55aE…a563` | `0x8f0ad03fb7363dc03e1fb7cca4ac5b7f4902ec92209a53505debd9cf237477fc` | 78,176,155 (10:55:59) |

Independently verified from public chain data ([deployment check](evidence/lending-delegate-deployment-check.json), [install verification](evidence/lending-delegate-install-verified.json), [pre-install snapshot](evidence/lending-delegate-pre-install-state.json), [broadcast journals](evidence/lending-delegate-mainnet-broadcast/)):

- The deployed runtime (24,572 bytes), its module, and the creation input equal the compiled reviewed build; the deployed codehash is `0x0528316d…11d8`.
- Each install was exactly `_setImplementation(delegate, false, "")`, from the governor, to the market, with status success.
- Both markets' `implementation()` and implementation slot equal the delegate.
- **Across each install block, every storage slot (0 to 28 except the implementation slot), the exchange rate, total supply, total borrows, reserves, borrow shares and the governor's share and borrow balances were identical.** Live debt at the time (pNVDA about 1.461e13 raw, pUSDG 5,961 raw) was untouched.
- A read-only `mint(0)` simulation on both markets now reverts with `ZeroSharesMinted()` (selector `0xd6a0a041`).

Not independently re-tested on mainnet: the rounded-up `redeemUnderlying` burn and the zero-share dust revert were validated on local forks of live state only, and I did not send any mainnet mint or redeem. The fork rehearsal suites assert the pre-install codehash in `setUp`, so they can be rerun only against a block before 78,176,131 (an archive-capable RPC); against the current chain they stop at that assertion by design. The sections below describe the procedure and evidence **as they were before installation**.

This addresses two **confirmed** findings from the partially triaged Almanax scan (see [ALMANAX_TRIAGE.md](ALMANAX_TRIAGE.md)). The scan reported 81 automated findings; most are not reviewed, and this change does not claim the review is complete.

| Finding | Evidence | Candidate behaviour |
| --- | --- | --- |
| A small deposit transfers underlying but mints zero pTokens | Mainnet-fork diagnostic: 200,000,899 raw NVDA units mint zero shares at the pinned state | `mint` reverts `ZeroSharesMinted()`; the whole transaction rolls back |
| Exact-underlying redemption rounds the shares burned **down**; in a local near-empty-market reproduction a donation lets a withdrawal leave debt under-collateralized | Local reproduction with the captured real controller and delegate. **Conditional local evidence, not an observed mainnet attack**; permanent seed liquidity has not been established | `redeemUnderlying` burns `ceil(amount * 1e18 / rate)` shares, at the rate after vault settlement and before the controller's collateral check |

## What changed, and what deliberately did not

[`RobinhoodBoostedDelegateV2.sol`](src/RobinhoodBoostedDelegateV2.sol) is a copy of the frozen delegate with three edits, shown exactly in [`lending-delegate-candidate-source.diff`](evidence/lending-delegate-candidate-source.diff): the import paths and name, a zero-share check in `mint`, and a rounded-up share count in `_redeemUnderlyingLossAware`. The frozen files under `contracts/` are byte-identical to the captured inputs (verified against the input manifest and `git diff HEAD`).

- **No storage change, no new external function.** Storage layout is identical in both builds and every existing selector and ABI entry is preserved. The only ABI addition is the revert reason `ZeroSharesMinted()`.
- Legacy `mint(uint256)` keeps its `NO_ERROR` (0) success return.
- Share-denominated `redeem(shares)` is unchanged and still rounds the payout down.

### Requirement change: no `mintWithMinShares`

The earlier handoff included a `mintWithMinShares(uint256,uint256)` entry point with a caller-selected minimum. **It is not in this candidate**, because it cannot fit under the contract size limit. This is a measured constraint, not a preference:

| Build | Original delegate | Candidate |
| --- | --- | --- |
| `lending_upgrade` (the captured deployment's settings, with trailing metadata) | 24,511 bytes | **24,625** (over EIP-170 by 49) |
| `lending_candidate` (same settings, metadata omitted) | 24,458 | **24,572** (4 bytes under 24,576) |

Intermediate measurements under the captured settings: the ceiling-division fix alone is 24,570 to 24,599 depending on how it is written; adding the zero-share check reaches about 24,650; adding the new entry point reaches about 25,086. The original has only 65 bytes of headroom. A refactor to deduplicate the redemption tail made the result larger. The size limit was not raised and no behaviour was removed to reach it.

The release build therefore omits trailing metadata (about 53 bytes). That is a real reduction in deployed bytes, and it has a consequence: explorer verification must use the same settings (`bytecodeHash = none`, `appendCBOR = false`; profile `lending_candidate` in `foundry.toml`). **Four bytes of headroom means any further change to this contract needs size work first.**

A caller minimum is instead offered by a separate small router, [`LendingMintRouter`](src/LendingMintRouter.sol), described [below](#optional-min-shares-router). It is a separate artifact that needs its own deployment, and it is not part of this candidate.

## Immutables: the verifier's assumption was wrong

The first verifier run failed with `Unexpected immutables`. The assertion assumed the delegate has none. It does: `PToken.sol` declares `BORROW_ACCOUNTING_MODULE = new BorrowAccountingModule()` as an immutable, so **the delegate constructor deploys its own accounting module** and bakes that address into the runtime at four positions; the module has its own `SELF` immutable. The **original has the identical immutable**. The verifier now checks that the candidate's immutable structure and declarations equal the original's (no immutable is added, removed or moved), rather than asserting there are none.

Consequences for deployment: deploying the candidate creates two contracts, and the candidate's runtime hash depends on the module address (which depends on the deployer nonce). The installed markets will point at a freshly deployed module with the same logic; the module logic is verified identical across builds, and the old module stays deployed and unused.

## Reproduction anchor

Compiling the **frozen original** under `lending_upgrade` and filling its immutable with the installed module reproduces the installed delegate (24,511 bytes) and module (2,752 bytes) **byte for byte except the 32-byte metadata hash** ([evidence](evidence/lending-delegate-installed-anchor.json)). So the captured settings are the real ones, and the candidate is compared against the true deployed logic. The installed delegate codehash is `0xa6913bd5…cc34` on both markets.

## Validation

All results are local or on a **local fork**; no transaction was signed or broadcast, and no governor credential was accessed.

| Check | Result |
| --- | --- |
| `LendingRounding.t.sol` (frozen controller + delegate; unit model, 18- and 6-decimal) | 17 passed |
| `LendingRoundingVault.t.sol` (mock vault: strict mint valuation, loss settlement) | 9 passed |
| Same suites at 5,000 fuzz runs ([log](evidence/lending-rounding-tests.txt)) | 26 passed, 0 failed |
| Router tests (`LendingMintRouter.t.sol`, 5,000 fuzz runs; [log](evidence/lending-mint-router-tests.txt)) | 10 passed, 0 failed |
| Local mainnet-fork upgrade rehearsal at the latest block ([log](evidence/lending-delegate-upgrade-fork.txt)): 12 upgrade/preservation/script tests, no oracle mocks; plus 9 tests driving the **live margin stack** with the candidate installed and 2 router tests on live markets | 22 passed |
| Mined local rehearsal: deploy, independent verify, install both markets, repeat as no-op ([log](evidence/lending-delegate-anvil-rehearsal.txt)) | Rate, supply and borrows unchanged on both markets |
| Gates: storage, selectors, ABI, immutables, module logic, settings, size ([report](evidence/lending-delegate-candidate-gates.json)) | All pass |
| Existing remediation suites (222 tests), Python tools (36 + 3 + 6 new), frozen snapshot (216 files, 26 artifacts, 16 ABIs) | All pass |

Note on the baseline tests: `LendingRounding.t.sol` was extended and then reformatted with `forge fmt`. Before formatting, the two original baseline test bodies were compared with the saved copy and are **byte-identical**; the file hash recorded in `almanax-rounding-baseline.json` no longer matches the extended file, and that record is historical.

Mutation check: replacing the ceiling with a floor fails 8 tests across the two suites; removing the zero-share check fails 5. The fuzz property (`burned * rate >= amount * 1e18` and `(burned - 1) * rate < amount * 1e18`) runs against the exact form in the contract, including with a loss realized during settlement.

What the tests establish:

- **Both decimal configurations.** USDG with 6 decimals: one raw unit mints 5,000 shares at the model rate, and `redeemUnderlying(3)` after a donation burns 15,000 (the ceiling of 14,999.9985). On the live fork, one raw USDG unit still mints shares and NVDA dust below one share is rejected atomically with no transfer.
- **The low-supply collateral case.** With the original delegate a donation lets a withdrawal leave a 0.35 USD shortfall; with the candidate the same redemption is rejected by the real controller (`RedeemPeridottrollerRejection(4)`) with nothing changed.
- **Upgrade preservation.** Slots 0 to 28 except the implementation slot, balances, borrow balance, reserves, vault accounted and liquid assets, pair id, buffer and pause flag are identical before and after on both markets. A borrow taken before the upgrade is repaid after it, and a fresh borrow after it works through the new module.
- **Vault loss settlement.** When a vault withdrawal realizes a loss during `redeemUnderlying`, every holder bears it before shares are priced, the exact output is still delivered, and the burn is the ceiling at the post-loss rate (original: floor, so exactly one share different). Share-denominated redemption pays the identical post-loss amount on both delegates.
- **Strict mint valuation.** With the vault claim unavailable, a mint reverts instead of pricing without the claim, while redemption stays available at the conservative claim-excluded rate. Mint prices against an appreciated claim.

What they do not establish: loss recognition and strict valuation use a mock vault, not the live vault's internals; the live fork has no open LP liquidity to realize a real loss against. No test shows how today's actual mainnet holders behave, and no minimum permanent supply was established.

## Callers other than users: the live margin stack

The candidate's `mint` now reverts on zero shares (including `mint(0)`, which the original accepted as a no-op), and exact-underlying redemption burns more. Margin positions are the only other callers, so every call site was read and the live stack was exercised.

**Call sites** (`peridot-contracts-2-5/.../margin/`): the stack calls `mint` and the share-denominated `redeem(shares)`. **Nothing calls `redeemUnderlying`**, so the rounded-up burn does not reach margin. The only enabled pairs are `(pUSDG margin, pNVDA position, pUSDG debt)` and `(pUSDG margin, pUSDG position, pNVDA debt)`: **margin is always pUSDG**, so margin re-mints can only break on a zero amount, never on dust (one raw USDG unit is already about 5,000 shares).

| Site | What it mints | Effect of the candidate |
| --- | --- | --- |
| Executor open | Position token, already rejects `minted == 0` (`ExecutorError(36)`) | Same outcome, different revert reason |
| Executor `repayWithPToken` remainder | Debt token; for a short that is **NVDA**, so the remainder can be sub-share dust | **Reverts on both delegates**: the original already fails (`RiskEngine: zero movement`), the candidate fails earlier with `ZeroSharesMinted`. No new failure (tested) |
| Executor close with debt (flash path) | Margin pUSDG, guarded by `> 0` | None |
| Executor `_closeWithoutDebt` | Margin pUSDG, **unguarded** | Reverts only if the redeemed position is worth less than one raw USDG unit (about $0.000001). Not exercised; treated as theoretical |
| Liquidator | Already pre-checks `floor(amount * 1e18 / rate) == 0` and returns the underlying instead of minting | Unreachable |

**Live-stack tests** (`LendingDelegateMarginCompat.t.sol`, local fork at the latest block, candidate installed on both markets, real executor, liquidator, margin vault, risk engine, quoter, swap module and flash vault): long and short round trips, partial then full close in both directions, repay with underlying then debt-free exit, `repayWithPToken` remainder on USDG debt, and **severe-shock liquidation in both directions** (a pool move plus a feed answer that tracks it, fork only). All pass, with market-wide borrows returning to their starting values. The suite skips loudly when the stock feed is stale because the margin oracle fails closed, so check the log shows these as run, not skipped. Local conveniences: actor top-up with `deal`, and the shock driver. These tests do not exercise LP-boosted liquidation, because allocation is currently paused with no open LP liquidity.

Residual: a position so small that closing returns less than a raw USDG unit would now revert on close instead of closing with a dust loss. It can still be exited through `repayWithUnderlying` and `exitDebtFreeToPTokens`, or liquidated.

## Optional min-shares router

**Status: written and tested, NOT deployed.** [`LendingMintRouter`](src/LendingMintRouter.sol) gives callers the `mintWithMinShares(market, amount, minShares)` bound that did not fit in the delegate. It is stateless and ownerless (no admin, no upgrade path, no stored balances) and accepts only the two fixed markets given at construction, so a caller cannot make it approve or call an arbitrary contract. It pulls exactly `amount`, supplies it, checks the share delta against `minShares` (which must be at least 1), forwards every share to the caller, revokes the approval and reverts if anything is left behind. Fee-on-transfer underlying is rejected.

It works against **both the original and the corrected delegate**: against the original, requiring a minimum of at least one share blocks the zero-share mint on its own, so it protects users even before the delegate is installed. It does not remove the need for the delegate fix, because direct callers of `mint` bypass it.

Validation: 10 local tests on both delegates at 5,000 fuzz runs (normal mint, nonzero rounding loss rejected, zero-share mint blocked, 6-decimal minimums, input checks, fee-on-transfer, stray balances, and a property that the outcome equals the floor estimate versus the minimum); two live-fork tests on the installed original delegate and with the candidate installed; a mined local deploy-and-verify rehearsal ([log](evidence/lending-mint-router-anvil-rehearsal.txt)), which also minted through it and left the router with no residue. Removing the minimum check fails 4 tests and removing the transfer check fails 1.

Deploy and verify (the router has no privileges, so any account can deploy it):

```sh
FOUNDRY_PROFILE=lending_candidate forge script \
  remediation/script/DeployLendingMintRouter.s.sol:DeployLendingMintRouter \
  --rpc-url https://rpc.mainnet.chain.robinhood.com \
  --sender 0x94696d767e65a75581145646960FA0eC886cE5d2
# then the same command with: --account robinhood-deployer --broadcast --slow
python3 remediation/tools/verify_lending_mint_router.py --router <address> --tx <deployment tx hash>
```

The verifier compares the deployed runtime with the compiled artifact with both market immutables filled in. Users approve the router for the underlying, then call `mintWithMinShares`; the frontend should not use it until the deployment is verified and announced.

Residual: the router adds a contract to trust and to audit, and a user's approval to it is spent only by the router's own logic. It does not bound the exchange rate between quote and execution beyond the stated minimum.

## Residual risks

1. **Legacy `mint` has no caller slippage bound.** Rejecting zero shares does not bound a nonzero rounding loss. After a donation a 1e18 deposit can price to a single share and is accepted (pinned by `testResidualRiskLegacyMintAcceptsNonzeroRoundingLoss`). Until a bound exists, the frontend should compute expected shares from the current exchange rate and refuse or warn when the result is a small fraction of the deposit.
2. **Donation amplification is reduced, not eliminated.** The collateral shortfall path is closed for exact-underlying redemption. Permanent seeding or virtual accounting would need separate design and migration review.
3. **Four bytes of size headroom** (above).
4. **Ceiling arithmetic edge.** `(amount * 1e18 + rate - 1) / rate` overflows only for amounts around 1.16e59 raw units or more, unreachable for these tokens. The original's `div_` also reverts on that overflow. A zero rate would panic with code 0x11 instead of 0x12; both revert.
5. **Exact-output redemptions can now revert where they previously succeeded**, when a loss realized during settlement makes the requested output worth more shares than the holder has. Maximum withdrawals should use `redeem(allShares)`.
6. **The verified module logic is identical, but the module address changes.** Anything that pinned the old module address would need updating; nothing in this repository does.
7. **`mint(0)` now reverts.** Anything that sends a zero-amount supply as a no-op will fail; no repository caller was found that does so on a reachable path.
8. **Not an audit.** The wider scan, the vault, the controller and margin code are not covered here.

## Deployment procedure (executed October 2, 2026; kept for reproduction, do not rerun)

Both markets are admin-controlled by the governor EOA (no timelock on `_setImplementation`), so each market is one transaction. No pause is required: each swap is atomic, and during the gap between the two transactions one market runs the original code and the other the candidate, which is harmless. Install pNVDA first: it is the market with the 18-decimal dust exposure. Do not rerun anything that already completed; each stage is safe to re-simulate.

**0. Local gates (no network, or read-only).**

```sh
make test-lending-candidate
make fork-lending-candidate        # local fork only; repins to latest state
python3 remediation/tools/verify_lending_delegate_candidate.py --rpc https://rpc.mainnet.chain.robinhood.com
```

**1. Deploy the candidate.** First simulate (no key, no broadcast); then you sign locally:

```sh
FOUNDRY_PROFILE=lending_candidate forge script \
  remediation/script/UpgradeLendingDelegate.s.sol:DeployLendingDelegate \
  --rpc-url https://rpc.mainnet.chain.robinhood.com \
  --sender 0x94696d767e65a75581145646960FA0eC886cE5d2
# then the same command with: --account robinhood-deployer --broadcast --slow
```

The script refuses any other build profile (it pins the candidate's creation-code hash `0xe6784d37…96e6`), requires both markets to still run the reviewed original, and sends one transaction.

**2. Verify the deployment independently** with the transaction hash. This derives the expected runtime hash from the compiled artifacts, not from the deployed contract, and prints the environment for stage 3:

```sh
python3 remediation/tools/verify_lending_delegate_deployment.py \
  --delegate <deployed address> --tx <deployment tx hash>
```

Do not proceed unless every check is `true`.

**3. Install on both markets.** Simulate, then sign locally with the printed environment:

```sh
export NEW_LENDING_DELEGATE=<from stage 2>
export EXPECTED_LENDING_DELEGATE_CODEHASH=<from stage 2>
FOUNDRY_PROFILE=lending_candidate forge script \
  remediation/script/UpgradeLendingDelegate.s.sol:InstallLendingDelegate \
  --rpc-url https://rpc.mainnet.chain.robinhood.com \
  --sender 0x94696d767e65a75581145646960FA0eC886cE5d2
# then the same command with: --account robinhood-deployer --broadcast --slow
```

It sends up to two transactions, fingerprints each market before and after (every storage slot except the implementation slot, rate, supply, borrows, reserves) and skips a market already on the candidate, so an interrupted run can be repeated.

**4. Verify the installation read-only.** Independently confirm, from the public chain: both markets' `implementation()` equals the verified delegate; its codehash equals the expected hash; `exchangeRateStored`, `totalSupply`, `totalBorrows` and the governor's balances match the pre-install values; the receipts match the simulated calldata. This was done and recorded in the installation record above.

**Rollback.** The original delegate stays deployed. `_setImplementation(0x31d7…2d99, false, "")` per market restores it, because storage is identical. That reinstates both findings and should be used only if the candidate misbehaves.

## Message for the frontend developer

> **Lending contracts: the rounding fix is now INSTALLED on both mainnet markets (verified, blocks 78,176,131 and 78,176,155).** The min-shares router is prepared but not deployed.
>
> What changed: addresses and ABI are unchanged (same pNVDA and pUSDG proxies; no new functions). There is one new revert reason, `ZeroSharesMinted()`: a supply that would credit zero pTokens now reverts instead of taking the underlying, and `mint(0)` now reverts too. There is no `mintWithMinShares`; it did not fit the contract size limit.
>
> 1. **Supply:** the delegate's `mint` still has no minimum-received bound. A separate router with a bound is prepared but not deployed; do not use it until I confirm. Until then, estimate pTokens as `floor(amount * 1e18 / exchangeRate)`, reading the rate with a static `eth_call` to `exchangeRateCurrent` (it is not a view function) or from `exchangeRateStored`, and warn or block when the result is far below the deposit's fair value, especially for tiny NVDA amounts (USDG has 6 decimals, NVDA 18, both pTokens 8). Simulating the `mint` and reading the `balanceOf` delta also works.
> 2. **Max withdraw:** use `redeem(allShares)`, not `redeemUnderlying(quotedValue)`. `redeemUnderlying` now burns the rounded-up share count and can revert if a loss recognized during settlement raises the shares needed.
> 3. Show `ZeroSharesMinted` as "amount too small", and do not send zero amounts.

## Evidence

[candidate gates](evidence/lending-delegate-candidate-gates.json) · [installed-bytecode anchor](evidence/lending-delegate-installed-anchor.json) · [source diff](evidence/lending-delegate-candidate-source.diff) · [test log](evidence/lending-rounding-tests.txt) · [fork rehearsal](evidence/lending-delegate-upgrade-fork.json) · [mined local rehearsal](evidence/lending-delegate-anvil-rehearsal.txt), [its deployment check](evidence/lending-delegate-anvil-deployment-check.json) and [install journal](evidence/lending-delegate-anvil-rehearsal/stage2-install-run.json). Each has a detached `.sha256`. The local rehearsal's delegate address is a local-fork address, not a mainnet deployment.
