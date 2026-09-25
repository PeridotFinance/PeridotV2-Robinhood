# Frontend workspace

The frontend is being developed separately. Add that application's source and lockfile here when it is ready. This initial repository does not include a runnable UI or substitute mock app.

Use the pinned Robinhood mainnet [manifest](../contracts/robinhood-vaults/frontend/margin-mainnet/manifest.json), [16 ABIs](../contracts/robinhood-vaults/frontend/margin-mainnet), and [implementation guide](../contracts/robinhood-vaults/frontend/margin-mainnet/FRONTEND_IMPLEMENTATION_GUIDE.md). They describe the deployed executor's exact tuple layouts and 5× settings. Do not generate interfaces from newer Avalanche margin sources.

When adding the app:

1. Keep the frozen contracts and `snapshot/` records unchanged.
2. Include its package-manager lockfile and `.env.example` containing public placeholders only.
3. Document exact install and local development commands here and wire the root `make frontend` target to the actual command.
4. Use chain ID 4663 and the deployed proxy/call addresses. Recheck live risk, oracle availability, cash and flash capacity before transaction requests.
5. Test the complete supply, margin deposit, open, manage, close, share withdrawal and redemption flow. Preserve stale-price repayment and in-kind exit options.
6. Use the user's connected wallet for signatures. No keeper, governor, cloud or database credentials belong in the app.

Mainnet `tradingReady=true` in the historical manifest means activation was verified; it does not establish current price availability, keeper health or action success. All public RPC reads can be configured locally without a signing key.
