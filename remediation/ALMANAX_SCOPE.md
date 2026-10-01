# Almanax review scope

Repository: `PeridotFinance/PeridotV2-Robinhood`. Use `fix/robinhood-mainnet-hardening`; the default branch is the original frozen snapshot. The current contract/recovery code snapshot is `37bf7e532bf6acd09c950b3e176dea99c42b8dd1`.

## Primary contract targets

```text
remediation/src/**/*.sol
contracts/robinhood-vaults/src/**/*.sol
contracts/robinhood-vaults/margin-mainnet/GuardedMarginPriceSource.sol
contracts/peridot-contracts-2-5/contracts/contracts/**/*.sol
```

The first two folders contain the installed `RobinhoodBoostedVaultV2.sol` and `RobinhoodLendingPriceAdapter.sol`, plus the shared `SettlementLib.sol`, `VaultMath.sol`, `VaultTypes.sol`, `StrategyLossReserve.sol`, `StockOracleGuard.sol`, `UniswapV4PairedAdapter.sol`, interfaces and proxies. The last folder includes `boosted/RobinhoodBoostedDelegate.sol`, lending/accounting/controller/oracle dependencies and all isolated margin contracts, including liquidation, custody, swaps, accounts, flash liquidity, configuration, insurance and fees.

**The archived `contracts/robinhood-vaults/src/RobinhoodBoostedVault.sol` is V1 and is no longer the active vault implementation.** Retain it for comparison and dependency context; attribute current findings to V2 and the actual call path. The old `StockSimplePriceOracle.sol` still provides the backing USD18 prices; the controller now uses the installed scaling adapter. Do not replace these sources with newer Avalanche contracts.

Focus on loss/claim accounting, withdrawal ordering, native-token composition shortages, checkpoint/reserve interactions, repeated reserve calls and daily coverage bounds, oracle freshness and decimal scaling, stale-price exits, pToken caught failures, flash/swap slippage constraints, liquidation solvency, account ownership and privileged upgrades.

## Secondary operational targets

```text
remediation/script/**/*.sol
remediation/tools/*.py
contracts/robinhood-vaults/margin-mainnet/script/**/*.sol
contracts/robinhood-vaults/margin-mainnet/tools/*.py
contracts/robinhood-vaults/margin-mainnet/keeper-service/*.py
contracts/robinhood-vaults/margin-mainnet/five-x/*
```

Review transaction intent/calldata validation, local signing boundaries, partial execution and retry handling, keeper execution conditions, and the difference between successful receipts and successful underlying protocol operations. Test files in these folders are context, not deployed code.

Keep `foundry.toml`, imported vendor libraries, interfaces and tests available for dependency resolution and context. Do not use generated build/cache files, artifacts or transaction logs as substitutes for source. No `.env`, keystore, API token or cloud credential is needed.

## Deployment and review context

- [Active addresses](MAINNET_DEPLOYMENTS.md)
- [Dated mainnet evidence](MAINNET_EVIDENCE.md)
- [Security model](SECURITY_MODEL.md)
- [Vault recovery and limitations](VAULT_RECOVERY.md)
- `remediation/test/`, especially V2 accounting/invariant and actual-mainnet fork tests

The MCP scan already authorized by the user is the **diff from `ea79067d2fae91a9f0530d5dbc43594977c0bb04` to `c9d5e3e8311d2abada68940509e431e49e9ebcdb`**. It has not started because Almanax returned `project not found`. That diff is a hardening review, not complete coverage of unchanged frozen lending/margin sources or the later recovery tools. The target paths above describe the desired full-source review if supported by the selected Almanax workflow; a diff-only result must retain its narrower scope. No clean scan or external audit is claimed.
