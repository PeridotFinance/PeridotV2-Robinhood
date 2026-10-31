# Handoff 08: Evidence-driven optimization of the live Uniswap v4 NVDA/USDG vault and keeper

Audience: the implementation LLM working in `PeridotFinance/PeridotV2-Robinhood` on `main` (`a410962` at writing).
Written: 2026-10-30, revised 2026-10-31 after review. Read `00-shared-risk-model-and-rollout.md` first.

**This is an evidence task, not a presumed APY improvement.** Nothing below is known to raise supplier returns. Measure first; change only what the evidence supports. Live system: vault proxy `0x280825b2d856706Ff7E0d6351CcB2e935E1a9A2f`, adapter `0xadA73211711e4790bc83B5d6B39f47fE04D276f3`, pNVDA `0xa155ccCB986774AE818b3F10F07d01D1b7A47b26`, pUSDG `0x55aEd0569c8f0D166D71facE57B57C2f2624a563`, timelock `0x6797FB8Ce049B42C5BC2b42Bf76c6d15C7B12498`. Contract changes are timelocked proxy upgrades with the same rehearsal and byte-for-byte verification as the Oct 9 install.

## 1. What is live (from the repo)

| Item | Value |
| --- | --- |
| Pool | Uniswap v4 NVDA/USDG, fee 0.30% (3000), tick spacing 60, no hooks, pool id `0x3bb34a44f1b2b5f32c034c38a53065a521a47b199700fa9bd19d60985ff24bf1` |
| Position | One ranged NFT, about $10 at the last README update |
| Range policy | half-width 1200 ticks (+12.7% / −11.3%), trigger 300 ticks or out of range, `minInterval` 1 h, `maxPerDay` 4, `maxLossBps` 10, `maxRangedValueUsd` $50 |
| Pair limits | `maxPairValueUSDG` $1,000, `maxCheckpointAge` 1 h, gates 300 / 600 bps, settlement swaps paused |
| Allocation | `RangeLib.deploy` limits **both** sides to the smaller oracle value of the idle inventories (and the per-side cap) **before** calling the adapter |
| Fee split | 20% of collected fees to the loss reserve |
| Market side | `vaultBufferMantissa` 50%, CF 80% |
| Keeper | `keeper/worker.py` → `remediation/tools/range_keeper.py`: every 300 s (min 60 s) simulates `recenter`; when `rebalance` reports `CheckpointStale` it sends `checkpoint`, re-simulates and sends `rebalance` **in the same cycle**; repeat rebalances are spaced ≥ 1 h; gas cap 0.1 gwei; recenter ≈ 1.4M gas |
| Margin | live 5x (20% / 10%), $2 gross / $1 debt per position, no aggregate cap |

**There is no current evidence of the CL strategy's return.** The only recorded yield snapshot (`remediation/evidence/vault-yield/frontend-yield.json`) is from **2026-10-06**, three days before the Oct 9 CL rollout, and reports borrow-interest APY separately from realized exchange-rate growth. It says nothing about the ranged position.

## 2. Hypotheses to test (not conclusions)

| # | Hypothesis | What would confirm or refute it |
| --- | --- | --- |
| H1 | The 0.30% Uniswap pool gets little volume because order flow routes to the Ramses NVDA/USDG pool (`0xdac1904D823F7d6bcAaF431ceEe40c934fEd321b`, ~$1.38M 24 h and ~$5.06M 7 d on ~$96k TVL per the Ramses API on 2026-10-30, plus RAM rewards) | Volume and fees per unit of active liquidity on both pools over identical windows |
| H2 | After a range exit, much of the capital stays idle because deployment is matched-only and swaps are paused | Time series of active vs inactive capital (in-range liquidity value / total vault assets) after each exit and recenter |
| H3 | A narrower base (±5% to ±6%) earns more net than ±12% | Replay of the same history with the contract's real policy at several widths, one P&L ledger, stressed exits |
| H4 | Deployment limits (50% market buffer, smaller-side matching, $50 / $1,000 caps) bind before strategy quality matters | Share of supplier assets that could have been deployed vs were |
| H5 | Supplier APY is limited by borrow demand, not by caps | Utilization history vs cap headroom: were opens rejected by caps, or simply absent? |

## 3. Phase 1: produce comparable evidence (no contract change)

Build `remediation/tools/strategy_evidence.py` (read-only, pinned block ranges, archive RPC required; a failed historical read is a failed run, never a skip). For the post-rollout period (block 84,107,567 onward) and for the Ramses NVDA pool over the **same** windows:

