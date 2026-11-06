# Cloud range keeper

An unattended worker that runs the concentrated-liquidity keeper (`remediation/tools/range_keeper.py`) with a
**dedicated wallet** on DigitalOcean App Platform. It only ever calls `checkpoint`, `rebalance` and `recenter`
on the vault; the contract enforces eligibility, cooldown, the rolling daily limit, the range, removal floors and the
loss bound, so the worker cannot do anything a keeper-role holder could not already do.

## Safety properties

- The wallet holds **only `KEEPER_ROLE`** (granted through the timelock) plus a little gas. The worker refuses to
  start sending if the wallet holds `DEFAULT_ADMIN_ROLE`, `CONFIG_ROLE` or `GUARDIAN_ROLE`.
- It refuses to send if the vault or adapter implementation is not the reviewed one (`worker.py` pins both).
- `EXECUTE=false` by default: it only monitors until the operator provisions the signer.
- The keystore and its password are uploaded as encrypted run-time secrets. **Cloud administrators can potentially
  read them**; that is why the wallet is dedicated and low-privilege. Never fund it beyond a few cents of gas.
- Logs are JSON lines with no secrets or transaction bodies.

## Setup (all local, user-signed)

```sh
python3 keeper/create_wallet.py                     # once: prints the public address only
# 1. grant KEEPER_ROLE through the timelock (1h delay)
KEEPER_ADDRESS=<address> FOUNDRY_PROFILE=vault_v3 forge script remediation/script/GrantRangeKeeperRole.s.sol:QueueGrantRangeKeeperRole ...
# 2. fund the address with ~0.002 ETH on chain 4663
# 3. after the delay: ...:ExecuteGrantRangeKeeperRole
python3 keeper/provision.py --deploy                # creates the app in monitor-only mode
python3 keeper/provision.py --enable                # uploads the signer, sets EXECUTE=true
python3 keeper/provision.py --disable               # back to monitor-only at any time
```

**Do not run `--deploy` on an app that is already enabled.** From reading `provision.py` (not executed): it rebuilds the spec from `app.json` and keeps only `EXECUTE`, dropping both `KEYSTORE_*` secrets, so the worker would crash-loop at startup.

**Gas is a known failure mode.** Below 0.0003 ETH the worker logs `awaiting_gas` and sends nothing, and the only configured alert is `DEPLOYMENT_FAILED`. That stopped it for 10.77 days (2026-10-22 to 2026-11-02); see `ramses-vaults/research/dual-range-backtest/evidence/README.md`, section 18. The live app is `peridot-range-keeper` ($5/month), separate from the $12/month margin keeper.

`--enable` refuses unless the wallet already holds `KEEPER_ROLE`, is funded, and the preflight passes.

## Tests

```sh
python3 -m unittest discover -s keeper -p 'test_*.py'
```
