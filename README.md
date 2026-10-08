# Peridot V2 on Robinhood Chain

Lending, a paired LP vault and isolated long/short margin for **tokenized NVDA and USDG**, live on **Robinhood Chain mainnet (chain ID 4663)**.

Suppliers deposit NVDA or USDG and receive pTokens. A controlled share of matched liquidity can be deployed into one full-range Uniswap v4 NVDA/USDG position. Fees are credited to suppliers' claims, a capped in-kind reserve covers bounded withdrawal shortfalls, and checkpoint losses are shared pro-rata. On top of the same markets, an isolated margin product lets a user open long or short NVDA positions at up to 5x, funded by flash loans and kept safe by a keeper-driven liquidator.

> **Status in one paragraph.** This is a small, real mainnet deployment (single-digit-dollar liquidity and deliberately tiny per-position limits), not an audited production protocol. It has had no independent external audit. Governance still sits with one bootstrap key behind a timelock; a Safe migration is planned but not done. Everything below says what is live, what was only tested, and what is still open.

## What is live on Robinhood Chain mainnet

| Component | Address |
| --- | --- |
| Paired LP vault (proxy) | `0x280825b2d856706Ff7E0d6351CcB2e935E1a9A2f` |
| Vault implementation (V2, native-backing correction) | `0x17f0cf262fbbf27e44756dba6d852815695e9c4a` |
| Uniswap v4 adapter | `0xadA73211711e4790bc83B5d6B39f47fE04D276f3` |
| Oracle guard | `0xaE4D4DdB8dD646951d54fE9B13BE23DcB61C6741` |
| Strategy loss reserve | `0x806b182B050f7EcF908758dD6bBF91DB8B2212aF` |
| Timelock (upgrades and configuration) | `0x6797FB8Ce049B42C5BC2b42Bf76c6d15C7B12498` |
| Lending controller | `0x6148183676E304dbe63a85C350c208DA3cEAc39C` |
| Lending price adapter | `0xe4e03C2FdaeF915ACe705D106b2660B1E342A2E4` |
| pNVDA market | `0xa155ccCB986774AE818b3F10F07d01D1b7A47b26` |
| pUSDG market | `0x55aEd0569c8f0D166D71facE57B57C2f2624a563` |
| Market delegate (rounding fix, installed Oct 2, 2026) | `0x0C6F6962d80390F6104f80811a150e8233bd8FF1` |
| Margin executor | `0x6A45Ae86bD992d250580d08D340A06A04D478977` |
| Margin liquidator | `0x1434CDa56d0Aeac4d5abC16F91ca76a8A989083c` |
| Margin vault / config | `0x04D4A5555b7a37017A67B4D21A1Da5838de28B9e` / `0x09F94fe0B79E000c8a26617c63E3427fdECB528b` |
| Margin account factory | `0x88BDf12F3b6B5C11bd0Ed5c117181FeA3130D15C` |
| Flash vault / insurance fund | `0x79d33c9BbC1D0711e88C5602f86135Ab4C088b06` / `0x17c72B8f171999C4d8863a3517D1C5d9cBfBb068` |

NVDA (`0xd0601CE157Db5bdC3162BbaC2a2C8aF5320D9EEC`), USDG (`0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168`) and the Uniswap v4 PoolManager (`0x8366a39CC670B4001A1121B8F6A443A643e40951`) are third-party contracts. USDG has 6 decimals, NVDA 18, and both pTokens 8. The full address manifest and all ABIs for integrators are in [`contracts/robinhood-vaults/frontend/margin-mainnet`](contracts/robinhood-vaults/frontend/margin-mainnet).

**Current settings:** margin up to **5x** both directions, 20% initial and 10% maintenance margin, **$2 gross and $1 debt per position**, no aggregate cap. Supply, borrowing and ordinary collateral seizure are enabled. LP allocation was **reopened on Oct 6, 2026** (four user-signed transactions, verified in [`remediation/evidence/reopen-allocation-verified.json`](remediation/evidence/reopen-allocation-verified.json)); the position is about **$4** of full-range liquidity. Settlement swaps remain paused.

## How it works

1. **Lending markets.** pNVDA and pUSDG are boosted pToken markets. Part of each market's cash can sit in the paired vault; the exchange rate counts that claim, so supplier yield shows up as exchange-rate growth plus ordinary borrow interest.
2. **Paired vault.** One position per pair, deposits only from the two configured pToken side accounts, no transferable shares (it is not ERC-4626). Collected fees are credited to both sides' principal at once (20% goes to the reserve). A checkpoint then compares the position's oracle-priced value to its benchmark, and a shortfall scales both sides' principal down pro-rata, so fees offset impermanent loss before suppliers lose principal. The reserve is not used at checkpoint: it only covers bounded native-token deficits when a withdrawal is settled. Price-driven gains are credited only when both native sides are at or above principal. Value at risk during a withdrawal is bounded by oracle-anchored amount floors and price-deviation gates.
3. **Isolated margin.** Each position is its own account contract. Opening, closing and liquidating all go through a flash loan; a permissionless keeper liquidates positions that cross the maintenance threshold. Margin is always pUSDG; the position or debt side is NVDA.
4. **Oracle policy.** The NVDA price comes from a Chainlink-style feed that only updates on 0.5% moves during trading sessions, so it is stale outside US market hours. The vault guard and the margin price source **fail closed** on a stale or paused feed. The tested fallback is to repay debt with underlying and exit debt-free into pTokens.

