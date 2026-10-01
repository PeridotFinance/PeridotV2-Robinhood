# Almanax review — 2026-10-01

The [completed scan](https://app.almanax.ai/scan/d17ca08e-e81f-43d8-bc3d-6af0b2d17d59/findings) targets commit `1cf786569303cb57d92afc228d034ead2e3fd0db`. It reports 81 automated findings: 3 high, 31 medium, 42 low and 5 informational. These are scanner classifications, not 81 confirmed vulnerabilities. No findings have been dismissed or resolved in Almanax.

This is a **partial triage**, covering the three high claims and source review of two settlement-related medium claims. It is not a completed audit or authorization to reopen strategy allocation. The returned findings do not establish that `remediation/src/RobinhoodBoostedVaultV2.sol` was analyzed; absence of findings against that path is not proof of coverage.

## Reproduced evidence

Six diagnostic tests passed against installed contracts at Robinhood Chain block **77,297,967**, with native EVM block **26,096,828**. The runner confirms that the pinned block hash remained canonical. Tests use actual governor token balances on the local fork. Only the zero-price cases mock oracle responses, also locally. No keys, funding transactions, mainnet state overrides or broadcasts are involved.

| Scanner finding | Current disposition | Evidence and remaining work |
| --- | --- | --- |
| `778002e8-d99c-4102-bf8b-fdb014adbc33`: zero price enables free borrowing | Claimed borrowing bypass disproved for tested installed markets | Both controller `borrowAllowed` and actual market `borrow` reject zero prices with `PRICE_ERROR` (13); balances and borrower debt remain unchanged. Zero collateral price also fails the liquidity calculation. This does not validate stale/manual **nonzero** fallback prices or remove oracle-availability risk. |
| `f0baa081-9c39-4af0-ac94-410188917042`: zero-share mint | **Confirmed code defect; not fixed** | A mint of **200,000,899 raw NVDA units** (0.000000000200000899 NVDA) transfers underlying but mints zero pTokens. At the pinned state pNVDA exchange rate is `200000899062619265120272280`, with total supply `114745933` raw shares. A minimum USDG unit currently mints 4,999 raw pUSDG shares. This does not rule out rate inflation later. Scanner attribution should point to `PToken.sol:mintFresh`, inherited through the boosted delegate, rather than the reported `PErc20.sol` lines. |
| `bbf5ca12-04b5-4d07-af1a-6ec5513e8e4d`: storage shift bricks proxy | Claimed current mismatch disproved for the two tested markets | Compiler layouts have matching proxy storage prefixes. Both markets' direct proxy getters, delegate getters and raw storage agree: underlying is slot 19, byte offset 1; implementation is slot 20. Installed delegate runtime hash is checked before testing. This does not establish compatibility with unrelated older proxies or future implementations. |

The zero-share mint test deliberately asserts the defective behavior. A passing diagnostic suite **does not mean the defect is remediated**.

### Donation amplification reproduced conditionally; mainnet reachability remains open

Two further local tests in [LendingRounding.t.sol](test/LendingRounding.t.sol) reproduce donation amplification with the captured real controller, delegator and original boosted implementation. Mock underlying assets use 18/6 decimals, fixed $1 model prices, zero interest and a 75% collateral factor. These fixtures intentionally begin with two raw collateral shares; they do not assert that mainnet is currently in that state.

- A donation raises the exchange rate enough that a victim's 0.1 model-stock deposit receives zero shares.
- The attacker borrows 0.35 model dollars, then requests all but one raw collateral unit through `redeemUnderlying`. The original delegate burns only one of two shares and passes the controller's pre-withdrawal check. The resulting position has 0.35 USD18 shortfall. Rejecting only zero-share redemptions does not prevent this positive-share rounding case.

The reproduction supports a serious conditional issue and a correction to exact-underlying burn rounding. It is not evidence that an attacker can force all other mainnet holders to exit. [Read-only observations](evidence/almanax-seed-observation.json) at block 77,300,964 show the governor owns 100,000,000 of 114,745,933 raw pNVDA shares, and 14,970,355,655 of 19,970,311,906 raw pUSDG shares. Other holders were not enumerated; no minimum permanent supply was established. The two test results and exact source hash are retained in [baseline evidence](evidence/almanax-rounding-baseline.json).

Current positive supply is not proof of permanent seeding. Before downgrading the mint finding, establish whether non-attacker shares can leave, and reproduce near-empty-market donation and redemption behavior. No irredeemable seed or minimum-supply invariant has been established by this review.

The installed exact-underlying redemption path rounds burned shares down. The controller rejects a positive redemption that burns zero shares; the fork test confirms that rejection is atomic. That guard does not prove correct rounding for redemptions that burn a positive share count. Low-supply tests must include that case and its interaction with collateral liquidity checks.

**Candidate correction (tested, not installed).** A delegate correction now exists in [`RobinhoodBoostedDelegateV2.sol`](src/RobinhoodBoostedDelegateV2.sol): zero-share mints revert, and exact-underlying redemption burns a rounded-up share count at the post-settlement rate. Storage layout and every existing selector are unchanged. The caller-controlled minimum-share entry point that was proposed alongside it **was dropped**: it does not fit under the 24,576-byte contract limit (the candidate fits by 4 bytes only with trailing metadata omitted). A nonzero-only mint check does not bound nonzero rounding loss, so legacy `mint` still has no caller slippage bound; that residual risk is documented. Permanent seeding or virtual accounting would require separate design and migration review.

Status: **the installed markets still run the original delegate.** Nothing has been deployed or installed on mainnet, and the deployment procedure has only been exercised on local forks. Full measurements, validation, residual risks and the unsigned procedure are in [LENDING_DELEGATE_CANDIDATE.md](LENDING_DELEGATE_CANDIDATE.md). No frozen sources, caps, reserve balances or governance were changed.

## Two medium claims reviewed in source

**`ff19d4aa-3ccd-47e3-826c-aaea605ca145`, reserve rounding:** the claimed rounding overshoot does not follow from the implemented arithmetic. With positive requested amount `R`, requested value `V`, and integer budget `B`, the code enforces `C <= floor(R * B / V)`. Therefore `V * C / R <= B`, and `ceil(V * C / R) <= B`. Both calculations use the same values and full-precision `Math.mulDiv`. Zero inputs return before division. This mathematical result does not rule out other failures such as a `mulDiv` result outside uint256 range; no broad reserve-solvency claim is made.

**`1dbb5749-da11-4530-9ea2-ae07624df0b5`, liquidator reentrancy:** the outer `liquidate` holds its OpenZeppelin reentrancy lock across the synchronous flash callback. The callback checks active context, initiator, trusted lender, token, amount and data hash, then clears `active` before external calls. A nested `liquidate` encounters the existing lock; callback replay encounters the cleared context. Adding the same `nonReentrant` modifier to the callback would break legitimate flash liquidation. This is a source-level assessment of the reported path, not a new adversarial callback test or a conclusion about all cross-contract interactions.

## Claude consultation

Two CLI consultations explicitly requested `claude-opus-5-5`, with tools disabled and generic descriptions only. Both completed successfully. The second corrected an assumption in the first: the guard belongs to the dedicated upgradeable liquidator, not a pToken. Responses are advisory; severity suggestions and untested assumptions are not adopted as established facts. In particular, no LOW severity conclusion is justified merely by today's positive supply.

The CLI also reports internal usage of `claude-fable-5-1`; the saved record preserves the model names rather than claiming an exclusively single-model process. A subsequent generic design consultation with Opus 5.5 reviewed a proposed unchanged-storage delegate correction: upward exact-underlying burn rounding, zero-share rejection, and an optional caller minimum-share entry point. Source review confirms inherited transfer, transferFrom and seize are guarded; mintInternal reverts on rejection; and the deployed-style delegator forwards unknown selectors through its fallback. The resulting candidate was reviewed and tested locally; see [LENDING_DELEGATE_CANDIDATE.md](LENDING_DELEGATE_CANDIDATE.md). No candidate implementation is installed.

## Reproduction and records

Run the current-state diagnostic suite without signing:

```sh
python3 remediation/tools/almanax_lending_rehearsal.py
```

This repins to latest state and replaces this diagnostic evidence bundle. Historical values above describe the recorded block, not a guarantee of current state.

- [Fork tests](test/fork/AlmanaxLendingMainnet.t.sol)
- [Pinned state, hashes and test scope](evidence/almanax-lending-rehearsal.json)
- [Full test output](evidence/almanax-lending-tests.txt)
- [Compiled storage comparison](evidence/almanax-storage-layout.json)
- [Raw scanner findings](evidence/almanax-d17ca08e-raw.json)
- [Claude advisory responses](evidence/almanax-claude-consultation.json)

The captured Foundry output includes resolver diagnostics from its linter despite successful Solc compilation and six passing runtime tests. This is not a clean static-analysis report. The remaining scanner findings, low-supply amplification, and any proposed correction still require review. Safe migration and alert routing remain deferred as requested.
