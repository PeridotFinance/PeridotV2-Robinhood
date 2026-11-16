# Evidence set `vault-upgrade-2026-09-26`

Vault V2 (native backing): timelock queue and execution records, queue simulation, queued-operation and upgrade fork pins/logs, storage layout and ABI comparison, Slither comparison, vault state, V2 test log, post-upgrade reactivation simulation (StaleOracle).

- Files: 22, 127,777 bytes
- Location: evidence store: PeridotFinance/peridot-evidence (private) path PeridotV2-Robinhood/vault-upgrade-2026-09-26/ — pending creation; export at .scratch/evidence-export/
- SHA-256 of the set's `MANIFEST.sha256`: `af41ebc5963ff7d57732c7c3cd12c27b40f88c6ac9f3d2a4e827ec07fc894d0a`
- Last committed in this repository at `5f542a0` (origin/main before the move). Path inside the set `<path>` was `remediation/evidence/<path>` in the repository.
  Recover any file from git: `git show 5f542a0:<original path>`.

Verify a downloaded copy: from the set directory, `shasum -a 256 -c MANIFEST.sha256`, and `shasum -a 256 MANIFEST.sha256` must print the digest above. The manifest below is byte-identical to that file.

```
e85096f91c144adacd54920372a809278c03aed5d3b11cfc9a851c4b8f685805  post-upgrade-reactivation-simulation.txt
d242b620dd8729cafa3946f7b5e62ef0942a3a638df7aedae4e5e86df59a8df3  post-upgrade-reactivation.json
a006d9c8d8e1e28e0d1951f6808713a4d81e29224ab36d1b320ed4a1438a484f  post-upgrade-reactivation.sha256
e249af8f83f451beba1f27deb65bd7ff570f94525d6efa911fde38afe89b6b6f  queued-vault-fork-pin.json
a883b34e59d069de49cd307c52198f4e09a5a390a7b30de66cd48ef2802bacc9  queued-vault-fork-pin.sha256
5434389d9aa01dd1558e83ddd809780fbf7b4522067aff96f052c3e8ff5a7da7  queued-vault-fork-tests.txt
b6cf824780052a3728c6aec5858ba9ade866cf1e5f27b4492f23be483446994d  vault-fork-pin.json
9cb4ea9fd80da91fa661304ed13f354d9c3a2cf69be92f692b038dd8eadf8b18  vault-fork-pin.sha256
fe3241463ae12a097522b2c4eec5820f4037cc576dbecda10e8d9dfb7e14d583  vault-fork-tests.txt
847b4436bc1e3687eb2c5bf2216639c8c2423b37bc47363934ebac10c6148072  vault-layout.json
39988445d8eb1cbeebec90af4bad04e05210a499d620aad81795c38712f2a55f  vault-layout.sha256
063e5267c161ce592dac90262721c4fb4f7b3a19f73adacf2a798909d3399658  vault-queue-simulation.txt
d502d250eba875c53097846d5747a692ccd7e75f88db7f73d03408140910d2c7  vault-slither-comparison.json
ac736c6be1c7bea36213bee1a92eff3c650be603d7827861f207c7044a6f54db  vault-slither-comparison.sha256
df33a8db0d5421bf031924d90c3254b0a390198c4cf9da25e15130fa48bc4ad3  vault-state.json
518dbc470278c14caa53b108030e5161a6762ed6a9e9695686fec1bfe0c1a288  vault-state.json.sha256
518dbc470278c14caa53b108030e5161a6762ed6a9e9695686fec1bfe0c1a288  vault-state.sha256
c164f259414679a596575011be76bce1f53bd9f1a3b0e85591bcd706d6c4b0ef  vault-upgrade-execution.json
c4b387a52c5e9de3f548d043f6ae2664d469031f7da1d5361d368c9a4edecf94  vault-upgrade-execution.sha256
24bd8e6fa8e199f20424a905cb29ac07f114ec9aa27e9b918eca80ac21f5100f  vault-upgrade-queue.json
cfc09afa75abf5d3fa57f793f52df37fb6b140ab0cc3e51d4905ea3696804135  vault-upgrade-queue.sha256
a477b0ddd7e1bfbd271f34915f8ea0a881762636a2857277ffe9bf6cb575ee0a  vault-v2-tests.txt
```
