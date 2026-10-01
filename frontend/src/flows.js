/**
 * Supply, withdraw, borrow, repay and the collateral switch, as plain async
 * orchestration over one runner:
 *
 *   switching -> simulating -> signing -> submitted -> confirmed
 *
 * Compound-style calls answer with an error code as well as by reverting:
 * `exitMarket` with debt outstanding returns 12 and changes nothing, and a
 * receipt with status "success" says nothing about which of the two it was.
 * So every call is simulated first and a nonzero code blocks the signature,
 * and every confirmed receipt is checked for the market's own event
 * (Mint / Redeem / Borrow / RepayBorrow) before the step counts as done.
 */
import { BaseError, createWalletClient, custom, decodeErrorResult, maxUint256, parseEventLogs } from "../vendor/viem-2.33.2.min.js"
import { CONTROLLER_ABI, readLendingAccount } from "./lending.js"
import { ensureChain } from "./wallet.js"

/** Allowance headroom for a full repayment: interest keeps accruing until the block that repays. */
const REPAY_BUFFER_BPS = 10n
const RECEIPT_TIMEOUT_MS = 120_000

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

/** Compound's ComptrollerErrorReporter.Error, as enterMarkets / exitMarket return it. */
const CONTROLLER_ERRORS = {
  3: "The account has no shortfall to act on.",
  4: "Not enough collateral for this amount.",
  8: "This market is not enabled as collateral.",
  9: "This market is not listed.",
  12: "Repay the debt in this market before turning its collateral off.",
  13: "The market's price is unavailable right now.",
  14: "The rest of your debt still needs this collateral. Repay first or keep it on.",
  16: "Too many markets are enabled as collateral.",
  17: "The amount is larger than the debt.",
}

export const describeLendingCode = (code) => CONTROLLER_ERRORS[Number(code)] ?? `The market refused the transaction (code ${Number(code)}).`

const NAMED_ERRORS = {
  PriceUnavailable: "Prices are unavailable right now. Borrowing waits for a fresh price; repaying still works.",
  BorrowCashNotAvailable: "The market does not have enough liquidity for this amount right now.",
  StrategyLiquidityShortfall: "The market does not have enough liquidity for this amount right now.",
  RedeemTransferOutNotPossible: "The market cannot pay out this amount right now.",
  MintFreshnessCheck: "The market needs an update first. Try again in a moment.",
  RedeemFreshnessCheck: "The market needs an update first. Try again in a moment.",
  BorrowFreshnessCheck: "The market needs an update first. Try again in a moment.",
  RepayBorrowFreshnessCheck: "The market needs an update first. Try again in a moment.",
  MarketNotFresh: "The market needs an update first. Try again in a moment.",
  TransferNotEnough: "Not enough balance for this amount.",
  TransferTooMuch: "The amount is larger than the balance.",
  InsufficientAllowance: "The approval does not cover this amount. Approve again.",
  ERC20InsufficientAllowance: "The approval does not cover this amount. Approve again.",
  InsufficientBalance: "Not enough balance for this amount.",
  ERC20InsufficientBalance: "Not enough balance for this amount.",
}

const REASON_PATTERNS = [
  [/insufficient (allowance|balance)|exceeds (allowance|balance)/i, "Not enough balance or approval for this amount."],
  [/borrow cap/i, "The market's borrow cap is reached. Try a smaller amount."],
  [/supply cap/i, "The market's supply cap is reached. Try a smaller amount."],
  [/paused/i, "This action is paused right now."],
  [/price unavailable/i, NAMED_ERRORS.PriceUnavailable],
]

/** Token-level errors the bundled ABIs do not carry (USDG reverts Solady-style). */
const TOKEN_ERRORS_ABI = [
  { type: "error", name: "InsufficientAllowance", inputs: [] },
  { type: "error", name: "InsufficientBalance", inputs: [] },
  {
    type: "error",
    name: "ERC20InsufficientAllowance",
    inputs: [{ name: "spender", type: "address" }, { name: "allowance", type: "uint256" }, { name: "needed", type: "uint256" }],
  },
  {
    type: "error",
    name: "ERC20InsufficientBalance",
    inputs: [{ name: "sender", type: "address" }, { name: "balance", type: "uint256" }, { name: "needed", type: "uint256" }],
  },
]

function walk(err, visit) {
  let current = err
  for (let depth = 0; current && depth < 12; depth++) {
    if (visit(current)) return true
    current = current.cause
  }
  return false
}

const isUserRejection = (err) =>
  walk(err, (e) => {
    if (e?.code === 4001 || e?.name === "UserRejectedRequestError") return true
    return /user (rejected|denied)|rejected the request|request rejected/i.test(String(e?.shortMessage ?? e?.message ?? ""))
  })

