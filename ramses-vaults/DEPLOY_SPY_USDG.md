# SPY/USDG supply-only range vault: mainnet ceremony

Window: **15 to 17 November, US market hours** (Mon to Fri 14:30 to 21:00 UTC), with the RHSPY answer under 12 h old at
execute. Nothing below is sent by the assistant.

**Signers:**
- A throwaway deployment key sends the 27 contract-creation transactions, with gas only.
- The Ledger `0x064eFE76812F5f18Bd1aA9708739Fb33C810B2dD` signs 4: the two seed transfers, the schedule and the execute.

**Seeds:**
- 0.1 SPY and 75 USDG, held by the timelock `0x6797FB8Ce049B42C5BC2b42Bf76c6d15C7B12498` before the execute.
- SPY must exceed 0.02, the market's minimum first deposit.

**Caps:** $1,000 on the pair.

## 0. Monday 10 November, during market hours, before the recovery session

The Ledger needs about **170 USDG** on Robinhood Chain first:
- 82.1 to buy SPY;
- 75 for the USDG seed;
- 11.1 for the reserve;
- gas.

1. Buy **0.11 SPY**: `python3 deployments/spy-seed-buy/spy_seed_buy.py plan` from the parent workspace. It rehearses the
   swap on a fork and prints fresh Ledger commands: approve, Permit2, swap. It spends about 82.1 USDG at about $737.
2. **Ledger:** `SPY.transfer(timelock, 100000000000000000)` (0.1 SPY seed).
3. **Ledger:** `USDG.transfer(timelock, 75000000)` (75 USDG seed).
4. **Keep on the Ledger** for the reserve: about 0.011 SPY and 11.1 USDG. The reserve accepts deposits only after the
   ceremony configures it (section 5b).
5. **Keeper:** create the Ramses keeper wallet (`RAMSES_KEEPER`). It must differ from the deployer and the timelock and
   hold no timelock role.

## 1. Throwaway deployer (gas only)

```sh
cast wallet new .ramses-deployer keystore            # git-ignored folder; note the printed address as DEPLOYER
# fund DEPLOYER with 0.001 ETH (rehearsal used 0.00018) from any wallet
```

## 2. Deploy (throwaway key, 27 transactions)

```sh
export RAMSES_PUBLIC_SENDER=$DEPLOYER RAMSES_TIMELOCK=0x6797FB8Ce049B42C5BC2b42Bf76c6d15C7B12498 RAMSES_KEEPER=<keeper>
export RAMSES_JOURNAL=ramses-vaults/deployments/spy-usdg-mainnet.json
export RAMSES_STOCK_SUPPLY_CAP=1500000000000000000 RAMSES_USDG_SUPPLY_CAP=1000000000
export RAMSES_STOCK_SEED=100000000000000000 RAMSES_USDG_SEED=75000000
FOUNDRY_PROFILE=ramses forge script ramses-vaults/script/DeployRamsesInKind.s.sol:DeployRamsesInKind \
  --rpc-url https://rpc.mainnet.chain.robinhood.com --sender $DEPLOYER --keystore .ramses-deployer/keystore --broadcast --slow
```

The script refuses to run unless all of these hold:
- the pool is SPY/USDG and the gauge matches;
- the timelock has at least a 1 h delay;
- the keeper holds no governance role;
- the seeds are above the market minimum;
- the journal file does not exist yet.

After broadcasting, it asserts:
- the vault implementation codehash equals a reference build from the same sender;
- every contract is under the EIP-170 size limit;
- the wiring is proxy → router → vault code, and the reserve proxy points at its implementation;
- both ProxyAdmins are owned by the timelock;
- the vault roles are as planned;
- **the deployer holds nothing**: no vault, reserve or guard role, no controller or market admin, no pending admin, and no proxy
  ownership.

## 3. Ledger: schedule and execute (US market hours)

```sh
J=ramses-vaults/deployments/spy-usdg-mainnet.json
cast send 0x6797FB8Ce049B42C5BC2b42Bf76c6d15C7B12498 $(jq -r .scheduleCalldata $J) --ledger --from 0x064eFE76812F5f18Bd1aA9708739Fb33C810B2dD --gas-limit 10000000 --rpc-url https://rpc.mainnet.chain.robinhood.com
# at least 3,600 s later, RHSPY answer under 12 h old:
cast send 0x6797FB8Ce049B42C5BC2b42Bf76c6d15C7B12498 $(jq -r .executeCalldata $J) --ledger --from 0x064eFE76812F5f18Bd1aA9708739Fb33C810B2dD --gas-limit 25000000 --rpc-url https://rpc.mainnet.chain.robinhood.com
```

