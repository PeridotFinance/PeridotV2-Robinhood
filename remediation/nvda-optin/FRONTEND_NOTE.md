# NVDA opt-in LP vault: frontend note

For the frontend developer. Status: **prepared, not live.** Addresses for upNVDA, upUSDG and the new controller oracle
are known only after the throwaway deploy; they will be in `remediation/nvda-optin/journals/nvda-optin-mainnet.json`.
Nothing changes for existing pNVDA / pUSDG integrations except that they no longer move money into the LP (see 4).

## 1. What the user sees

The NVDA and USDG cards on the Robinhood lending page get a **slider** that splits one deposit between two markets on
the same controller (Peridottroller `0x6148183676E304dbe63a85C350c208DA3cEAc39C`):

| market | what it is | collateral | can be borrowed |
| --- | --- | --- | --- |
| pNVDA `0xa155…7b26` / pUSDG `0x55aE…a563` | plain lending (unchanged) | yes (80 %) | yes |
| **upNVDA / upUSDG** (new, 8 decimals) | opt-in Uniswap LP vault: half stays as cash in the market, half goes into a concentrated NVDA/USDG Uniswap v4 position | **no** (collateral factor 0: no borrowing power) | **no** (borrowing paused) |

A deposit with the slider at x % is two transactions per asset: `approve` + `pNVDA.mint(amount·(1−x))` and `approve` +
`upNVDA.mint(amount·x)` (same for USDG). Withdrawals are ordinary `redeem` / `redeemUnderlying` on each market.

## 2. Disclosure text (plain words; show before the first opt-in deposit and link from the card)

> **The LP vault is riskier than plain lending, and on our own measurements it has lost money.**
>
> Your deposit is paired with deposits on the other side (NVDA with USDG) in a Uniswap liquidity position. Both sides
> share the result in dollars. If NVDA's price moves either way, the position ends up holding more of the token that
> fell, and the vault books a loss that is split pro rata between NVDA and USDG depositors. **A USDG depositor in the
> vault carries NVDA price risk.**
>
> Trading fees are the only income. They have not covered what the position loses to price moves:
>
> - In our 11-06 backtest of the live NVDA/USDG range position (2026-10-19 14:39 to 2026-11-06 08:44 UTC, 17.75
>   days, NVDA −14.5 %), the LP **without** a hedge returned **−7.19 %**, simply holding the same tokens returned
>   **−4.89 %**, and the LP with its NVDA exposure shorted in the margin market returned **−4.16 %**. One window, one
>   parameter set, at a size of about $19 where fixed gas costs weigh heavily.
> - Over a 90-day NVDA backtest window (2026-08-04 to 2026-11-02, measured on another NVDA/USDG pool), trading fees covered only **58 % to 78 %** of the
>   loss to arbitrage (LVR), at every range width tested. Below 100 % means the strategy loses value to arbitrage
>   faster than it earns fees.
> - The earlier live NVDA/USDG pair lost $1.93 in total on about $36 between October and November 2026 and was closed.
>
> **Expect a negative return.** You may get back less than you put in, in either token.
>
> Withdrawals: about half of each vault market is kept as cash and is always withdrawable. The rest comes out of the
> Uniswap position when you withdraw more than that. It can be **delayed** while the NVDA price feed is older than
> 25 hours or the pool is more than 6 % away from the oracle price. When your side is short of its own token, the vault
> covers it from a loss reserve and then swaps (at most $25 per withdrawal, at most 1 % below the oracle price), so you
> receive your own token, minus any loss already booked.
>
> The vault takes at most $250 in total. Above that, extra deposits stay as cash in the market and earn nothing.

Sources (for the developer, not for the UI):
- Backtest: `RobinhoodVaults/deployments/hedged-lp-2026-11-06/HEDGED_LP.md` and `hedged-lp.json` (script `hedged_lp.py`),
  window 2026-10-19 14:39 to 2026-11-06 08:44 UTC.
- Fees over LVR: PeridotV2-Robinhood branch `feat/ramses-group-phase0`,
  `ramses-vaults/research/dual-range-backtest/evidence/README.md` section 18F ("gross fees over LVR are 0.58 to 0.78
  for NVDA in this window"). The NVDA runs there use a 90.0-day window, 2026-08-04 to 2026-11-02, with Ramses NVDA/USDG
  pool data, not the live Uniswap pool (section 7 of that README). Single short samples.
- Realised loss: vault ledger `cumulativeLossUSDG` of pair NVDA/USDG = 1.9315 USD (read 2026-11-19); balance sheet
  2026-11-03 in the same README section 18A.

## 3. Data to show per vault market

| item | read |
| --- | --- |
| vault TVL against the cap | `vault.ledger(OPTIN)` principals at oracle prices against `vault.pairConfig(OPTIN).maxPairValueUSDG` (250e18 = $250); vault `0x280825b2d856706Ff7E0d6351CcB2e935E1a9A2f`, `OPTIN = keccak256("NVDA/USDG/OPTIN") = 0x2b51aefddb0c4559298a4c9696c1b39f8885ac13462a3b428b2414bb97f88deb` |
| headroom (disable the slider above it) | cap minus current principal value; a deposit above it stays as cash (event `VaultDepositFailed` on the market) |
| withdrawable right now | `market.getCash()` + `market.vaultWithdrawableAssets()`; show "vault exits delayed" when `vaultWithdrawableAssets() == 0` while `vaultAccountedAssets() > 0` |
| result so far | exchange-rate history (`exchangeRateStored`), `market.cumulativeVaultLoss()`, `vault.ledger(OPTIN).cumulativeLossUSDG` |
| matched or waiting | the vault deploys only the smaller side in dollars: show "waiting for the other side" when one side's idle is much larger |
| range | `adapter.positionTicks(OPTIN)` and `positionState(OPTIN)` on adapter `0xadA73211711e4790bc83B5d6B39f47fE04D276f3` |

## 4. Interface changes

- New markets in `getAllMarkets()`: upNVDA, upUSDG. Do not show them in the borrow list or as collateral options.
  `enterMarkets` on them is harmless (no borrowing power) but should not be offered.
- Controller oracle becomes a 5-market adapter (`RobinhoodLendingPriceAdapterN`); prices for pNVDA, pUSDG and pSPY are
  unchanged to the wei. Read prices through `controller.oracle()` and do not hard-code the oracle address.
- **pNVDA / pUSDG become plain by configuration:** `vaultPaused() == true`, `vaultAccountedAssets() == 0`. Stop showing
  any LP or vault figure on the plain cards.
- Errors: a mint while the market is mint-paused reverts "mint is paused"; a borrow on the new markets reverts "borrow is
  paused"; a redeem above local cash while vault exits are delayed reverts with `StrategyLiquidityShortfall()`.
