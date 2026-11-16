# Evidence index

Evidence is no longer committed here (operator rule, 2026-11-16). Raw logs, attempt folders, broadcast journals, run outputs and recorded JSON live in the evidence store; this directory keeps one README per set with its SHA-256 manifest, so every record stays checkable from the repository alone.

Location of every set: evidence store `PeridotFinance/peridot-evidence` (private), path `PeridotV2-Robinhood/<set>/` — pending creation; export at `.scratch/evidence-export/` (operator machine). Until the store is verified the files also remain in git history at `5f542a0` (`git show 5f542a0:remediation/evidence/<file>`).

Records describe a block, a transaction or a test run at a point in time; they are history, not a live status page.

| Set | What it records | Files | Bytes | SHA-256 of `MANIFEST.sha256` |
| --- | --- | ---: | ---: | --- |
| [`adapter-install-2026-09-26`](adapter-install-2026-09-26/README.md) | Sep 26, 2026 containment pauses and lending price adapter installation | 54 | 113,094 | `8293b9fa2cfe4f00b97e554e08f29d5e124d6642a2895f61e78ea1ffd8c41fb3` |
| [`almanax-2026-10`](almanax-2026-10/README.md) | Almanax scans | 20 | 187,155 | `00b6871f98989e12bd3bb69670fa2f2e991882a99d1d81ab2828bf773ef67e2f` |
| [`attempts-2026-10-01`](attempts-2026-10-01/README.md) | The unsigned first short-open attempt (block 77254313) kept for the record, with its recovery note | 8 | 15,898 | `987441ee1d75fef1b372f8ba4b5b3fcfc0b21655d01129043ecca8cfd9ee1ac8` |
| [`canary-recovery-2026-10-01`](canary-recovery-2026-10-01/README.md) | Oct 1 canary residue recovery (simulation, broadcast, verification), settlement readiness and rehearsal, and the Oct 1 vault state | 16 | 34,753 | `27666a427fb35168de721c66f27d1673e3738481d772dcdefd78d6c387716c79` |
| [`governance-inventory-2026-09`](governance-inventory-2026-09/README.md) | Late-September authority inventory (roles and admins), keeper health and loss-reserve bounds at a point in time | 9 | 13,479 | `c57db1c18e09be91abedfef472fa37b9d52837dfccd907b8486db72b2181e181` |
| [`lending-acceptance-2026-09-30`](lending-acceptance-2026-09-30/README.md) | Operator lending round trip (0.001 NVDA supply, 0.05 USDG borrow) | 20 | 89,263 | `578a213ce7171a93503855324990d42807b3c0b61f1ed19bccca639950ec60ff` |
| [`lending-delegate-2026-10-02`](lending-delegate-2026-10-02/README.md) | Oct 2 lending delegate rounding fix | 27 | 110,235 | `962eb118a293952f9f6437c2d426263a6f7b2c0fab6e0918f5d4adc9c9b538e9` |
| [`lending-mint-router-2026-10-01`](lending-mint-router-2026-10-01/README.md) | Optional LendingMintRouter (not deployed on mainnet) | 8 | 10,203 | `4216554e7692875e97af99e79187b28b4ff65c685298b3049e71805f60526909` |
| [`lending-reactivation-2026-09-29`](lending-reactivation-2026-09-29/README.md) | Sep 29 lending reactivation | 5 | 19,785 | `aef137b088ce6c580fc1a005c41acb811923d146e01b44e21e134f3135d7658a` |
| [`margin-acceptance-2026-09-30`](margin-acceptance-2026-09-30/README.md) | Operator long (Sep 30) and short (Oct 1) margin round trips | 70 | 595,131 | `048b0cd8de4fc6885fc754718502526e5a495321efda31c8add8ffe888494db2` |
| [`margin-deployment-rehearsal-2026-09`](margin-deployment-rehearsal-2026-09/README.md) | Run outputs from the original margin deployment preparation, previously under contracts/robinhood-vaults/deployments/ | 5 | 1,055,697 | `4cad1af0624707d1da671d09ac3e83eb361fb47f20b2aafd67cc5b02c7963ed6` |
| [`reopen-allocation-2026-10`](reopen-allocation-2026-10/README.md) | Reopening LP allocation | 6 | 14,061 | `8c07d093760d4e224c26a68215174e1f01fd43fe564c9d84364bf7d922cb5e5b` |
| [`vault-upgrade-2026-09-26`](vault-upgrade-2026-09-26/README.md) | Vault V2 (native backing) | 22 | 127,777 | `af41ebc5963ff7d57732c7c3cd12c27b40f88c6ac9f3d2a4e827ec07fc894d0a` |
| [`vault-yield-2026-10`](vault-yield-2026-10/README.md) | Output of the read-only yield recorder (remediation/tools/vault_yield.py) | 5 | 10,610 | `a7fc8b1313e8b13ebeb8258a176798185ee12de2da96c10b6dcbde408217a42e` |
| **Total** | | **275** | **2,397,141** | |

## Check a set

Download the set directory from the store, then run `shasum -a 256 -c MANIFEST.sha256` inside it and compare `shasum -a 256 MANIFEST.sha256` with the value above (and with the manifest printed in the set README). Each original record also keeps its detached `.sha256` inside the set.

## Tools

The tools in `remediation/tools` write new records to `$PERIDOT_EVIDENCE_DIR` (default `.scratch/evidence/` at the repository root, git-ignored) and read prior records from the same directory, failing with a message naming the missing file. That directory is flat: a tool that reads a prior record needs that file copied into it from its set (for example `record_vault_execution.py` reads `vault-upgrade-queue.json` from `vault-upgrade-2026-09-26` and `installed-adapter.json` from `adapter-install-2026-09-26`). New records go to the store as a new set with a README and manifest here.