function fromName(name, args) {
  if (/PeridottrollerRejection$/.test(name)) return describeLendingCode(args?.[0] ?? 0)
  if (name === "Error" && typeof args?.[0] === "string") {
    const hit = REASON_PATTERNS.find(([re]) => re.test(args[0]))
    return hit ? hit[1] : args[0]
  }
  if (name === "Panic") return `The contract stopped with panic code ${String(args?.[0])}.`
  return NAMED_ERRORS[name] ?? `The contract refused the transaction (${name}).`
}

export function decodeError(err, abis) {
  const detail = err instanceof BaseError ? err.shortMessage || err.message : err instanceof Error ? err.message : String(err)
  if (isUserRejection(err)) return { kind: "rejected", message: "The request was declined in the wallet.", detail }
  if (err?.flowMessage) return { kind: err.kind ?? "lending", message: err.flowMessage, detail }

  let message = null
  walk(err, (e) => {
    // viem already decoded it when the call was made with one of our ABIs.
    if (e?.data?.errorName) {
      message = fromName(e.data.errorName, e.data.args)
      return true
    }
    const raw = typeof e?.data === "string" ? e.data : typeof e?.data?.data === "string" ? e.data.data : null
    if (raw && /^0x[0-9a-fA-F]{8}/.test(raw)) {
      try {
        const decoded = decodeErrorResult({ abi: [...abis.pToken, ...abis.erc20, ...abis.oracle, ...TOKEN_ERRORS_ABI], data: raw })
        message = fromName(decoded.errorName, decoded.args)
        return true
      } catch {
        // Unknown selector; keep walking.
      }
    }
    if (typeof e?.reason === "string" && e.reason) {
      message = fromName("Error", [e.reason])
      return true
    }
    return false
  })
  if (message) return { kind: "contract", message, detail }
  if (/fetch|network|timeout|timed out|HTTP request failed/i.test(detail)) {
    return { kind: "network", message: "Robinhood Chain did not answer. Try again in a moment.", detail }
  }
  return { kind: "unknown", message: detail || "Something went wrong.", detail }
}

const flowError = (message, kind = "lending") => Object.assign(new Error(message), { flowMessage: message, kind })

// ---------------------------------------------------------------------------
// Pending transactions
// ---------------------------------------------------------------------------

const PENDING_KEY = "peridot.rh.pending"

function readPending() {
  try {
    return JSON.parse(localStorage.getItem(PENDING_KEY) || "[]")
  } catch {
    return []
  }
}

function writePending(list) {
  try {
    localStorage.setItem(PENDING_KEY, JSON.stringify(list))
  } catch {
    // Storage blocked: reconciliation is then per tab only.
  }
}

const remember = (hash, user) => writePending([...readPending().filter((p) => p.hash !== hash), { hash, user: user.toLowerCase(), at: Date.now() }])
const forget = (hash) => writePending(readPending().filter((p) => p.hash !== hash))

/**
 * A hash still waiting from an earlier attempt is checked before anything new
 * is sent, so a slow confirmation never invites a second transaction.
 */
export async function reconcilePending(cfg, user) {
  const open = []
  for (const entry of readPending().filter((p) => p.user === user.toLowerCase())) {
    const receipt = await cfg.client.getTransactionReceipt({ hash: entry.hash }).catch(() => null)
    if (receipt) {
      forget(entry.hash)
      continue
    }
    const tx = await cfg.client.getTransaction({ hash: entry.hash }).catch(() => null)
    // Unknown to the node after ten minutes: dropped from the mempool.
    if (!tx && Date.now() - entry.at > 10 * 60_000) forget(entry.hash)
    else open.push(entry)
  }
  return open
}

// ---------------------------------------------------------------------------
// One call
// ---------------------------------------------------------------------------

/** Block the signature when the simulated call answered a nonzero code. */
function checkLendingCode(result) {
  if (typeof result === "bigint") return result === 0n ? null : describeLendingCode(result)
  if (Array.isArray(result)) {
    const bad = result.find((r) => typeof r === "bigint" && r !== 0n)
    return bad === undefined ? null : describeLendingCode(bad)
  }
  return null
}

