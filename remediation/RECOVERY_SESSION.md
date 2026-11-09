# USDG-shortage recovery: sequential path, Ledger session runbook

Prepared 2026-11-06, updated 2026-11-09. **Nothing has been sent.** The operator schedules the session; the user signs every transaction
on the Ledger `0x064eFE76812F5f18Bd1aA9708739Fb33C810B2dD`.

## What it does

The pair's USDG side is short: at L2 109,891,406 `usdgPrincipal` is 13,449,637 raw (13.449637 USDG) and
`cumulativeLossUSDG` is 1.8247 USD, while the NVDA side holds a surplus. The session's own preflight is authoritative
for the starting numbers. Unwinding the LP with the settlement swap enabled sells the NVDA surplus for USDG, returns
the USDG claim to pUSDG less a realised loss of at most 0.50 USDG, and leaves the pair paused with no liquidity.

| Step | Signer and call | Effect |
| --- | --- | --- |
| 1a | Ledger (timelock proposer): `timelock.schedule(vault, 0, setPairPause(pair, true, false, false), 0, salt, 3600)` | starts the 1 h delay; no state change |
| 1b | Ledger (timelock executor), at least 3,600 s later: `timelock.execute(...)` | flags 010 to 100: allocation paused, **settlement swap enabled**; the window opens |
| 2 | Ledger (pUSDG vault operator): `pUSDG.syncVault(type(uint256).max)`, gas limit 3,000,000 | LP fully unwound, NVDA surplus sold, USDG claim returned |
| 3 | Ledger (vault guardian): `vault.setPairPause(pair, true, true, false)` | flags 100 to 110; the window closes |

**Ledger signatures: 4** (1a, 1b, 2, 3). A cancel or an early close adds at most one. Allocation stays paused at the
end; reopening it is a separate timelock decision.

Salt: `keccak256("peridot-recovery-2026-11-sequential-1")`; operation id
`0x2e7e247f4b224d9498c59f74432e353954b86025867bcb49e4254c3f477f2e16`.

## Session window

- **US regular session:** Monday to Friday, 14:30 to 21:00 UTC (9:30 to 16:00 New York, standard time since 1 November).
  Sign 1a at least an hour before; the schedule does not expire, so a late start only waits.
- **Fresh NVDA answer:** the feed is deviation-triggered (0.5 %) with no heartbeat. The script refuses 1b unless the answer
  is at most 1 h old; the vault itself refuses exits beyond 12 h, and that failure is silent (below). Weekends and US
  holidays are out.
- **Back to back:** sign 1b, 2 and 3 in one sitting, a few minutes apart. Between 1b and 3 the settlement swap is open to
  anyone: a public redeem above local cash can trigger it, and a public deposit is credited without entering the vault
  (tested in `LiveRecovery.t.sol`, `testSequentialWindowInterveningCallsAndSafeStop`). The floor still bounds the price.
- The keeper keeps running. With allocation paused its `rebalance` reverts and it sends checkpoints only.

## Commands

From the repository root, with the Ledger connected and the Ethereum app open:

```sh
bash remediation/tools/recovery_session.sh preflight   # read-only; roles, flags, feed age, session, operation status
bash remediation/tools/recovery_session.sh schedule    # signature 1a
# at least 1 h later, inside the window:
bash remediation/tools/recovery_session.sh execute     # signature 1b; refuses outside the session or on a stale answer
bash remediation/tools/recovery_session.sh gonogo      # read-only eth_call of step 2
bash remediation/tools/recovery_session.sh sync        # repeats the go/no-go, then signature 2
bash remediation/tools/recovery_session.sh close       # signature 3
```

Every step re-reads state first and stops on anything unexpected. `state` prints the post-state at any time.

## Go/no-go before step 2

The read, from the Ledger with the same gas limit:

```sh
cast call 0x55aEd0569c8f0D166D71facE57B57C2f2624a563 'syncVault(uint256)(uint256,uint256)' \
  115792089237316195423570985008687907853269984665640564039457584007913129639935 \
  --from 0x064eFE76812F5f18Bd1aA9708739Fb33C810B2dD --gas-limit 3000000 \
  --rpc-url https://rpc.mainnet.chain.robinhood.com
```

**GO** only if all three hold:

- something comes back: `returned` is not 0;
- the claim is fully accounted for: `returned + realizedLoss` equals the current `ledger.usdgPrincipal`;
- the loss is bounded: `realizedLoss` is at most `MAX_GO_LOSS` = 500,000 raw, which is 0.50 USDG.

The script prints the realised loss in USDG. Zero loss is no longer achievable: keeper checkpoints booked another $0.22
since 5 Nov, and cents must not abort the session (operator, 2026-11-09).

Expected end state after step 2: `positionState.liquidity` 0, `usdgPrincipal` below 0.01 USDG (fees credited during the
call), `cumulativeLossUSDG` rises by at most 0.50 USD, flags 100. Today's rehearsal booked 0.025998 USD.

