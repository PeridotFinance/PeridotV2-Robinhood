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

## 0. Before the day (by 14 November)

- Buy the SPY: `python3 deployments/spy-seed-buy/spy_seed_buy.py plan` from the parent workspace. It rehearses the swap and
  prints fresh Ledger commands.
- **Ledger 1:** `SPY.transfer(timelock, 100000000000000000)`.
- **Ledger 2:** `USDG.transfer(timelock, 75000000)`.
- **Keeper:** create the Ramses keeper wallet (`RAMSES_KEEPER`). It must differ from the deployer and the timelock, and hold
  no timelock role.

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
