# Chainlink "Robinhood NVDA (24/7)": behaviour, and the prepared oracle-source switch

> **Operator decisions, 2026-11-09:**
> - The switch uses **M = 90,000 s**, with a separate alarm check on the 24/7 feed's age.
> - The timelock becomes lending-oracle admin.
> - The switch goes into the **15 to 17 Nov ceremony**, together with the executor upgrade.
>
> The script now defaults to 90,000.

## 0. The weekend and the overnight open, measured 2026-11-09 07:39 UTC (`feed247-2026-11-09-0738.json`)

- **23 answers since launch.** Saturday 22:31:07 was the heartbeat answer, 86,426 s after Friday's: 26 s over the
  nominal 24 h, inside the 90,000 s bound with 3,574 s to spare.
- **Sunday from 20:41 UTC:** the 24/7 feed walked down in 0.5% steps, from 186.34 to 181.70 by 00:09, while the 24/5 feed
  stayed frozen at Friday's 188.24.
- **This weekend against the Uniswap pool (835 swaps):**
  - pool vs 24/7: median 0.80%, p90 2.16%, max 3.24%;
  - pool vs the frozen 24/5: median 2.96%, p90 3.89%, max 5.08%.
  - On Sunday evening the pool moved first: 182.60 against a 24/7 answer of 186.34 at 20:41, a 2.05% gap.
  - So a 24/7-priced weekend is not a pool-matched weekend. The swap module's 1% deviation check would have refused
    liquidation swaps during those gaps (fail closed).
- **Overnight open, Sunday 20:00 ET (Mon 01:00 UTC):**
  - In force at the open: 24/7 181.70 (3,025 s old) and 24/5 188.24 (181,766 s old). Gap −3.48%.
  - First answers after the open: 24/5 at 01:00:30 (183.19), 24/7 at 01:02:06 (180.64).
  - From 01:04 the two feeds agree within 0.03% to 0.09%, apart from two 24/5 lags of about 0.5% at 01:40 and 01:50.
- **Never paused** (token flags false), and never older than 90,000 s.


Read 2026-11-07 about 12:00 UTC. Read-only analysis plus a prepared switch; **nothing is sent.**
- **Evidence:** `remediation/evidence/feed247/`. `feed247.py` is rerunnable and writes `feed247.json`.
- **Switch:** `remediation/tools/switch_nvda_feed.sh`. Fork test: `ramses-vaults/test/fork/Nvda247FeedSwitch.t.sol`.

## 1. The feed

| | 24/7 `0x321A...3D1F` | 24/5 RHNVDA `0x379E...9F15` (live today) |
| --- | --- | --- |
| description | "Robinhood NVDA (24/7)" | "RHNVDA / USD" |
| decimals | 8 | 8 |
| owner | `0xeE27...9C52` | `0xeE27...9C52` (same Chainlink owner) |
| rounds | **4: the feed went live Fri 6 Nov 20:21 UTC** | 210 since 30 Oct |
| triggers | 0.5% deviation, 86,400 s heartbeat (as stated) | 0.5% deviation, no off-hours heartbeat |

**History is the four answers since launch.** It did not exist over the last weekend (31 Oct to 2 Nov), and **no session
open has happened since it went live.** The two opens to measure are:
- the overnight open, Sunday 20:00 ET (Mon 10 Nov 01:00 UTC);
- the regular open, Monday 09:30 ET (14:30 UTC).

To measure them, rerun `python3 feed247.py` after each open: the overnight one is readable at Monday's morning ping, the
regular one at about 14:45 UTC, before the 15:00 UTC recovery session.

| 24/7 answer (UTC) | 24/7 | 24/5 answer in force | gap to 24/5 | Uniswap NVDA/USDG last swap | gap to pool |
| --- | --- | --- | --- | --- | --- |
| Fri 20:21:23 | 188.1265 | 188.1287 | −0.001% | 188.4281 | −0.16% |
| Fri 20:51:09 | 187.1401 | 187.0999 | +0.021% | 187.8935 | −0.40% |
| Fri 20:51:39 | 187.2716 | 187.0999 | +0.092% | 187.6894 | −0.22% |
| Fri 22:30:41 | 188.2137 | 188.2403 | −0.014% | 187.3114 | +0.48% |

Both feeds then posted at 22:30 Fri, seven seconds apart, and have been silent since.

**Weekends against the Uniswap pool** (pool price from each v4 `Swap` event's `sqrtPriceX96`):

| weekend | swaps | feed answers | pool vs the answer in force (abs.) |
| --- | --- | --- | --- |
| 31 Oct to 2 Nov (24/7 did not exist) | 370 | 24/5: 0 | 24/5: median 0.44%, p90 0.95%, max 1.03% |
| this one, Sat 01:00 to 12:01 UTC so far | 31 | 24/7: 0, 24/5: 0 | 24/7: median 0.06%, p90 0.17%, max 0.18% |

**Paused or stale:**
- **Paused:** never. The NVDA token's `paused()` and `oraclePaused()` read false now. The feed has no pause flag of its
  own, and every answer is above 0.
- **Past its heartbeat:** never.
- **Past the 12 h guard:** yes, right now. The last answer is 13.4 h old at the read and 14.7 h at the fork read-back.
  With a 0.5% trigger and a 24 h heartbeat, a quiet market leaves the answer older than 12 h for up to half of every day.

## 2. The prepared switch (3 Ledger signatures; NOT executed)

Both price paths read the RHNVDA feed today:
- **Margin oracle path:** margin oracle `0x6315` → `GuardedMarginPriceSource` `0x25E0`. That source requires the vault
  guard's price (`0xaE4D`, 12 h bound plus the token's pause flags) and the lending source's price to be **exactly
  equal**.
