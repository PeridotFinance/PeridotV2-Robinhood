# Evidence set `margin-deployment-rehearsal-2026-09`

Run outputs from the original margin deployment preparation, previously under contracts/robinhood-vaults/deployments/: the 1.05 MB localhost rehearsal record and its digest, the fork and keeper test logs and the activation follow-up log. Their SHA-256 values are also listed in contracts/robinhood-vaults/deployments/robinhood-mainnet.margin-validation.json, which stays.

- Files: 5, 1,055,697 bytes
- Location: evidence store: PeridotFinance/peridot-evidence (private) path PeridotV2-Robinhood/margin-deployment-rehearsal-2026-09/ — pending creation; export at .scratch/evidence-export/
- SHA-256 of the set's `MANIFEST.sha256`: `4cad1af0624707d1da671d09ac3e83eb361fb47f20b2aafd67cc5b02c7963ed6`
- Last committed in this repository at `5f542a0` (origin/main before the move). Path inside the set `contracts-deployments/<name>` was `contracts/robinhood-vaults/deployments/<name>` in the repository.
  Recover any file from git: `git show 5f542a0:<original path>`.

Verify a downloaded copy: from the set directory, `shasum -a 256 -c MANIFEST.sha256`, and `shasum -a 256 MANIFEST.sha256` must print the digest above. The manifest below is byte-identical to that file.

```
def2f031ef21cbbbc08158d07ecbfd8a33e1a99bc26c7990826d95cb451f7616  contracts-deployments/robinhood-mainnet.margin-activation-followup.txt
4b6f502e72e91d09fd076cb4171e6936b64e93e4759e3e2b83e260e4ef74ddc7  contracts-deployments/robinhood-mainnet.margin-fork-tests.txt
87c3ccb4243fc9a33cf830370b110c75f4b79c7750e4c027ed8ed8f9f32a709f  contracts-deployments/robinhood-mainnet.margin-keeper-tests.txt
bc2f3998c6b68d95218b41737a9b039b48cb0e49dde17f118a3383524ba3f07f  contracts-deployments/robinhood-mainnet.margin-rehearsal.json
93d2116436f9b53e4ce5297020b78125bb455c9e921d5f9f3970599f29ae6c20  contracts-deployments/robinhood-mainnet.margin-rehearsal.sha256
```
