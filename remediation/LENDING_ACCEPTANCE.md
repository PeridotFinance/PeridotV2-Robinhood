# Small lending acceptance test

The runner tests **0.001 NVDA supplied and 0.05 USDG borrowed**, then repays the complete USDG debt and redeems 0.001 NVDA. Repayment approval is capped at 0.051 USDG; unused approval is revoked. It preserves existing supply shares apart from rounding/interest and restores the initially absent market memberships. It refuses existing debt/membership for opening. This is an operator test, not independent adoption.

The full ten-transaction round trip passed a local mainnet-fork simulation at block 76,020,704. A subsequent check at block 76,447,247 stopped at the stock guard's stale-oracle check. After prices refreshed, the user signed the four opening transactions. Independent verification at block **76,556,183** confirms the 0.001 NVDA supply and 0.05 USDG borrow, with matching receipts, events and balances. [Opening evidence](evidence/lending-acceptance-open-verified.json). The six user-signed closing transactions were independently verified at block **76,565,683**: zero debt in both markets, 0.001 NVDA returned, approvals cleared and memberships restored. The final underlying balances and original pNVDA share balance exactly match the pre-test snapshot (ETH gas costs are separate). [Closing evidence](evidence/lending-acceptance-close-verified.json). Earlier simulation success is not continuous readiness. No oracle threshold, protocol limit, strategy pause or governance setting is changed.

## Historical commands — completed; do not rerun

Run from the local checkout:

```bash
cd /Users/joshua/Peridot/RobinhoodVaults/PeridotV2-Robinhood
python3 remediation/tools/lending_acceptance.py
```

This first command only simulates. If it fails (including the stale-oracle error), stop. Run it again after the feed has refreshed. After it succeeds, open the small lending position:

```bash
python3 remediation/tools/lending_acceptance.py --stage open --broadcast
```

Foundry asks for the existing `robinhood-deployer` keystore password locally. The runner does not read or store the key/password. It sends four transactions, checks exact calldata, sender/nonce/chain, canonical successful receipts, Mint/Borrow events and balance/debt deltas. Wait for **OPEN VERIFIED** before proceeding:

```bash
python3 remediation/tools/lending_acceptance.py --stage close --broadcast
```

The closing stage sends six transactions: bounded approval, full repayment, approval revocation, redemption, and exit from the two market memberships. Wait for **CLOSE VERIFIED**. The receipt/state verification confirms zero debt, the returned underlying, cleared allowances and restored memberships. Redeeming the original deposit can leave interest/rounding dust in pTokens. It does not redeem all pre-existing holdings.

Both stages keep public journals and verification evidence under `remediation/evidence/lending-acceptance-*`. An interrupted/failed attempt is deliberately blocked from automatic retry; retain its intent and Foundry broadcast files for reconciliation. Do not delete the intent or use `--resume` blindly. Avoid concurrent transactions from the same wallet. If opening completed but closing fails, the position remains open until the failed step is resolved.

The native EVM block number is read with a read-only RPC code override and used only to align local Foundry execution; no mainnet state is overridden. The initial full script simulation remains mandatory. When native EVM height differs from RPC height, the runner skips Foundry's incompatible secondary replay (`--skip-simulation`), which otherwise invents extra accrued interest. This is the same handling used by the mainnet deployment runner; no oracle checks or repayment bounds are relaxed. Verification uses one RPC and is not independent L1 settlement proof. A successful outer receipt is supplemented with Compound failure-event rejection and state-delta checks.

Margin and LP reactivation are separate follow-up steps. Safe migration and Telegram alerts remain deferred.

## Recover verification after a completed broadcast

Foundry names journals after the selected entrypoint: `supplyAndBorrow-latest.json` and `repayAndRedeem-latest.json`. The initial verifier incorrectly assumed `run-latest.json`; that local verification failure did not undo or resend the successful opening transactions. The stage-specific paths are now used without falling back to generic or dry-run files. Existing nonces, calldata, canonical receipts, events and state deltas remain checked.

To verify an already-signed opening without sending anything:

```bash
python3 remediation/tools/lending_acceptance.py --stage open --verify
```

Use `--stage close --verify` for an already-signed close. Verification needs the saved stage intent and expected public Foundry journal. It never signs, simulates or rebroadcasts. Both stages have already been reconciled; do not run either broadcast again.
