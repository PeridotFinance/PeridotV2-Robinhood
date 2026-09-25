# Robinhood margin keeper

The keeper uses Foundry account `robinhood-deployer`, expected sender
`0x94696d767e65a75581145646960FA0eC886cE5d2`, as requested. It scans positions on
the verified mainnet executor and simulates each liquidation on the native RPC.
No position or swap is opened by this service.

## Local operation

From the RobinhoodVaults repository, check once without signing:

```bash
bash script/run-margin-keeper.sh --once
```

Monitor continuously without signing:

```bash
bash script/run-margin-keeper.sh
```

Run with signing in an attended terminal:

```bash
bash script/run-margin-keeper.sh --execute
```

Foundry prompts for the encrypted account password when a liquidation must be
signed. This attended mode is not an unattended liquidation service. For
unattended operation, the operator can configure a local owner-readable password
file and pass its absolute path with `--password-file`. The service never prints
or copies its contents. Do not send its contents or a private key in chat.

The deployer is also governance. A shared local signer lock prevents this updater and keeper from signing
concurrently on the same checkout. External wallet tools and other hosts cannot
see that lock. Run only one signing keeper, and stop it while
sending deployment/governance transactions with this account. Do not run separate
keepers with different state directories and the same signer. A dedicated keeper
account is the planned replacement; changing the signer requires updating the
explicit sender/recipient policy and validating it again.

## Execution and recovery

- Only the fixed mainnet liquidator and `liquidate` calldata with the expected
  position and deployer recipient are allowed; native value is always zero.
- Per-call gas limit: at most 8,000,000. Maximum gas price: 0.1 gwei. Maximum
  execution-gas spend: 0.0008 ETH per call. There is no cumulative spending cap;
  many positions or retries after successful partial liquidations can cost more.
- Poll interval: 15 seconds. Scan limit: 100 historical position IDs. It fails
  explicitly if that limit is exceeded; increase it deliberately or add an indexer
  before growing beyond the canary.
- Runtime hashes and proxy implementations are verified at startup. Restart and
  reverify after any upgrade; do not keep this service running across upgrades.
- An intent is persisted before submission. Receipt checks bind sender, nonce,
  target, calldata, zero value, chain and canonical block. On mainnet, the keeper
  waits for a following block before considering a receipt confirmed.
- Pending receipts are reconciled before any other position is considered.
  Unknown submissions, failed receipts, no debt reduction, or an eligible but
  unexecutable liquidation stop execution for operator inspection. Never delete
  the journal to force a retry. Inspect the recorded nonce/hash first.
- Price unavailability is reported as `degraded_oracle`; the keeper keeps
  monitoring. It cannot bypass the contracts' stale-feed guard. Healthy-position
  simulation reverts are normal; liquidatable-position simulation failures need
  attention.

State is in `deployments/margin-keeper-live/` by default. `health.json` reports the
last poll, execution mode, sender and position outcomes. Monitor both its status
and timestamp; a dead process cannot update its own health file. Receipt and
attempt journals are per position. They must survive process/host restarts.

At these tiny caps, liquidation rewards need not cover gas; the governor funds
operations. Never treat `manifest.tradingReady` as proof that the keeper is running.

## DigitalOcean preparation

`peridot-margin-keeper.service` is a systemd template, not an installed service.
It expects the repository at `/opt/peridot/RobinhoodVaults`, a `peridot` service
user, Foundry under `/home/peridot/.foundry/bin`, a persistent state directory at
`/var/lib/peridot-margin-keeper`, and an owner-only password file at
`/etc/peridot/keeper-password`. The encrypted Foundry keystore must also be
provisioned by the operator with restricted permissions. The DigitalOcean API
credential only provisions infrastructure; it does not unlock the wallet.

Before enabling it on a host: validate runtime/receipt checks in read-only mode,
configure external health/staleness alerts and restart recovery, and verify the
keystore address locally. A server and credential have not been supplied yet;
no DigitalOcean resources or unattended mainnet keeper have been started.
