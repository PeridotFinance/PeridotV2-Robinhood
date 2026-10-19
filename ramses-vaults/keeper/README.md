# Ramses cloud keeper

This is the isolated Ramses adaptation of `keeper/worker.py`, its Docker image and operator tools. The original `keeper/` is unchanged. The service is monitor-only until the operator explicitly enables it. Never use a governance signer: the dedicated address holds only vault `KEEPER_ROLE` and the two markets’ `vaultOperator` assignment, plus a small gas balance.

The worker sends only fixed `harvest`, `checkpoint`, `rebalance`, `recenter` and market `rebalanceVault` calls. A maintenance pass harvests, checkpoints, restores both market cash targets, rebalances the vault and attempts recentering. Each transaction is simulated, gas-bounded, journaled before submission, and checked for a successful receipt. A caught delegate `VaultDepositFailed`/`VaultWithdrawalFailed` event is an alert even with receipt status 1. After checkpoint and market maintenance, the keeper previews recentering. If due, the vault-rebalance step retains idle assets for the new range instead of increasing the NFT about to be removed. Otherwise it rebalances before checking recenter again. This includes first range setup and avoids resetting the upstream reward-validator age immediately before removal.

The default loop interval is 300 seconds, with a 60-second minimum. Maintenance runs hourly or when the oracle price differs by **at least 50 bps in either direction** from the last confirmed checkpoint price. Attempts are rate-limited to 60 seconds. A cooldown wait records its on-chain expiry so recenter is reconsidered then even without another price move or an elapsed hourly cadence. The baseline is read at the successful transaction’s receipt block, persisted across restarts, and never advanced on a failed or merely submitted transaction. First startup with no baseline checkpoints conservatively. The vault’s internal harvest also checkpoints, but the worker requires a separate successful checkpoint before rebalancing.

Gas defaults to a 0.1-gwei maximum; `MAX_GAS_PRICE_WEI` may raise it to at most 2 gwei. Limits are 4M harvest, 3M checkpoint, 6M per delegate, 5M rebalance and 8M recenter, with a 32M total submitted gas-limit budget per cycle. Deadlines use `max(chain timestamp, wall clock) + 120 seconds`. The on-chain 300-second deadline limit remains authoritative; a badly skewed clock fails closed. Estimates never select the transaction gas limit or gas price.

`RecenterNotNeeded`, cooldown/rate limits, `DeployLossTooHigh`, and a stale feed are waits. `CheckpointStale` is a wait only on the clean initial harvest attempt; it cannot excuse rebalancing after a failed checkpoint. `InvalidConfiguration`/`InsufficientLiquidity` count as dust only for vault rebalance when the freshly measured smaller native-side idle value is below $0.01 (the matched deployment budget). Other reverts, reward mismatches, code/role changes, malformed responses and mined reverts stop the service and alert. The worker never enables reward forfeiture or changes governance settings.

## Operator deployment

These commands are tooling for the operator. Development tests never execute wallet creation, secret provisioning, cloud APIs or real-network broadcasts.

1. Run `python3 ramses-vaults/keeper/create_wallet.py` locally. It creates owner-only encrypted files under `ramses-vaults/.keeper-signer/`, refuses overwrite and prints only the public address. Keep the generated public wallet record. No source contains a private key or password.
2. Deploy the stack following README sections 10–11, passing this public address as `RAMSES_KEEPER`. Review the timelock batch: configure caps/markets, seed both markets, fund 0.0002 NVDA reserve, then enable both supplies and borrowing at 80% CF. The deployment already assigns the keeper role/operator. To grant or rotate later, use the script below. Rotation must also revoke the old vault keeper role via the timelock; the grant script deliberately does not guess which old addresses to revoke.
3. Verify the deployed stack, then build a public manifest against the reviewed local build:

   ```sh
   python3 ramses-vaults/keeper/build_manifest.py \
     --rpc-url "$ROBINHOOD_RPC_URL" \
     --deployment ramses-vaults/deployments/live.json \
     --out ramses-vaults/keeper/manifest.json
   ```

   Review the deployment addresses, code hashes, proxy/beacon slots, dependency getters, pool, gauge, feed, reward-token set and bootstrap nonce. Keep the manifest with the reviewed release. The worker never refreshes pins automatically. Supply its JSON as `RAMSES_MANIFEST_JSON`. Any changed pin requires review and an explicit new manifest. Code hashes cannot detect malicious storage-only governance changes that do not affect pinned getters; upstream governance remains a trust boundary.
