# Mainnet margin preparation and fork rehearsal

Prepared 2026-09-17. **No public-mainnet transactions were submitted.** The new
margin addresses and transaction receipts in this package exist only on a local
fork of Robinhood Chain, chain ID 4663. Mainnet margin remains undeployed.

## Evidence and result

The pinned snapshot is L2 block **65499727**, hash
`0x01a1f2e3c6bfb57b53bf88e3eb53f912273df3d6fe2786b69baa1b5e570aa861`,
with native EVM block number **25998316**. The public RPC prunes historical state;
reproduction of this exact snapshot requires the captured Foundry cache or an
archive endpoint. A fresh snapshot needs a new complete run and new evidence.

- [Preflight](./robinhood-mainnet.margin-preflight.json) records existing contracts,
  code hashes, oracle configuration, liquidity and roles.
- [Borrow history](./robinhood-mainnet.margin-borrower-history.json) covers both
  markets from deployment through the pin: zero Borrow events and zero debt.
- [Local rehearsal](./robinhood-mainnet.margin-rehearsal.json) contains mined local
  receipts for the staged deployment, long/short round trips and keeper liquidation.
- [Runtime verification](./robinhood-mainnet.margin-runtime-verification.json)
  checks compiled code, proxy administrators, ownership and wiring.
- [Validation manifest](./robinhood-mainnet.margin-validation.json) binds tests,
  source hashes, measurements and evidence. Detached SHA-256 files accompany JSON.

The Solidity suite passes **23 tests**: actual router swaps, long/short opening and
closing, partial close, both repayment methods, migration preservation, governance
delays, exact activation risk checks, oracle failures, rollback and liquidation.
The Python keeper suite passes **11 tests**. The full local rehearsal finishes
with zero debt, zero borrower shares, zero free/locked margin for the actor, and
new opens and flash lending paused.

The original lifecycle mined 99 local transactions, including one keeper
liquidation. After strengthening the script's activation assertions, a saved
state replay mined another seven transactions to queue, activate and pause again:
**106 local transactions in total**. The final 23-test suite covers those added
assertions as well. The replay uses the captured state and deterministic local
timestamps; it does not substitute fresh public-mainnet state.

Verification covers **31 executable runtimes** (including the immutable
borrow-accounting helper), seven timelock-owned proxy administrators and 53
address getters. Constructor immutables are excluded from byte comparison and
their wiring checked separately. Nine artifacts differ only in compiler CBOR
metadata between compilation units; the record includes both metadata blobs and
actual runtime hashes instead of claiming complete byte identity for those nine.
The new boosted delegate runtime is **24,511 bytes**, leaving **65 bytes** below
EIP-170. Pin compiler settings and repeat the size check on every code change.

## Mainnet prerequisites discovered

Both existing boosted pTokens use implementation
`0xf64b9835741c91805e96c9fe4d27704d259d769b`, whose runtime hash is
`0x9e40397488f537aae7be96a4cc23e409a18b5fa722bc2dd71d8473adf2185481`.
It does not expose the borrower-share accounting required by isolated margin.
The package upgrades both to the existing `RobinhoodBoostedDelegate` source and
then calls `activateBorrowAccounting([],0,0)` separately on each market.

**Borrowing must remain paused across these transactions.** Empty upgrade
become-data is intentional: the boosted hook interprets nonempty data as vault
configuration, not accounting migration. Fork assertions preserve supply,
reserves, cash, exchange rates, seed shares, vault claims, vault configuration,
operator, delay, controller and administrator across migration. Borrow accounting
uses its existing namespaced storage. Any historical Borrow event or nonzero
debt causes this empty migration package to stop for a separate migration plan.

The lending oracle can return `lastValidChainlinkPrice` after the stock feed is
stale; the original margin wrapper assumes unavailable prices return zero.
The new [GuardedMarginPriceSource](../margin-mainnet/GuardedMarginPriceSource.sol)
uses the existing vault guard and requires its price to equal the lending price.
It returns zero on stale/invalid/paused feeds, guard failure or disagreement.
Margin therefore inherits the guard's **12-hour** bound instead of the lending
stock oracle's 72-hour bound and cached fallback. USDG retains the existing $1
valuation policy; this is not a new USDG depeg oracle.

Stale prices block new risk, swap closes and liquidation. Underlying debt
repayment and a subsequent debt-free in-kind pToken exit were tested. That exit
does not promise immediate underlying redemption from an oracle-blocked LP.
Stock oracle pause is tested; there is no separate exchange-calendar restriction
while the feed remains fresh. Stale weekend/overnight positions can remain exposed
without executable liquidation until the guard becomes priceable again.

