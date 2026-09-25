# Robinhood mainnet deployment evidence

This folder preserves historical public deployment records for the snapshot. The final selected state is the deployed margin stack plus the **September 19, 2026 5× risk update**. Earlier paused/2×/preparation records describe completed earlier stages and do not imply that deployment must be rerun.

Start with:

- [Actual margin addresses and deployment transactions](margin-mainnet-live/addresses.json).
- [Mainnet runtime and wiring verification](margin-mainnet-live/active-verification.json).
- [Final 5× completion verification](margin-mainnet-5x-live/completion-check.json).
- [Final directional risk state](margin-mainnet-5x-live/status.json).
- [Vault/oracle/adapter implementation upgrade](robinhood-mainnet.oracle-priced-loss-upgrade.json).
- [Production lending pair and mainnet reserve cover](robinhood-mainnet.production-pair.json).
- [Deployment source hashes and test evidence](robinhood-mainnet.margin-user-runner-validation.json).
- [5× source hashes and test evidence](robinhood-mainnet.margin-5x-validation.json).
- [Fresh snapshot mainnet check](../../../snapshot/mainnet-check.json).

Detached SHA-256 files are included where present in the original records. Some historical reports reference larger localhost rehearsals or testnet evidence not included in this mainnet-focused snapshot. Their absence must not be interpreted as newly reproduced evidence.

The frontend [manifest and ABIs](../frontend/margin-mainnet/manifest.json) use deployed proxy/call addresses. Rehearsal addresses and historical predicted addresses are never frontend destinations. The [snapshot provenance](../../../docs/SNAPSHOT.md) explains exact source selection and bytecode reproduction.
