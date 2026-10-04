# Evidence index

Dated verification records. Each `*.json` or `*.txt` has a detached `.sha256`. Files describe a block, a transaction or a test run at a point in time; they are history, not a live status page. Read the newest record in each family.

| Family (file prefix) | What it records |
| --- | --- |
| `lending-delegate-*`, `lending-rounding-*` | The Oct 2, 2026 market delegate fix: candidate gates, bytecode anchor to the installed code, deployment check, pre-install snapshot, mainnet broadcast journals, `lending-delegate-install-verified.json`, test and fork logs |
| `lending-mint-router-*` | The optional mint router: tests and a local-fork deploy and verify rehearsal (not deployed on mainnet) |
| `almanax-*` | Partial triage of the Almanax scan: raw findings, mainnet-fork diagnostics, storage comparison, seed observation |
| `vault-upgrade-*`, `vault-v2-*`, `vault-queue-*`, `queued-vault-*`, `vault-fork-*`, `vault-layout-*` | Vault V2: timelock queue, execution, layout and ABI checks, fork results |
| `installed-adapter`, `installation-*`, `install-*`, `containment-*` | The lending price adapter installation and the containment pauses that preceded it |
| `reactivation-*` | The Sep 29 lending reactivation: readiness, simulation, execution |
| `lending-acceptance-*`, `margin-acceptance-*`, `margin-long-*`, `margin-short-*` | Operator lending and margin round trips with intents, simulations, broadcast receipts and verification |
| `canary-recovery-*`, `settlement-*` | Recovery of the old canary residue and production settlement rehearsals |
| `reopen-allocation-*` | Fork rehearsal of reopening LP allocation (prepared, not executed) |
| `vault-yield/` | Output of the read-only yield recorder: snapshots, decoded events, frontend JSON |
| `governance-inventory*`, `keeper-health*`, `reserve-limits*` | Authority inventory, keeper status and reserve bounds at a point in time |
| `slither-*`, `vault-slither*`, `*-tests.txt`, `fork-*`, `final-*` | Static analysis and test logs |
| `attempts/` | Earlier runs kept for the record |

Check a record: `shasum -a 256 <file>` and compare with the digest in the matching `.sha256` file.
