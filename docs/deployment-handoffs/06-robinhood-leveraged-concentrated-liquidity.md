# Handoff 06: Leveraged concentrated liquidity on Robinhood Chain (Ramses SPY/USDG and GLD/USDG)

Audience: the implementation LLM working in `PeridotFinance/PeridotV2-Robinhood`.
Written: 2026-10-30 (revised after design review). **Priority 1 for Robinhood, after handoff 01's group is live.** Read `00-shared-risk-model-and-rollout.md` first (it overrides this file where they differ), then handoff 01 completely; this file assumes its group, pairs, guard, adapter, keeper and margin stack.

## 0. What the operator wants

Users can hold keeper-managed concentrated-liquidity positions on the Ramses SPY/USDG and GLD/USDG pools through Peridot, first at 1x and then with leverage borrowed from the Ramses group markets. Lenders earn the borrow interest. The goal is the best **sustainable** yield for both sides.

Phase 0 checks whether anyone already offers leveraged CL on Robinhood Chain (Ramses' own products, Revert-style lending, other money markets). Record what exists; it does not block the build.

Rollout, per the shared model: **1x share vault first → share collateral → leveraged long LP (capped) → stock-debt mode as a separately validated mode.** Activate per pair, only for pairs that pass 01's gates.

## 0a. What the existing margin stack already gives you (read the code before designing)

From `contracts/peridot-contracts-2-5/contracts/contracts/margin/` and the captured `Peridottroller.sol` (the same logic the Ramses group controller uses):

- **Margin accounts bypass the controller's collateral factor.** Each position is an `IsolatedMarginAccount` registered in the controller (`isolatedMarginAccounts[account]`). For those accounts the controller's `borrowAllowed`, `redeemAllowed` and `transferAllowed` call the `IIsolatedMarginRiskHook` (the risk engine) **instead of** the CF-based liquidity check.
- **The risk engine already separates opening from liquidation** per margin pair (`IsolatedMarginTypes.PairRiskConfig`): `maxLeverageX100`, `initialMarginBps` (opening), `maintenanceMarginBps` (liquidation), `liquidationTargetBps`, `fullLiquidationHealthBps`, `maxLiquidationBps`, `liquidationBonusBps`, `maxSlippageBps`, `oracleDeviationBps`, `maxPositionValueUsd`, `maxDebtValueUsd`. So opening and liquidation limits are already separate for the margin product (shared risk model section 5a).
- Redeem and transfer out of a margin account are allowed only inside a same-block `authorizeMovement` by the executor or liquidator, or when debt is zero. Users cannot pull collateral out of a levered account directly.
- Prices **revert** (`PriceUnavailable`) instead of returning zero, which matches the fail-closed rule.
- Opens and liquidations are flash-loan funded through `SimpleFlashLoanVault`; swaps go through `IMarginRouterAdapter`. The vault's balance of the debt asset limits position sizes, which is why the unexecuted "flash-vault-funded cap raise" script exists.
- Executor direction rules: **LONG** requires `marginAsset == debtAsset`; **SHORT** requires `marginAsset == positionAsset`.

Consequences for this handoff (these replace the CF guidance in section 2b for the margin product):

1. List `rpCL-*` with **controller CF = 0**. Ordinary accounts then cannot borrow against shares at all, so direct looping is impossible. Margin accounts can still use the shares, because their risk comes from the pair config, not the CF. Prove with a fork test that a CF-0 market works as `positionPToken` for a margin account (oracle `registerMarket(rpCL, share, boosted=false)`, `marketPriceable`, executor mint, liquidator seize).
2. **Leverage is set per margin pair, no controller change.** Recorded live NVDA profile: **5x, `initialMarginBps` 2000, `maintenanceMarginBps` 1000**. The earlier canary profile (`maxLeverageX100` 200, 5000 / 2500) is a **candidate** for the first LP pair, not the current configuration. At 2x with 25% maintenance, liquidation comes after about a 33% fall in **gross collateral value with debt fixed**; for LP shares the underlying price move that causes that depends on composition and range, so compute it from the share NAV curve. Choose values from the backtest and the liquidation-capacity tool.
3. **Long LP** = margin `rpUSDG-*`, position `rpCL-*`, debt `rpUSDG-*`, side LONG. Expressible as is.
4. **Stock-debt (hedged) mode is not expressible with USDG margin** (LONG needs margin = debt; SHORT needs margin = position). It works as side **SHORT with margin = `rpCL-*` shares**, debt = `rpSPY`: the user first deposits into the 1x share vault, posts shares as margin, borrows SPY and zaps it into more shares. Still subject to the separate validation gate in 2c.
5. The flash-loan vault needs funded USDG liquidity sized to the caps; include it in the operator decisions.
6. The `RamsesCLZapAdapter` must serve both the executor (asset → shares on open) and the liquidator (shares → debt asset on seize).
7. The **funded in-kind liquidation fallback is new work**: the existing liquidator always requests a flash loan. `exitDebtFreeToPTokens` (user repays, exits debt-free) helps the user during a swap outage but does not replace third-party liquidation.

## 1. Economics to model before turning leverage on

- A CL position earns fees and rewards per unit of in-range liquidity and pays LVR (loss to arbitrageurs) per unit of in-range liquidity. Both scale with concentration in the same way. **Concentration and leverage multiply the net; they do not create it.**
- Rough order of magnitude (to be replaced by the backtest): in-range LVR ≈ `multiplier × σ²/8` per year, multiplier about 200 at ±1% and 40 at ±5%. For SPY at 16% volatility that is roughly 64%/yr at ±1%; the Ramses API shows about 57% in-range income at ±1% (13.5% fees + 43.9% RAM). For GLD, income about 62.5% against roughly 56% to 80% LVR. Very sensitive to volatility and to how much of it is overnight gaps.
- **Our own liquidity dilutes fees and rewards, not LVR.** The whole SPY pool pays LPs roughly $74k/yr (about $26k swap fees after a 50% protocol share, about $48k RAM at today's price; the fee split must be confirmed by 01's fork test), GLD roughly $13k/yr.
- **RAM is most of the income.** Its entitlement must be decided and implemented (section 2a) before profitability is validated. If users have no enforceable claim to RAM, the backtest counts it as zero.
- Borrow interest paid by leveraged users moves income to lenders; it is not new yield for the system.
- **Gate:** the 01 backtest, extended with our capital at several sizes, leverage 1x / 1.5x / 2x with borrow cost, and markout analysis of actual swaps, uses one P&L ledger and ends in a profitable-capacity sentence per pair (shared risk model sections 2 and 3). Leverage above 1x is enabled only where that sentence is positive at the size we plan to allow.

## 2. Architecture

```
RamsesCLShareVault (new, ERC-20 shares, one per pair + preset)      e.g. "pcl-SPY-core"
  └── Ramses CL custody via the 01 adapter code (core/backup layers, RangeLib policy, guard)
rpCL-SPY, rpCL-GLD (new markets in the Ramses group controller; underlying = share token)
  collateral only: CF > 0, borrowing disabled
RamsesCLZapAdapter (new, IMarginRouterAdapter)
  USDG -> shares, SPY -> shares, shares -> USDG, shares -> SPY
Margin stack from 01 (one per group): new pair configs where positionPToken = rpCL-*
```

### 2a. `RamsesCLShareVault`

A new contract, not the paired `RamsesBoostedVault` (no shares, 107 bytes of headroom). Port, do not reinvent:

- Custody and Ramses calls through the 01 adapter code (layering, harvest before every decrease and burn). Prefer composing an adapter instance owned by the share vault over copying code.
- Range policy via `RamsesRangeLib` with 01's independent layer triggers and hysteresis; keeper-only `recenter`; contract-enforced cooldown, rolling limit, loss bound, oracle and session gates.
- **NAV per share at the oracle price**: position amounts at `guard.referenceSqrtPriceX96` plus idle balances, valued with `guard.pricesUSD18`. Never pool spot. Fail closed on a stale, paused or closed-session feed (shared risk model section 7).
- **Deposit and withdrawal accounting** must satisfy every proof in shared risk model section 8. Specifically:
  - Deposits: single asset or both; the depositor's own zap swap executes first and the depositor bears its measured cost; shares minted at post-zap NAV. Before minting, **collect all pending swap fees into NAV** so a deposit just before a fee collection cannot capture earlier fees. RAM is handled by the entitlement index below, never by NAV.
  - Donations to the vault or adapter do not increase NAV used for minting or for collateral pricing (track booked balances, not raw `balanceOf`).
  - Withdrawals: proportional entitlement only, after booking pending fees. In-kind (pro-rata both tokens) always available when the pool is usable; single-asset withdrawal zaps under the exit gate.
  - **Emergency in-kind redemption** for unencumbered shares that works without the oracle and without swaps, as far as Ramses allows (decrease + collect pro-rata). It must **not require a successful RAM claim**; the redeemer's outstanding RAM entitlement is recorded in the auditable entitlement ledger per shared risk model section 8a. Note the adapter's existing rule "harvest before every decrease and burn, failure blocks exits by default" conflicts with this; use its emergency mode (atomic rollback of the failed harvest) and record the ledger entry instead of forfeiting. If the entitlement cannot be proven (no earned view, no period accounting), the call requires `accept_reward_waiver = true` (shared risk model section 8a). Forced liquidation takes no waiver: it proceeds and quarantines the borrower's unprovable RAM for later attribution. Shares used as collateral exit through liquidation or repay first.
  - Locked first-deposit seed to `0xdead`; ERC-20 shares, 18 decimals.
- **RAM ownership: entitlement index (shared risk model section 8a), required before profitability is validated.** Booking RAM outside NAV and later selling it into common NAV would hand earlier holders' RAM to new depositors (the Alice/Bob test). Therefore:
  - A RAM-per-share accumulator; before every mint, burn and **transfer**, a **successful upstream claim** (`getReward` through the manager) at the old supply, then settlement of affected holders at their old balances. Claim fails → ordinary deposits and transfers pause; liquidation, repay-and-release and emergency redemption keep working and write entitlement ledger entries (shared risk model section 8a).
  - Holders claim RAM in kind, or call "claim and swap my RAM" for their own amount (guarded route: WETH/RAM CL pool `0x5860dd468b4fc1740eaf80a3b7f22e78d70a076a`, fee 1.75%, then ETH to USDG, TWAP-bounded minimum). Optional auto-compound mints new shares **to that holder only**.
  - Shares posted as collateral sit in `rpCL-*` or a margin account. **RAM passes through** to the position owner (shared risk model section 8a): the margin account claims and forwards, or the market keeps a per-account sub-ledger. Liquidation moves only future RAM to the liquidator.
  - Unclaimed or unsold RAM is **never** in NAV or collateral value.
  - The current paired-vault rule (segregated bucket, timelock-only forwarding) gives users no claim and is not used here.
  - Required tests: the Alice/Bob invariant with unclaimed upstream RAM at the new deposit, transfers between holders mid-accrual, collateral posting and withdrawal, emergency burn with the claim failing and its later settlement.
- **Capacity cap**: our in-range liquidity ≤ a timelock-set share of the pool's active liquidity (start 30%), plus an absolute USD cap from the profitable-capacity sentence.
- Size gate: split into linked libraries if over EIP-170.

Presets: one share vault per pair using 01's chosen layer config. A wider "conservative" preset is a second vault only if the backtest favors it at leverage. No custom ticks in v1.

### 2b. Collateral markets `rpCL-SPY`, `rpCL-GLD`

- Plain `PErc20Delegator` with the captured delegate, listed in the Ramses group controller.
- Borrowing paused permanently; borrow cap 1 raw unit if zero means unlimited.
- **Controller CF = 0** (shared risk model section 5a). Ordinary accounts cannot borrow against shares; manual looping is impossible. Leverage and liquidation come only from the margin pair config (section 0a). Shares are not usable as ordinary collateral in v1.
- Price: extend 01's group lending price source with share tokens, `price(share) = navPerShareUSD18()` only if the pair's feed and session checks pass; else 0. Same source for margin.

### 2c. Leverage through the existing margin stack

- **`RamsesCLZapAdapter`** implements `IMarginRouterAdapter.swap(...)` for `{USDG, SPY, GLD, share tokens}`: deposit into or redeem from the share vault, measured balance deltas, exact approvals, **zero residual from the operation** (per-operation balance deltas; do not require absolute balances to be zero, so donated dust cannot disable it; pre-existing balances never subsidize a swap; an owner sweep may move only unencumbered donations), `onlyOperator`. Plain USDG↔stock swaps stay on 01's `RamsesV3MarginRouterAdapter`.
- **Launch mode: long LP** (`marginPToken = rpUSDG-SPY`, `positionPToken = rpCL-SPY`, `debtPToken = rpUSDG-SPY`; same for GLD). Exposure: leveraged LP that is roughly half long the stock at the center and becomes fully long the stock as price falls through the range.
- **Stock-debt mode** (`debtPToken = rpSPY`), described as **initially hedged**, not delta-neutral:
  - At entry with equal-value composition, equity `m` and stock debt `d = m` give near-zero net stock exposure.
  - As price rises the LP sells stock for USDG while the stock debt is fixed in shares and appreciates, so the account turns **net short**; above the range it is a full short. As price falls the LP accumulates stock and the account turns net long. Recentering the LP does not restore the hedge; only changing the debt does.
  - Exposure must be computed from the **actual position composition at the oracle price and the outstanding debt**, never from the entry formula.
  - Not offered and not recommended as a default until a **portfolio-level stress test** exists: collateral (share NAV) and debt (stock-denominated) moved under the same price paths, including ±5%/±10% gaps over closed sessions, accrued interest, and keeper outages of 1h, 12h and a full weekend. The earlier `min(NAV(p), NAV(p·(1−s)), NAV(p·(1+s)))` haircut stresses only the asset side and is not sufficient for this mode.
- **Liquidation**: the existing liquidator seizes `rpCL` shares, redeems through the zap adapter, repays, normally flash-loan funded. Caps from the liquidation-capacity simulation (shared risk model section 4): the redemption removes our liquidity before the swap, correlated liquidations run in sequence, third-party liquidity is stressed. GLD's caps follow its measured USDG-side exit depth.
- **Fallback when the swap leg is unavailable** (pool unusable for the zap, exit gate failing while the feed itself is trustworthy): funded in-kind liquidation per shared risk model section 10. The liquidator **supplies the debt asset**, repayment and seizure settle atomically, the liquidator receives the shares (or the redeemed SPY/GLD and USDG in kind) at the bonus and bears the exit risk. No flash loan on this route. Specify the funded liquidator, its USDG budget and the route's capacity; size aggregate caps so it covers the stressed scenario.
- If the **price itself** is untrustworthy (stale feed, closed session, paused oracle), ordinary seizure is disabled and only repay works (shared risk model section 6).
- Raising leverage requires stressed liquidation rehearsals on a fork, not a quiet week.

### 2d. Lender side

- Leveraged LPs borrow USDG (and the stocks in stock-debt mode). Lenders earn that interest plus the 01 boosted-vault yield.
- Revisit the Phase 3 interest model once borrow demand is visible; the backtest's net LP return bounds what leveraged users can pay.
- Frontend: show leveraged LP APY as levered (fees + RAM under the implemented entitlement) − borrow cost, with the backtest's net range and its worst week, not a single headline number.

## 3. Keeper

Extend the 01 keeper: recenter and harvest each share vault (event-driven, simulate-before-send, independent layer triggers with hysteresis); RAM sale or distribution per 2a; margin liquidation keeper covers the new pair configs; exposure monitor for any stock-debt positions with user warnings; session-aware weekend mode (no recenters, no feed-based liquidations while closed; log drift).

## 4. Tests and gates

| Gate | Evidence |
| --- | --- |
| Economics | Profitable-capacity sentence per pair at 1x and at the intended leverage (shared risk model sections 2, 3); RAM entitlement implemented and modeled; operator signs off on caps |
| Share vault | Every proof in shared risk model section 8 and the **Alice/Bob RAM invariant** (8a); fuzz both token orders and decimals; invariants for NAV conservation across deposit/withdraw/recenter/harvest; RAM index conservation across mint/burn/transfer/claim; fork tests on both real pools; emergency in-kind redemption with oracle stale, swaps failing and the RAM claim failing (entitlement ledger entry recorded and later settled); size gate |
| Collateral market | CF 0 market works as a margin `positionPToken` end to end (oracle registration, open, mint, seize, redeem); ordinary accounts cannot borrow against shares at all; feed or session invalid → risk-increasing actions revert, never a zero price into seize maths |
| Leverage | Fork: open/close long LP at the chosen pair config (e.g. 1.5x and 2x via `maxLeverageX100`); opening fails cleanly when costs breach the initial margin; liquidation through both range edges; correlated liquidation sequence from the capacity tool; funded in-kind fallback; zap minimums; closed-session fail-closed; repay-to-exit |
| Stock-debt mode (separate) | Portfolio stress test above; only then a capped activation |
| Review | Adversarial second-model review, mutations for every new safeguard, no open high/medium |

Mainnet order: 01 group live with keeper → share vault at 1x with small capacity → collateral market at CF 0 → long-LP margin pair config (candidate 2x, 50% / 25%), capped → stock-debt mode only after its own gate. Never combine steps.

## 5. Open operator decisions

1. RAM pass-through implementation for shares posted as collateral (shared risk model section 8a; forfeiting is not used).
2. Presets offered.
3. Margin pair config for the first LP pair (`maxLeverageX100`, initial and maintenance margin, caps).
4. Funded liquidator for the in-kind fallback and its budget.
5. Capacity per pool from the profitable-capacity sentence.
6. Whether stock-debt mode is pursued at all.
