# Native-backing correction for closed LP positions

Status: **V2 installed and independently verified on mainnet; markets remain paused**. Verification at block **73,403,815** confirmed the active proxy implementation matches the reviewed runtime, the timelock operation is complete, both pair ledgers are unchanged, and both pToken stored exchange rates are unchanged. The separate lending-oracle adapter is also installed.

- Active V2: `0x17f0cf262fbbf27e44756dba6d852815695e9c4a`.
- Execution transaction: `0x24eb2c2545f073041c37850046f2a4cf2ff561be7a17322bc865d03f7d6d0e41`.
- Deployment transaction: `0x42d4f224e9aad9bc93cc25c0c0791e912b39a024e01fc723dfd4b5fb587f2a72`.
- Queue transaction: `0x867a7f0c0e1177b91b1efbdeb3a9d7eb8f24a5e3a96dcbd759475d2134a14b42`.
- Completed operation: `0xacc0e39a4de59d916d7ddb337dedc5717ab64479bb449714d70fcd05ad7b6067`.
- Supply, borrowing, ordinary seizure, production allocation and settlement swaps remain paused. Both markets have zero debt. Vault cash is currently guard-blocked; pToken local cash remains separate, without reducing recorded NAV merely because of that restriction.

Evidence: [`vault-upgrade-execution.json`](evidence/vault-upgrade-execution.json), [`vault-upgrade-queue.json`](evidence/vault-upgrade-queue.json) and their SHA-256 digests. `python3 remediation/tools/record_vault_execution.py` independently checks the current installation while containment remains in place. The older queue verifier intentionally rejects the now-completed upgrade/overwritten broadcast journal; use its archived evidence.

The post-upgrade reactivation simulation passed the oracle/vault identity gates, then reverted with `StaleOracle` for the stock feed. No reactivation transaction was sent. Fresh guarded prices are required before reopening or settling the remaining composition imbalance; weekend staleness is not bypassed.

## Finding and change

Closing LP liquidity does not eliminate shared exposure if one native claim is backed by the other token's surplus. V1 permits a zero-LP, sufficiently stocked side to withdraw without current loss recognition. After a price move this can shift its share of loss onto the other side. `VaultPostExitExposure.t.sol` reproduces that behavior against the archived source.

V2 adds one internal predicate: both idle balances must cover their respective native principals. Only a closed position satisfying that predicate can bypass the existing guarded settlement path. Otherwise withdrawals retain oracle, emergency, deadline and shared-loss checks. `withdrawableAssets` uses the same predicate to exclude guard-blocked vault cash. Fully backed idle withdrawals retain their existing behavior.

The public ABI, every declared storage slot/offset/type, nested struct and storage gap match V1. The inherited OpenZeppelin namespace implementations and settlement library are unchanged. No Avalanche source is imported. The actual pToken fork test confirms that restricted cash does not reduce the stored exchange rate/NAV merely because the guard is unavailable.

| Item | Value |
| --- | --- |
| Vault proxy | `0x280825b2d856706Ff7E0d6351CcB2e935E1a9A2f` |
| V1 implementation | `0x21c7e1c2caded480fa373c5c9b3f51492b2d50ac` |
| ProxyAdmin | `0xad2165E6f3b8146D17815968470eDb8B9a0A4ab7` |
| Timelock | `0x6797FB8Ce049B42C5BC2b42Bf76c6d15C7B12498` |
| Existing linked SettlementLib | `0x813AbFeC0DE50f8674798CbaB72Ed7b5D8CcB9cB` |
| Candidate linked runtime hash | `0xfd8fba1858dc625afd24cdbf0d0461329ae83943cb7639800e4618c762c48c84` |
| Candidate runtime size | 22,352 bytes |

## Validation

- 50 unique V2 tests: 40 compatibility tests, four post-exit regressions including 256 fuzz runs, three recovery tests and three invariants. One compatibility expectation deliberately changes: a closed but natively underbacked pair remains blocked in emergency mode. Derived suites repeat those 40 tests; totals in the raw runner output are not unique coverage.
- Three mainnet-fork tests exercise actual proxy state/roles, pToken cash versus NAV, and the full queue/execute script including rejection before the timelock deadline.
- Current-state lending and vault fork fixtures reconstruct archived V1 locally before replaying their regressions; no mainnet downgrade is performed. Six lending-fork tests include reactivation rejection on V1, rejection with unavailable guard prices, and successful restoration with V2 plus locally simulated fresh prices.
- `verify_vault_layout.py` compares storage topology and public ABI. `artifacts/RobinhoodBoostedVaultV2.json` archives the candidate compiled with the existing mainnet library link.
- `evidence/vault-queue-simulation.txt` records a successful read-only live simulation. No signing credential was accessed by these checks.