async function runCall(ctx, call, onUpdate) {
  const { cfg, provider, user } = ctx
  const emit = (u) => onUpdate?.({ id: call.id, label: call.label, ...u })
  const fail = (err, phase, hash) => {
    const decoded = decodeError(err, cfg.abis)
    emit({ phase: "failed", hash, error: decoded })
    throw Object.assign(flowError(decoded.message, decoded.kind), { phase, hash, reported: true })
  }

  emit({ phase: "switching" })
  try {
    await ensureChain(provider, cfg.chain)
  } catch (err) {
    fail(isUserRejection(err) ? flowError("Switch the wallet to Robinhood Chain to continue.", "rejected") : err, "switching")
  }

  emit({ phase: "simulating" })
  let simulated
  try {
    simulated = (await cfg.client.simulateContract({ account: user, address: call.address, abi: call.abi, functionName: call.functionName, args: call.args })).result
  } catch (err) {
    fail(err, "simulating")
  }
  const blocked = call.checkCode ? checkLendingCode(simulated) : null
  if (blocked) fail(flowError(blocked), "simulating")

  emit({ phase: "signing" })
  let hash
  try {
    const wallet = createWalletClient({ account: user, chain: cfg.chain, transport: custom(provider) })
    hash = await wallet.writeContract({ address: call.address, abi: call.abi, functionName: call.functionName, args: call.args, chain: cfg.chain })
  } catch (err) {
    fail(err, "signing")
  }

  emit({ phase: "submitted", hash })
  remember(hash, user)
  let receipt
  let cancelled = false
  try {
    receipt = await cfg.client.waitForTransactionReceipt({
      hash,
      timeout: RECEIPT_TIMEOUT_MS,
      onReplaced: (r) => {
        if (r.reason === "repriced") hash = r.transaction.hash
        else cancelled = true
      },
    })
  } catch {
    // No answer is not a failure: the transaction may still land.
    const message = "The transaction was sent but not confirmed yet. Check it before trying again."
    emit({ phase: "unconfirmed", hash, error: { kind: "network", message } })
    throw Object.assign(flowError(message, "network"), { phase: "unconfirmed", hash, reported: true })
  }
  forget(hash)
  if (cancelled) fail(flowError("The transaction was replaced or cancelled in the wallet."), "submitted", hash)
  if (receipt.status !== "success") fail(flowError("The transaction failed on chain. Nothing was changed."), "submitted", hash)

  emit({ phase: "confirmed", hash })
  return { hash, receipt }
}

// ---------------------------------------------------------------------------
// Calls
// ---------------------------------------------------------------------------

function calls(cfg) {
  const P = cfg.abis.pToken
  const E = cfg.abis.erc20
  const C = cfg.tokens.controller
  return {
    approve: (m, amount) => ({
      id: amount === 0n ? "reset-approval" : "approve",
      label: amount === 0n ? `Reset ${m.symbol} approval` : `Approve ${m.symbol}`,
      address: m.underlying,
      abi: E,
      functionName: "approve",
      args: [m.pToken, amount],
    }),
    supply: (m, amount) => ({ id: "supply", label: `Supply ${m.symbol}`, address: m.pToken, abi: P, functionName: "mint", args: [amount], checkCode: true }),
    /** `all` redeems every share, so no dust of the position is left behind. */
    withdraw: (m, amount, all) => ({
      id: "withdraw",
      label: `Withdraw ${m.symbol}`,
      address: m.pToken,
      abi: P,
      functionName: all ? "redeem" : "redeemUnderlying",
      args: [amount],
      checkCode: true,
    }),
    borrow: (m, amount) => ({ id: "borrow", label: `Borrow ${m.symbol}`, address: m.pToken, abi: P, functionName: "borrow", args: [amount], checkCode: true }),
    /** uint256.max repays exactly the accrued balance at execution time. */
    repay: (m, amount) => ({ id: "repay", label: `Repay ${m.symbol}`, address: m.pToken, abi: P, functionName: "repayBorrow", args: [amount], checkCode: true }),
    enter: (m) => ({ id: "enter", label: `Use ${m.symbol} as collateral`, address: C, abi: CONTROLLER_ABI, functionName: "enterMarkets", args: [[m.pToken]], checkCode: true }),
    exit: (m) => ({ id: "exit", label: `Stop using ${m.symbol} as collateral`, address: C, abi: CONTROLLER_ABI, functionName: "exitMarket", args: [m.pToken], checkCode: true }),
  }
}

const USER_ARG = { Mint: "minter", Redeem: "redeemer", Borrow: "borrower", RepayBorrow: "borrower" }

function hasLendingEvent(cfg, receipt, market, eventName, user) {
  const logs = parseEventLogs({ abi: cfg.abis.pToken, logs: receipt.logs, eventName, strict: false })
  return logs.some(
    (log) =>
      log.address?.toLowerCase() === market.pToken.toLowerCase() &&
      String(log.args?.[USER_ARG[eventName]] ?? "").toLowerCase() === user.toLowerCase(),
  )
}

