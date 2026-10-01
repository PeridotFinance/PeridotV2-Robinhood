# Lending delegate rounding correction: tested candidate

**Status: TESTED CANDIDATE. NOT DEPLOYED. NOT INSTALLED.** As of Robinhood Chain block 77,342,338 both installed markets still run the original delegate `0x31d7C960C1EB542e4243e80D2270220e63002d99`. Nothing in this document, the evidence or the local fork rehearsals changes that. A tested candidate is not an installed correction: it takes effect only after the governor signs the two procedure stages below and the result is independently verified.

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

A caller minimum could be offered later by a small stateless periphery contract (pull underlying, call `mint`, check the share delta, forward the shares). That would be a separate artifact needing its own review, and it is not part of this candidate.

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
| Local mainnet-fork upgrade rehearsal at the latest block ([log](evidence/lending-delegate-upgrade-fork.txt)): 12 upgrade/preservation/script tests, no oracle mocks; plus 9 tests driving the **live margin stack** with the candidate installed | 21 passed |
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

## Residual risks

1. **Legacy `mint` has no caller slippage bound.** Rejecting zero shares does not bound a nonzero rounding loss. After a donation a 1e18 deposit can price to a single share and is accepted (pinned by `testResidualRiskLegacyMintAcceptsNonzeroRoundingLoss`). Until a bound exists, the frontend should compute expected shares from the current exchange rate and refuse or warn when the result is a small fraction of the deposit.
2. **Donation amplification is reduced, not eliminated.** The collateral shortfall path is closed for exact-underlying redemption. Permanent seeding or virtual accounting would need separate design and migration review.
3. **Four bytes of size headroom** (above).
4. **Ceiling arithmetic edge.** `(amount * 1e18 + rate - 1) / rate` overflows only for amounts around 1.16e59 raw units or more, unreachable for these tokens. The original's `div_` also reverts on that overflow. A zero rate would panic with code 0x11 instead of 0x12; both revert.
5. **Exact-output redemptions can now revert where they previously succeeded**, when a loss realized during settlement makes the requested output worth more shares than the holder has. Maximum withdrawals should use `redeem(allShares)`.
6. **The verified module logic is identical, but the module address changes.** Anything that pinned the old module address would need updating; nothing in this repository does.
7. **`mint(0)` now reverts.** Anything that sends a zero-amount supply as a no-op will fail; no repository caller was found that does so on a reachable path.
8. **Not an audit.** The wider scan, the vault, the controller and margin code are not covered here.

## Deployment procedure (governor signs locally; none of it has been run on mainnet)

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

**4. Verify the installation read-only.** Independently confirm, from the public chain: both markets' `implementation()` equals the verified delegate; its codehash equals the expected hash; `exchangeRateStored`, `totalSupply`, `totalBorrows` and the governor's balances match the pre-install values; the receipts match the simulated calldata. Do not claim the correction is installed until this is recorded. Record it as new evidence (do not edit this document's "not deployed" status until then).

**Rollback.** The original delegate stays deployed. `_setImplementation(0x31d7…2d99, false, "")` per market restores it, because storage is identical. That reinstates both findings and should be used only if the candidate misbehaves.

## Message for the frontend developer

> **Lending contracts: rounding fix is prepared but NOT deployed yet.** Nothing changes on mainnet until the governor installs it, and I'll tell you when that is verified. Plan the integration for after that.
>
> After installation: addresses and ABI are unchanged (same pNVDA and pUSDG proxies; no new functions). There is one new revert reason, `ZeroSharesMinted()`: a supply that would credit zero pTokens now reverts instead of taking the underlying, and `mint(0)` now reverts too. There is no `mintWithMinShares`; it did not fit the contract size limit.
>
> 1. **Supply:** `mint` still has no minimum-received bound. Estimate pTokens as `floor(amount * 1e18 / exchangeRate)`, reading the rate with a static `eth_call` to `exchangeRateCurrent` (it is not a view function) or from `exchangeRateStored`, and warn or block when the result is far below the deposit's fair value, especially for tiny NVDA amounts (USDG has 6 decimals, NVDA 18, both pTokens 8). Simulating the `mint` and reading the `balanceOf` delta also works.
> 2. **Max withdraw:** use `redeem(allShares)`, not `redeemUnderlying(quotedValue)`. `redeemUnderlying` now burns the rounded-up share count and can revert if a loss recognized during settlement raises the shares needed.
> 3. Show `ZeroSharesMinted` as "amount too small", and do not send zero amounts.

## Evidence

[candidate gates](evidence/lending-delegate-candidate-gates.json) · [installed-bytecode anchor](evidence/lending-delegate-installed-anchor.json) · [source diff](evidence/lending-delegate-candidate-source.diff) · [test log](evidence/lending-rounding-tests.txt) · [fork rehearsal](evidence/lending-delegate-upgrade-fork.json) · [mined local rehearsal](evidence/lending-delegate-anvil-rehearsal.txt), [its deployment check](evidence/lending-delegate-anvil-deployment-check.json) and [install journal](evidence/lending-delegate-anvil-rehearsal/stage2-install-run.json). Each has a detached `.sha256`. The local rehearsal's delegate address is a local-fork address, not a mainnet deployment.