- **pNVDA lending path:** controller → adapter `0xe4e0` → the lending source, `StockSimplePriceOracle` `0x266F`.

Both must therefore change in one transaction; a split would zero every margin price in between.

| # | signer | call | why |
| --- | --- | --- | --- |
| 1 | Ledger, direct | `0x266F.setAdmin(timelock)` | the lending oracle's owner and admin is the Ledger alone; the timelock must be an admin for the batch |
| 2 | Ledger | `timelock.scheduleBatch([guard.configurePair(NVDA pair, config with stockFeed = 24/7 and maxStaleness = M), lending.registerChainlinkFeed(NVDA, 24/7)], ..., 3600)` | the guard's `CONFIG_ROLE` is the timelock's; one atomic batch |
| 3 | Ledger, ≥ 3,600 s later | `timelock.executeBatch(...)` | then `verify` |

- **Unchanged in the config:** allocation 300 bp, removal 600 bp, USDG fixed at $1, sequencer grace, decimals, enabled.
- **M is the choice:** 43,200 (today's 12 h, the default) or 90,000 (the 24 h heartbeat plus 1 h).

**Read-backs (`verify`):**
- the guard's stock feed is the 24/7 feed, with bound M and the deviation bounds unchanged;
- the lending oracle's NVDA aggregator is the 24/7 feed;
- the margin oracle and the guarded source both price NVDA at exactly the 24/7 answer × 10^10, or 0 if it is older than
  M;
- pNVDA is priced by the controller's oracle.

**Evidence:**
- **Fork test,** 4/4 at L2 108,395,256 against the live contracts. Margin price today 0, because the 24/5 answer is
  13.6 h old.
  - Switch with M = 43,200: margin price still 0 (the 24/7 answer is 14.6 h old). Lending $188.21.
  - Switch with M = 90,000: margin $188.21, lending $188.21.
  - Only the Ledger schedules; an early execute is refused; a cancelled batch changes nothing.
  - Without step 1, the batch reverts atomically and the guard is untouched.
- **Anvil rehearsal of the script,** both variants (`switch-rehearsal-anvil.txt`): prereq, schedule, early execute
  refused, execute, verify.
- **Mainnet `preflight`:** GO, operation `0x76e6e5a4...4bd5` for M = 43,200.

**Timing: the 15 to 17 Nov ceremony (operator, 2026-11-09), so after Monday's recovery session.** The vault guard is also the Uniswap NVDA vault's guard, and the
recovery session (PR 20) is written for the 24/5 feed (US session, NVDA answer ≤ 1 h). Switching first would change that
session's preconditions.

## 3. What changes if the switch is made

**The 12 h guard.**
- *M = 43,200:* the guard keeps refusing any answer older than 12 h. The 24/7 feed updates whenever NVDA moves 0.5% in
  24/7 trading, so weekends and nights are priced only after such a move. A flat market makes it stale between 12 h and
  24 h after each answer, as now.
- *M = 90,000:* stale only if the feed misses its heartbeat (an outage) or the issuer sets a pause flag; pause flags stay
  enforced either way.
  - **The cost:** the guard then trusts an answer up to 25 h old. A healthy feed's old answer means less than a 0.5%
    move, but a silent feed outage is only detected after 25 h instead of 12 h.
  - The pool-deviation bounds (300 bp to allocate, 600 bp to remove) still compare every vault action with the pool.
- **The lending path is not the guard:** it keeps its own 72 h stock threshold. With a 24 h heartbeat it would no longer
  reach its stale branch (its cache is empty), so pNVDA is priced either way.

**Margin liquidation refusals.** Today the margin oracle returns 0 whenever the 24/5 answer is older than 12 h: about a
quarter of the time, every weekend (gaps of about 52 h or more), and right now. Liquidations, opens, closes and TP/SL all
refuse then.
- *M = 43,200:* refusals shrink to quiet stretches longer than 12 h.
- *M = 90,000:* refusals happen only on a feed outage beyond 25 h or an issuer pause.
- **Either way, liquidations become possible on weekends** at the 24/7 price, executed through the Uniswap pool. The swap
  module's 1% oracle-deviation check still applies; this weekend the pool has stayed within 0.18% of the 24/7 answer.
- **New exposure:** a thin weekend market can now liquidate positions the 24/5 feed would have held until Monday. Weekend
  stock gaps are then realised at weekend prices, not deferred to the open.

**Range-order finalisation (prototype on PR 7).** `finalize` and placement need the margin oracle's price, and the pool
within the pair's 1% of it.
- **Today:** a band that fills over a weekend cannot be finalised until the 24/5 feed updates on Monday, and a reversal
  before then undoes the fill.
- **After the switch:** finalisation becomes possible on weekends whenever the margin price is available. With
  M = 90,000 that is effectively always, since the 0.5% trigger is inside the 1% bound. With M = 43,200 it is only after
  a 0.5% move.
- **Unchanged:** the unwind paths (liquidation, stop-loss, owner) need no price for the band itself; liquidation still
  needs it.
