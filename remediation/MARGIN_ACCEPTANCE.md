# Small isolated-margin acceptance tests

The deployed long and short contracts each passed an **open → full close → withdraw** simulation against mainnet state, with the native EVM clock and no injected balances, oracle overrides or governance changes. The **long live test has since completed**: position 1 opened, fully closed and withdrew; final block **76,612,481** confirms zero debts, position pToken balances, free/locked margin and approval. It used 10 pUSDG and returned 9.85191185 pUSDG (0.197039 USDG equivalent at the recorded rate). [Completed long evidence](evidence/margin-acceptance-long-completed.json). The **short live test completed October 1**: position 2 closed and withdrew, with zero final debts, position shares, free/locked margin and approval at block **77,263,055**. It returned 9.85120721 pUSDG (0.197025 USDG equivalent at the recorded rate) from 10 pUSDG supplied; requested gross leverage was 2× and recorded entry leverage 1.99×. [Completed short evidence](evidence/margin-acceptance-short-completed.json). Lending's separate ten-transaction mainnet round trip is also verified.

The operator uses **1,000,000,000 raw pUSDG shares (10 pUSDG, about 0.20 USDG)** from existing wallet holdings per direction and requests **2× gross leverage**. The configured 5× maximum, $2 gross/$1 debt per-position caps and 1% swap/deviation guards are unchanged. There are no new token purchases, reserve funding or LP allocations. Returns remain in pUSDG; this margin test does not redeem them into USDG. Trading/flash fees and interest can reduce the returned shares. The close quote targets at least 0.18 USDG returned at the quoted debt and prices; the first-swap minimum also accounts for the short's second-leg guard. Debt can accrue before mining, so this is not an unconditional net-return guarantee. Post-transaction verification flags a return below the target (allowing one raw USDG unit of share rounding). A failed postcheck does not undo a mined close.

## Historical commands — both directions completed; do not rerun broadcasts

From the checkout:

```bash
cd /Users/joshua/Peridot/RobinhoodVaults/PeridotV2-Robinhood
python3 remediation/tools/margin_acceptance.py --side long
```

This command only simulates. Signing stages also run a fresh mandatory simulation before prompting for the existing local keystore password. Run each command separately and wait for its VERIFIED message:

```bash
python3 remediation/tools/margin_acceptance.py --side long --stage open --broadcast
```

Opening approves the exact share amount, deposits it into margin custody, clears the approval and opens one position. Wait for **LONG OPEN VERIFIED**. The mined PositionOpened event supplies the actual position ID/account; predicted IDs are not used for subsequent signing.

```bash
python3 remediation/tools/margin_acceptance.py --side long --stage close --broadcast
```

Closing is one transaction. Wait for **LONG CLOSE VERIFIED**: the position is closed, both account debts and pToken balances are zero, locked margin is zero and returned pUSDG is available in free margin.

```bash
python3 remediation/tools/margin_acceptance.py --side long --stage withdraw --broadcast
```

Wait for **LONG WITHDRAW VERIFIED**: free margin is zero and its pUSDG shares have returned to the wallet.

The completed short test used the same three stages with `--side short`, preceded by `python3 remediation/tools/margin_acceptance.py --side short` to simulate its round trip. These commands are retained as historical documentation; do not repeat the completed broadcasts. Keep one test position open at a time. Both directions use about 0.20 USDG of existing pUSDG collateral; short gross leverage includes stable collateral and is not the same as directional stock exposure.

## Verification and interrupted stages

Every live stage saves an intent before signing and refuses automatic retries. It verifies exact pre-simulated calldata, sender, nonce, chain, target and zero ETH value, successful canonical receipts, exact event emitter, position ownership/direction, custody balances and debt. Public journals are archived separately by direction and stage so subsequent Foundry output cannot overwrite their evidence.

If signing or verification is interrupted, retain all files and inspect the state. Verify an already-signed stage without sending anything:

```bash
python3 remediation/tools/margin_acceptance.py --side long --stage open --verify
```

Change the side/stage to the actual attempt. Do not delete intents, rerun a broadcast or use `--resume` blindly. A partial opening can leave free collateral deposited without an open position. A failed closing can leave a live position. The previously documented debt repayment followed by in-kind debt-free exit is a separate recovery path when guarded swaps are unavailable; this runner does not silently select it or loosen bounds.

## Checks and quote handling

- Runtime hashes and active implementation slots match the published mainnet deployment evidence. Both directional risk tuples must match the recorded 5× configuration.
- The first opening requires empty existing free/locked margin, zero pUSDG approval to the vault, adequate existing shares, current prices, available flash/borrow liquidity and a freshly funded, execution-enabled dedicated keeper. Keeper availability is checked using the operator workspace's existing `infra/app-platform/signer.py status`; it is not provisioned or reconfigured here.
- The current zero protocol open/close fees are explicitly required; a fee change stops the test. Flash/trading fees are still part of execution.
- Both swaps on a short close retain the protocol's 1% minimum-output and oracle-value checks. Passing an arbitrary nonzero second-leg minimum below the computed protocol floor is rejected by the deployed swap module. This runner uses `minMarginUnderlying=0` for the short's second leg to select that module's runtime-computed protocol floor, while adding enough residual NVDA to the **first-leg minimum** to protect the operator's quoted USDG return target. Zero does not disable the module's safeguards. See `_close` in [MarginAcceptance.s.sol](script/MarginAcceptance.s.sol).
- The full initial Foundry script simulation is mandatory. The incompatible secondary replay is skipped because it uses RPC block height instead of Robinhood Chain's native EVM block number; that replay can invent excess interest. The native number is read through an eth_call code override and only local simulation is adjusted.
- Post-close pToken valuation can round down by one raw USDG unit. Verification permits that rounding unit when checking the 0.18 USDG return floor; it requires exactly zero remaining debt and position pToken balances.

[Long simulation](evidence/margin-long-roundtrip-simulation.json), [short simulation](evidence/margin-short-roundtrip-simulation.json), [readiness observation](evidence/margin-acceptance-readiness.json). Long and short live operator acceptance are complete. A fresh short round trip passed simulation after the long closed: [current short simulation](evidence/margin-acceptance-short-roundtrip-simulation.json). Operator tests are not independent adoption or evidence of a live cloud liquidation. Verification uses a single public RPC; no independent L1 finality proof is claimed.

The Safe, Telegram alerts, risk-limit increases and LP allocation/settlement remain outside this test.

The long's six canonical receipts and success events were independently rechecked. The public RPC could no longer serve the older balance snapshots; their original stage records remain archived, and the final closed/withdrawn state was read afresh. [Receipt recheck and limitation](evidence/margin-acceptance-long-receipt-recheck.json).

The short's six canonical receipts, calldata and events were independently rechecked, and final state was read afresh. [Short receipt recheck](evidence/margin-acceptance-short-receipt-recheck.json). Its initial password-error attempt was confirmed unsigned with unchanged nonce/balances and preserved in [the recovery archive](evidence/attempts/short-open-77254313-unsigned/recovery.json) before the successful fresh attempt. No password or signing key is included.
