# Ramses range vault: production decision (Phase 0 close-out)

2026-11-06. Decides what the production range vault needs beyond the prototypes in `ramses-vaults/test/prototypes/`,
and proposes the smallest diff that ships it. Research stops here; the README is not extended.

## Starting point

- **Production base:** `RamsesBoostedVault` (24,469 bytes, 107 under EIP-170), with its adapter, guard, reserve and lending
  stack in `ramses-vaults/src`. Nothing Ramses is deployed. This is a new deployment, not an upgrade.
- **What the prototypes add:** an in-kind exit for the side that holds the scarce token. `RoutedVault` (1,591 bytes) sits
  in front of the unmodified vault and sends four selectors to `InKindExtension` (4,861 bytes). `InKindRedeemer`
  (5,825 bytes) is the only caller. `CoverageGuard` blocks allocation while reserve coverage is short.
- **Evidence:** 37 local tests, 26 fork tests (NVDA and SPY), 19/19 integration mutants and 10/10 reserve mutants killed.
- **Not Robinhood-ready:** the extension calls Ramses interfaces (`adapter.poolOf`, `validateRemovalPrice(pairId, IRamsesPool)`,
  `pricesUSD18`). The live Robinhood V3 (24,408 bytes, 168 spare) uses `poolKey` and `pricesUSD`, so a Robinhood port is a
  separate decision.

## Decisions

| Item | Decision | Reason |
| --- | --- | --- |
| **Router** | Keep it, as the **proxy's implementation** (proxy, then router, then vault and extension), not as a second proxy. The vault implementation ships byte-identical to the audited code. **Fix the gate:** `setGate` moves from the deployer-fixed `_owner` to `CONFIG_ROLE`, which is the timelock. | Inline does not fit: 107 spare bytes against a 4,861-byte extension. A library split would rewrite a 24 KB contract. The router is the tested path, at one extra delegatecall per call (`ledger()` 3,438 gas). The deployer-owned gate contradicts the design: that key could zero the guard and disable every check. |
| **`KEEPER_ROLE` on the vault** | **Grant it**, with a written invariant: no code path makes an external call to `address(this)` with caller-controlled calldata. Enforce it with a test and an audit item. | `inKindCheckpoint` has to self-call the keeper-only `checkpoint`. The role lets the vault, as `msg.sender`, run `rebalance`, `recenter`, `collectFees`, `checkpoint`, `burnEmptyPosition`, `harvest` and `claimPendingRewards`, but only through code the vault itself executes. No narrower role exists without changing the vault. |
| **Contract size** | No additions to the vault. The extension, redeemer and guard stay separate. The 107-byte margin is not to be spent. | Keeps the audited bytecode unchanged and leaves room for an emergency fix. |
| **Funding source** | **Self-funded only:** the redeemer redeems just enough shares through the real market. **Remove the `bridge` float** from production code. If no scarce token exists anywhere, the call reverts `NoFundingSource` atomically and nothing changes. | The float adds a trusted counterparty and audit surface for a case where reverting is the correct outcome. |
| **Limits** | Ship with the daily in-kind limit at 0, which refuses every exit. Raise it later with a timelocked `CONFIG_ROLE` call after launch checks. | Default-deny. |

## Minimal production diff

1. **Move code into `src/`:** `RoutedVault`, `InKindExtension`, `InKindRedeemer` and `CoverageGuard` go from
   `test/prototypes/` to `ramses-vaults/src/inkind/`. Logic unchanged except:
   - `RoutedVault.setGate`: replace `_owner` with a `hasRole(CONFIG_ROLE, msg.sender)` check through the vault, and delete
     the `_owner` immutable.
   - `InKindRedeemer`: delete `bridge` (constructor argument, immutable and float branch). With no funding it reverts.
   - `CoverageGuard`: correct the stale header. The router now enforces the guard atomically.
2. **Add tests:**
   - Move the prototype tests to `test/inkind/`.
   - Gate: a non-`CONFIG_ROLE` caller, including the deployer, cannot call `setGate`.
   - Funding: the redeemer has no bridge, and a missing scarce token reverts with no state change.
   - Self-call invariant: a fuzzed handler drives every external entry point and asserts the vault never calls itself
     outside `inKindCheckpoint`.
   - A storage-layout check that ties `LEDGER_SLOT = 4` to `forge inspect RamsesBoostedVault storageLayout`.
3. **Add a deployment script, `script/DeployRamsesInKind.s.sol`:**
   - Deploy the vault implementation, extension and router, with the proxy pointing at the router.
   - Grant `IN_KIND_OPERATOR_ROLE` to the redeemer, `KEEPER_ROLE` to the vault itself and `GUARDIAN_ROLE` to the guard.
   - The gate is set by the timelock.
   - Assert all of the above, and that the vault implementation's codehash equals the audited build.
4. **Change no existing `src/` file.** The vault, adapter, guard, reserve and lending code stay byte-identical.

## Not in this diff (gates before mainnet)

- **Ramses stale-price liquidation:** open and unchanged, README 18I item 1. It blocks lending on Ramses whatever the exit does.
- **Audit:** of `src/inkind/` plus the router-as-implementation pattern.
- **Reserve:** a funding plan, and the coverage parameter the guard enforces.
- **Signers:** Ledger ceremonies throughout. Every Phase 0 signer table that names the old governor EOA is superseded.
- **Robinhood:** porting the in-kind exit to the live V3 needs its own diff and a timelocked upgrade. It is not proposed here.
