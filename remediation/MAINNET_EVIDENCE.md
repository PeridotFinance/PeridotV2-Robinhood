# Mainnet evidence and submission claims

## September 30: long margin round trip completed

Position **1**, account `0xa1a7a0f0c270fd9229ff7a2e6a67b0c2eb67d45b`, opened with 10 pUSDG collateral at **2× requested gross leverage**; its entry metric was **1.98×**. Six user-signed transactions deposited collateral, opened, fully closed and withdrew the returned shares. At final block **76,612,481**, status is CLOSED, both account debts and position pToken balances are zero, and free margin, locked margin and the pUSDG approval are zero.

The wallet received **9.85191185 pUSDG**, equivalent to **0.197039 USDG** at the recorded exchange rate, versus approximately **0.200001 USDG** supplied. These are pToken underlying equivalents, not redeemed USDG cash. Gas costs are separate; the difference is the observed aggregate round-trip result, not a fee-only attribution.

[Summary](evidence/margin-acceptance-long-completed.json), [opening](evidence/margin-acceptance-long-open-verified.json), [closing](evidence/margin-acceptance-long-close-verified.json), [withdrawal](evidence/margin-acceptance-long-withdraw-verified.json), [independent receipt/event recheck](evidence/margin-acceptance-long-receipt-recheck.json). The older balance snapshots could not be re-served by the public RPC; their original verification records are retained and final state was independently refreshed. No independent L1 finality claim is made.

This is one operator long lifecycle, not independent adoption, a short lifecycle, LP reactivation or a live cloud liquidation. The short is next; it passed a fresh post-long simulation. Protocol 5×/$2 gross/$1 debt limits are unchanged. LP reactivation, Safe migration and Telegram alerts were not performed.

## September 30: operator lending round trip completed

All ten user-signed transactions are verified: 0.001 NVDA supplied, collateral market entered, 0.05 USDG borrowed, full debt repaid, approval revoked, 0.001 NVDA redeemed, and original market memberships restored. Opening was verified at block 76,556,183 and closing at block **76,565,683**. Both final debts and approvals are zero. The two underlying wallet balances and pre-existing pNVDA share balance exactly match the pre-test snapshot; gas costs are separate.

[Opening receipts and state](evidence/lending-acceptance-open-verified.json), [closing receipts and state](evidence/lending-acceptance-close-verified.json), [runner and recovery notes](LENDING_ACCEPTANCE.md). Exact calldata/sender/nonce/chain, canonical successful receipts, action events and before/after balances were checked through one public RPC. This is an operator acceptance test, not independent adoption, margin execution, LP reactivation or independent L1 settlement proof.

The initial opening verifier filename error was reconciled without rebroadcast. The closing simulation required the existing native-EVM-clock handling to avoid Foundry's incorrect RPC-height interest replay. No oracle threshold, allowance cap or risk limit was relaxed. LP allocation/settlement remain paused; long/short and frontend acceptance remain outstanding. Safe and Telegram work remain deferred.

## September 29: lending reactivation verified

At block **75,678,599**, all five user-signed transactions passed independent checks for sender, controller target, exact calldata, successful canonical receipt, matching `ActionPaused` event and execution order. Both markets have supply/borrow enabled; ordinary seizure is restored. Oracle, vault V2, controller and pToken delegate runtime identities match their recorded hashes. Guarded prices were available and both margin markets were priceable. Directional margin limits remain unchanged.

| Action | Transaction |
| --- | --- |
| Restore ordinary seizure | `0xfd6bc0cbe1f86f2fc03e6f6ff41d9d11f7d993eafd36f6536373e017eec47fd9` |
| Restore pNVDA borrowing | `0x816853d5053089b59c61ed28646ca5235cec3a09dc16344ec09cb0b4fae4a240` |
| Restore pUSDG borrowing | `0x09c649506646b6259de074167129729f34b7d4ba9ca519eedb0e007353e1ca35` |
| Restore pNVDA supply | `0x8278e138dfcfd3690d9b37059d003fa75c1d60495dd5419df8425eb98c8d9581` |
| Restore pUSDG supply | `0x6534fe8caf90d2e5036ea290e18bcfd7f4256eb3592f2acecd6499704349f667` |

**LP allocation and settlement swaps remain paused; production LP liquidity is zero.** At this September 29 observation, lending and margin acceptance flows were outstanding; the September 30 lending result above supersedes that portion. Zero debt at this block is not a lifetime usage measurement. Safe migration and Telegram alerts remain explicitly deferred.

[Execution record](evidence/reactivation-execution-2026-09-29.json), [SHA-256](evidence/reactivation-execution-2026-09-29.sha256), [read-only verifier](tools/record_reactivation.py). Verification uses one public RPC. Its `safe` and `finalized` block tags did not yet cover all receipts when sampled; this is canonical L2 inclusion and state verification, not independent L1 settlement proof.