## Measured execution and liquidation behavior

Baseline round trips use the real mainnet markets, feed and v4 pool, without
adding liquidity to the markets or pool. Wallet funding is a local fixture.

| Initial margin | Long returned | Short returned |
| --- | --- | --- |
| 0.25 USDG | 0.246296 USDG | 0.246285 USDG |
| 0.50 USDG | 0.492596 USDG | 0.492574 USDG |
| 1.00 USDG | 0.985192 USDG | 0.985154 USDG |

These are snapshot results after opening and closing, excluding gas. Reported
gross leverage rounds to 1.98× long and 1.99× short. Short gross leverage is not
the same as net directional stock exposure. These tiny trades do not establish
capacity for larger trades or profitability after mainnet gas.

The real pool was moved across actual ticks in stress tests, and the mock feed
then followed that resulting pool price. A roughly 40% down move left the long at
health 0.6417; a roughly 70% up move left the short at 0.5570. Both liquidated
successfully after checkpointing the boosted LP. These are stress scenarios,
not measured liquidation boundaries. The short LP scenario explicitly adds
0.005 NVDA of market supply to form an LP; baseline size tests do not.

The native-RPC keeper integration separately uses an explicitly local oracle-only
40% shock with the pool unchanged. It proves transaction execution and recovery
handling; the Solidity stress tests cover moving the real pool.

The existing **1.25 health target remains a soft target**: liquidation may stop
below it when health improves, and further liquidation is unavailable once the
position is healthy. Requiring 1.25 unconditionally could reject useful partial
liquidations. The keeper re-reads and simulates again after each confirmed receipt.
It stops on healthy/stale states, pending or ambiguous submissions, reverted
receipts, or no debt reduction. It never blindly resubmits an unknown transaction.

The risk engine's **$10 dust-debt threshold** exceeds the canary's $1 debt cap,
so canary liquidations fully close even though the configured ordinary close
factor is 50%. Repeated partial calls are covered by keeper unit tests and the
earlier [5× public-testnet results](./robinhood-testnet.margin-5x.md).
Those prior measured 5× boundaries remain −10.74% long / +13.05% short for that
testnet snapshot; they are not mainnet guarantees.

## Proposed canary configuration and funding

| Setting | Value, both directions |
| --- | --- |
| Maximum leverage | 2× |
| Initial / maintenance margin | 50% / 25% |
| Maximum gross position / debt | $2 / $1 per position |
| Liquidation target / full-liquidation health | 1.25 / 0.50 |
| Ordinary close factor / liquidation bonus | 50% / 5% |
| Slippage / oracle deviation limits | 1% / 1% |
| Manual canary margin | 0.25 USDG per trade |
| Governance delay | One hour for risk; separate one hour for activation |

At the pin, lending cash was 4.000035 USDG and 0.019983457829087898 NVDA;
the production boosted LP had zero liquidity with residual idle claims. The
package funds flash liquidity with **2 USDG + 0.01 NVDA**, and insurance with
**$1 equivalent of existing pUSDG shares**, preserving the governor's remaining
seed shares. Budget another 0.50 USDG for two fresh canary deposits plus gas;
do not count withdrawn pToken shares as underlying wallet cash.

The actor held only 0.997001 USDG and 0.004787149114389371 NVDA at the snapshot.
It needs funding before actual execution. Local tests inject wallet balances
(20 USDG, 1 NVDA and local gas); these are not evidence of mainnet funding.
Insurance is a small canary reserve, not proof that arbitrary losses are covered.
The caps are per position, not an aggregate exposure cap or user allowlist.

New proxy administrators are owned by the existing timelock
`0x6797FB8Ce049B42C5BC2b42Bf76c6d15C7B12498`. Operational owners and the existing
market administrator remain `0x94696d767e65a75581145646960FA0eC886cE5d2`.
The timelock still depends on that single EOA. Moving the relevant governance and
operational powers to an approved multisig remains a gate on material exposure.

## Staged execution plan — requires separate mainnet authorization

Implementation: [PrepareRobinhoodMainnetMargin](../margin-mainnet/script/PrepareRobinhoodMainnetMargin.s.sol).
Its functions simulate without `--broadcast`. Simulation addresses are never
authoritative; reconcile mined creation receipts, code hashes and wiring before
using a mainnet address record. Never point a mainnet keeper at the local record.

1. Build the package; obtain a fresh pin, preflight and complete borrower history.
   Verify the actor, old implementation hash, roles, funding, fresh oracle and
   zero debt. Review the added freshness gate and market upgrade before signing.
