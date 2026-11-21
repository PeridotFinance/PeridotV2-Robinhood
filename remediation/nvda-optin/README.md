# NVDA opt-in LP vault (pair NVDA/USDG/OPTIN)

Status: **prepared, not deployed, nothing signed.** The operator accepted the scope (option A) on 2026-11-21. The
rollout runs only after the SPY margin ceremony and then the pSPY collateral ceremony, and only as an operator-signed
list.

## What it is

- **Plain markets stay plain:** pNVDA and pUSDG on Peridottroller `0x6148` become plain by configuration
  (`vaultPaused = true`, residual claims pulled home).
- **New pair:** `NVDA/USDG/OPTIN` (`0x2b51aefd…8deb`) goes on the live Uniswap v4 paired vault `0x2808`, using the
  existing NVDA/USDG v4 pool (fee 3000, spacing 60).
- **New markets:** the pair's side accounts are two new markets, **upNVDA** and **upUSDG**. Both are `PErc20Delegator`s
  on the live delegate `0x0C6F`, listed on `0x6148` with collateral factor 0 and borrowing paused.
- **Precedent:** the canary pair already shared this vault, adapter and pool. Ledger, position NFT, fees, guard config
  and reserve balance are all per pair.

| setting | value |
| --- | --- |
| pair cap | $250 |
| ranged cap | $50 (as production) |
| settlement swaps | on, $25 per call, 100 bp floor |
| reserve | 0.03 NVDA + 5 USDG ($10 per call, $25 per day, 50 % coverage) |
| seeds | 0.02 NVDA + 4 USDG, minted then sent to `0x…dEaD` |
| buffer | 0.5 |

**New contract: `src/RobinhoodLendingPriceAdapterN.sol`.** It is a 5-market controller oracle with no admin, pricing
pNVDA, pUSDG, pSPY, upNVDA and upUSDG. It must be installed before listing. Both the live adapter and adapter3 revert
`UnsupportedMarket` for unknown markets, and because `enterMarkets` is permissionless, a borrower who enters an
unpriceable market becomes unliquidatable. It has the same arithmetic as adapter3, and preflight/t0 and the fork tests
check that the existing three markets read identically.

## Files

| file | purpose |
| --- | --- |
| `src/RobinhoodLendingPriceAdapterN.sol`, `test/LendingPriceAdapterN.t.sol` | adapter + unit tests (incl. the enterMarkets grief against the 2-market adapter) |
| `script/DeployNvdaOptIn.s.sol` | throwaway-sender creations (upNVDA, upUSDG, adapterN) + `verify` |
| `test/fork/NvdaOptIn.t.sol` | fork tests on the post-SPY state; they replay the ceremony |
| `nvda-optin/ceremony.sh` | Ledger calls with checks; phases fund / preflight / t0 / gate / t1 / verify / rollback / rollback-t1 |
| `nvda-optin/rehearse.sh` | anvil rehearsal: SPY margin → pSPY collateral → fork tests → this ceremony → keeper → exits → rollback |
| `tools/range_keeper.py`, `keeper/worker.py` | multi-pair keeper (`--pair`, `PAIRS=NVDA/USDG,NVDA/USDG/OPTIN`; one journal per pair) |
| `nvda-optin/FRONTEND_NOTE.md` | frontend integration + disclosure |

All Foundry commands use `FOUNDRY_PROFILE=nvda_optin`.

## Ceremony (Ledger `0x064eFE76…B2dD` signs everything; one weekday US session, fresh NVDA answer)

**Before T0:**
1. **fund:** Ledger sends 0.0005 ETH to a throwaway deployer.
2. **Deploy:** the throwaway runs `DeployNvdaOptIn --broadcast`. It makes 3 creations, takes no admin power, and the
   market admin is the Ledger.

**T0:**
- 2: timelock `scheduleBatch`. The batch is guard `configurePair` (a copy of production), reserve `configurePair`,
  vault `registerPair` (flags 000) and `setRangePolicy`; salt `peridot-nvda-optin-v1`; delay 1 h.
- 3: `_setPriceOracle(adapterN)` [LIVE-NVDA, bit-identity checked].
- 4–5: `_supportMarket` for both new markets.
- 6–7: borrow pause on both.
- 8–9: mint pause on both.
- 10–13: queue vault config and vault unpause on both new markets.
- O1–O2: queue vault pause on pNVDA / pUSDG.

**gate (read-only, at T0 + 1 h):**
- Timelock batch and all six delegate actions are ready.
- Both new markets are still empty.
- NVDA answer is at most 1 h old and the pool is within 300 bp.

**T1:**
- 14: `executeBatch`.
- 15–18: vault config and unpause on both new markets.
- 19–22: reserve approvals and deposits.
- 23–24: seed approvals.
- 25a/b, 26a/b: unpause mint, re-check the market is empty, then mint the seed. Done for one market, then the other.
- 27–28: seed shares to the dead address.
- O3–O4: pNVDA / pUSDG `_setVaultPaused(true)` [LIVE-NVDA].

**Count:** 1 + 14 + 19 = **34 Ledger transactions** (rehearsal counts them). Afterwards the range keeper (not the
Ledger) checkpoints and rebalances OPTIN once both sides have deposits.

**Rollback (instant, Ledger):**
- R1: guardian pauses OPTIN allocation and swaps.
- R2–R3: mint pause on both new markets.
- R4–R5: queue vault pause on both.
- After 1 h, R6–R7 apply the vault pause.
- The controller oracle stays adapterN, because it is a superset.

## Keeper

The vault-wide `KEEPER_ROLE` is already held by range keeper `0x82AB…9f7e`. Set `PAIRS=NVDA/USDG,NVDA/USDG/OPTIN` on
app `peridot-range-keeper` and force-rebuild. Two other things are handled separately: the default-sender fix, and
the 50 bp price checkpoint `81036aa` (not on this base).
