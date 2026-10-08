# Remediation

Corrections, tests and verification written on top of the frozen deployment sources. Nothing under `contracts/` is edited here.

## What is in this folder

| Path | Contents |
| --- | --- |
| [`src`](src) | `RobinhoodBoostedVaultV2` (native-backing correction), `RobinhoodLendingPriceAdapter` (USDG unit fix), `RobinhoodBoostedDelegateV2` (zero-share and rounding fix), `LendingMintRouter` (optional min-shares bound), and the concentrated-liquidity package (`RobinhoodBoostedVaultV3`, `UniswapV4PairedAdapterV3`, `libraries/RangeLib`), written and tested, not executed |
| [`script`](script) | Reviewed, user-signed procedures: vault upgrade, lending reactivation, delegate deploy and install, plus prepared-but-unexecuted LP reopen, margin cap raise and router deploy |
| [`test`](test) | Unit, fuzz and invariant tests, and fork tests against the live deployment |
| [`tools`](tools) | Read-only verifiers, rehearsal runners and the vault yield recorder |
| [`evidence`](evidence/README.md) | Dated records of every verification, with detached SHA-256 digests |
| [`dune`](dune/README.md) | Dune queries for the vault's events |

## Executed on mainnet, then verified independently

| Change | Date |
| --- | --- |
| Lending price adapter installed; borrowing and ordinary seizure paused during the fix | Sep 2026 |
| Vault V2 installed through the timelock | Sep 26, 2026 |
| Lending reactivation (five calls) | Sep 29, 2026 |
| Operator lending and margin round trips | Sep 30 to Oct 1, 2026 |
| Lending delegate rounding fix: deploy at block 78,172,768, installs at 78,176,131 and 78,176,155 | Oct 2, 2026 |

## Written and tested, not executed

LP allocation reopen (timelock queue, execute, keeper checkpoint and rebalance), a flash-vault-funded margin cap raise, the min-shares mint router, and the Safe governance migration. Their scripts refuse to run unless the preconditions hold.

## Concentrated liquidity (V3): written and tested, not executed

The live LP position is a single full-range Uniswap v4 position, which earns little on a small pool. The V3 package lets the vault hold ONE position over a band around the oracle price (default about +12.7% / -11.3%) and lets a restricted keeper recenter it. It is NOT deployed or queued; the live vault is still V2.

- **What the contracts enforce, not the keeper:** the new range is always the rounded oracle centre plus or minus a governance-set width; a recenter is only allowed after a cooldown and a price move (or once the price leaves the range); at most N recenters in any rolling 24 hours (a ring buffer, not a fixed window); the pair is checkpointed at the oracle price before and after; the oracle-valued loss of a recenter or of a ranged deployment is bounded in bps; the ranged value is capped; and the removal floors follow the token order and the guard's removal gate. The keeper can only call `recenter(pairId, deadline)` and `rebalance`; it chooses no price, range or amount.
- **Governance:** `setRangePolicy` is `CONFIG_ROLE` (the timelock). Upgrading the adapter and vault proxies and setting the policy is one atomic `scheduleBatch` (`script/UpgradeConcentratedLiquidity.s.sol`), so the delay is the timelock's existing one hour. Nothing else changes: pauses, caps, roles, lending and margin are untouched.
- **Known limits:** it does not make small pools profitable (Astra and the economics estimate put it near cents per year at a few dollars); concentration increases impermanent loss in proportion; when the price leaves the range one token is fully converted and, with settlement swaps paused, that side is illiquid (not lost) until the price returns or a recenter re-pairs it; if the guard's removal gate is later widened beyond what the registered tolerance covers, ranged exits and recenters are blocked until the range is cleared; the vault runtime is within about 170 bytes of the EIP-170 limit.
- **Evidence:** 9 library unit tests, the existing V2 unit, recovery, post-exit and invariant suites re-run against V3 (127 tests plus 3 invariants), 33 mainnet-fork tests (live proxies upgraded through the timelock, recenter in both directions, pool shocks, redemptions through the ranged LP, emergency exit, the rollout scripts end to end) and a local-fork rehearsal of the real `forge script --broadcast` path with `tools/verify_concentrated_liquidity.py` matching the deployed runtimes byte for byte. Three rounds of adversarial review by a second model found two high and several medium issues, all fixed with regression tests. This is not an independent audit.
- **Keeper:** `tools/range_keeper.py` is read-only by default: it simulates the real call and sends only with `--execute`, signing through the operator's own encrypted Foundry keystore.

```sh
make test-concentrated   # unit suites, size gate and keeper tests (no RPC)
make fork-concentrated   # fork suites; needs REMEDIATION_FORK_BLOCK and REMEDIATION_NATIVE_BLOCK
```

## Commands

```sh
make test-remediation        # vault, adapter and recovery suites plus Python tooling
make test-lending-candidate  # delegate, router and rounding suites, size gate and verifier
python3 remediation/tools/vault_yield.py report   # read-only yield report
```

Fork rehearsals need an archive-capable `ROBINHOOD_RPC_URL`. The delegate rehearsals assert the pre-install code hash on purpose.

## Review limits

No independent external audit. An Almanax scan reported 81 automated findings; only part of it has been triaged (the three high-severity claims and two medium ones), and that is not a claim that the rest are resolved.
