# Source and license provenance

This snapshot combines Peridot-authored sources from `joshschcom/LP_VaultsUniswap` and `PeridotFinance/peridot-contracts-2-5` with their exact imported dependency files. Original Solidity SPDX headers are preserved. This repository does not apply a replacement blanket license to those files.

Dependency revisions are recorded in [`dependencies.lock.json`](contracts/robinhood-vaults/dependencies.lock.json); per-file SHA-256 values are in [`snapshot/sources.json`](snapshot/sources.json). Included dependency licenses are retained alongside their source trees. These include Foundry forge-std, OpenZeppelin contracts and upgradeable contracts, Uniswap v4 core/periphery, Permit2, Universal Router, and the Chainlink AggregatorV3 interface.

The `contracts/peridot-contracts-2-5/contracts/node_modules/@chainlink/contracts/` path contains only the imported, pinned interface and any available package-level license notices. It is vendored source, not an npm-installed application dependency. Its original path is retained for compiler provenance.
