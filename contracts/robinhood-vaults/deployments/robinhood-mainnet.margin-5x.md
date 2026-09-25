# Mainnet 5× risk update

The deployed stack supports up to 5× without another deployment. This package
changes both NVDA/USDG risk directions from 2× / 50% initial / 25% maintenance to
**5× / 20% initial / 10% maintenance**. All other risk fields stay unchanged,
including **$2 gross and $1 debt per position**, 5% liquidation bonus, and 1%
slippage/oracle-deviation limits. The debt cap remains binding: a full 5×
position cannot use the full $2 gross cap. There is no aggregate cap or tester
allowlist.

**The update is complete on mainnet.** All four user-signed queue/apply
transactions are verified. Follow-up at block **67180996** confirmed both 5×
tuples, consumed queues and unpaused margin/flash loans. The frontend manifest
is `MAINNET_5X_RISK_VERIFIED`, with `tradingReady=true` and all 16 ABI hashes
checked. No further update command is required. See
[completion verification](./margin-mainnet-5x-live/completion-check.json).

## Queue completed on mainnet

Both queue transactions succeeded at blocks **67136983 / 67137003**. A wrapper
receipt-file lookup error after broadcast has been fixed and both receipts
reconciled without new transactions. Five regression tests cover the coexistence
of dry-run and mined receipts. **Do not queue again.**

The apply stage is also complete. Both stage journals are verified. The commands
below are retained as a record of the process; do not replay them.

## Completed signing process (reference)

First start the keeper monitor as described in the
[keeper operations guide](../margin-mainnet/keeper-service/OPERATIONS.md).
Arrange attended or unattended signing before team positions. Stop a signing
keeper while using the same deployer for the governance commands below.

```bash
cd /Users/joshua/Peridot/RobinhoodVaults
bash script/update-margin-5x.sh queue --broadcast
```

Foundry requests the encrypted `robinhood-deployer` password in your terminal.
This queues two risk changes and prints the earliest application timestamp
(current governance delay: one hour). At or after that timestamp:

```bash
bash script/update-margin-5x.sh apply --broadcast
```

No pause or additional activation queue is required. The update leaves existing
pause flags unchanged. Both tuples are checked before any transaction is planned;
unexpected risk settings, owner, implementation or bytecode changes stop the
runner. Caps are retained. The same on-chain routines skip an already queued or
applied direction, and do not reset a queued timestamp.

Read-only status: `bash script/update-margin-5x.sh status`.
Omitting `--broadcast` from queue/apply only simulates. For an interrupted signed
invocation, run `bash script/update-margin-5x.sh --reconcile` first. It verifies
already-mined complete receipts without sending anything. Partial, failed or
unknown submissions stop for manual inspection; do not delete the intent journal.

The final apply verifies both settings and refreshes
`frontend/margin-mainnet/manifest.json`. ABIs and addresses do not change. The
frontend should read on-chain risk, allow requests up to `500`, and continue to
simulate opening with the caps. The current quoter sizes by leverage and liquidity;
it does not enforce the dollar caps itself. The executor/risk engine rejects
oversized positions, so do not interpret a successful quote as a guaranteed open.

Do not rerun the original deployment runner after this update: its final checks
intentionally expect the original 2× deployment configuration. Preserve the
original 66-transaction deployment evidence as historical evidence.

## Measured mainnet-fork results

Full suite: **16 passed, 0 failed/skipped**, mainnet L2 block **66736808**,
native EVM block **26008668**. The test uses the actual live margin proxies,
Peridot markets, token balances, flash reserves, vault and router. The baseline
open/close has no token funding injection or price override. Governance changes
and elapsed time occur only in the fork.

Both directions start with **$0.20** margin at an entry oracle price of
**$222.44729849**. Realized gross leverage is **4.84× long / 4.92× short**; 5× is
an upper bound after conservative quoting and execution costs. Short directional
stock exposure is gross leverage minus one.

| Scenario | Last healthy NVDA price | First liquidatable NVDA price | Adverse move from entry |
| --- | ---: | ---: | ---: |
| Long, current vault allocation | $196.141964 | $196.122352 | about −11.83% |
| Long, rebalanced boosted LP | $196.200813 | $196.181194 | about −11.81% |
| Short, current vault allocation | $251.219317 | $251.244439 | about +12.95% |
| Short, rebalanced boosted LP | $251.118854 | $251.143966 | about +12.90% |

The pool is moved across its real ticks using explicitly injected shock-driver
funds; the external feed is overridden only in shock scenarios to track that
moved pool, followed by a real vault checkpoint. Boundaries are searched in
one-basis-point pool-move increments. Healthy-side liquidation reverts and
first-liquidatable-side execution completes with zero debt. These are scenario
measurements at the pinned state, not guaranteed future price buffers.

Immediate round trips return **$0.192533 long / $0.192501 short** as underlying
value of withdrawn pUSDG shares from $0.20 margin, excluding gas. Shares need a
separate market redemption to become wallet USDG. Coverage also includes
partial/full close, the 5× ceiling, debt-cap rejection, a stale-price repay and
in-kind exit, and severe pool shocks (−40% long / +70% short). The canary debt is
below the engine's $10 dust threshold, so liquidations are full at these caps.

The public RPC prunes historical storage quickly. The original pin and incomplete
follow-up run are retained separately; the completed suite uses the refreshed
pin above. A reproduction may need a new explicitly recorded pin or an archive
provider. Do not silently substitute latest state and call it the same pin.

## Keeper rehearsal and evidence

The mined local rehearsal at a separate fresh pin, L2 **66833049** / native
**26009474**, completed **15 transactions**: risk queue/apply, opening, a pool/feed
shock and the keeper liquidation. The keeper repaid all debt; a repeat sent no
additional transaction. The liquidation used **1,928,296 gas** with a conservative
7,351,188 gas limit. An initial 5M policy cap correctly refused execution before
submission; the tested policy cap is now 8M. Ten new keeper safety tests and 20
existing keeper/receipt tests passed.

[Validation/source hashes](./robinhood-mainnet.margin-5x-validation.json),
[measured limits](./robinhood-mainnet.margin-5x-measurements.json), and
[local mined evidence](./margin-mainnet-5x-local/result.json) preserve results.
The local record includes scenario-only funding and feed modifications; none
of those transactions or artificial prices were submitted to public mainnet.

## Operations

The user selected the existing deployer for local keeper tests and a later
DigitalOcean host. The service and systemd template are prepared in
`margin-mainnet/keeper-service/`. No unattended mainnet service or cloud host is
claimed installed. See its operations guide for signing, gas bounds, persistent
journals and monitoring. The 5× update requires the user-operated keystore flow;
no new mainnet transaction was submitted by the assistant.

Safe migration remains planned after frontend setup and before $1,000 total
protocol value. This threshold is operational, not an enforced aggregate cap.