## Earlier dated observations

The records below describe their stated blocks. The September 29 execution above supersedes earlier paused/pending operational status.

**September 29 readiness update (not an unpause):** at block 75,671,997, stock prices passed the guard and both margin markets reported priceable. The stock feed last updated at 07:36:58 UTC / 09:36:58 Europe/Berlin. The dedicated keeper reported execution enabled, gas ready and no positions. The existing reactivation script simulated all five admin calls successfully without signing/broadcast. Both supply/borrow pauses and ordinary seizure remain enabled, debt is zero, and LP allocation/settlement remain paused with zero position liquidity. [Pinned reads and keeper health](evidence/reactivation-readiness-2026-09-29.json), [simulation](evidence/reactivation-simulation-2026-09-29.txt). At this pre-execution observation, reopening was still pending. The execution record above supersedes that status; transaction-flow verification was outstanding at that time; the September 30 lending record above supersedes it.

All observations are dated/pinned. Code deployment, enabled operations, a successful local fork and an observed mainnet transaction are distinct claims.

## September 26 containment

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

At block **73,364,898**, independent checks confirmed all five user-signed queue transactions, candidate `0x17f0cf262fbbf27e44756dba6d852815695e9c4a` matching the reviewed linked runtime, exact schedule payload and operation hash. V1 remained active at that observation. Execution became eligible **September 26, 2026 at 21:15:52 UTC (5:15:52 PM New York)**. Both supply flags, both borrow flags, ordinary seizure, production allocation and settlement swaps are paused; debt remains zero. Both pair ledgers match the earlier pre-queue snapshot.

The exact receipts and pinned state are in `evidence/vault-upgrade-queue.json` with its SHA-256 digest. See [the upgrade record](VAULT_UPGRADE.md) for addresses and operation identity. This evidence proves deployment/queueing, not activation.

## Vault correction activated and verified

Execution transaction `0x24eb2c2545f073041c37850046f2a4cf2ff561be7a17322bc865d03f7d6d0e41` completed the queued operation. At block **73,403,815**, independent public RPC checks confirmed canonical success and exact execution calldata, the active V2 runtime, timelock completion, unchanged production/canary ledgers and unchanged pToken stored exchange rates. Both markets still have zero debt; all containment flags remain set. Their reported cash equals local balances plus reachable vault cash (currently zero under the unavailable guard).

`evidence/vault-upgrade-execution.json` and its SHA-256 record contain the proof. A read-only reactivation simulation passed implementation/oracle checks and failed at the stock guard with `StaleOracle`; see `evidence/post-upgrade-reactivation.json`. No unpause or stale-price exception is claimed.

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

The public RPC could not serve the earlier fork block. The fork was rerun successfully against a freshly pinned block; missing historical state is not counted as a passing test. Compiler warnings inherited from archived sources are retained in the test output. The oracle installation changed no proxy layout. The installed vault V2 preserves storage and ABI; its mainnet activation is independently evidenced above.

## Vault and keeper observations

At block **73,323,967**, production LP liquidity was **zero**, though an empty NFT remained. The historical canary held zero principals and `24,697,449,583` raw NVDA in its idle ledger. Guarded prices were unavailable for both pairs. Do not describe capital as currently deployed in LP without a new liquidity read. The production ledger also has a native-token composition mismatch. Further testing found a zero-LP loss-recognition bypass in V1; `VAULT_UPGRADE.md` tracks the correction, and `VAULT_RECOVERY.md` explains settlement and oracle dependencies.

The dedicated keeper's fresh direct health read reported execution enabled, gas ready and no positions; the timestamp is in `evidence/keeper-health.json`. No live cloud liquidation is claimed. External Telegram outage alerts are deferred and not installed.

## Claims suitable for the submission

Use: “Peridot has deployed NVDA/USDG boosted lending, a paired Uniswap v4 strategy with capped in-kind loss mitigation, and isolated long/short margin on Robinhood Chain mainnet under restricted canary limits.” Follow this with the current operational state and dated receipts.

Current wording: “Lending reactivation was verified September 29; supply and borrowing are enabled, while LP allocation/settlement remain paused. An operator lending cycle completed September 30; margin and frontend acceptance testing remain outstanding.” Do not claim successful margin fills or a reproduced supply → borrow → leverage journey from cleared pause flags alone.

Use “capped reserve-backed loss mitigation,” not guaranteed impermanent-loss insurance. Use “standard v4 adapter,” not a custom hook. Show 5× as a configured limit; actual margin fills remain to be demonstrated after reopening.

Cross-chain access, fiat onboarding, IBANs and the separate frontend require their own repository/provider/transaction evidence. This contract package does not prove those integrations.

Explorer source verification was attempted but the Blockscout API returned a Cloudflare browser challenge. No explorer verification badge is claimed. Independent runtime/immutable matching and canonical receipt checks are complete.