2. Execute `pauseBorrowing()`. Confirm **both** pause receipts. A partially
   submitted phase requires explicit receipt/state reconciliation, not replay.
3. Refresh the pin **after those receipts** and rerun `borrower_history.py`.
   `postPauseSnapshot` must be true and Borrow count must still be zero. The
   supplied public-mainnet history deliberately has `postPauseSnapshot=false`;
   it cannot authorize migration. The local true record is only a fork fixture.
4. Execute `migrateMarkets()`, reconciling all five receipts (implementation
   creation, two upgrades and two accounting activations). Verify unchanged
   assets/configuration, both accounting flags and zero debt/shares.
5. Execute `deployPaused()`. Confirm all code and wiring independently. Keep
   opens and flash paused. Both directional risk proposals are queued, but no
   activation is queued. Put verified receipts in a **new mainnet address file**.
6. After the risk delay, execute `applyRisk()`. Execute `fundCanary()` while
   paused. Verify both full risk structs, reserves and governance ownership.
7. Review production keeper readiness and the canary window before
   `queueActivation()`. After its separate delay, refresh prices and cash,
   simulate both sides again, then execute `activate()`. It rechecks identity,
   accounting, controller wiring, execution endpoints, both exact risk structs,
   priceability and funding. The staged script itself is not an on-chain atomic
   governance guard; recheck state before each signed transaction.
8. Run `openCanary(false)`, close its actual emitted position ID with
   `closeCanary(uint256)`, and `withdrawCanary()`. Repeat with `openCanary(true)`.
   Use receipt IDs, never assume the rehearsal's IDs 1/2 apply to mainnet.
9. Execute `finishCanary()` only after verifying zero debt/shares and no actor
   margin remains. It pauses margin opens and flash lending; ordinary lending
   borrow gates remain restored to their previous unpaused state. Any unexpected
   failure requires reconciliation and a separately reviewed recovery path.

The generic older atomic deployment script is not sufficient for these discovered
mainnet prerequisites. The new script records proposal intent; an interrupted
deployment is never safe to replay blindly.

## Reproduction commands

```bash
export FOUNDRY_PROFILE=margin_mainnet
export PYTHONPYCACHEPREFIX=/tmp/rh-margin-mainnet-pycache
forge build
# New snapshot: run only when intentionally replacing the evidence as a whole.
python3 margin-mainnet/tools/pin.py
python3 margin-mainnet/tools/preflight.py
python3 margin-mainnet/tools/borrower_history.py
ROBINHOOD_RPC_URL=https://rpc.mainnet.chain.robinhood.com \
  forge test --match-contract RobinhoodMainnetMarginForkTest -vv
python3 -m unittest discover -s margin-mainnet/tools -p test_keeper.py -v

# Disposable local node; no keys, no public transaction submission.
anvil --host 127.0.0.1 --port 8556 --accounts 0 --silent \
  --fork-url https://rpc.mainnet.chain.robinhood.com --chain-id 4663
# In another terminal, after deliberately archiving prior rehearsal outputs:
python3 margin-mainnet/tools/rehearse_local.py
python3 margin-mainnet/tools/verify_local.py
```

The runner refuses to overwrite an existing rehearsal result. It explicitly
aligns Anvil's native EVM clock using a local state dump/load and verifies NUMBER
with a read-only call override. Robinhood's RPC L2 height differs from EVM
`block.number`; blindly using the former miscalculates interest. Scripts require
`MARGIN_EVM_BLOCK_NUMBER` from the current native clock. The local runner uses
`--skip-simulation` to bypass Foundry's incompatible **secondary transaction
replay**; the full script simulation and mined local transactions still run.
Do not copy that flag as justification to skip mainnet transaction simulation.

For a reviewed future phase, the non-broadcast simulation shape is:

```bash
MARGIN_EVM_BLOCK_NUMBER=<verified-native-height> \
MARGIN_RECORD=<receipt-verified-mainnet-address-file> \
FOUNDRY_PROFILE=margin_mainnet forge script \
  margin-mainnet/script/PrepareRobinhoodMainnetMargin.s.sol:PrepareRobinhoodMainnetMargin \
  --sig 'applyRisk()' --rpc-url https://rpc.mainnet.chain.robinhood.com \
  --sender 0x94696d767e65a75581145646960FA0eC886cE5d2
```

`keeper.py` is a read-only planner on public RPCs and supports transaction
submission only on loopback for this rehearsal. A production position-discovery
loop, funded secure signer, transaction/nonce coordination, monitoring and alerting
still need deployment. No production keeper service was installed, no multisig
migration was executed, and no new external security scan was performed here.
