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

## Liveness alarm

`.github/workflows/keeper-alarm.yml` runs `keeper/alarm.py` on a schedule. It only reads. A page fails the job and keeps
one issue open, assigned to the operator; it closes itself on recovery.

| state | meaning | pages |
| --- | --- | --- |
| `HEALTHY` | checkpoint recent, deployment ACTIVE, heartbeat recent | no |
| `CHAIN_OK_PROCESS_UNKNOWN` | checkpoint recent, process not readable: **not** a claim of health | no |
| `EXPECTED_WAIT` | feed over 12 h old, or just back: expected oracle refusal | no |
| `EXPECTED_PAUSED` | allocation paused or emergency mode: the worker cannot send | no |
| `GAS_LOW` / `GAS_CRITICAL` | under 3 active days of gas (`ALARM_BURN_WEI_PER_DAY`) / under the worker's floor | yes |
| `STALLED` | gas, fresh feed, no pause, checkpoint over 7,800 s old, nonce not moving | yes |
| `FAILED_SENDS` | nonce moved without the checkpoint advancing, or `SEND FAILED` / `REVERTED ON-CHAIN` in the logs | yes |
| `PROCESS_DOWN` | deployment not ACTIVE, no heartbeat for 900 s, or `EXECUTE=false` (needs DigitalOcean access) | yes |
| `RPC_FAILURE` | the alarm's own reads failed: says nothing about the keeper | yes |

A passing job means "no page", not "healthy". **While the feed is stale or the pair is paused, a dead process is invisible
from chain reads.** The optional `KEEPER_ALARM_DO_READ_TOKEN` secret (read-only `app:read` scope) closes that gap.

**A silent alarm is not evidence of health:** GitHub delays scheduled runs and disables them after 60 days without repo
activity, and nothing watches the alarm itself. Delivery test: `gh workflow run keeper-alarm.yml -f drill=true`, confirm the
notification reached a person, then `gh workflow run keeper-alarm.yml` and confirm the issue closed.

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

`--enable` refuses unless the wallet already holds `KEEPER_ROLE`, is funded, and the preflight passes.

## Tests

```sh
python3 -m unittest discover -s keeper -p 'test_*.py'
```
