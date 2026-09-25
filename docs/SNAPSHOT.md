# Deployed source snapshot

This repository preserves the Robinhood deployment independently of ongoing Avalanche development in the original Peridot repository. Files were selected from deployment manifests and compiler metadata, not from the current sibling repository as a whole.

## Chain of evidence

1. The vault `src/` files were compared byte-for-byte with vault source commit `b96914e0ff562bbaf64ca94acfbd4eef0643bcdb`, identified in the original README as the source revision for the deployed oracle-priced-loss upgrade.
2. All **119** inputs in `robinhood-mainnet.margin-user-runner-validation.json:sourceSha256` matched their original SHA-256 values.
3. All **168** inputs in `robinhood-mainnet.margin-5x-validation.json:sourceHashes` matched their original values. The 5× update changed risk configuration, not the margin contract implementations. These input sets overlap.
4. For the **31** targets in the original mainnet active-verification record, the exact saved artifact was located by its recorded SHA-256. Several targets use the same proxy/admin artifact, giving **19 distinct margin artifacts**. Seven vault/library/proxy artifacts are also retained.
5. [`make reproduce`](../Makefile) independently compiles these **26 artifacts** using Solidity `0.8.26+commit.8a97fa7a` and each group's archived settings. Creation bytecode and runtime templates match including CBOR metadata; ABI entries match after top-level ordering normalization. Tuple/input order is never normalized or changed.
6. A fresh read-only mainnet check at block **72447565** matched **35 runtime hashes**, **11 proxy implementation slots**, both lending-market delegate targets, and both 5× risk tuples. [`mainnet-check.json`](../snapshot/mainnet-check.json) records the block hash and scope. No transaction was submitted.

The mainnet check links the archived deployment evidence to the current checked block. Artifact reproduction is a separate check: constructor immutables and linked library addresses in actual deployed code are handled by the original runtime verification records, not by claiming an unlinked template is byte-identical to live code.

## Layout and dependencies

The original Foundry project is under `contracts/robinhood-vaults/`. The exact lending/margin dependency closure is under its sibling `contracts/peridot-contracts-2-5/`. This retains the original `../peridot-contracts-2-5/...` source names without requiring any repository outside this clone.

Only the compiler-referenced dependency sources, licenses and required tests/scripts are vendored. No dependency installation is needed after cloning. `snapshot/sources.json` pins **216** source, test, configuration and deployment-tool inputs. Original deployment manifests also remain unchanged at their original paths within the embedded Foundry project.

The compiler metadata records remappings from the original build environment. Some were unused imports whose directories are absent in the reduced snapshot. Ordinary `forge build` can therefore generate different metadata. `make reproduce` supplies the archived settings explicitly and proves exact artifact reproduction without restoring unrelated dependency trees.

Some artifacts also contain absolute source-unit names from the original Mac. The verifier preserves those names in compiler input for identical metadata, but resolves their contents to the pinned files inside this clone. It rejects external paths and unpinned sources. The initial Linux CI run exposed this path-resolution issue; the follow-up tooling fix leaves every frozen contract, artifact and ABI unchanged.

## Maintaining the freeze

Treat `snapshot/` and the deployed Solidity source as immutable provenance. The initial Git tag identifies this baseline. CI checks the file hashes and reproducibility. Add the independently developed frontend under `frontend/`; do not update the frozen margin contracts to accommodate an ABI generated from the newer Avalanche stack.

A future Robinhood upgrade should have its own reviewed source snapshot, artifacts, deployment receipts and updated interface manifest. Preserve this baseline and its tag. Changing a hash file merely to make a source edit pass would break its link to the original deployment evidence.

## Historical scripts

Deployment, migration and risk-update scripts are retained as evidence and for review. The original mainnet rollout and 5× update are complete. Do not rerun them against the existing deployment. Some operational runners intentionally bind journals to original absolute source paths, actors or risk settings; a relocated snapshot is not authorization or a drop-in journal migration for new broadcasts.

Use `python3 tools/verify_mainnet.py` for the repository's purpose-built read-only check. It pins a block and checks canonicality before recording the result. It does not check keeper health, price freshness, balances or all governance roles.