**NO-GO** in every other case. The script prints the reason: a silent failure (nothing comes back), a loss above the
limit, or a claim not accounted for. A failed settlement does not revert: `syncVault` succeeds, returns 0 and leaves the
position open, and the receipt proves nothing.

Measured on a fork at L2 109,896,890 (2026-11-09):

| oracle move | verdict | returned (USDG raw) | loss (USDG raw) |
| --- | --- | --- | --- |
| -300 bp | GO | 13,199,596 | 250,041 |
| -100 bp | GO | 13,367,830 | 81,807 |
| -50 bp | GO | 13,408,923 | 40,714 |
| 0 | GO | 13,429,010 | 20,627 |
| +50 bp | GO | 13,429,124 | 20,513 |
| +100 bp | NO-GO (silent failure; position untouched) | 0 | 0 |
| +300 bp | NO-GO (silent failure; position untouched) | 0 | 0 |

At +100 bp and above the oracle floor refuses the swap. Other measured cases that come back NO-GO:

| Condition over the delay | eth_call result |
| --- | --- |
| feed answer older than 12 h | NO-GO: nothing unwound |
| gas limit 600,000 | NO-GO: nothing unwound |

## Post-state after each step

Values from the fork rehearsal at L2 block 109,896,890 (native 26,375,953). Live values move with fees and the feed;
the checks are the rules on the right, not the exact numbers.

| After | flags (alloc, swap, emerg) | `usdgPrincipal` | `liquidity` | `cumulativeLossUSDG` | rule |
| --- | --- | --- | --- | --- | --- |
| start | 010 | 13,449,637 | 2,219,690,406,849 | 1.824722085173445060 | position open |
| 1a | 010 | 13,449,637 | 2,219,690,406,849 | unchanged | operation pending |
| 1b | **100** | 13,449,637 | 2,219,690,406,849 | unchanged | window open, nothing moved yet |
| 2 | 100 | **0** | **0** | 1.850719779035220902 | fully unwound; loss rises by at most 0.50 USD |
| 3 | **110** | 0 | 0 | as after 2 | window closed |

After step 2 at the pin: pUSDG cash rose from 12.798247 to 26.227257 USDG against claims of 27.195709; the difference
is lent to the open margin position and returns when it repays. The sync realised 0.020627 USDG and booked 0.025998 USD
of loss. Gas for step 2 was 887,664 in the script rehearsal.

## Abort at each point

| Stopped | State | Do this |
| --- | --- | --- |
| after 1a | operation pending, nothing changed | `recovery_session.sh cancel` (Ledger as canceller), or simply never execute |
| after 1b, go/no-go says NO-GO | flags 100, position open | `recovery_session.sh close` at once: the guardian closes the window with no timelock; no loss is added and the position stays open. Retry needs a new schedule and another hour |
| step 2 reverts | flags 100, nothing changed | close, then investigate |
| step 2 succeeds but the post-state check fails | flags 100, possibly partly unwound | close at once; do not retry blindly; read `state` |
| after 2 | settled | close |
| after 3 | flags 110, settled | done; reopening allocation is a separate timelock call, `setPairPause(pair, false, true, false)` |

## Evidence

- `ramses-vaults/test/fork/SequentialRecoveryLedger.t.sol`: 7 fork tests against the deployed contracts with the
  Ledger as signer, all passing: preconditions, the full path, cancel, guardian close inside the window, silent
  low-gas failure, stale feed, and the oracle grid. 7/7 pass at L2 109,896,890 on 2026-11-09. Log with the pin on its
  first line: `ramses-vaults/research/dual-range-backtest/evidence/logs/sequential-recovery-ledger-fork-2026-11-09.log`.
- The session script itself was rehearsed end to end on a local fork with the Ledger account unlocked: all four
  signatures, the early-execute refusal, the go/no-go and every post-state check. The market interest clock was aligned
  to the fork block, as in earlier rehearsals; the session-hours and feed-age gates are skipped only under `REHEARSE=1`.

### Rehearsal 2026-11-09

Anvil run of the session script at L2 109,898,090:

- schedule: 57,570 gas;
- execute: 57,064 gas, flags 100;
- gonogo: GO, "realised loss if signed now: 0.020627 USDG (GO limit 0.50 USDG)";
- sync: 887,664 gas; after it `usdgPrincipal` 0, liquidity 0, loss booked 0.025998 USD;
- close: 39,820 gas, flags 110.

Run the fork tests again at a new pin on the day:

```sh
R=https://rpc.mainnet.chain.robinhood.com; PIN=$(( $(cast block-number --rpc-url $R) - 20 ))
NATIVE=$(cast rpc eth_getBlockByNumber $(cast to-hex $PIN) false --rpc-url $R | python3 -c "import json,sys;print(int(json.load(sys.stdin)['l1BlockNumber'],16))")
ROBINHOOD_RPC_URL=$R REMEDIATION_FORK_BLOCK=$PIN REMEDIATION_NATIVE_BLOCK=$NATIVE FOUNDRY_PROFILE=ramses \
  forge test --match-path ramses-vaults/test/fork/SequentialRecoveryLedger.t.sol -vv
```
