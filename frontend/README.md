# Frontend workspace

The frontend is being developed separately. Add that application's source and lockfile here when it is ready. This initial repository does not include a runnable UI or substitute mock app.

Use the pinned Robinhood mainnet [manifest](../contracts/robinhood-vaults/frontend/margin-mainnet/manifest.json), [16 ABIs](../contracts/robinhood-vaults/frontend/margin-mainnet), and [implementation guide](../contracts/robinhood-vaults/frontend/margin-mainnet/FRONTEND_IMPLEMENTATION_GUIDE.md). They describe the deployed executor's exact tuple layouts and 5× settings. Do not generate interfaces from newer Avalanche margin sources.

The September 26 corrections preserve the vault proxy address and public ABI. Read [current deployment status](../remediation/MAINNET_EVIDENCE.md) alongside the historical manifest. USDG has 6 decimals, NVDA 18, and pTokens 8. The controller's USDG price is `1e30` at $1 while the USD18 API remains `1e18`; use `controller.oracle()` instead of an old hardcoded oracle. Display actual market pause flags, price availability and LP allocation separately. Never treat a successful outer receipt as proof of a vault operation when a `VaultDepositFailed` or `VaultWithdrawalFailed` event was emitted.

## September 30 developer update

The operator's full lending supply → borrow → repay → redeem cycle is verified on mainnet, with final debt zero and original token balances restored. The vault proxy address and exported ABIs remain unchanged. Use the current hardening branch and its [lending proof](../remediation/LENDING_ACCEPTANCE.md); the default branch is still the frozen original snapshot.

Both small margin round trips now pass mainnet-state simulation using existing pUSDG collateral and 2× requested leverage. The long is now verified live: position 1 opened at 2× requested leverage (1.98× recorded at entry), fully closed and withdrew at final block 76,612,481. Both debts, free/locked margin and the approval are zero. The test supplied 10 pUSDG and returned 9.85191185 pUSDG (approximately 0.197039 USDG at the recorded exchange rate; gas separate). The short live test remains pending. [Long evidence](../remediation/evidence/margin-acceptance-long-completed.json). [Margin acceptance commands and quote details](../remediation/MARGIN_ACCEPTANCE.md) explain the three stages and the short close's dynamic second-leg protocol floor. Margin closes return pUSDG to free margin; withdrawing shares to the wallet and redeeming them into USDG are distinct actions. Keep the existing $2 gross/$1 debt caps and show current quote, fee and availability checks.

LP allocation/settlement remain paused with no claim of currently deployed LP liquidity. The UI should present actual lending interest, trading fees, reserve coverage and residual-loss exposure separately. The next frontend deliverable is a runnable supply/borrow/repay/redeem journey with live status and explorer links, then reproduce the verified long margin flow; short live acceptance is the next operator step.

## Submission acceptance criteria

The separate frontend developer owns implementation. The deliverable is a runnable app with a live preview (or clear local start instructions), wallet connection on chain 4663, portfolio/market reads, honest paused/unavailable states, and transaction/explorer links. Lending reactivation was verified September 29 at block 75,678,599; LP allocation and settlement remain paused. A [real operator supply → borrow → repay/redeem flow](../remediation/LENDING_ACCEPTANCE.md) completed September 30. Reproduce it through the UI next; the long lifecycle is now verified on mainnet and the short live test remains outstanding. Label operator acceptance tests separately from independent user activity. Show actual LP allocation and reserve bounds rather than a fixed yield or coverage promise. Fiat/IBAN and cross-chain screens require linked implementation/transaction evidence before being presented as working features.

When adding the app:

1. Keep the frozen contracts and `snapshot/` records unchanged.
2. Include its package-manager lockfile and `.env.example` containing public placeholders only.
3. Document exact install and local development commands here and wire the root `make frontend` target to the actual command.
4. Use chain ID 4663 and the deployed proxy/call addresses. Recheck live risk, oracle availability, cash and flash capacity before transaction requests.
5. Test the complete supply, margin deposit, open, manage, close, share withdrawal and redemption flow. Preserve stale-price repayment and in-kind exit options.
6. Use the user's connected wallet for signatures. No keeper, governor, cloud or database credentials belong in the app.

Mainnet `tradingReady=true` in the historical manifest means activation was verified; it does not establish current price availability, keeper health or action success. All public RPC reads can be configured locally without a signing key.
