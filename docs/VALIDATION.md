# Validation

## Fresh checks of this standalone snapshot

Prepared September 25, 2026. Environment: Foundry 1.7.1, Solidity 0.8.26, Python 3. Source and dependency files are included in this repository; neither original checkout is needed.

| Check | Result |
| --- | --- |
| Original deployment inputs | 119/119 SHA-256 matches |
| Later 5× package inputs | 168/168 SHA-256 matches; overlaps previous set |
| Frozen file manifest | 216 files match |
| Frontend ABI files | 16/16 SHA-256 matches |
| Default vault build | Passed |
| Margin mainnet build | Passed |
| Local vault Solidity tests, excluding fork suite | 92 passed, 0 failed, 0 skipped; includes fuzz and invariant tests |
| Python deployment/keeper tests | 20 passed |
| Python 5× update tests | 5 passed |
| Python keeper-service tests | 10 passed |
| Snapshot portability regression tests | 3 passed; all archived source names resolve inside the clone |
| Compiler reproduction | 26 archived artifacts exactly reproduced; creation/runtime templates including metadata and equivalent ABIs |
| Read-only mainnet verification | 35 code hashes, 11 proxy targets, two market delegates and both directional 5× risk configurations matched at block 72447565 |

Run `make verify`, `make build`, `make test`, and `make reproduce` to repeat local checks. See [`reproduction-result.json`](../snapshot/reproduction-result.json) and [`mainnet-check.json`](../snapshot/mainnet-check.json).

The historical fork suites were included but not rerun for this packaging task. Their pinned archive data may no longer be served by the public RPC. They are not counted among the 92 local tests. The fresh mainnet check is read-only code/configuration verification, not a lifecycle, liquidation or frontend test.

## Recorded deployment validation

- [Original user-operated margin runner](../contracts/robinhood-vaults/deployments/robinhood-mainnet.margin-user-runner-validation.json): 24 fork tests, 20 Python tests, and 66 mined local rehearsal transactions. The actual mainnet rollout separately recorded 66 user-signed transactions.
- [5× validation](../contracts/robinhood-vaults/deployments/robinhood-mainnet.margin-5x-validation.json): 16 deployed-mainnet fork tests and a 15-transaction local keeper rehearsal. The actual mainnet risk update separately recorded four queue/apply transactions.
- [Production paired-vault evidence](../contracts/robinhood-vaults/deployments/robinhood-mainnet.production-pair.json): reserve-backed cover exercised on mainnet with a 50% coverage cap.

These historical counts span different scopes and revisions and must not be added together as one unique test-suite total. Local stress scenarios use controlled forks and are not stock-price manipulation or liquidations on public mainnet. Tests and automated scans are not an independent security audit.
