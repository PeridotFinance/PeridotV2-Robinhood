# Keeper alerting follow-up

The user prefers Telegram and explicitly deferred choosing/configuring the destination. No Telegram bot, webhook, new paid service, notification, or external heartbeat monitor has been installed by this remediation.

The latest recorded direct health read found the dedicated keeper execution-enabled, gas-ready with `0.002 ETH`, the existing durable journal, and no positions. This proves a recent loop ran, not future availability or an executed liquidation. Keep the dated JSON beside the runtime evidence.

## Required design when resumed

Run the watchdog outside the keeper's worker and preferably outside its provider failure domain. A process that has died cannot send its own Telegram outage notification. The watchdog should consume a signed/authenticated heartbeat or independently fetch the existing sanitized health record. Require both receipt time and monotonic/fresh `checkedAtUnix`; a repeated old payload must not keep the service healthy.

Use separate conditions:

| Signal | Action |
| --- | --- |
| No fresh heartbeat for 120 seconds (15-second worker cycle) | Page once; repeat on a configured escalation interval, then send recovery. |
| `operator_attention`, lost journal lock, ambiguous transaction | Immediate operator page; preserve journal, never retry an unknown submission automatically. |
| `executionEnabled=false` or `gasReady=false` unexpectedly | Page; distinguish maintenance mode from an unplanned loss of execution. |
| `degraded_oracle` | Report oracle unavailability separately from worker death. Weekend/holiday freshness failures may be expected, but positions still lack liquidation availability. |
| Receipt remains pending | Track age and alert beyond the chosen threshold; do not confuse a healthy process with successful settlement. |

Before declaring alerts active, inject a missed heartbeat and prove a Telegram message reaches the intended recipient; restore the signal and prove recovery. Test a stale replay and a synthetic oracle-unavailable condition. Store bot credentials only in the monitoring service's secret store, never source files or chat. Record recipient authorization, live test timestamps and the monitoring ownership/runbook.
