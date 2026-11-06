# Reopen allocation after the recovery

Prepared 2026-11-06. **Nothing is sent.** Runs only after the sequential recovery (`RECOVERY_SESSION.md`) has passed.
**2 Ledger signatures:** a timelock schedule and, at least 1 h later, the execute. Script:
`remediation/tools/reopen_allocation.sh`.

## The call

`timelock.schedule/execute(vault, 0, setPairPause(pair, false, true, false), 0, keccak256("peridot-reopen-allocation-2026-11-1"), 3600)`

The flags go from 110 to **010**: allocation opens and settlement swaps stay paused. Only the timelock can do this; the
guardian can only pause.

## Conditions (all checked by `preflight`, `schedule` and `execute`)

1. **The recovery completed:** flags 110, `positionState.liquidity` 0, `usdgPrincipal` under 0.01 USDG, and
   `cumulativeLossUSDG` equal to the value read right after the recovery. Pass that value as `RECOVERY_LOSS`.
2. **The range keeper can act:** `0x82AB...9f7e` holds `KEEPER_ROLE` and at least 0.001 ETH of gas.
3. **At execute only:** inside the US session (Mon to Fri 14:30 to 21:00 UTC), with an NVDA answer under 1 h old.
   The flag change itself does not read the oracle. Every allocation after it does, and on a stale feed allocation
   simply waits (tested).
4. **Operator decision (not checkable on chain):** that the pilot should keep allocating at all. The 18-day
   counterfactual (`deployments/fee-apr-2026-11-06/counterfactual.json` in the parent workspace) found the range
   earned about 20 % APR in fees but ended 2.9 % behind holding. A weekend and outage withdrawal recovered none of that.

## What reopening does

Fork rehearsal at L2 107,52x after a full recovery:
- After the execute, the keeper's checkpoint and rebalance succeed.
- **The keeper deploys only the matched idle amounts:** about 0.054 USDG and the NVDA it pairs with (liquidity
  33,794,150,368). Almost all the NVDA, about 0.022 NVDA (around $4), stays idle, because the recovery left almost no USDG
  to pair it with.
- Allocation grows only as new USDG supply arrives.

So reopening has almost no immediate effect, and its risk is small at today's size.

## Commands

```sh
bash remediation/tools/reopen_allocation.sh preflight      # must print GO
bash remediation/tools/reopen_allocation.sh schedule       # Ledger 1
# at least 1 h later, US session, fresh answer:
RECOVERY_LOSS=<value from the recovery's state> bash remediation/tools/reopen_allocation.sh execute   # Ledger 2
bash remediation/tools/reopen_allocation.sh state          # flags 010
```

**Abort:** before the execute, `cancel` (Ledger as canceller). After it, the guardian can re-pause allocation at once with
`vault.setPairPause(pair, true, true, false)`.

## Evidence

- `ramses-vaults/test/fork/ReopenAfterRecovery.t.sol`: 3 fork tests, all passing.
  - The full recovery then reopen, showing what the keeper deploys.
  - Only the proposer can schedule, and the schedule can be cancelled.
  - A stale feed blocks allocation, though not the flag change.
- Log: `ramses-vaults/research/dual-range-backtest/evidence/logs/reopen-after-recovery-fork.log`.
- **Script rehearsal:** `recovery_session.sh` (schedule, execute, sync, close), then `reopen_allocation.sh`, on a local fork
  with the Ledger unlocked. Preflight said GO, an early execute was refused, the execute succeeded with flags 010, and the
  keeper checkpoint was accepted.
