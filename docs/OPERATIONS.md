# Operational boundaries

This repository describes an existing mainnet deployment with deliberately small margin limits. A clone and local build require no credentials. No live signing, deployment or keeper provisioning happens through the root Makefile or CI.

## Prices and withdrawals

Stock oracle updates follow trading sessions, including weekend and holiday gaps. The configured oracle guard checks freshness, stock-token oracle pause and pool deviation. A calendar weekday is not evidence of a valid price. Freshness failures can block new margin positions, swap-based closes, liquidations and LP-backed withdrawals. Keep repayment and debt-free in-kind exit visible in the eventual frontend.

The paired vault's accounting values loss using the oracle reference price. USDG suppliers share residual strategy losses. Native-token reserve cover is bounded by reserve balance, per-call use, the UTC-day budget and a percentage of the attested deficit. It is not guaranteed principal or unlimited insurance. USDG is fixed at $1 in the recorded pricing policy; that does not detect a depeg.

## Historical keeper and governance status

September 20 records describe a DigitalOcean worker with a durable PostgreSQL journal and funded dedicated liquidation signer. The signer has no checked protocol admin/owner roles. No live cloud liquidation was observed in that record. This packaging task did not verify current cloud health, install monitoring or provision a signer.

The archived local keeper service code is included for review and tests. Existing cloud credentials, operator keystores, passwords, local database state and cloud app configuration are excluded from this repository.

Governance migration from the bootstrap EOA to Safe remains outstanding in the handoff. The former governor credentials were represented in historical cloud deployment configuration; a dedicated keeper does not revoke that former key. Address this through the operator's key/admin migration process. Do not restore superseded cloud deployments.

## Historical findings to retain during review

The paired-vault documentation records a checkpoint composition mismatch and a small stranded surplus. The boosted delegate can catch a failed vault operation while the outer transaction succeeds. Operator actions require suitable gas and resulting-state/event verification. A checkpoint must precede rebalance. See the [vault introduction](../contracts/robinhood-vaults/README.md) and [post-upgrade canary record](../contracts/robinhood-vaults/deployments/robinhood-mainnet.post-upgrade-canary.execution.json).

The records are deliberately preserved with their historical dates and statements. Earlier files may describe paused, undeployed or 2× stages; the final 5× completion evidence and fresh snapshot check establish the selected baseline. Read current on-chain state before any future operation.
