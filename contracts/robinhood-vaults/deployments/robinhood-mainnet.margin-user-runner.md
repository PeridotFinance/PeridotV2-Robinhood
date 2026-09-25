# Run the mainnet margin deployment from your terminal

This is the user-operated version of the authorized rollout. Foundry prompts
for your existing encrypted keystore password locally. No password file is
required, and the runner never reads, stores or prints the password/private key.

From the repository:

```bash
cd /Users/joshua/Peridot/RobinhoodVaults
bash script/deploy-margin-mainnet.sh --broadcast
```

The default account is `robinhood-deployer`, and the expected signer is
`0x94696d767e65a75581145646960FA0eC886cE5d2`. Foundry selects the encrypted file
under `~/.foundry/keystores/`. Enter its password when **Foundry** prompts. There
are multiple Foundry phases, so it can prompt more than once. The runner does
not unlock or cache your key between phases.

**Run the same command again after each printed governance timestamp.** The
runner records completed phases and advances only when the relevant on-chain
delay has elapsed. It exits while waiting; your terminal need not stay open.
Do not run other transactions from this deployer concurrently with a phase.

## What each invocation does

| Invocation | Actions | Result |
| --- | --- | --- |
| First | Check chain, governor, funding and empty borrower history; pause both borrowing gates; refresh post-pause history; upgrade/activate borrower accounting; restore ordinary lending; deploy and verify margin | Lending restored; new margin waits for its risk delay |
| After the first printed time | Apply both small risk configurations; deposit reserves; queue activation | Separate activation delay begins |
| After the second printed time | Recheck identity, wiring, prices, funding and both risk structs; activate; verify; populate frontend addresses | Margin and flash lending active at 2× |

The two one-hour delays remain intact. No `finishCanary()` or trading functions
are called. The runner neither buys stock tokens nor opens test positions from
the governor. Your team uses its own wallets after deployment.

Final limits are $2 gross position / $1 debt **per position**, 2× leverage,
50% initial / 25% maintenance margin, 1% slippage/deviation and 5% liquidation
bonus. There is no aggregate margin cap or team allowlist in this version.
Safe migration remains planned after frontend setup and **before $1,000 of
total protocol value**; the runner does not perform that migration.

## Funding and access

The runner checks for at least 2 USDG + 0.01 NVDA in the deployer wallet and ETH
for gas. Insurance receives $1 equivalent of existing pUSDG shares while leaving
seed shares with the governor. The latest checked wallet has sufficient tokens:
see [funding verification](./robinhood-mainnet.margin-funding-check.json). Gas
estimation and signing still occur when you run the actual deployment.

The deployment does not install a production liquidation service. The existing
keeper planner is read-only against mainnet; arrange the signer, operator and
monitoring before the team opens leveraged positions. Contract activation and
successful execution do not establish legal eligibility for any participant.

## Records and frontend

All new live records are under **`deployments/margin-mainnet-live/`**:

- `progress.json`: signer/RPC identity, source fingerprint, completed receipts
  and any interrupted phase.
- `pin.json`, `pre-pause-history.json`, `borrower-history.json`: fresh execution
  evidence. Historical preparation evidence is not overwritten.
- `addresses.json`: receipt-backed deployment addresses, promoted only after
  runtime and ownership verification.
- `broadcast-<stage>/`: Foundry's actual transaction records.
- `<stage>-receipts.json`: transactions and canonical successful receipts.
- `staged-verification.json`, `configured-verification.json`,
  `active-verification.json`: bytecode, owners, wiring and state checks.

At completion, the runner populates
[`frontend/margin-mainnet/manifest.json`](../frontend/margin-mainnet/manifest.json)
with verified live addresses and updates its ABIs. `tradingReady=true` means
the contracts were verified active; it does not mean a keeper service was
installed. Give your developer the [integration guide](../frontend/margin-mainnet/README.md).

Source files must stay unchanged between phases. The runner fingerprints the
deployment sources and helpers before submitting its first phase and refuses
to continue if they change. Keep this working tree available until completion.

## Status and interrupted execution

Read-only status, without signing:

```bash
bash script/deploy-margin-mainnet.sh
```

If a phase was interrupted after transactions were sent, reconcile it first:

```bash
bash script/deploy-margin-mainnet.sh --reconcile
```

This checks the exact transaction count, sender, nonce, chain, destination,
calldata, value, successful canonical receipts, creation addresses and phase
postconditions. It sends **no** transactions. Once reconciliation succeeds,
run the normal `--broadcast` command again.

If transactions are missing, pending, reverted or only partly sent, it stops.
Do not delete the journal, rerun that phase directly, or use Foundry `--resume`
without reviewing the actual remaining calls. Even a cancelled password prompt
can leave a conservative pending intent; check the nonce and artifacts before
clearing it. Never point the live runner at the localhost rehearsal directory.

## Foundry implementation and clock handling

The actual Solidity script is
[`DeployRobinhoodMainnetMargin.s.sol`](../margin-mainnet/script/DeployRobinhoodMainnetMargin.s.sol),
which reuses the rehearsed deployment/configuration implementation. The shell
entry point invokes a small Python controller for fresh snapshots, full borrower
history, receipt verification and governance-delay bookkeeping. It requires
Python 3.9+ and the existing `forge`/`cast` installation and dependencies.

Robinhood's RPC block height differs from native EVM `block.number`. The runner
sets `MARGIN_EVM_BLOCK_NUMBER` from the current L1-derived native height. Foundry
executes the complete Solidity script simulation with that clock before signing.
`--skip-simulation` bypasses its incompatible **secondary on-chain transaction
replay**, not the initial script execution. Actual mined receipts and on-chain
postconditions are independently checked. The runner does not change public
chain time or bypass governance delays.

The tested local-only mode uses an unlocked, impersonated account on a disposable
fork, a separate record directory, and no private keys. It cannot populate the
production frontend manifest. Only the user-run live command above signs
public-mainnet transactions.
