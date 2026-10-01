/**
 * The two boosted lending markets on Robinhood Chain (4663): USDG and NVDA,
 * behind the Peridottroller. Compound style: mint / redeem / borrow /
 * repayBorrow on the pToken, enterMarkets / exitMarket on the controller.
 *
 * Three things are specific to this deployment:
 *
 * 1. Interest accrues per L1 block. `block.number` here is the Ethereum block
 *    (about 12s), not the L2 block, so APYs use the interest-rate model's own
 *    `blocksPerYear()`.
 *
 * 2. Two price sources. The controller values collateral and debt with its own
 *    oracle in Compound units (USD * 1e18 per smallest token unit). The margin
 *    oracle reports USD with 18 decimals per whole token. Values shown use the
 *    margin (reference) oracle; borrow and withdraw limits are the
 *    controller's own, because that is what the contract will accept.
 *
 * 3. Boosted markets. Part of each market sits in the paired liquidity vault;
 *    its results show up in the exchange rate, not in the supply rate.
 *
 * Every read degrades to null rather than to zero: an unreadable price or
 * balance must never render as "$0" or "0 available".
 */
import { parseAbi } from "../vendor/viem-2.33.2.min.js"

export const WAD = 10n ** 18n

export const CONTROLLER_ABI = parseAbi([
  "function oracle() view returns (address)",
  "function markets(address) view returns (bool isListed, uint256 collateralFactorMantissa, bool isComped)",
  "function mintGuardianPaused(address) view returns (bool)",
  "function borrowGuardianPaused(address) view returns (bool)",
  "function borrowCaps(address) view returns (uint256)",
  "function checkMembership(address account, address pToken) view returns (bool)",
  "function getAccountLiquidity(address account) view returns (uint256 err, uint256 liquidity, uint256 shortfall)",
  "function enterMarkets(address[] pTokens) returns (uint256[])",
  "function exitMarket(address pToken) returns (uint256)",
])

const LENDING_ORACLE_ABI = parseAbi(["function getUnderlyingPrice(address pToken) view returns (uint256)"])
const IRM_ABI = parseAbi(["function blocksPerYear() view returns (uint256)"])

export const FALLBACK_BLOCKS_PER_YEAR = 2_628_000n
/** How far the controller's price may sit from the reference before the market counts as mispriced. */
export const PRICE_AGREEMENT_BPS = 500n
/** Below this, a computed borrow limit is rounding and is offered as zero. $0.001. */
export const DUST_USD18 = 10n ** 15n
/** Headroom under every computed maximum: interest accrues between read and signature. */
export const MAX_HEADROOM_BPS = 50n

export function lendingMarkets(cfg) {
  return [
    {
      id: "usdg-robinhood",
      symbol: "USDG",
      name: "Global Dollar",
      kind: "Stablecoin",
      pToken: cfg.tokens.pUSDG,
      underlying: cfg.tokens.USDG,
      decimals: cfg.decimals.USDG,
      icon: "./assets/usdg.png",
    },
    {
      id: "nvda-robinhood",
      symbol: "NVDA",
      name: "NVIDIA",
      kind: "Tokenized stock",
      pToken: cfg.tokens.pNVDA,
      underlying: cfg.tokens.NVDA,
      decimals: cfg.decimals.NVDA,
      icon: "./assets/nvda.svg",
    },
  ]
}

// ---------------------------------------------------------------------------
// Pure math
// ---------------------------------------------------------------------------

/** Compound's APY: compound the per-block rate daily for a year. Percent. */
export function ratePerBlockToApy(ratePerBlock, blocksPerYear) {
  if (ratePerBlock === null) return null
  if (ratePerBlock <= 0n) return 0
  const perDay = (Number(ratePerBlock) / 1e18) * (Number(blocksPerYear) / 365)
  const apy = (Math.pow(1 + perDay, 365) - 1) * 100
  return Number.isFinite(apy) ? apy : null
}

/** Underlying units -> USD18 at a reference price (USD18 per whole token). */
export const usd18 = (amount, priceUsd18, decimals) => (amount * priceUsd18) / 10n ** BigInt(decimals)
/** USD value the way the controller sees it (amount * price / 1e18). */
export const controllerUsd18 = (amount, controllerPrice) => (amount * controllerPrice) / WAD
export const controllerPriceAsUsd18 = (controllerPrice, decimals) => (controllerPrice * 10n ** BigInt(decimals)) / WAD
export const amountToNumber = (amount, decimals) => Number(amount) / 10 ** decimals
export const usd18ToNumber = (value) => Number(value) / 1e18
const withHeadroom = (amount) => (amount * (10_000n - MAX_HEADROOM_BPS)) / 10_000n

