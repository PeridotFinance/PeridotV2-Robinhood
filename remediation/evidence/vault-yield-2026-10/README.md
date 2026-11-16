# Evidence set `vault-yield-2026-10`

Output of the read-only yield recorder (remediation/tools/vault_yield.py): snapshots, decoded events and the frontend yield JSON, as of early October.

- Files: 5, 10,610 bytes
- Location: evidence store: PeridotFinance/peridot-evidence (private) path PeridotV2-Robinhood/vault-yield-2026-10/ — pending creation; export at .scratch/evidence-export/
- SHA-256 of the set's `MANIFEST.sha256`: `a7fc8b1313e8b13ebeb8258a176798185ee12de2da96c10b6dcbde408217a42e`
- Last committed in this repository at `5f542a0` (origin/main before the move). Path inside the set `<path>` was `remediation/evidence/<path>` in the repository.
  Recover any file from git: `git show 5f542a0:<original path>`.

Verify a downloaded copy: from the set directory, `shasum -a 256 -c MANIFEST.sha256`, and `shasum -a 256 MANIFEST.sha256` must print the digest above. The manifest below is byte-identical to that file.

```
bcc2bd044603ade8de8dac14f5e41c8f08573f3d1c4e7e519bf5bd0e7c323f74  vault-yield/events.csv
6e2aeda6cc87c290acda7cef9819b3899d890cd95e27cce49f148b8b78cff4af  vault-yield/events.json
e7a9d2b524d23000e9a76f167df6171374616041fc09c77d6c47928585b5a9a3  vault-yield/frontend-yield.json
3bcd30b813255a2f51a3f03717f119205f8b33b1c7c435033b35014b4658f216  vault-yield/snapshots.csv
ee5b0c8808f656a6c0100ede558e638a145f709c24f32bea62bfcd1dccb738a1  vault-yield/snapshots.jsonl
```