Rehearsal: schedule 322,117 gas; execute 3,082,214 gas. Check the receipt status, then verify state; never rely on the
receipt alone.

## 4. Verify

```sh
FOUNDRY_PROFILE=ramses forge script ramses-vaults/script/DeployRamsesInKind.s.sol:DeployRamsesInKind --sig 'verify(string)' $J \
  --rpc-url https://rpc.mainnet.chain.robinhood.com
```

It must print `verified: ... deployer holds nothing`. It re-checks:
- the codehash, proxy and router;
- every role, including the vault's `KEEPER_ROLE` on itself, the redeemer and the coverage guard;
- the gate, the in-kind limit 0, and the 2400/600 policy with the $1,000 cap;
- borrowing paused with collateral factor 0 on both markets;
- the deployer holding nothing.

## 5. Retire the deployer (only after verify passes)

```sh
BAL=$(cast balance $DEPLOYER --rpc-url https://rpc.mainnet.chain.robinhood.com); GP=$(cast gas-price --rpc-url https://rpc.mainnet.chain.robinhood.com)
cast send 0x064eFE76812F5f18Bd1aA9708739Fb33C810B2dD --value $((BAL - 21000*GP*2)) --gas-limit 21000 --gas-price $((GP*2)) --legacy \
  --keystore .ramses-deployer/keystore --rpc-url https://rpc.mainnet.chain.robinhood.com
rm -rf .ramses-deployer
```

Rehearsal: swept to 0 wei. The deployer never held a role, so retiring it changes nothing on chain.

## 5b. Fund the loss reserve (seed-sized for launch), right after verify and before the keeper starts

The router's coverage gate refuses every `recenter` and `rebalance` (`CoverageInsufficient`) unless the reserve holds at
least 10 % of the ranged exposure in USDG and 5 % in SPY. With an empty reserve the keeper waits forever (rehearsed).
**Launch: seed-sized, 11.1 USDG and 0.0076 SPY**, which covers the seeds' exposure of about $111. The full-cap amount, 100 USDG and
0.068 SPY, follows when the cap is used. `deposit` is open to anyone; here the Ledger, 4 signatures:

```sh
RES=$(jq -r .reserve $J); PAIR=$(jq -r .pair $J); L=0x064eFE76812F5f18Bd1aA9708739Fb33C810B2dD; R=https://rpc.mainnet.chain.robinhood.com
cast send 0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168 'approve(address,uint256)' $RES 11100000 --ledger --from $L --rpc-url $R
cast send $RES 'deposit(bytes32,address,uint256)' $PAIR 0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168 11100000 --ledger --from $L --rpc-url $R
cast send 0x117cc2133c37B721F49dE2A7a74833232B3B4C0C 'approve(address,uint256)' $RES 7600000000000000 --ledger --from $L --rpc-url $R
cast send $RES 'deposit(bytes32,address,uint256)' $PAIR 0x117cc2133c37B721F49dE2A7a74833232B3B4C0C 7600000000000000 --ledger --from $L --rpc-url $R
cast call $(jq -r .coverageGuard $J) 'headroomUsd()(uint256)' --rpc-url $R   # USD of exposure the reserve now covers
```

## Gauge reward underpayment: booked, bounded, alerted (handoff 00 section 8a)

**Measured on forks of both live Ramses gauges** (SPY 0x739E, NVDA 0x93c6), 2026-11-06:
`ramses-vaults/test/fork/RewardShortfallFork.t.sol`, log `reward-shortfall-fork.log`.

| claim, seconds after the position's last change | SPY gauge: RAM earned / paid | NVDA gauge: RAM earned / paid | shortfall share |
| --- | --- | --- | --- |
| 30 | 0.000144 / 0 | 0.000618 / 0 | 100 % |
| 99 | 0.000475 / 0 | 0.00204 / 0 | 100 % |
| 100 | 0.000480 / 0.000480 | 0.00206 / 0.00206 | 0 % |
| 1 h | 0.0173 / 0.0173 | 0.0742 / 0.0742 | 0 % |
| 7 days | 2.29 / 2.29 | 9.82 / 9.82 | 0 % |

Anti-sybil withholds everything claimed within 99 s of a position change and nothing after. The most that can be lost
is the RAM earned inside that window, about 0.0005 RAM per change on SPY at today's size. Grid re-measured at L2 107,566,558
with the same result.

**Second mechanism: the treasury slice.** If two position changes come less than 99 s apart, the gauge pays the RAM accrued
between them to the Ramses treasury `0x74F1...17a3` (`treasury()` on `0x2Bef16A0`) inside the next claim. It is not withheld,
and `earned()` does not include it. The keeper's normal recenter followed seconds later by a rebalance does exactly this.
The old adapter reverted `RewardClaimMismatch` on that claim, which blocked every later recenter and harvest. This was found
in the full-stack rehearsal and is fixed.

