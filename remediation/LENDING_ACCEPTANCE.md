# Small lending acceptance test

The runner tests **0.001 NVDA supplied and 0.05 USDG borrowed**, then repays the complete USDG debt and redeems 0.001 NVDA. Repayment approval is capped at 0.051 USDG; unused approval is revoked. It preserves existing supply shares apart from rounding/interest and restores the initially absent market memberships. It refuses existing debt/membership for opening. This is an operator test, not independent adoption.

The full ten-transaction round trip passed a local mainnet-fork simulation at block 76,020,704. A subsequent check at block 76,447,247 stopped at the stock guard's stale-oracle check. No mainnet acceptance transactions have been sent. Earlier simulation success is not current readiness. No oracle threshold, protocol limit, strategy pause or governance setting is changed.

## Commands

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

The native EVM block number is read with a read-only RPC code override and used only to align local Foundry execution; no mainnet state is overridden. Both normal Foundry simulation passes remain enabled. Verification uses one RPC and is not independent L1 settlement proof. A successful outer receipt is supplemented with Compound failure-event rejection and state-delta checks.

Margin and LP reactivation are separate follow-up steps. Safe migration and Telegram alerts remain deferred.