## Security work done during the buildathon

Found through fork tests against the live deployment and an Almanax scan (partially triaged, not complete), then fixed with verified on-chain installs:

| Issue | Fix | Status |
| --- | --- | --- |
| USDG (6 decimals) was priced with 18-decimal scaling in ordinary lending | Lending price adapter (`1e30` USDG controller price at $1) | Installed and verified |
| Closing the LP let one side withdraw ahead of shared loss while native claims were underbacked | Vault V2 predicate: fast path only when both native claims are fully backed | Installed and verified |
| A tiny deposit could take underlying and mint **zero** pTokens | Market delegate reverts on zero-share mints | Installed Oct 2, verified |
| Exact-underlying redemption rounded burned shares **down**, which in a near-empty market let a withdrawal leave debt under-collateralized | Rounds the burn **up** at the post-settlement rate | Installed Oct 2, verified |

Every upgrade was rehearsed on a local mainnet fork before signing, installed by user-signed transactions, then verified independently from public chain data (exact calldata, byte-for-byte runtime match, storage and balances unchanged across the install block). The records are in [`remediation/evidence`](remediation/evidence).

Ideas that are written and tested but **not executed**: reopening LP allocation, a flash-vault-funded raise of the margin caps, and a stateless mint router that gives callers a minimum-shares bound. They are scripts under [`remediation/script`](remediation/script) and are not deployed.

## Verify it yourself

Needs Foundry (Solidity 0.8.26), Python 3.10+ and Make. No credentials, no RPC, no signing.

```sh
git clone https://github.com/PeridotFinance/PeridotV2-Robinhood.git
cd PeridotV2-Robinhood
make verify            # frozen source digests, archived artifacts and ABI hashes
make build             # vault and margin builds
make test              # Solidity unit and invariant tests plus Python tests
make test-remediation  # the remediation suites
make reproduce         # exact recompilation against archived deployment bytecode
```

`make reproduce` rebuilds the archived deployment bytecode from the frozen sources and compares it. Optional fork suites (`make fork-vault`, `make fork-margin`, `make fork-lending-candidate`) need an archive-capable `ROBINHOOD_RPC_URL`; the public RPC prunes old state, and a fork test that cannot read its block is not a pass. The post-install fork rehearsals assert the pre-install code hash on purpose and only run against a block before the Oct 2, 2026 install.

## Repository map

| Path | Purpose |
| --- | --- |
| [`contracts/robinhood-vaults`](contracts/robinhood-vaults) | Frozen paired vault, v4 adapter, guard, reserve, margin deployment scripts, keeper service and tests |
| [`contracts/peridot-contracts-2-5`](contracts/peridot-contracts-2-5) | Frozen lending and isolated-margin source snapshot (only the deployed dependency closure) |
| [`remediation`](remediation) | Corrections written on top of the frozen sources: vault V2, price adapter, market delegate V2, mint router, scripts, tests, tools and evidence |
| [`snapshot`](snapshot) | Source digests and archived deployment compiler artifacts |
| [`frontend`](frontend) | Integration notes for the separately developed application |
| `Makefile`, `.github/workflows` | Verification entry points and CI |

The two directories under `contracts/` preserve the original Solidity import paths and are not edited. Do not replace them with a newer upstream checkout.

## Known limitations

- **No independent audit.** Local tests, fork rehearsals and Slither are engineering evidence, not an audit.
- **Governance.** Proxy upgrades and configuration go through the timelock, but the timelock proposer, vault keeper and guardian, controller admin and market admins are still one bootstrap key. A Safe migration and old-role revocation are outstanding.
- **Reserve is not insurance.** In-kind cover is capped per call, per UTC day and as a share of the deficit, and its balances are tiny. Uncovered loss reduces both NVDA and USDG claims.
- **Stale-price gaps.** Weekend and holiday gaps can block new margin positions, swap-based closes, liquidations and LP-backed withdrawals. USDG is fixed at $1 in the pricing policy and a depeg is not detected. The ordinary-lending price source keeps its original cached/manual fallback.
- **Mint slippage.** The market's `mint` has no caller-selected minimum. Integrators should estimate shares from the exchange rate first (see [`frontend/README.md`](frontend/README.md)).
- **Scale.** Liquidity is a few dollars, the per-position caps are deliberately small, and the insurance fund holds about $1, so a very violent gap can exceed it.

Source files keep their original SPDX and license notices; see [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md). No private keys, keystores or cloud credentials are in this repository.
