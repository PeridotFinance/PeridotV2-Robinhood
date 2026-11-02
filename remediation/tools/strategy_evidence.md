# Handoff 08 Phase 1: collection checkpoint

This tool is the first **data collection** checkpoint. It does not complete Phase 1's
economic analysis and must not authorize range, allocation, cap, reserve or keeper changes.
No signing key is loaded and no transaction is submitted. Existing `vault_yield.py` and
keeper behavior are unchanged.

## Run

```sh
# No third-party packages are required (Keccak-256 is bundled in keccak.py).
export ROBINHOOD_ARCHIVE_RPC_URL='https://your-archive-provider'
python3 remediation/tools/strategy_evidence.py \
  --from-block 84107567 --to-block YOUR_FINALIZED_END_BLOCK \
  --sample-step 10000 --log-step 2000 \
  --output remediation/evidence/strategy-evidence/UNIQUE_WINDOW.json
```

Use a new output filename each run. `--to-block` is mandatory. The archive provider
must support EIP-1898 canonical block-hash selectors for `eth_call` and `eth_getCode`,
plus historical logs and receipts. The same block window is used for both venues.
There is no current-state or public-RPC fallback. Any required RPC, ABI or identity read
failure makes the run fail without publishing a success artifact. JSON-RPC IDs, chain
ID, contract code at boundaries, block hashes, log ranges/topics/duplicates, transaction
receipts and final canonicality are checked. The endpoint URL is not written to evidence.
`collectionStatus: requests_completed` means the requested reads succeeded. It does not
establish log completeness: a provider may silently truncate or omit logs. Every requested
page and returned count is recorded, and `logCompleteness.status` remains
`not_independently_verified`. Before volume or yield claims, cross-check against complete
per-block receipts or an independently verified source.

The public endpoint in the repository returned HTTP 403 to an `eth_chainId` request in
this execution environment on 2026-10-31. No historical run or actual yield measurement
was obtained here. Separately, the existing `vault_yield.py` warns that the public endpoint
prunes historical state; a reachable endpoint alone would not establish archive support.

## What the artifact contains

- Historical raw calls and decoded snapshots of both markets, vault ledger/configuration,
  range policy, position quantities/ticks and pool active liquidity.
- Raw oracle feed/configuration/pause data, including during closed sessions. Spot
  composition is labelled; it is **not** presented as oracle-valued or executable NAV.
- Native local token cash separately from the delegate's `getCash`, which can include
  strategy-withdrawable amounts. NVDA and USDG remain separate throughout.
- Provider-returned vault, adapter, market and both pool logs over the requested window,
  with known event decoding and canonical timestamps. Unrecognized events remain raw.
- Receipts and transactions for observed vault/adapter actions, with `gasUsed ×
  effectiveGasPrice`. These do not enumerate keeper calls that reverted or were never sent.
- Stored exchange-rate change over the stated window, **without annualization**. This
  is an accounting observation, not TWR, IRR, fully accrued yield or LP-only return.

Amounts remain integers in native units (and ABI words); consumers must use recorded
token decimals. Raw feed and closed-session status require a separate valuation policy.
The script does not turn a paused stock feed into a trustworthy liquidation price.

## Evidence still required before Phase 1 passes

The output explicitly reports `phase1AnalysisStatus: incomplete`, `profitableCapacity:
null` and the missing analyses. Archive access and independent log completeness must
first be demonstrated. Continue with:

1. Reconcile supplier exchange-rate changes to accrual, market flows, vault fees, reserve
   movements, rounding and realized losses. Stored exchange rates alone cannot support
   per-source attribution; mint/redeem and transfer cash flows need transaction ordering.
2. Price active, inactive and local assets with historical oracle/session validity.
3. Validate each venue's actual fee/reward entitlement and protocol share. Swap input ×
   advertised fee is not sufficient proof of distributable LP income.
4. Build the single portfolio ledger, holding and delta-matched benchmarks, and +1 minute
   and +1 hour markouts, with sufficient observation history beyond the window end.
5. Ingest a sanitized keeper journal or enumerate transactions to include failed/waiting
   actions. Successful event-emitting transactions alone understate operating cost.
6. Replay dilution and stressed liquidation at $1k/$10k/$50k and produce the profitable-
   capacity sentence only once the preceding evidence is complete.

The collector records historical proxy bytecode hashes, but does not certify implementation
identity across upgrades. The implementation slots and upgrade history must also be pinned
before a historical replay claims exact deployed behavior.

## Offline tests

```sh
cd remediation/tools
python3 -m unittest -v test_strategy_evidence
```

These test failure handling and interpretation, using explicitly synthetic responses.
They are not mainnet measurements and do not establish RPC data completeness.
