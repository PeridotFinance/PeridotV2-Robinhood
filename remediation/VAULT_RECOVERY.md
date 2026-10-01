# Composition mismatch and terminal surplus

The original canary record called `24,697,449,583` raw NVDA of residue permanently unattributable. Review of the deployed source shows that statement was too strong: when both principal claims are zero, a successful checkpoint's gain branch credits the remaining accounted idle tokens to their respective side account. A subsequent authorized withdrawal can return them. Recovery still depends on the oracle and emergency/pause settings.

## October 1 recovery preparation — no mainnet recovery sent

Five [current-mainnet fork tests](evidence/settlement-rehearsal-tests.txt) pass against the installed V2: exact canary recovery and production isolation, rejection of the wrong side caller, the exact canary signing script and repeat refusal, and production settlement in each withdrawal order through actual timelock scheduling/execution on the fork. The timelock tests advance local time by the configured delay; there are no injected balances or oracle overrides. Both production orders returned `11,474,700,317,638,473` raw NVDA to pNVDA and `2,005,167` raw USDG to pUSDG at that pinned state. This observed equality is not a guarantee for all future prices or reserve states. [Block, source and log hashes](evidence/settlement-rehearsal.json).

The production rehearsal leaves both principal and idle ledgers zero, burns the empty NFT, clears vault-to-adapter allowances and ends with allocation and settlement paused. It verifies successful underlying cash movement through the pToken operator calls, which otherwise can catch vault failures. Production settlement remains a separate pending governance action with a one-hour timelock; no production signing command is authorized by the canary command below.

The next isolated step returns **24,697,449,583 raw NVDA (0.000000024697449583 NVDA)** from the old canary to its already-configured side owner, `0x94696d767e65a75581145646960FA0eC886cE5d2`. It costs ETH gas. The script performs exactly two calls: checkpoint, then withdrawal. It deploys nothing, uses no reserve, and changes no production ledger, pause, cap or role. Both Foundry simulation phases pass. [Prepared state and simulation](evidence/canary-recovery-simulation.json).

Reproduce without signing:

```bash
python3 remediation/tools/settlement_rehearsal.py
python3 remediation/tools/simulate_canary_recovery.py
```

User-local signing only, from this repository:

```bash
FOUNDRY_PROFILE=vault_upgrade forge script remediation/script/RecoverCanaryResidue.s.sol:RecoverCanaryResidue \
  --rpc-url https://rpc.mainnet.chain.robinhood.com \
  --sender 0x94696d767e65a75581145646960FA0eC886cE5d2 \
  --account robinhood-deployer --broadcast --slow
```

Each call has an explicit 1,000,000 gas limit. The checkpoint uses a five-minute deadline; a stale oracle or changed starting state stops the simulation. These are **separate transactions**: local script assertions are not an atomic mainnet rollback mechanism. If signing or execution fails, preserve the journal and inspect receipts before retrying. A completed checkpoint alone changes the starting principal; the script then refuses a blind repeat. Do not use `--resume` or replace the preparation snapshot blindly.

After signing, verify without sending anything:

```bash
python3 remediation/tools/record_canary_recovery.py
```

The verifier checks exact calldata, sender, sequential nonces, canonical success receipts, checkpoint/withdrawal/token-transfer events, zero canary principals/idles, the installed vault runtime, and unchanged production/configuration/reserve state. Balance changes are compared with the saved preparation; unrelated intervening transfers require separate review. Only its `CANARY RECOVERY VERIFIED` result establishes completion. Do not rerun the completed broadcast.

Claude Opus 5.5 was consulted through the user-requested CLI for generic operational review advice, which was checked against the actual contracts. That is advisory input, not an audit. The separately authorized Almanax scan of `ea79067..c9d5e3e` has **not started**: Almanax returned `project not found`; the repository must be linked to its organization first.

## Confirmed withdrawal defect and correction

The three recovery tests below did not cover a price change after closing LP liquidity while a native-token deficit remains. A fourth test confirmed a defect in the archived implementation: its zero-liquidity fast path lets an adequately stocked side withdraw before a shared economic loss is recognized. With 10 NVDA/1,000 USDG claims and 9 NVDA/1,100 USDG idle, a stock-price move from $100 to $200 leaves $2,900 assets against $3,000 claims. The old path pays the USDG side 1,000 instead of its loss-adjusted 966.666666 USDG.

`RobinhoodBoostedVaultV2` retains the fast path only when both native claims are fully backed. Otherwise withdrawal must pass the existing oracle/emergency/deadline and shared-loss accounting path, even with zero LP liquidity. The cash view uses the same native-backing condition. This correction is now installed and independently verified; see [deployment status and procedure](VAULT_UPGRADE.md). The bypass is corrected, but the existing native composition mismatch and terminal-surplus recovery still require fresh guarded prices and a separate settlement transaction.

## Composition is also a liquidity constraint

A pair can hold enough total USD value while lacking one native token. For example, at $100/NVDA, 11 NVDA plus 901 USDG is worth $2,001, but cannot immediately pay claims of 10 NVDA plus 1,000 USDG without conversion.

The checkpoint's missing-gain branch in that state does not itself erase claims. Crediting all stock surplus before satisfying the outstanding dollar claim would create an unfair exit opportunity. Writing the dollar claim down solely because swaps are paused would also misclassify a temporary liquidity shortage as economic loss.

The existing bounded settlement swap is the mechanism for conversion. When paused, withdrawal can return the available target token and leave the remainder owed. This limitation is real: no accounting change can produce the missing native asset while conversion is disabled.

## Settlement-only wind-down

1. Verify the V2 correction is installed before resuming production activity. Inspect pair config, both ledger principals/idles, reserve availability and the live position. Check the oracle and pool-removal guard before scheduling an exit.
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

These are local regressions, not a new mainnet withdrawal. No mainnet recovery or V2 activation is established by those tests. Equivalent recovery tests also pass against the candidate V2.
