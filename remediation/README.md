# Remediation

Corrections, tests and verification written on top of the frozen deployment sources. Nothing under `contracts/` is edited here.

## What is in this folder

| Path | Contents |
| --- | --- |
| [`src`](src) | `RobinhoodBoostedVaultV2` (native-backing correction), `RobinhoodLendingPriceAdapter` (USDG unit fix), `RobinhoodBoostedDelegateV2` (zero-share and rounding fix), `LendingMintRouter` (optional min-shares bound) |
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
| LP allocation reopened: queue (80,853,835), execute (81,683,197), checkpoint (81,684,587), rebalance (81,684,611); about $4 of liquidity | Oct 6, 2026 |

## Written and tested, not executed

A flash-vault-funded margin cap raise, the min-shares mint router, and the Safe governance migration. Their scripts refuse to run unless the preconditions hold.

## Commands

```sh
make test-remediation        # vault, adapter and recovery suites plus Python tooling
make test-lending-candidate  # delegate, router and rounding suites, size gate and verifier
python3 remediation/tools/vault_yield.py report   # read-only yield report
```

Fork rehearsals need an archive-capable `ROBINHOOD_RPC_URL`. The delegate rehearsals assert the pre-install code hash on purpose.

## Review limits

No independent external audit. An Almanax scan reported 81 automated findings; only part of it has been triaged (the three high-severity claims and two medium ones), and that is not a claim that the rest are resolved.
