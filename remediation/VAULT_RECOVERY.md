# Composition mismatch and terminal surplus

The original canary record called `24,697,449,583` raw NVDA of residue permanently unattributable. Review of the deployed source shows that statement was too strong: when both principal claims are zero, a successful checkpoint's gain branch credits the remaining accounted idle tokens to their respective side account. A subsequent authorized withdrawal can return them. Recovery still depends on the oracle and emergency/pause settings.

## Composition is a liquidity constraint

A pair can hold enough total USD value while lacking one native token. For example, at $100/NVDA, 11 NVDA plus 901 USDG is worth $2,001, but cannot immediately pay claims of 10 NVDA plus 1,000 USDG without conversion.

The checkpoint's missing-gain branch in that state does not itself erase claims. Crediting all stock surplus before satisfying the outstanding dollar claim would create an unfair exit opportunity. Writing the dollar claim down solely because swaps are paused would also misclassify a temporary liquidity shortage as economic loss.

The existing bounded settlement swap is the mechanism for conversion. When paused, withdrawal can return the available target token and leave the remainder owed. This limitation is real: no accounting change can produce the missing native asset while conversion is disabled.

## Settlement-only wind-down

1. Inspect pair config, both ledger principals/idles, reserve availability and the live position. Check the oracle and pool-removal guard before scheduling an exit.
2. Keep allocation paused. Fully unwind the LP using the existing guarded guardian path. A partial emergency exit is not a drained pair.
3. After review, the timelock may clear emergency mode and enable settlement swaps while allocation remains paused. The guardian cannot unpause either flag. Do not bypass stale prices or widen bounds merely to force a weekend transaction.
4. Checkpoint, then request native withdrawal from the configured side account. Bounded settlement and reserve rules remain active. Read state after each transaction, because the integrating boosted delegate can catch an inner withdrawal failure.
5. Repeat as needed within the configured swap bound. Fully withdraw both principals. Collect/burn the empty position through the existing keeper functions if an NFT remains.
6. Run a final checkpoint with fresh guarded prices. If it re-credits tracked surplus, withdraw it through its configured side account. Repeat the final balance check.
7. A strict drained assertion requires **both principals = 0, both ledger idle balances = 0, LP liquidity = 0 and no remaining NFT/fees to collect**. Check reserves and allowances separately; a reserve may intentionally retain funds. Custody balances shared across pairs cannot substitute for per-pair ledger checks. Re-pause settlement after the wind-down.

For the historical canary, recovery must use its own pair ID and side accounts, not the production pair. The reviewed record is preserved unchanged under `contracts/robinhood-vaults/deployments/`; `evidence/vault-state.json` is the new read-only observation.

## Regression evidence

`test/VaultRecovery.t.sol` runs against the unchanged archived vault implementation:

- `testCompositionDoesNotWriteOffSolventClaimsWhenSwapsPaused`: the native shortage remains an outstanding claim, not a reported loss.
- `testBoundedSettlementThenCheckpointRecoversAllSurplus`: after bounded conversion and a final checkpoint, both principal and idle ledgers reach zero, with zero LP liquidity and approvals.
- `testUncheckpointedSurplusIsRecoverableButStillOracleGated`: the same zero-claim residue is recoverable after checkpoint; stale-oracle and side-account authorization guards still apply.

These are local regressions, not a new mainnet withdrawal. No upgrade or claim reallocation has been performed for these findings.