export function isMispriced(controllerPrice, referencePriceUsd18, decimals) {
  if (controllerPrice === null || referencePriceUsd18 === null || referencePriceUsd18 <= 0n) return null
  const asUsd18 = controllerPriceAsUsd18(controllerPrice, decimals)
  const diff = asUsd18 > referencePriceUsd18 ? asUsd18 - referencePriceUsd18 : referencePriceUsd18 - asUsd18
  return diff * 10_000n > referencePriceUsd18 * PRICE_AGREEMENT_BPS
}

// ---------------------------------------------------------------------------
// Reads
// ---------------------------------------------------------------------------

async function settle(p) {
  try {
    return await p
  } catch {
    return null
  }
}

const read = (client, address, abi, functionName, args = []) => settle(client.readContract({ address, abi, functionName, args }))

let cachedOracle = null
let cachedBlocksPerYear = null

async function controllerOracle(cfg) {
  if (cachedOracle) return cachedOracle
  const addr = await read(cfg.client, cfg.tokens.controller, CONTROLLER_ABI, "oracle")
  if (addr) cachedOracle = addr
  return addr
}

async function blocksPerYear(cfg, markets) {
  if (cachedBlocksPerYear) return cachedBlocksPerYear
  const irm = await read(cfg.client, markets[0].pToken, cfg.abis.pToken, "interestRateModel")
  const value = irm ? await read(cfg.client, irm, IRM_ABI, "blocksPerYear") : null
  if (value && value > 0n) {
    cachedBlocksPerYear = value
    return value
  }
  return FALLBACK_BLOCKS_PER_YEAR
}

async function readMarket(cfg, market, oracle, perYear) {
  const { client, abis } = cfg
  const C = cfg.tokens.controller
  const p = market.pToken
  const P = abis.pToken
  const [
    totalSupply, totalBorrows, cash, totalReserves, exchangeRate, supplyRate, borrowRate,
    vaultAccounted, vaultPaused, marketInfo, mintPaused, borrowPaused, borrowCap,
    controllerPrice, referencePrice, priceable,
  ] = await Promise.all([
    read(client, p, P, "totalSupply"),
    read(client, p, P, "totalBorrows"),
    read(client, p, P, "getCash"),
    read(client, p, P, "totalReserves"),
    read(client, p, P, "exchangeRateStored"),
    read(client, p, P, "supplyRatePerBlock"),
    read(client, p, P, "borrowRatePerBlock"),
    read(client, p, P, "vaultAccountedAssets"),
    read(client, p, P, "vaultPaused"),
    read(client, C, CONTROLLER_ABI, "markets", [p]),
    read(client, C, CONTROLLER_ABI, "mintGuardianPaused", [p]),
    read(client, C, CONTROLLER_ABI, "borrowGuardianPaused", [p]),
    read(client, C, CONTROLLER_ABI, "borrowCaps", [p]),
    oracle ? read(client, oracle, LENDING_ORACLE_ABI, "getUnderlyingPrice", [p]) : Promise.resolve(null),
    // The margin oracle reverts with PriceUnavailable instead of answering 0.
    read(client, cfg.marginOracle, abis.oracle, "getPrice", [market.underlying]),
    read(client, cfg.marginOracle, abis.oracle, "marketPriceable", [p]),
  ])

  const totalSupplyUnderlying = totalSupply !== null && exchangeRate !== null ? (totalSupply * exchangeRate) / WAD : null
  const referencePriceUsd18 = referencePrice && referencePrice > 0n ? referencePrice : null
  const priceUsd = referencePriceUsd18 !== null ? usd18ToNumber(referencePriceUsd18) : null
  const tvlUsd =
    totalSupplyUnderlying !== null && referencePriceUsd18 !== null
      ? usd18ToNumber(usd18(totalSupplyUnderlying, referencePriceUsd18, market.decimals))
      : null

  let utilizationPct = null
  if (cash !== null && totalBorrows !== null) {
    const base = cash + totalBorrows - (totalReserves ?? 0n)
    utilizationPct = base > 0n ? (Number(totalBorrows) / Number(base)) * 100 : 0
  }

  let vaultShare = null
  if (vaultAccounted !== null && totalSupplyUnderlying !== null && totalSupplyUnderlying > 0n) {
    vaultShare = Math.min(1, Number(vaultAccounted) / Number(totalSupplyUnderlying))
  }

  return {
    market,
    totalSupplyUnderlying,
    totalBorrows,
    cash,
    totalReserves,
    exchangeRate,
    vaultShare,
    collateralFactor: marketInfo ? marketInfo[1] : null,
    isListed: marketInfo ? marketInfo[0] : null,
    mintPaused,
    borrowPaused,
    vaultPaused,
    borrowCap,
    controllerPrice,
    referencePriceUsd18,
    priceable,
    mispriced: isMispriced(controllerPrice, referencePriceUsd18, market.decimals),
    supplyApy: ratePerBlockToApy(supplyRate, perYear),
    borrowApy: ratePerBlockToApy(borrowRate, perYear),
    tvlUsd,
    utilizationPct,
    priceUsd,
  }
}