async function runChecked(ctx, call, onUpdate, confirm) {
  const res = await runCall(ctx, call, onUpdate)
  if (confirm && !hasLendingEvent(ctx.cfg, res.receipt, confirm.market, confirm.event, ctx.user)) {
    throw flowError("The transaction confirmed but the market did not record it. Nothing was changed.")
  }
  return res
}

async function freshPosition(ctx, market) {
  const account = await readLendingAccount(ctx.cfg, ctx.markets, ctx.user)
  const pos = account.positions.find((p) => p.market.id === market.id)
  if (!pos) throw flowError("The market could not be read. Try again in a moment.", "network")
  return pos
}

/** Approve, and if the token refuses to move a nonzero allowance, reset it to zero first. */
async function approveWithReset(ctx, market, current, amount, onUpdate) {
  const c = calls(ctx.cfg)
  try {
    await runCall(ctx, c.approve(market, amount), onUpdate)
  } catch (err) {
    if (!(err?.phase === "simulating" && (current ?? 0n) > 0n)) throw err
    await runCall(ctx, c.approve(market, 0n), onUpdate)
    await runCall(ctx, c.approve(market, amount), onUpdate)
  }
}

// ---------------------------------------------------------------------------
// Flows
// ---------------------------------------------------------------------------

export async function runAction(ctx, kind, input, onUpdate) {
  const c = calls(ctx.cfg)
  const { market } = input

  if (kind === "supply") {
    const { amount } = input
    if (amount <= 0n) throw flowError("Enter an amount greater than zero.")
    const pos = await freshPosition(ctx, market)
    if (pos.walletBalance !== null && pos.walletBalance < amount) throw flowError(`Not enough ${market.symbol} in the wallet.`)
    // An unreadable allowance counts as zero: one extra approval beats a mint that reverts.
    if ((pos.allowance ?? 0n) < amount) await approveWithReset(ctx, market, pos.allowance, amount, onUpdate)
    await runChecked(ctx, c.supply(market, amount), onUpdate, { market, event: "Mint" })
    if (input.enableCollateral && !pos.isCollateral) await runChecked(ctx, c.enter(market), onUpdate)
    return
  }

  if (kind === "withdraw") {
    const pos = await freshPosition(ctx, market)
    let call
    if (input.all) {
      if (!pos.shares) throw flowError("Nothing supplied in this market.")
      call = c.withdraw(market, pos.shares, true)
    } else {
      if (input.amount <= 0n) throw flowError("Enter an amount greater than zero.")
      call = c.withdraw(market, input.amount, false)
    }
    await runChecked(ctx, call, onUpdate, { market, event: "Redeem" })
    return
  }

  if (kind === "borrow") {
    if (input.amount <= 0n) throw flowError("Enter an amount greater than zero.")
    await runChecked(ctx, c.borrow(market, input.amount), onUpdate, { market, event: "Borrow" })
    return
  }

  if (kind === "repay") {
    const pos = await freshPosition(ctx, market)
    const debt = pos.borrowed ?? 0n
    if (debt === 0n) throw flowError("Nothing to repay in this market.")
    // A full repayment pays the balance accrued at execution, a little more
    // than the stored one; it needs an allowance with that headroom.
    const fullAllowance = debt + (debt * REPAY_BUFFER_BPS) / 10_000n + 1n
    const canRepayAll = input.all && pos.walletBalance !== null && pos.walletBalance >= fullAllowance
    let amount
    if (canRepayAll) amount = maxUint256
    else if (input.all) amount = pos.walletBalance !== null && pos.walletBalance < debt ? pos.walletBalance : debt
    else amount = input.amount
    const approval = canRepayAll ? fullAllowance : amount
    if (approval <= 0n) throw flowError(`No ${market.symbol} in the wallet to repay with.`)
    if (!canRepayAll && pos.walletBalance !== null && pos.walletBalance < amount) throw flowError(`Not enough ${market.symbol} in the wallet.`)
    if ((pos.allowance ?? 0n) < approval) await approveWithReset(ctx, market, pos.allowance, approval, onUpdate)
    await runChecked(ctx, c.repay(market, amount), onUpdate, { market, event: "RepayBorrow" })
    return
  }

  if (kind === "collateral-on" || kind === "collateral-off") {
    const enable = kind === "collateral-on"
    await runChecked(ctx, enable ? c.enter(market) : c.exit(market), onUpdate)
    // exitMarket can confirm with a code on a state that moved after the simulation; membership is the ground truth.
    const after = await freshPosition(ctx, market)
    if (after.isCollateral !== null && after.isCollateral !== enable) {
      throw flowError(
        enable
          ? "The market did not enable this collateral. Nothing was changed."
          : "The market kept this collateral on because your debt still needs it.",
      )
    }
    return
  }

  throw flowError(`Unknown action ${kind}.`)
}