## Completed local governor procedure (historical)

The governor signs locally; these steps do not deploy/fund a Safe or migrate any authority. Both borrow flags and ordinary seizure must already be paused. The script checks chain 4663, existing implementation, ProxyAdmin owner and linked library hash.

**The queue invocation below has already completed; do not rerun it.** It is retained to reproduce the recorded procedure:

```sh
FOUNDRY_PROFILE=vault_upgrade forge script \
  remediation/script/UpgradeNativeBacking.s.sol:DeployAndQueueNativeBacking \
  --rpc-url https://rpc.mainnet.chain.robinhood.com \
  --sender 0x94696d767e65a75581145646960FA0eC886cE5d2 \
  --account robinhood-deployer --broadcast --slow
```

It sends five transactions: pause pUSDG supply, pause pNVDA supply, pause production allocation/settlement swaps, deploy V2, and schedule the ProxyAdmin upgrade with the current timelock delay (at least one hour). It preserves the pair's current emergency flag. The V1 withdrawal bypass still exists during the delay; these actions do not represent a blanket redemption freeze.

After submission, independently verify canonical receipts, exact creation/runtime and schedule calldata, pause flags, actual operation ID and execution timestamp. If submission is interrupted, reconcile the public broadcast journal before retrying. `NEW_VAULT_IMPLEMENTATION` supports reusing a verified deployment only when the upgrade operation was not already scheduled; it is not a blind resume switch.

Before mainnet execution, `python3 remediation/tools/fork.py --queued` rehearsed the actual pending operation using fork time only; its pinned log is retained as historical evidence. It requires pre-execution state and is not a current-state rerun. The subsequent completed execution used `UpgradeNativeBacking.s.sol:ExecuteNativeBacking` using `FOUNDRY_PROFILE=vault_upgrade` and `NEW_VAULT_IMPLEMENTATION` set to the independently verified deployed address. The governor signed that invocation locally; do not rerun it. The runner checks candidate runtime, executes through the timelock, verifies the new implementation and preserves the production ledger. The public receipt, implementation slot/code and both pair ledgers were independently verified afterward.

Neither script reopens markets. `ReactivateLending` requires the installed V2 runtime and fresh guarded prices matching the corrected oracle APIs before restoring seizure, borrowing and supply. Allocation and settlement remain paused pending their separate [settlement review](VAULT_RECOVERY.md). Weekend/holiday feed closure is not bypassed. The Safe and Telegram work remain explicitly deferred until the user resumes them.

## Static analysis

Slither 0.11.4 returned 25 target findings for V1 and 25 for V2, with identical detector/severity counts; none are High. The JSON comparison retains every finding description. Imported dependency/library/interface paths were filtered: this is not an audit of the full stack.

| Findings | Triage |
| --- | --- |
| Five Medium `reentrancy-no-eth`, four Low `reentrancy-benign` | Existing post-external-call accounting patterns. Relevant mutating entrypoints use `nonReentrant`; registration is also config-role restricted. Internal helpers run inside guarded entrypoints. The fix adds no external calls. Trusted adapter/reserve behavior and read-only callback interactions remain part of the existing trust boundary; these findings are retained, not described as absent. |
| One Medium `uninitialized-local` | The pool key is assigned in the successful `try`; the `catch` returns zero before use. |
| Nine Medium `unused-return` | Existing calls use reversion for validation or deliberately ignore additional outputs. The required oracle reference value is consumed by loss accounting. |
| Two Low `timestamp` | Existing checkpoint/deadline checks intentionally depend on time. |
| Four Informational | Existing naming, reserved storage gap, redundant statement and function complexity. Guarding one additional condition increases withdrawal complexity; retaining the gap preserves upgrade compatibility. |

This scoped review and passing tests do not establish an independent audit or guarantee economic safety.