export async function readLendingMarkets(cfg, markets) {
  const [oracle, perYear] = await Promise.all([controllerOracle(cfg), blocksPerYear(cfg, markets)])
  const states = await Promise.all(markets.map((m) => readMarket(cfg, m, oracle, perYear)))
  // A chain that answers nothing at all is an error, not a table of dashes.
  if (states.every((s) => s.totalSupplyUnderlying === null && s.cash === null)) throw new Error("Robinhood Chain did not answer.")
  return { markets: states, blocksPerYear: perYear, readAt: Date.now() }
}

export async function readLendingAccount(cfg, markets, user) {
  const { client, abis } = cfg
  const C = cfg.tokens.controller
  const positionsP = Promise.all(
    markets.map(async (market) => {
      const [shares, borrowed, exchangeRate, walletBalance, allowance, isCollateral] = await Promise.all([
        read(client, market.pToken, abis.pToken, "balanceOf", [user]),
        read(client, market.pToken, abis.pToken, "borrowBalanceStored", [user]),
        read(client, market.pToken, abis.pToken, "exchangeRateStored"),
        read(client, market.underlying, abis.erc20, "balanceOf", [user]),
        read(client, market.underlying, abis.erc20, "allowance", [user, market.pToken]),
        read(client, C, CONTROLLER_ABI, "checkMembership", [user, market.pToken]),
      ])
      const supplied = shares !== null && exchangeRate !== null ? (shares * exchangeRate) / WAD : null
      return { market, shares, supplied, borrowed, walletBalance, allowance, isCollateral }
    }),
  )
  const [positions, liquidity, nativeBalanceWei, gasPriceWei] = await Promise.all([
    positionsP,
    read(client, C, CONTROLLER_ABI, "getAccountLiquidity", [user]),
    settle(client.getBalance({ address: user })),
    settle(client.getGasPrice()),
  ])
  const liquidityOk = liquidity !== null && liquidity[0] === 0n
  return {
    user,
    positions,
    contractLiquidity: liquidityOk ? liquidity[1] : null,
    contractShortfall: liquidityOk ? liquidity[2] : null,
    nativeBalanceWei,
    gasPriceWei,
    readAt: Date.now(),
  }
}

// ---------------------------------------------------------------------------
// Derived account view
// ---------------------------------------------------------------------------

