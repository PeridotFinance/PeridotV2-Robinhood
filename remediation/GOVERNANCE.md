# Governance migration and key retirement

User-designated destination: **`0x80f4207e0810EA2C39B6C8387E5ffC6FF34dfB12` on chain 4663**. The user confirmed this is an undeployed Safe created through the Safe website and explicitly deferred **deployment, funding and migration until a later instruction**. Do not initiate any of those actions as part of finishing this change. The read-only inventory found no contract code there; deployment and verification of intended owners/threshold must precede assigning authority when the user resumes this work.

## Verify the destination first

Refresh `python3 remediation/tools/governance.py`. Require deployed code, a recognized Safe implementation, the intended distinct owners, threshold of at least two, and review of enabled modules/guard/fallback handler. An address deployed on a different chain is not sufficient. Fund only as needed for the chosen execution model after confirming it. Verify the Safe can sign and execute on Robinhood Chain before irreversible owner transfers.

Do not treat a balance top-up as Safe deployment. Do not weaken the quorum check to make migration pass.

## Authority inventory and sequence

The exact dated inventory is [`evidence/governance-inventory.json`](evidence/governance-inventory.json). Preserve the timelock as proxy-upgrade/configuration authority.

| Authority | Transfer / retirement action |
| --- | --- |
| Timelock proposer, canceller, executor | Schedule grants to Safe; wait the live minimum delay; execute grants. Demonstrate a Safe-originated schedule **and execution**, then schedule old-governor revocations. |
| Vault keeper and guardian | Timelock grants Safe the operational roles, verifies them, then revokes old governor. Do not grant these powers to the liquidation wallet by default. |
| Controller admin | Old admin calls `_setPendingAdmin(Safe)`; Safe executes `_acceptAdmin()`; verify admin/pendingAdmin. |
| pUSDG and pNVDA admins | Same two-step pending/accept pattern, verified independently for each market. |
| Controller pause guardian | Current admin sets the verified Safe with `_setPauseGuardian(Safe)`; confirm the old address is gone. Borrow-cap guardian is currently zero; recheck rather than inventing a new role. |
| Margin config, flash vault, insurance fund, fee distributor, margin oracle and router | Each current owner calls `transferOwnership(Safe)` after the Safe execution proof. These are one-step owner changes; no automatic rollback. Verify each `owner()`. |
| Original StockSimplePriceOracle | Owner calls `setAdmin(Safe)`, removes old governor's admin via `removeAdmin(old)`, then `setOwner(Safe)`. Verify both admin mapping entries and private owner storage (slot 4 in the pinned nonproxy layout). Transferring owner alone leaves old price-admin rights alive. Review transaction history for any additional unenumerable admins. |
| ProxyAdmin owners | Keep timelock ownership. Verify all vault and margin proxies remain bound to the expected ProxyAdmins/timelock. |
| Router manager / operators | Keep the legitimate swap-module manager. Old governor is currently not an operator; verify after migration. Do not replace protocol contract wiring with the Safe. |
| Factory configurator | Immutable historical governor address; `setExecutor` is one-shot and executor is already nonzero. Verify that binding; this is not a currently reusable administrative power. |

`contracts/robinhood-vaults/script/MigrateTimelockGovernance.s.sol` already produces staged timelock role payloads. It does **not** migrate the other rows above. Grant and revoke phases must remain separate. The timelock's self-admin role and delay remain intact.

## Finish retiring the old key

The dedicated keeper does not revoke historical copies of the governor credential. After completing and verifying every transfer/revocation, move the old governor's remaining assets using the user's local signer and review token approvals. Keep enough gas for outstanding revocation transactions until they finish.

Request deletion of historical cloud secret-bearing deployments through the provider and record the provider response. Do not restore superseded deployment `7b3b0db7-3184-4c9f-b1a2-35ba87c235d5`. Do not claim provider erasure has occurred without evidence. Changing the keystore password does not retire the signing key.

Completion means current onchain proof that the old governor has no reusable admin/owner/price-admin/operational roles, a tested Safe execution path, and documented handling of remaining assets and provider history. Funding the Safe alone is not completion.