1. **Supplier returns**: pNVDA and pUSDG exchange-rate growth split into borrow interest, LP fees credited, LP losses booked, reserve cut. Time-weighted and money-weighted.
2. **Active vs inactive capital**: in-range liquidity value, out-of-range liquidity value, vault idle, market local cash, all at the oracle price, sampled per block range.
3. **Fees and volume**: swaps through each pool, fees to LPs (after protocol share where applicable), active liquidity near price, our share of fees.
4. **Inventory P&L**: one portfolio ledger per shared risk model section 3, against holding and against the delta-matched rebalancing benchmark; markouts of swaps at +1 min / +1 h split by session, overnight, weekend.
5. **Keeper costs**: gas per action, actions per day, failed or waiting actions by reason.
6. **Proposed allocation sizes**: recompute (3) and (4) with our liquidity scaled to $1k, $10k, $50k to show dilution.

Output: a profitable-capacity sentence per venue and per candidate policy (shared risk model section 2). The operator decides from this.

## 4. Phase 2: test range policies against the evidence

Replay the measured history with the vault's actual policy code paths (trigger, cooldown, rolling limit, loss bound, oracle and session blocks, matched deployment):

- Baseline: the current ±12% / 300-tick policy.
- Candidates: half-width 360, 480, 600 ticks (≈ ±3.7%, ±4.9%, ±6.2%), triggers 120 to 240, cooldown 30 to 60 min, `maxPerDay` 4 to 8.
- Select by **net return and stressed exits** (weekend gap, removal gate breach, emergency path). Neither faster polling nor narrower ranges is assumed to help.
- If a candidate wins clearly, the change is a single timelocked `setRangePolicy`.

## 5. Phase 3: inventory management, only if H2 is confirmed

Compare three designs on the Phase 1 data, with targets stated as **measurable active-capital share and withdrawal capacity reported separately for NVDA and USDG**, not "zero idle":

| Design | What it does | Caveats |
| --- | --- | --- |
| A. Keep cash (current) | Unmatched inventory stays idle in the vault | Earns nothing; simplest. Withdrawal capacity must be reported **per asset**: idle NVDA cannot satisfy a USDG withdrawal while conversion (settlement swaps) is unavailable, and vice versa |
| B. Base + limit position | Unmatched surplus goes into a one-sided range next to the price | **Out-of-range liquidity earns no fees until price enters it** (Uniswap docs). It only helps if price trades into the limit range often enough. Needs changes in **both** `RangeLib.deploy` (allocation of the unmatched surplus) and the adapter (second NFT) |
| C. Bounded rebalance swap | Swap part of the surplus through the pool to re-pair, within an oracle-anchored minimum and the loss bound | Pays the 0.30% fee and price impact; needs settlement-swap policy review; changes each side's native composition |

If B or C is chosen, the spec must define for **both positions**: partial withdrawals (which NFT first, or proportional liquidity removal), fee collection on both, how removal floors and the loss bound apply to the sum, emergency unwind of both, and the vault's withdrawal sizing (the current vault assumes one position's liquidity). Summing token balances in `positionState` is necessary but not sufficient. The vault has about 170 bytes of headroom, so logic belongs in `RangeLib` and the adapter.

## 6. Phase 4: capacity where demand exists

- Raise `maxRangedValueUsd`, `maxPairValueUSDG` and margin caps **only where Phase 1 shows the limit was binding** (H4, H5). Raising a cap removes a restriction; it does not create demand.
- Keep aggregate exposure limits and run aggregate liquidation rehearsals (shared risk model section 4) before each step. The written flash-vault-funded cap raise (`remediation/script/RaiseMarginCaps.s.sol`) stays gated by that.
- Note: leveraged LP from handoff 06 borrows from the **separate Ramses group**, so it does not raise utilization of these pUSDG/pNVDA markets.

## 7. Keeper changes (low risk, still measured)

- Optional: 60 s polling or event-driven triggers; atomic batching of checkpoint + rebalance where the vault allows it (today the keeper already sends both in one cycle, as separate transactions).
- Optional economic gate for **discretionary** actions only (a recenter whose expected gain is below its cost). **Never** gate required valuation checkpoints, recovery actions or anything that keeps withdrawals and liquidations working behind a profitability check.
- Log the Phase 1 metrics continuously so later decisions have data.

## 8. Phase 5: reserve cut (last)

Lowering `reserveFeeBps` (or making it depend on reserve coverage) **reallocates** revenue from the reserve to suppliers; it does not increase gross strategy earnings. Do it last, once a reserve target is set.

## 9. Decision points for the operator

1. After Phase 1: keep NVDA on Uniswap, move it to the Ramses group (the pair already exists on `feat/ramses-vaults`; see handoff 01), or split.
2. After Phase 2: range policy.
3. After Phase 3 analysis: A, B or C.
4. Cap changes per Phase 4 evidence.

At about $10 of liquidity none of this produces meaningful absolute yield; the point is to have evidence and a tested policy before deposits grow.
