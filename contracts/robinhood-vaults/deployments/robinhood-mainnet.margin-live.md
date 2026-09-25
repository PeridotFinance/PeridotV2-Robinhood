# Authorized mainnet margin rollout

The user authorized deploying the small 2× stack and leaving it **active** for
frontend integration and Germany-based team tests. The latest instruction is
that **the user runs Foundry and signs locally**. No password file or secret in
chat is needed. All six stages have now been signed by the user and
confirmed on public mainnet.

## Latest: 5× update complete

The [5× update](./robinhood-mainnet.margin-5x.md) is signed, applied and verified
on mainnet in both directions. The original deployment record below describes
the earlier 2× state; the live frontend manifest now reflects **5×**. The keeper uses the same deployer
for initial local operation; DigitalOcean hosting is pending user provisioning.

## Current verified state

All **66 user-signed public-mainnet transactions** across six stages are complete;
no phase is pending and no further deployment command or governance wait remains.
The active verifier passed 31 executable runtimes and 53 address getters,
including exact 2x risk settings in both directions ($2 gross/$1 debt per position).
Read-only follow-up at block **66725458** (2026-09-19 02:28:33 UTC / September 18
22:28:33 New York) rechecked all four activation receipts and confirms margin
opens, flash loans and ordinary borrowing are unpaused. Flash reserves remain
**2 USDG + 0.01 NVDA**; insurance holds 4,999,956,251 pUSDG shares.
The deployment source fingerprint is unchanged.

`frontend/margin-mainnet/manifest.json` is **MAINNET_ACTIVE_VERIFIED**, with
`tradingReady=true`, actual mainnet addresses and **16 verified ABI hashes**.
Give the frontend developer that directory and its integration README.
Arrange the production liquidation operator before live team positions; the
runner installed no keeper service. Migrate governance to Safe after frontend
setup and before $1,000 total protocol value, as agreed.

Actual addresses/receipts: `deployments/margin-mainnet-live/`.
Read-only status snapshot: `deployments/robinhood-mainnet.margin-live-stage-check.json`.
No stock-token positions were opened by the deployment runner.


## Completed runner sequence (reference)

```bash
cd /Users/joshua/Peridot/RobinhoodVaults
bash script/deploy-margin-mainnet.sh --broadcast
```

Foundry prompts for the encrypted `robinhood-deployer` password in the user's
terminal. **All stages are complete; no further broadcast is required.**
The [full runner guide](./robinhood-mainnet.margin-user-runner.md) documents the
phases, status command, interruption recovery, final settings and output files.

This runner passed 24 fork tests, 20 Python tests and a complete 66-transaction
localhost rehearsal using real funded mainnet snapshot balances. The local
rehearsal ended active and verified 31 executable runtimes plus 53 address
getters. Recovery and completed reruns sent no duplicate transactions. See
[runner validation](./robinhood-mainnet.margin-user-runner-validation.json).
This is rehearsal evidence, not a public-mainnet deployment claim.

## Funding before deployment (historical)

At block 66354064, the governor
`0x94696d767e65a75581145646960FA0eC886cE5d2` held **10.997001 USDG**,
**0.105669533924500770 NVDA** and **0.042543614909104898 ETH**. Both planned
flash reserves (2 USDG + 0.01 NVDA) were available in its wallet; these have now
been deposited, alongside $1 equivalent of existing pUSDG insurance shares.
The earlier NVDA shortfall is resolved. Both debts were zero and the margin risk
hook unset. The runner refreshes state before execution.
See [funding verification](./robinhood-mainnet.margin-funding-check.json).

## Final state and governance

The migration temporarily paused borrowing and then restored ordinary lending.
Both governance delays and activation are complete; margin is **active at 2×**. The
runner does not call finishCanary or open/close stock positions for the user.
Limits are $2 gross/$1 debt per position; there is no aggregate cap or tester
allowlist in this version.

The user will migrate to **Safe after deployment/frontend setup and before total
protocol value reaches $1,000**. This is an operational threshold, not an enforced
aggregate cap. Safe's address is not yet supplied. The migration must cover
relevant existing market, timelock and new margin operational powers.

The runner does not install a production liquidation signer/service or monitor.
Arrange the liquidation operator before live team positions. The team's location
and the contracts' active state do not establish legal eligibility by themselves.

## Handoff and authoritative records

Actual user-run receipts and progress are under
`deployments/margin-mainnet-live/`. The production frontend manifest now has
tradingReady=true, verified live addresses and refreshed ABIs. Give the developer the [frontend guide](../frontend/margin-mainnet/README.md).

The original [preparation report](./robinhood-mainnet.margin.md), including its
23-test/106-transaction evidence, is historical. New local runner evidence is
separate under `deployments/margin-mainnet-user-runner-local/`. Never use those
local addresses or receipts as a mainnet deployment record.