export function summarizeLending(markets, account) {
  const empty = {
    suppliedUsd18: null,
    borrowedUsd18: null,
    borrowLimitUsd18: null,
    contractLiquidityUsd18: account?.contractLiquidity ?? null,
    limitUsedPct: null,
    liquidatable: (account?.contractShortfall ?? 0n) > 0n,
    pricesMissing: false,
    undercreditedMarkets: [],
    netApy: null,
  }
  if (!markets || !account) return empty

  let supplied = 0n
  let borrowed = 0n
  let limit = 0n
  let pricesMissing = false
  let ratesMissing = false
  let supplyYield = 0
  let borrowCost = 0
  const undercredited = []

  for (const pos of account.positions) {
    const state = markets.markets.find((m) => m.market.id === pos.market.id)
    const price = state?.referencePriceUsd18 ?? null
    const hasSupply = (pos.supplied ?? 0n) > 0n
    const hasDebt = (pos.borrowed ?? 0n) > 0n
    if (!hasSupply && !hasDebt) continue
    if (price === null || pos.supplied === null || pos.borrowed === null) {
      pricesMissing = true
      continue
    }
    const s = usd18(pos.supplied, price, pos.market.decimals)
    const b = usd18(pos.borrowed, price, pos.market.decimals)
    supplied += s
    borrowed += b
    // An unreadable rate on a held side makes the net unknown, not that side 0%.
    const supplyApy = hasSupply ? state?.supplyApy ?? null : 0
    const borrowApy = hasDebt ? state?.borrowApy ?? null : 0
    if (supplyApy === null || borrowApy === null) ratesMissing = true
    supplyYield += usd18ToNumber(s) * ((supplyApy ?? 0) / 100)
    borrowCost += usd18ToNumber(b) * ((borrowApy ?? 0) / 100)
    if (pos.isCollateral && hasSupply) {
      limit += (s * (state?.collateralFactor ?? 0n)) / WAD
      if (state?.mispriced && state.controllerPrice !== null) {
        const credited = controllerUsd18(pos.supplied, state.controllerPrice)
        if (credited < s) undercredited.push(pos.market.id)
      }
    }
  }

  const limitUsedPct = pricesMissing ? null : limit > 0n ? (Number(borrowed) / Number(limit)) * 100 : borrowed > 0n ? 100 : null
  const net = supplied > 0n && !ratesMissing ? ((supplyYield - borrowCost) / usd18ToNumber(supplied)) * 100 : null

  return {
    ...empty,
    suppliedUsd18: pricesMissing ? null : supplied,
    borrowedUsd18: pricesMissing ? null : borrowed,
    borrowLimitUsd18: pricesMissing ? null : limit,
    limitUsedPct,
    pricesMissing,
    undercreditedMarkets: undercredited,
    netApy: net,
  }
}

// ---------------------------------------------------------------------------
// Per-action limits
// ---------------------------------------------------------------------------

const blockedLimit = (reason) => ({ max: 0n, reason, blocked: true, boundBy: "none" })

/**
 * The borrow and withdraw limits are the controller's: what the contract
 * would accept right now, less a small headroom, and never more than the
 * market's cash or borrow cap. Reference prices do not narrow them.
 */
export function lendingLimit(action, marketId, markets, account) {
  const state = markets?.markets.find((m) => m.market.id === marketId)
  const pos = account?.positions.find((p) => p.market.id === marketId)
  if (!state || !pos) return blockedLimit("Market data is still loading.")
  const { symbol } = state.market

  if (action === "supply") {
    if (state.mintPaused) return blockedLimit("Supplying is paused in this market.")
    if (pos.walletBalance === null) return blockedLimit("The wallet balance could not be read.")
    if (pos.walletBalance === 0n) return { max: 0n, reason: `No ${symbol} in this wallet on Robinhood Chain.`, blocked: false, boundBy: "wallet" }
    return { max: pos.walletBalance, reason: null, blocked: false, boundBy: "wallet" }
  }

  if (action === "repay") {
    if (pos.borrowed === null) return blockedLimit("The debt could not be read.")
    if (pos.borrowed === 0n) return { max: 0n, reason: "Nothing to repay in this market.", blocked: false, boundBy: "debt" }
    if (pos.walletBalance === null) return blockedLimit("The wallet balance could not be read.")
    const short = pos.walletBalance < pos.borrowed
    return {
      max: short ? pos.walletBalance : pos.borrowed,
      reason: short ? `The wallet holds less ${symbol} than the debt.` : null,
      blocked: false,
      boundBy: short ? "wallet" : "debt",
    }
  }

  const summary = summarizeLending(markets, account)

  if (action === "withdraw") {
    if (pos.supplied === null) return blockedLimit("The supplied balance could not be read.")
    if (pos.supplied === 0n) return { max: 0n, reason: "Nothing supplied in this market.", blocked: false, boundBy: "supplied" }
    let max = pos.supplied
    let boundBy = "supplied"
    let reason = null
    const hasDebt = (summary.borrowedUsd18 ?? 0n) > 0n || account.positions.some((p) => (p.borrowed ?? 0n) > 0n)

    // Collateral backing a loan can only leave as far as the controller allows.
    if (pos.isCollateral && hasDebt) {
      const cf = state.collateralFactor ?? 0n
      if (cf > 0n) {
        if (summary.contractLiquidityUsd18 === null || !state.controllerPrice || state.controllerPrice <= 0n) {
          return blockedLimit("The withdrawal limit could not be read from the market. Repaying still works.")
        }
        const contractMax = withHeadroom((((summary.contractLiquidityUsd18 * WAD) / cf) * WAD) / state.controllerPrice)
        if (contractMax < max) {
          max = contractMax
          boundBy = "collateral"
          reason = "The rest backs your loan. Repay first to withdraw more."
        }
      }
    }
    if (state.cash !== null && state.cash < max) {
      max = state.cash
      boundBy = "liquidity"
      reason = "The market cannot pay out more right now; the rest is lent out or in the paired vault."
    }
    return { max: max < 0n ? 0n : max, reason, blocked: false, boundBy }
  }

  // borrow
  if (state.borrowPaused) return blockedLimit("Borrowing is paused in this market.")
  if (summary.liquidatable) return blockedLimit("This account is below its collateral requirement. Repay or add collateral first.")
  if (summary.contractLiquidityUsd18 === null) return blockedLimit("The borrow limit could not be read from the market.")
  if (!state.controllerPrice || state.controllerPrice <= 0n || state.priceable === false) {
    return blockedLimit("Prices are unavailable right now. Borrowing waits for a fresh price; repaying still works.")
  }

  let max = withHeadroom((summary.contractLiquidityUsd18 * WAD) / state.controllerPrice)
  let boundBy = "collateral"
  let reason = null
  // A limit worth less than a tenth of a cent is rounding, not capacity.
  if (controllerUsd18(max, state.controllerPrice) < DUST_USD18) max = 0n
  if (max === 0n) {
    reason = summary.undercreditedMarkets.length
      ? "Your collateral is not counted by the market right now, so it lends nothing against it."
      : "Supply an asset and turn it on as collateral to borrow."
  }
  if (state.cash !== null && state.cash < max) {
    max = state.cash
    boundBy = "liquidity"
    reason = "The market has no more to lend right now."
  }
  if (state.borrowCap !== null && state.borrowCap > 0n && state.totalBorrows !== null) {
    const room = state.borrowCap > state.totalBorrows ? state.borrowCap - state.totalBorrows : 0n
    if (room < max) {
      max = room
      boundBy = "cap"
      reason = room === 0n ? "The market's borrow cap is reached." : "Limited by the market's borrow cap."
    }
  }
  return { max: max < 0n ? 0n : max, reason, blocked: false, boundBy }
}

