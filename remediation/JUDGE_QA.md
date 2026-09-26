# Technical judge questions

**How is this different from an ordinary lending pool?**

Matched value from the NVDA and USDG markets can enter one shared full-range v4 LP position, subject to buffers/caps. That creates potential trading-fee income and Stock Token/USDG liquidity alongside credit. Current LP allocation must be shown from live state, not assumed from the architecture.

**Who pays losses?**

Fees affect strategy economics; the reserve can provide capped in-kind cover under its budgets. Uncovered recognized loss reduces both sides' claims. USDG suppliers can lose value. There is no unlimited insurance promise.

**What if the pair is solvent but cannot return the requested token?**

It needs a bounded conversion or replenishment. With settlement swaps paused, available native tokens can be paid and the unpaid claim remains outstanding. We now test that behavior and the final checkpoint that recovers residual tracked surplus.

**What was the USDG bug?**

Token metadata was correct at six decimals. The ordinary-lending oracle supplied a USD18 price to arithmetic expecting `10^(36-underlyingDecimals)`. The correction adapts the controller API to `1e30` for a $1 USDG while preserving the USD18 margin API. Borrowing and ordinary seizure were paused before installation; show the latest installation/verification receipts separately.

**What happens when the stock exchange is closed?**

The stock feed may stop updating and the guarded paths reject stale prices. We do not disable that check for the demo. Existing underlying repayment and debt-free pToken exit are the tested fallback. The original ordinary-lending source's cached/manual fallback remains a separate limitation.

**Are corporate actions fully handled?**

We use the feed without applying the Stock Token multiplier a second time. Full split-transition and trading-capability handling has not been demonstrated, so we do not claim it.

**How safe is governance?**

Upgrades/configuration use a timelock, but operational control remains with a bootstrap EOA until the staged Safe migration is completed. The intended Safe must first be deployed/verified on chain 4663. Historical credential exposure requires retiring actual powers, not just changing a password.

**Are liquidations automated?**

A dedicated funded cloud signer runs a durable-journal keeper and was recently execution-ready. A live cloud liquidation has not yet been observed in this evidence. External Telegram outage alerts are planned but explicitly deferred; process availability and price availability are separate concerns.

**How do you justify the security claim?**

We provide source provenance, runtime/implementation checks, scoped tests and static analysis with the remaining limitations. We do not describe the project as independently audited. Historical test suites overlap and are reported separately.
