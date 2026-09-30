# Peridot V2 — Robinhood Chain

Peridot's deployed NVDA/USDG boosted lending vaults and isolated long/short margin on **Robinhood Chain mainnet (4663)**. This repository freezes the contract inputs used for the September 18, 2026 margin deployment and September 19 risk update to **5×**. It includes the paired vault's deployed source revision, historical deployment evidence, tests, and the frontend integration interfaces.

The frontend is being developed by another team member and will be added to [`frontend/`](frontend/README.md). There is no runnable frontend application in this initial snapshot.

For application reviewers, [submission evidence and remaining gaps](docs/SUBMISSION_READINESS.md) separates deployed contracts, current operational status, missing frontend/access-layer proof and usage measurements. Historical deployment notes are dated evidence, not the current status page.

**September 29 lending reactivation:** all five user-signed transactions were independently verified at block **75,678,599**, including exact calldata, successful canonical receipts, emitted events and resulting flags. Supply and borrowing are enabled in both markets, and ordinary collateral seizure is restored. The corrected lending oracle, vault V2, controller and market delegate runtimes match the recorded identities. Both margin markets were priceable at that block; their canary limits are unchanged. **LP allocation and settlement swaps remain paused, with zero LP liquidity.** The [operator lending round trip](remediation/LENDING_ACCEPTANCE.md) completed September 30: 0.001 NVDA supplied, 0.05 USDG borrowed, debt repaid and supplied NVDA redeemed; zero final debt verified at block 76,565,683. The [long margin operator test](remediation/evidence/margin-acceptance-long-completed.json) also completed: position 1 opened, fully closed and withdrew, with zero final debt/custody at block 76,612,481. Short and frontend acceptance tests remain outstanding. See [execution evidence](remediation/evidence/reactivation-execution-2026-09-29.json) and the [hardening record](remediation/README.md). These are dated observations, not continuous availability guarantees. The frozen snapshot is unchanged. Safe migration and external keeper alerts remain user-deferred.

## Run locally

The current corrections and evidence are on `fix/robinhood-mainnet-hardening` ([PR #1](https://github.com/PeridotFinance/PeridotV2-Robinhood/pull/1)); the default branch still contains the original snapshot.

Install Foundry with Solidity 0.8.26 support, Python 3.10+, and Make. Solidity dependencies are vendored as the exact source files required by the snapshot; no npm install, sibling repository, submodule update or dependency upgrade is required for the contracts.

```sh
git clone --branch fix/robinhood-mainnet-hardening https://github.com/PeridotFinance/PeridotV2-Robinhood.git
cd PeridotV2-Robinhood
make verify       # Deployment source hashes, archived artifacts and frontend ABI hashes
make build        # Vault and margin builds
make test         # Local Solidity unit/invariant tests and Python tests; no RPC/signing
make reproduce    # Exact recompilation against archived artifact bytecode and ABIs
```

`make reproduce` uses the archived compiler settings, including historical remappings, to reproduce creation/runtime templates including metadata. An ordinary Foundry build may produce different metadata because dependency discovery differs in a reduced snapshot. See [snapshot provenance](docs/SNAPSHOT.md) for the distinction between artifact reproduction and deployed runtime verification.

Optional fork suites need access to the historical blocks recorded in the tests:

```sh
export ROBINHOOD_RPC_URL=https://rpc.mainnet.chain.robinhood.com
make fork-vault
make fork-margin
```

The public RPC may prune historical state. Missing archive data is not a passing fork test. No local command above deploys contracts, signs a transaction, or starts an executing keeper.

## What is deployed

Suppliers receive pTokens from the NVDA and USDG lending markets. A controlled portion of matched market liquidity can enter one full-range, zero-hook Uniswap v4 position. LP fees provide a potential second source of income alongside lending interest. The strategy reserve provides bounded in-kind cover; uncovered losses can reduce both sides' claims, including USDG claims.

The paired liquidity vault is not ERC-4626 and only accepts deposits from its configured market side accounts. The separate margin vault holds pToken collateral for isolated positions. Long positions borrow USDG against NVDA exposure; short positions borrow NVDA against USDG assets. Both use pUSDG as deposited margin.

| Recorded setting | Value |
| --- | --- |
| Chain | Robinhood Chain mainnet, 4663 |
| Pair | NVDA / USDG |
| Maximum gross leverage | 5× in both directions |
| Initial / maintenance margin | 20% / 10% |
| Gross assets / debt cap | $2 / $1 per position |
| Aggregate margin cap / tester allowlist | None |
| Margin deployment | Completed September 18, 2026 |
| Risk-only 5× update | Completed September 19, 2026 |

These are dated deployment observations, not a live availability guarantee. Stock oracle updates follow trading sessions; weekends and holidays can leave prices stale. The vault and margin guards reject unavailable/stale prices; the original ordinary-lending source has cached/manual fallback and must be assessed separately. Underlying debt repayment followed by debt-free exit to pTokens is the tested fallback; redemption into underlying still depends on liquidity. The September 29 reactivation cleared both borrow pauses; margin execution still depends on current prices, liquidity and the unchanged canary limits.

## Repository map

| Path | Purpose |
| --- | --- |
| [`contracts/robinhood-vaults/src`](contracts/robinhood-vaults/src) | Paired vault, v4 adapter, oracle guard, reserve and settlement library |
| [`contracts/peridot-contracts-2-5/contracts/contracts`](contracts/peridot-contracts-2-5/contracts/contracts) | Exact imported lending and isolated margin source snapshot |
| [`contracts/robinhood-vaults/margin-mainnet`](contracts/robinhood-vaults/margin-mainnet) | Deployment/risk-update scripts, keeper service and margin tests |
| [`contracts/robinhood-vaults/test`](contracts/robinhood-vaults/test) | Vault unit, invariant and fork tests |
| [`contracts/robinhood-vaults/deployments`](contracts/robinhood-vaults/deployments/README.md) | Historical mainnet addresses, receipts, hashes and validation evidence |
| [`contracts/robinhood-vaults/frontend/margin-mainnet`](contracts/robinhood-vaults/frontend/margin-mainnet/README.md) | Verified address manifest, 16 ABIs and frontend implementation guide |
| [`snapshot`](snapshot) | Source digests and archived deployment compiler artifacts |
| [`frontend`](frontend/README.md) | Reserved for the independently developed runnable frontend |

The internal two-directory layout preserves original Solidity import paths. The embedded Peridot tree intentionally contains only the deployed Robinhood dependency closure. Later Avalanche/Pharaoh margin features are not included. Do not replace it with the latest upstream checkout.

## Review status

The recorded mainnet deployment verification covered 31 runtime targets and 53 wiring reads across 66 user-signed transactions. The later risk update had four mainnet transactions. Historical tests and fresh snapshot checks are distinguished in [validation](docs/VALIDATION.md).

The code has not completed an independent external audit. Reserve cover is capped; it does not guarantee principal or yield. Governance migration from the bootstrap EOA remains outstanding in the records. [Operational limitations](docs/OPERATIONS.md) describes market-hour availability, governance, and historical keeper status.

Files preserve their original SPDX/license notices. See [third-party notices](THIRD_PARTY_NOTICES.md). No private signing material or cloud deployment credentials are included.
