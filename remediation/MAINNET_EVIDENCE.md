# Mainnet evidence and submission claims

All observations are dated/pinned. Code deployment, enabled operations, a successful local fork and an observed mainnet transaction are distinct claims.

## Current containment

The governor signed three mainnet transactions locally. Receipt identities and resulting flags are recorded in `evidence/containment-transactions.json` and `evidence/contained-state.json`, with SHA-256 digests:

| Action | Transaction |
| --- | --- |
| Pause pUSDG borrowing | `0x010ac505aa22b4b209211b84ca112d96f9ae0bd88987b82c51217c199a07d68e` |
| Pause pNVDA borrowing | `0xf3a7125bee535cd4459f04da55218de7e95568786574175a464eb7f381f4602a` |
| Pause ordinary collateral seizure | `0x67275bb587346eae4a8959274698222c502fc9ed3060f90ea69d8d2aaa346c8e` |

An independent read at block **73,325,978** confirmed all three flags and zero outstanding debt in both markets. The controller still pointed to the original oracle at that block. The containment does not represent adapter installation or reactivation.

## Installed lending correction

Adapter: [`0xe4e03c2fdaef915ace705d106b2660b1e342a2e4`](https://robinhoodchain.blockscout.com/address/0xe4e03c2fdaef915ace705d106b2660b1e342a2e4).

- Deployment: `0x5eda954469fdfdfbd06023ec39c9204774ebcae25f51cca3a95b6a58ab1107e4`.
- Controller oracle switch: `0x9f6a473141be3ac8a114280a83747287be2f09a6cbbc657667b667287707d5a0`.
- Verified at block **73,329,306**, runtime hash `0x8a44437ef2c35e187c49f54aa92fcc3c51c3a30c1dc795cc713f610eaf353404`.
- USDG controller price is `1e30`; USD18 API remains `1e18` at the configured peg. Margin backing source is unchanged.
- A 1 USDG repayment now quotes **23,929,678** raw pNVDA shares, matching decimal-normalized arithmetic. The reverse direction also matches.
- Both borrow pauses and ordinary-seizure pause remain enabled. No reactivation is claimed.

`evidence/installed-adapter.json` verifies all immutable occurrences, compiled runtime outside those values, getters, prices and both liquidation quotes. `evidence/installation-transactions.json` independently checks canonical successful receipts, exact reviewed creation bytecode/constructor arguments, and switch calldata.

## Vault correction deployed and queued

At block **73,364,898**, independent checks confirmed all five user-signed queue transactions, candidate `0x17f0cf262fbbf27e44756dba6d852815695e9c4a` matching the reviewed linked runtime, exact schedule payload and operation hash. V1 remains active. Execution is eligible **September 26, 2026 at 21:15:52 UTC (5:15:52 PM New York)**. Both supply flags, both borrow flags, ordinary seizure, production allocation and settlement swaps are paused; debt remains zero. Both pair ledgers match the earlier pre-queue snapshot.

The exact receipts and pinned state are in `evidence/vault-upgrade-queue.json` with its SHA-256 digest. See [the upgrade record](VAULT_UPGRADE.md) for addresses and operation identity. This evidence proves deployment/queueing, not activation.

## Validation by scope

| Evidence | Scope |
| --- | --- |
| Original snapshot checks | 216 file hashes, 26 archived artifacts and 16 frontend ABIs still match. |
| Baseline rerun | 92 Solidity tests and 38 Python tests passed, separately from new coverage. |
| Adapter regressions | 8 new tests, including two 256-run fuzz properties, exercise price APIs, controller collateral/debt units, borrow boundaries and both liquidation directions. |
| Vault review regressions | 3 new tests against unchanged deployed source prove composition/terminal-surplus behavior. The runner also repeats 40 inherited vault tests; these are not 40 additional unique tests. |
| Lending mainnet fork | The updated suite contains three controller price/account/quote checks and three reactivation checks: old vault rejected, unavailable guard rejected, corrected vault plus simulated fresh prices accepted. See the pinned result in `evidence/fork-pin.json`; all state changes are local. |
| V2 vault candidate | 50 unique tests: 40 compatibility tests (one emergency expectation deliberately tightened), four post-exit regressions including 256-run fuzz, three recovery tests, three invariants. Derived fixtures repeat compatibility tests. See `evidence/vault-v2-tests.txt`. The separate archived-V1 reproduction asserts the unsafe result to document the defect. |
| Vault mainnet fork | Three passing tests: actual proxy state/authority preservation, real pToken cash-versus-NAV behavior under unavailable guards, and queue/execute runners respecting the real timelock delay. See `evidence/vault-fork-pin.json`. |
| Vault storage/size | Full declared storage type topology and public ABI match V1; inherited namespace imports remain unchanged. Linked V2 runtime 22,352 bytes. |
| Static analysis | Slither 0.11.4 analyzed the adapter and its three interfaces with 100 detectors: zero findings after explicitly implementing the compatibility interface. This is not an audit of the entire protocol. |
| Standalone reproduction | Standard JSON input reproduces exact creation/runtime templates including metadata; ABI matches after top-level entry ordering normalization. |
| Size | Adapter runtime 1,471 bytes; constructor/init code 2,307 bytes with the recorded compiler settings. |
| Installation simulation | Deployment and oracle switch simulated successfully; no automatic unpause. Simulation address is not proof of deployment. |
| Operational runner checks | 9 Python tests cover read-only defaults, interactive-only signing, ambiguous submission handling, receipt identity/canonicality and immutable runtime verification. |
| Fresh runtime audit | At block 73,326,475: 35 recorded runtime hashes, 11 proxy targets, both market delegates and both 5× risk tuples matched. See `evidence/runtime-check.json`. |

The public RPC could not serve the earlier fork block. The fork was rerun successfully against a freshly pinned block; missing historical state is not counted as a passing test. Compiler warnings inherited from archived sources are retained in the test output. The oracle installation changed no proxy layout. The separate vault candidate preserves storage and ABI; mainnet activation must be evidenced separately.

## Vault and keeper observations

At block **73,323,967**, production LP liquidity was **zero**, though an empty NFT remained. The historical canary held zero principals and `24,697,449,583` raw NVDA in its idle ledger. Guarded prices were unavailable for both pairs. Do not describe capital as currently deployed in LP without a new liquidity read. The production ledger also has a native-token composition mismatch. Further testing found a zero-LP loss-recognition bypass in V1; `VAULT_UPGRADE.md` tracks the correction, and `VAULT_RECOVERY.md` explains settlement and oracle dependencies.

The dedicated keeper's fresh direct health read reported execution enabled, gas ready and no positions; the timestamp is in `evidence/keeper-health.json`. No live cloud liquidation is claimed. External Telegram outage alerts are deferred and not installed.

## Claims suitable for the submission

Use: “Peridot has deployed NVDA/USDG boosted lending, a paired Uniswap v4 strategy with capped in-kind loss mitigation, and isolated long/short margin on Robinhood Chain mainnet under restricted canary limits.” Follow this with the current operational state and dated receipts.

While containment remains active, say: “The lending-oracle correction is deployed and verified; new borrowing remains paused pending the vault correction and guarded reactivation.” Do not present the supply → borrow → leverage journey as currently executable until it has been reactivated and reproduced.

Use “capped reserve-backed loss mitigation,” not guaranteed impermanent-loss insurance. Use “standard v4 adapter,” not a custom hook. Show 5× as a configured limit, not a claim that new positions can open while borrowing is paused.

Cross-chain access, fiat onboarding, IBANs and the separate frontend require their own repository/provider/transaction evidence. This contract package does not prove those integrations.

Explorer source verification was attempted but the Blockscout API returned a Cloudflare browser challenge. No explorer verification badge is claimed. Independent runtime/immutable matching and canonical receipt checks are complete.
