# Submission evidence and remaining gaps

Reviewed September 27, 2026. This page separates implementation evidence from product availability and adoption. Contract observations are pinned in the linked records; they are not continuous monitoring.

The current submission review branch is `fix/robinhood-mainnet-hardening`, [PR #1](https://github.com/PeridotFinance/PeridotV2-Robinhood/pull/1). The public default branch still contains the earlier snapshot. Share the explicit branch link until the reviewed corrections are incorporated into the default submission view; do not expect judges to infer this distinction.

## Claims and evidence

| Claim | Evidence and permitted wording |
| --- | --- |
| Robinhood Chain mainnet contracts | Deployed NVDA/USDG lending, paired v4 vault/reserve, isolated long/short margin and liquidation contracts. [Addresses](../remediation/MAINNET_DEPLOYMENTS.md), [dated receipts](../remediation/MAINNET_EVIDENCE.md). Deployment is not proof that all actions are currently enabled. |
| Installed hardening | Oracle units corrected and vault post-LP shared-loss bypass fixed on mainnet. Independent runtime/receipt/state checks confirm the installations. Both pair ledgers and stored exchange rates were preserved. |
| Operational product | At the latest recorded verification, supply/borrow, ordinary seizure and strategy allocation/settlement remain paused; the stock guard rejects stale prices. Reopening needs fresh guarded prices, signed admin actions, liquidity/keeper checks and observed transaction flows. LP reactivation is separate. |
| Runnable frontend | Missing from this repository. A separate developer is building it. No public app or runnable local frontend is established by these sources. [Acceptance criteria](../frontend/README.md). |
| Cross-chain entry, fiat on-ramp, virtual IBAN | Not evidenced here. Link working code plus reproducible flows/provider evidence separately, or label as planned/unverified. A diagram, provider capability or UI mock is not implementation proof. |
| Loss mitigation | Capped, available, in-kind reserve support for qualifying realized deficits. Both NVDA and USDG claims can absorb uncovered losses. [Current bounds](OPERATIONS.md#exact-reserve-bounds) include the small actual token balances; dollar ceilings are not funded guarantees. |
| Mainnet adoption | Not established by deployment receipts or local forks. Historical zero-Borrow reports stop at their stated blocks; current zero debt does not establish zero lifetime borrowing. Do not claim a current wallet count, borrow volume or PMF without a dated event analysis. |
| Keeper | Dedicated funded cloud signer and durable journal are evidenced. No observed live cloud liquidation is established; external heartbeat alerts remain deferred. |
| Governance | Bootstrap EOA control and historical governor credential exposure remain unresolved. Safe deployment/funding/migration is explicitly user-deferred. Do not call governance migrated, multisig-controlled or the prior credential fully retired. |
| Security | Unit/fuzz/invariant, fork, reproducibility and scoped static-analysis evidence exists. There is no completed independent external audit. Preserve separate test scopes; inherited repetitions are not new unique tests. |

Suggested current description: “Peridot has deployed productive lending liquidity and isolated margin infrastructure for NVDA Stock Token/USDG markets on Robinhood Chain mainnet, under restricted canary limits. The reserve provides capped in-kind loss mitigation. Reopening and frontend acceptance testing remain in progress.”

## Finish in this order

1. **Runnable frontend — separate frontend developer.** Publish the app's source/lockfile, public environment placeholders, exact local commands and a preview link. Make unavailable-price and pause states usable now; complete transaction acceptance when the contracts reopen. The contract team supplies interfaces and verified state, not a replacement UI.
2. **Reopening and demonstrated lifecycle — operator/contract team.** Confirm fresh prices, liquidity, risk/keeper state; simulate; locally sign reviewed actions; record successful supply, borrowing, repayment, redemption and small long/short tests. Keep LP allocation status distinct from lending availability. Never bypass guards to obtain a demo.
3. **Governance — owner resumes explicitly.** Deploy and verify the designated Safe's owners/threshold, then follow the existing timelock/ownership migration and old-role revocation procedure. Document actual transactions before changing the claim. This review does not override the user's deferral.
4. **Limits and reserve funding — explicit risk decision after validation.** The $2 gross/$1 debt per-position caps are canary limits. Raising them merely for presentation is not validation. Review liquidity, reserve balances, liquidations, governance and total exposure first; there is no established aggregate margin cap or tester allowlist. This review changes neither limits nor funding.
5. **Independent tester evidence and analytics — team.** Invite actual interested testers after the relevant readiness checks. Record their experience and returning use. Separate operator/funded acceptance tests from independent activity. Twenty addresses are not necessarily twenty people, and repeated loops are not product-market fit.
6. **Application — submission author.** Link each claimed feature to working code, a reproducible UI path and dated evidence. Keep unavailable integrations in a clearly labeled roadmap/unverified section. Use the current branch and evidence, not an undated mixture of historical reports.

## Usage dashboard acceptance

A public Dune dashboard can be useful if chain data or a documented ingestion path is available; this repository does not establish either dashboard availability or Robinhood Chain indexing support. A reproducible event export/dashboard is an acceptable first evidence source while that is checked.

Every metric must state chain 4663, contract addresses, starting/ending blocks, timestamp, query/source and coverage limitations. Include successful supply/borrow/repay events, position openings/closings by direction, funded accounts, outstanding balances and LP/reserve state. Separate historical volume from current debt, operator tests from external activity, and wallet addresses from position-account contracts. Do not double-count pToken assets and the vault assets those claims represent when calculating TVL. Report actual fees and reserve balances rather than projected APY or coverage ceilings as funded amounts.

Useful adoption evidence includes completed tester journeys, failed-action rates, repeat use and concrete feedback. Report only collected results. No usage target or fabricated event total is presented as achieved here.

## Historical record precedence

The [September 17 preparation report](../contracts/robinhood-vaults/deployments/robinhood-mainnet.margin.md) describes a fork before deployment and now carries an explicit superseded notice. September 18 rollout, September 19 risk configuration and September 26 corrections supersede its operational status. Its original body remains intact; pinned Solidity, artifacts, JSON manifests and ABIs remain unchanged.

The old “stranded surplus” label is qualified in [operations](OPERATIONS.md): a guarded checkpoint can re-credit tracked residue, but actual recovery is still outstanding. The boosted delegate's caught failures are documented with event/state verification requirements; a successful outer receipt alone does not prove a vault action succeeded.
