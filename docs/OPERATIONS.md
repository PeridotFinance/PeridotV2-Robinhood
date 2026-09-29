# Operational boundaries

This repository describes an existing mainnet deployment with deliberately small margin limits. A clone and local build require no credentials. No live signing, deployment or keeper provisioning happens through the root Makefile or CI.

## Current status and record precedence

Read [the current dated evidence](../remediation/MAINNET_EVIDENCE.md) before archived reports. The margin rollout completed September 18 and the 5× configuration September 19. The lending price adapter and vault V2 correction were installed September 26. At block 75,678,599 on September 29, five user-signed reactivation calls were verified: supply/borrow and ordinary seizure are enabled. Guarded prices were available and both margin markets were priceable. Strategy allocation/settlement remain paused with zero LP liquidity. Actual lending and long/short acceptance flows remain outstanding. Zero debt at this block is not proof of zero historical usage.

## Prices and withdrawals

Stock oracle updates follow trading sessions, including weekend and holiday gaps. The configured oracle guard checks freshness, stock-token oracle pause and pool deviation. A calendar weekday is not evidence of a valid price. Freshness failures can block new margin positions, swap-based closes, liquidations and LP-backed withdrawals. Keep repayment and debt-free in-kind exit visible in the eventual frontend.

The paired vault's accounting values loss using the oracle reference price. USDG suppliers share residual strategy losses. Native-token reserve cover is bounded by reserve balance, per-call use, the UTC-day budget and a percentage of the attested deficit. It is not guaranteed principal or unlimited insurance. USDG is fixed at $1 in the recorded pricing policy; that does not detect a depeg.

### Exact reserve bounds

At block **74,113,271** on September 27, the production reserve configuration was:

| Bound | Observed value |
| --- | --- |
| Coverage ratio | At most 50% of the deficit presented to each `cover()` call |
| Per-call value ceiling | $10, using the protocol's USD18 accounting |
| UTC-day value ceiling | $25 shared across that pair's reserve calls |
| Available NVDA | 0.000183490408179855 NVDA (183490408179855 raw units) |
| Available USDG | 0.000011 USDG (11 raw units) |
| Reserve paused | No |

The available balance of the requested token is a further binding limit. The dollar ceilings are **not funded guarantees**, and the parameter named `maxUsePerTxUSDG` is enforced per `cover()` invocation, not as an aggregate transaction counter. Daily usage resets by UTC day. The reserve does not convert its NVDA balance into USDG to cover a USDG request. Evidence: [reserve limits and balances](../remediation/evidence/reserve-limits.json), with SHA-256 digest. Re-read balances before using numbers in a demo.

Use **“capped reserve-backed loss mitigation”**. “IL cushion” may be informal shorthand, but it must not imply full divergence-loss reimbursement or a guaranteed HODL benchmark. The present balances are very small and must be considered before increasing activity or claiming meaningful coverage.

## Historical keeper and governance status

September 20 records describe a DigitalOcean worker with a durable PostgreSQL journal and funded dedicated liquidation signer. The signer has no checked protocol admin/owner roles. No live cloud liquidation was observed in that record. A later [September 26 health read](../remediation/evidence/keeper-health.json) reported execution enabled, gas ready and no positions. The [September 29 readiness record](../remediation/evidence/reactivation-readiness-2026-09-29.json) also records execution enabled, gas ready and no positions. External heartbeat alerts remain deferred; recheck health before live acceptance testing.

The archived local keeper service code is included for review and tests. Existing cloud credentials, operator keystores, passwords, local database state and cloud app configuration are excluded from this repository.

Governance migration from the bootstrap EOA to Safe remains outstanding. The user-designated Safe is undeployed; deployment, funding and migration are explicitly deferred until the user resumes that work. This is an unresolved control risk, not something fixed by rewording the submission. The former governor credentials were represented in historical cloud deployment configuration; a dedicated keeper does not revoke that former key. Address this through the operator's key/admin migration process. Do not restore superseded cloud deployments.

## Findings and their current disposition

| Finding | Current disposition |
| --- | --- |
| Withdrawals could skip shared losses after LP closure while one native claim remained underbacked | Corrected by the installed vault V2, with unchanged storage/ABI and independently verified mainnet execution. See [vault correction](../remediation/VAULT_UPGRADE.md). |
| Checkpoint composition mismatch / “stranded” residue | Missing native tokens still require bounded settlement or replenishment. Tests show a final guarded checkpoint can re-credit zero-principal tracked surplus; actual recovery is outstanding. Do not call it permanently stranded or already recovered. See [recovery procedure](../remediation/VAULT_RECOVERY.md). |
| Boosted delegate catches a failed vault operation | Existing behavior retained. A reverted inner vault call rolls back its own effects; the catch emits `VaultDepositFailed`/`VaultWithdrawalFailed` rather than certifying strategy success. Operators/UI must inspect failure events and resulting balances/claims, not just the outer receipt. This is an operational limitation, not evidence that every successful transaction performed its intended vault action. |

A checkpoint must precede rebalance, and operator calls need suitable gas. The separately tested V2 change does not redesign the boosted delegate. No contract source was changed to make the submission look cleaner.

The [September 17 fork report](../contracts/robinhood-vaults/deployments/robinhood-mainnet.margin.md) now carries a superseded-status notice. Its original body, source hashes, deployment JSON and receipt history remain intact. Earlier undeployed/paused/2× stages must not override the newer dated records.