4. Fund the dedicated keeper with a small gas balance; 0.002 ETH is the inherited operational starting point. The worker alerts below 0.0003 ETH and checks each transaction’s maximum gas cost. Choose funding against the configured gas cap and measured consumption; those amounts are not a guaranteed runway at 2 gwei.
5. Review `app.json` repository/branch and Docker build, configure the operator’s `doctl` context `ramses-keeper`, and run `python3 ramses-vaults/keeper/provision.py --deploy`. This always deploys monitor-only. It never copies the Uniswap app or signer. Only one instance may use the wallet.
6. Verify the monitoring heartbeat, pins, identity, journal storage and alerts. Then run `python3 ramses-vaults/keeper/provision.py --enable`. Preflight runs before reading the dedicated signer files or calling the cloud API. It verifies the keystore address and uploads keystore/password only as runtime secrets. Cloud administrators can access those secrets; that is why this wallet has no governance power. `--disable` returns to monitor-only; it retains secrets until the operator removes them.

The exact RPC allow-list is the production Robinhood URL or parsed HTTP loopback (`127.0.0.1`, `localhost`, `::1`). Credentials, query strings, redirects and prefix lookalikes are rejected. Unlocked sends exist only in the test harness, after both loopback and anvil checks; the production entry point has no unlocked option.

## Timelock role grant or rotation

Set public `RAMSES_PUBLIC_SENDER`, `RAMSES_TIMELOCK`, `RAMSES_VAULT`, `RAMSES_VAULT_CODEHASH`, `RAMSES_KEEPER`, `RAMSES_STOCK_MARKET`, `RAMSES_USDG_MARKET`, `ROBINHOOD_RPC_URL`, and the encrypted keystore alias `FOUNDRY_ACCOUNT`.

```sh
# Default mode prints ordered schedule/execute payloads; no transaction is broadcast.
bash ramses-vaults/keeper/grant_keeper.sh
RAMSES_GRANT_MODE=queue bash ramses-vaults/keeper/grant_keeper.sh
# Operator reviews simulation, then explicitly adds --broadcast to queue.
# Wait the actual timelock delay, simulate execute, review, then explicitly broadcast.
RAMSES_GRANT_MODE=execute bash ramses-vaults/keeper/grant_keeper.sh
```

The wrapper always uses `--account` and the explicit public sender. The Foundry script checks chain 4663, the vault runtime hash, timelock delay/admin, least privilege and both markets’ vault/admin wiring. It prints a three-call batch: vault keeper grant and market operator configuration preserving pair/buffer. Keep governance credentials local; never upload them to this service.

## Journal, restarts and one signer

`STATE_DIRECTORY` defaults to `/state`. Preserve `journal.json` on durable storage and keep a backup with the release manifest. Its atomic rename, file/directory fsync and owner-only permissions protect local persistence. A signer-address lock under the OS temporary directory prevents concurrent local workers, independently of journal pathname. The deployment spec has one instance; **the lock does not coordinate different hosts**. Stop and drain the old instance before starting another. Never run a local signer alongside the cloud instance.

A known pending hash is polled and recovered without resubmission. A persisted intent with no trustworthy hash is ambiguous and stops all sending, including after restart. A mined revert or caught delegate failure also remains pending for operator acknowledgement. Reconcile the nonce and transaction history, verify the receipt and actual state, then update the journal deliberately. Never delete the journal or bump `bootstrapNonce` to conceal an uncertain transaction.

DigitalOcean App Platform’s container filesystem is not treated as durable. The supplied template can monitor there, and its first enabled session works, but a replacement container that loses the journal **refuses to sign after the wallet nonce advances**. Use a persistent single-worker host/mount for unattended restart continuity, or restore the verified journal before enabling a replacement. `provision.py --enable` refuses a used signer under a fresh bootstrap manifest. Automatic App Platform redeployment is not a journal migration or a safe signer handover. This is an explicit availability limitation, not permission to reset nonce checks.

## Local reproduction

```sh
python3 -m unittest discover -s ramses-vaults/keeper -p 'test_*.py'
python3 ramses-vaults/tools/mutations_phase4.py
# Fork commands, results and ten-minute bounds: ../README.md section 11.
```

See [OPERATIONS.md](OPERATIONS.md) for alert thresholds, pending-transaction handling and accepted stale-feed boundaries.