| recenter, then rebalance 5 s later, claim 200 s after | SPY gauge | NVDA gauge |
| --- | --- | --- |
| RAM paid by the gauge for the position | 0.004376 | 0.001126 |
| received by the adapter (= `earned()`) | 0.004273 | 0.001102 |
| sent to the treasury, booked as shortfall | 0.000103 (2.3 %) | 0.000024 (2.1 %) |

The slice is the gap between the changes as a share of the claim's accrual time (5 s of 205 s here). In the anvil
rehearsal the gap was about 5 s and the claim came about 2 h later: 0.0000088 RAM, 0.07 %.
Test: `testTreasurySliceAfterRecenterThenRebalance`.

How the contracts handle it:
- **Forfeiture is on** (`setAllowRewardForfeiture(pair, true)`). `verify` checks it.
- **A shortfall is booked, not rejected.**
  - The entitlement is the larger of `earned()` and what left the gauge for the position.
  - Nothing may arrive that the gauge did not pay, and never more than `earned()`; either reverts `RewardClaimMismatch`.
  - The gap between entitlement and receipt is booked if it is no more than **20 % of the entitlement**. That is the
    ceiling set by `setRewardShortfallCeiling(pair, 2000)` in the batch, and `verify` checks it.
  - A non-exact reward token would also show up as a gap. It is booked under the same ceiling, not rejected outright.
  - The shortfall is added to `rewardShortfall(pair, RAM)`, a per-pair period-loss ledger.
  - Only what arrived is credited. The vault's claim-first accumulator then spreads the loss over that period's holders
    in proportion.
  - Every occurrence emits `RewardShortfallBooked`; alert on it.
- **Above the ceiling,** which in practice means inside the 99 s window, a harvest or recentre reverts
  `RewardShortfallTooHigh` and is retried later. Exits are never blocked: they forfeit the claim (`ExitHarvestFailed`) and
  proceed.
- **The keeper waits 120 s of block time** after any position change before it harvests, recentres or rebalances, so
  neither mechanism normally fires. A `RewardShortfallBooked` alert then means something unexpected happened. The keeper
  treats `RewardShortfallTooHigh` as a wait and `RewardClaimMismatch` as ATTENTION.

## 6. After a clean day of checkpoints

Raise the in-kind limit from 0 with one timelock call: `setInKindLimit(SPY/USDG pair, <USD 1e18 per day>)`. Until then
every in-kind exit is refused.

## Rehearsal record

Local fork of mainnet, 2026-11-06:
- 27 deployment transactions, 41.9M gas, all succeeded.
- Ledger seed transfers, schedule and execute all succeeded.
- `verify` passed.
- Deployer swept to 0.
- Vault ledger after the execute: 0.05 SPY and 37.5 USDG principal, with the rest held as market cash (vault buffer 0.5).

Full-stack rerun after the treasury-slice fix, fork at L2 107,567,951, 2026-11-06:
- The 27 deployment transactions succeeded.
- **Ledger, 8 signatures, all status 1:**
  - seed transfers: 75,642 and 65,378 gas;
  - schedule: 335,794 gas;
  - execute: 3,131,038 gas, 39 actions including `setRewardShortfallCeiling(pair, 2000)`. The fork's RHSPY feed was held
    at its real last answer across the 1 h warp.
  - reserve: approve and deposit of 11.1 USDG and 0.0076 SPY. Coverage was then OK, with $111 of headroom.
- `verify` passed.
- **Keeper, chain time:**
  - +0 s: recenter sent; rebalance sent 1 block later; harvest held.
  - +60 s: recenter refused by the ceiling (`RewardShortfallTooHigh`, a wait); harvest held.
  - +130 s: harvest sent. The treasury slice, 0.0000071 RAM, was booked and the keeper raised the `RewardShortfallBooked`
    alert. Recenter waited on its cooldown.
  - +2 h: `RecenterNotNeeded`. No second harvest: that keeper version still timed the harvest interval by wall clock.
- At launch size a rebalance (about $1.68 of gas at the rehearsal's gas cap) is not worth the matched value. The default
  keeper value multiple waits; the rehearsal lowered it on purpose.

Rerun with the keeper also spacing the rebalance (fork at L2 107,573,092): the ceremony gave the same results.
- **Keeper:**
  - +0 s: recenter sent, rebalance held.
  - +60 s: everything held.
  - +150 s after the recenter: rebalance sent.
  - +130 s after the rebalance, then 2 h later: harvests sent.
- **Booked shortfall: 0 RAM, no alert.**
