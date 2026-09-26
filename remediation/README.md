# September 26 hardening work

This directory contains a correction candidate and new evidence. It is separate from the frozen deployed source under `contracts/` and the `robinhood-mainnet-5x-2026-09-19.1` tag. A passing local test is not evidence of a mainnet installation.

| Workstream | Current status |
| --- | --- |
| Ordinary lending price scale | Corrected on mainnet at adapter `0xe4e03c2fdaef915ace705d106b2660b1e342a2e4`; runtime/immutables, both liquidation quotes and unchanged margin source verified at block 73,329,306. Borrowing and ordinary seizure remain paused. |
| Vault composition / surplus | Reviewed against unchanged source. Three new regressions prove claims remain outstanding when swaps are paused and terminal surplus can be recovered by checkpoint. No vault upgrade proposed for these observations. |
| Governance and old key | Inventory and migration procedure complete. User confirmed the designated Safe is undeployed and explicitly deferred deployment, funding and transfers until a later instruction. Existing key/role exposure remains unresolved. |
| Keeper alerts | Keeper freshly observed running. Telegram destination and external monitoring deployment explicitly deferred by the user. Alerts are not installed. |
| Evidence and claims | Dated evidence, security model, settlement procedure and submission claim boundaries below. |

## Reproduce

From the repository root:

```sh
make verify
make test-remediation
make fork-remediation
python3 remediation/tools/state.py
python3 remediation/tools/vault_state.py
python3 remediation/tools/governance.py
```

The fork command pins a fresh block because the public RPC prunes older state. It writes the block/hash and test output to `evidence/`. All state tools are read-only. `make test` still exercises the original snapshot separately. The remediation suite repeats 40 existing vault tests through its fixture; do not add those to the baseline and claim unique coverage.

## Lending containment and installation

USDG has 6 decimals; pUSDG has 8. The controller needs a USDG price of `1e30` at a $1 peg, while `assetPrices(USDG)` remains `1e18`. This follows the controller's arithmetic and the [Compound oracle convention](https://docs.compound.finance/v2/prices/). Changing token metadata would not repair the defect.

`RobinhoodLendingPriceAdapter` serves only the two configured markets. It checks underlying decimals during construction, uses checked scaling, exposes the original USD18 asset-price API, and has no administrator or mutable configuration. The controller changes its oracle pointer; the margin source retains its existing immutable backing source. No proxy storage layout changes.

1. Simulate containment: `python3 remediation/tools/contain.py`.
2. The governor runs `python3 remediation/tools/contain.py --broadcast` in their own terminal. Foundry unlocks the existing keystore locally. The runner records intents before signing, refuses ambiguous retries, validates receipt identity/canonical block and checks final pause flags. New borrows in both markets and ordinary collateral seizure are paused; repayment remains enabled. The borrow pauses also prevent new isolated margin borrowing.
3. Finish unit/fork checks and static analysis. Confirm zero outstanding debt and paused state afresh. If debt appears, review account-level effects before proceeding.
4. Simulate `remediation/script/InstallLendingPriceAdapter.s.sol:InstallLendingPriceAdapter` using `forge script` and the mainnet RPC. Only after successful simulation and review does the user add `--broadcast --account robinhood-deployer`. The script deploys one adapter and switches the controller pointer; all three pauses stay enabled. Two transactions are involved. If submission is interrupted, reconcile the saved Foundry broadcast receipts before retrying; do not blindly redeploy.
5. Record the deployed address, transaction receipts, code hash, constructor inputs and controller pointer; independently rerun both liquidation quote directions and account valuation. Check the margin USD18 source remains unchanged. Resume only after these checks and the governance decision. Neither the deployment script nor containment runner automatically unpauses.

Installation is complete: do not rerun the deployment script. Use `python3 remediation/tools/verify_install.py` to verify the installed adapter while containment remains active. Canonical creation and switch receipts are recorded in `evidence/installation-transactions.json`.

`remediation/script/ReactivateLending.s.sol` is prepared for a later local signing session. It checks the installed runtime hash and exact oracle identity, requires fresh guarded stock/USDG prices matching the corrected APIs, then restores ordinary seizure before borrowing. It intentionally fails while the stock feed is stale. No reactivation transaction has been sent. Reactivation is separate from the user-deferred Safe migration. Simulate it before any local `--broadcast` invocation and independently verify all resulting flags.

The adapter deliberately preserves the source oracle's policy, including cached/manual fallback and the static USDG peg. This patch does not claim to add a fresh-price guarantee or depeg handling to ordinary lending. See [security model](SECURITY_MODEL.md).

## Supporting records

- [Vault recovery procedure](VAULT_RECOVERY.md)
- [Governance and key retirement](GOVERNANCE.md)
- [Keeper monitoring follow-up](KEEPER_ALERTS.md)
- [Security model](SECURITY_MODEL.md)
- [Evidence and submission claims](MAINNET_EVIDENCE.md)
- [Deployment addresses](MAINNET_DEPLOYMENTS.md)
- [Judge questions](JUDGE_QA.md)

No frontend application is changed here. Frontend implementation remains with the separate developer.