/** Borrow-limit usage after a proposed action, for the capacity bar preview. */
export function projectedLimitUsedPct(action, marketId, amount, markets, account) {
  if (amount <= 0n) return null
  const summary = summarizeLending(markets, account)
  const state = markets?.markets.find((m) => m.market.id === marketId)
  const pos = account?.positions.find((p) => p.market.id === marketId)
  if (!state?.referencePriceUsd18 || !pos || summary.borrowLimitUsd18 === null || summary.borrowedUsd18 === null) return null
  const value = usd18(amount, state.referencePriceUsd18, state.market.decimals)
  const cfValue = (value * (state.collateralFactor ?? 0n)) / WAD
  let limit = summary.borrowLimitUsd18
  let debt = summary.borrowedUsd18
  if (action === "borrow") debt += value
  if (action === "repay") debt = debt > value ? debt - value : 0n
  if (action === "supply" && pos.isCollateral) limit += cfValue
  if (action === "withdraw" && pos.isCollateral) limit = limit > cfValue ? limit - cfValue : 0n
  if (limit === 0n) return debt > 0n ? 100 : 0
  return (Number(debt) / Number(limit)) * 100
}

// ---------------------------------------------------------------------------
// Gas (ETH on Robinhood Chain)
// ---------------------------------------------------------------------------

const GAS_BUDGET_ACTION = 400_000n
const GAS_HEADROOM = 2n
/** Used when the gas price could not be read or came back zero. */
const FALLBACK_GAS_PRICE_WEI = 500_000_000n

/** Is there enough ETH to sign? An unreadable balance never blocks. */
export function gasStatus(account) {
  const price = account?.gasPriceWei && account.gasPriceWei > 0n ? account.gasPriceWei : FALLBACK_GAS_PRICE_WEI
  const required = price * GAS_HEADROOM * GAS_BUDGET_ACTION
  const balance = account?.nativeBalanceWei ?? null
  if (balance === null) return { blocks: false, message: null }
  if (balance === 0n) {
    return {
      blocks: true,
      message: "This wallet holds no ETH on Robinhood Chain. ETH pays the network fee for every step, so nothing can be signed until some arrives.",
    }
  }
  if (balance < required) {
    return { blocks: true, message: "There is not enough ETH for the network fee. Top the wallet up with ETH on Robinhood Chain before trying again." }
  }
  return { blocks: false, message: null }
}
