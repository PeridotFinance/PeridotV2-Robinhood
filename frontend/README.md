# Frontend integration

The application is developed separately and lives at [peridot.finance](https://peridot.finance). This folder holds what an integrator needs; there is no UI source here.

**Pinned interfaces:** the address manifest and 16 ABIs are in [`contracts/robinhood-vaults/frontend/margin-mainnet`](../contracts/robinhood-vaults/frontend/margin-mainnet) (`manifest.json` has a detached SHA-256). Use them instead of generating interfaces from other sources: they carry the deployed executor's exact tuple layouts. Chain ID is **4663**. Contract addresses are in the [root README](../README.md#what-is-live-on-robinhood-chain-mainnet).

## Units

USDG has 6 decimals, NVDA 18, both pTokens 8. The lending controller prices USDG at `1e30` at $1 (the Compound-style `1e(36 - decimals)` scale) while the plain USD API stays `1e18`. Read `controller.oracle()` rather than hardcoding an oracle.

Underlying value of a pToken balance: `balance * exchangeRate / 1e18` in raw underlying units. Read the live rate with a static `eth_call` to `exchangeRateCurrent` (it is not a view function) or use `exchangeRateStored`.

## APY: what to show

- **Borrow-interest supply APY.** `APR = supplyRatePerBlock / 1e18 * 2_628_000`, `APY = (1 + APR/365)^365 - 1`. Use **2,628,000 blocks per year**. Block numbers here are L1-derived (about 12 s); `eth_blockNumber` returns L2 blocks at about 10 per second, which makes the number roughly 120 times too large.
- **Vault yield.** There is no APY function on the vault. LP yield reaches suppliers only through growth of the pToken `exchangeRateStored`, and only when a checkpoint credits a gain (both native sides at or above principal). Compute realized yield from exchange-rate history: `growth ^ (365 d / elapsed) - 1`, label the window, and show "n/a" under an hour of history.
- **Position performance.** Show fees collected (`FeesProcessed`), recognized loss (`PairCheckpoint.pnlUSDG`, `ledger.cumulativeLossUSDG`) and the LP status separately, labelled as not yet credited to suppliers.
- [`remediation/tools/vault_yield.py`](../remediation/tools/vault_yield.py) is a read-only recorder that snapshots rates and ledger state, decodes the vault events and writes frontend-ready JSON and CSV. [`remediation/dune`](../remediation/dune/README.md) has matching Dune queries (Dune indexes Robinhood Chain natively).
- The LP position is closed right now, so vault LP yield is 0%. Do not annualize a single short window.

## Transactions

- **Set the gas limit explicitly** (estimate times 1.3 to 1.5). `eth_estimateGas` can land a few thousand gas below what a nested call needs on this chain, and the transaction then fails out-of-gas with an empty revert (for example a margin vault `deposit`, which needs about 380,000 gas).
- **Do not use a "max" button with outstanding debt.** Moving pTokens that back a borrow reverts with `TransferPeridottrollerRejection(code)`: selector `0x0c93cb5b`, code `4` means insufficient liquidity. Cap the amount with `getAccountLiquidity`, or simulate first.
- **Supply:** `mint(amount)` reverts `ZeroSharesMinted()` (selector `0xd6a0a041`) for zero amounts and amounts too small to mint one pToken (below about 2e-10 NVDA). The `mint` has no minimum-received bound, so estimate `floor(amount * 1e18 / exchangeRate)` and warn when it is far below the deposit's value.
- **Withdraw all:** use `redeem(allShares)`. `redeemUnderlying(amount)` burns a rounded-up share count and can revert if a loss recognized during settlement raises the shares needed.

## Margin

Margin is always pUSDG; the position or debt side is NVDA. Live limits are 5x, 20% initial margin, **$2 gross and $1 debt per position**. At 5x the debt is four times the margin, so the debt cap means at most **$0.25 of margin per position**. `quoter.quoteOpen` does not enforce the dollar caps: clamp the input in the UI and simulate the open before sending. Opening, closing and liquidating borrow from the flash vault, which only lends what it holds, so a position above the flash balance cannot be closed or liquidated.

## Honest states

- NVDA's feed updates on moves during trading sessions and is **stale on weekends and holidays**. The guard and margin oracle then fail closed (the margin oracle returns 0 for NVDA): new positions, swap-based closes, liquidations and LP-backed withdrawals are unavailable. Show that state, and keep repay-with-underlying and debt-free exit to pTokens visible.
- Show live pause flags, liquidity, and flash-vault capacity before sending a transaction. LP allocation is paused and no LP liquidity is open.
- No keeper, governor, cloud or database credential belongs in the app. Use the connected wallet for every signature.

An optional stateless router (`remediation/src/LendingMintRouter.sol`) gives `mintWithMinShares(market, amount, minShares)`. It is **written and tested but not deployed**; do not call it until a verified address is published.
