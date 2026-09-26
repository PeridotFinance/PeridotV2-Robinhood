# Security model and remaining limitations

Peridot's deployment is a mainnet canary. It has not completed an independent external audit. This document describes verified boundaries and pending work, not a claim of production safety.

## Lending and prices

The original ordinary-lending oracle returned uniform USD18 prices while the controller combines prices with raw underlying amounts. USDG's six decimals require a `1e30` controller price at $1. The installed adapter repairs only this unit mismatch. Borrow-limit and bidirectional liquidation regressions exercise the controller implementation and a current mainnet fork.

The original source remains privileged and can use cached/manual prices. Ordinary lending must not be described as universally failing closed on stale stock feeds. The separate vault guard and guarded margin price source enforce their configured freshness/pause checks; the adapter does not weaken or replace them.

USDG is fixed at $1 under the recorded policy; a depeg is not detected. The stock feed already reflects the token multiplier, and the integration does not multiply it again. Comprehensive split-transition handling, trading-session metadata and `tradingCapabilities` checks are not demonstrated. Weekend/holiday oracle gaps are expected operational constraints, not permission to use stale prices indefinitely.

## Paired strategy and withdrawals

Vault positions use standard Uniswap v4 PoolManager/PositionManager through an adapter, with a full-range zero-hook position. Only configured pToken side accounts may deposit/withdraw. Idle liquidity remains exposed to shared strategy loss while LP liquidity is open OR native claims depend on the other token’s surplus. The deployed V1 bypasses guarded loss recognition once LP liquidity reaches zero; a confirmed regression demonstrates the remaining composition exposure. The prepared V2 restricts oracle-free withdrawal to zero LP liquidity with both native claims fully backed. Until the upgrade is verified, treat this defect as open; see [vault correction](VAULT_UPGRADE.md).

Loss accounting values liquidity at the oracle reference price. Allocation and removal deviation bounds, amount floors, approval cleanup and exact balance-delta checks limit pool manipulation and token-transfer ambiguity. Cash buffers and allocation caps constrain deployment; they do not guarantee immediate native-token redemption.

The reserve offers capped, available, in-kind deficit coverage. Coverage depends on balances, per-event/daily budgets and the coverage ratio. Uncovered loss can reduce both NVDA and USDG claims. This is not insurance guaranteeing principal or outperformance against holding. Composition shortages require conversion or replenishment; paused settlement can leave solvent claims temporarily unpaid. See [recovery](VAULT_RECOVERY.md).

`totalPairAssets` is a pool-priced market view and must not replace oracle-priced loss accounting. A “drained” check must include idle ledger amounts and remaining LP fees/NFT state, not principals alone.

## Isolated margin and liquidation

Margin custody is separate from the paired vault. The recorded maximum is 5× gross leverage, 20% initial/10% maintenance margin, $2 assets/$1 debt per position. No aggregate cap or tester allowlist is established by those per-position limits.

The ordinary-lending correction must preserve the margin USD18 API. Controller-wide borrow pauses also prevent new margin borrowing. Existing debt repayment must remain available. A successful liquidation needs usable prices, liquidity, a passing simulation, gas and an available keeper; deployment alone is not evidence of all those conditions.

The dedicated cloud signer is restricted by worker policy to the deployed liquidator and uses a durable journal/nonce reconciliation. It holds no recorded governor roles. Cloud operators with runtime access can access its signing credentials; dedicated scope limits exposure. No completed live cloud liquidation has been established by the current evidence. External outage alerts remain deferred.

## Governance and history

Proxy upgrades/configuration remain behind the timelock. Bootstrap EOA control persists over multiple operational owners/admins and timelock roles. Historical governor credentials were present in a superseded cloud deployment. Neither dedicated keeper provisioning nor removing current environment fields revokes that key. Safe migration, old-role revocation and provider-history handling remain required. The designated Safe had no code at the inventory block.

## Validation boundaries

The correction changes no deployed proxy layout and does not import later Avalanche code. Archived source hashes remain independently checked. The new adapter has immutable wiring and no setters, token custody or approvals. Constructor checks bind it to an 18-decimal stock and six-decimal dollar market; unsupported markets/assets revert.

Local/fork tests and Slither are engineering evidence, not an external audit or proof of every economic invariant. Adapter static analysis returned zero findings. A separate V2-versus-V1 Slither comparison returned 25 findings in each, with the same detector/severity counts; the [triage](VAULT_UPGRADE.md#static-analysis) records their disposition. Matching counts alone do not establish safety, and neither scan is an external audit. Keep historical test reports separate from current test counts and avoid counting inherited tests twice.
