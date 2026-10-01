/** Small DOM, icon and formatting helpers shared by the page. */
import { formatUnits, parseUnits } from "../vendor/viem-2.33.2.min.js"

export const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c])

/** Assign innerHTML only when it changed, so running animations are not restarted. */
export function setHTML(el, html) {
  if (el.__html !== html) {
    el.innerHTML = html
    el.__html = html
  }
}

export function h(tag, attrs = {}, html = "") {
  const el = document.createElement(tag)
  for (const [k, v] of Object.entries(attrs)) {
    if (v === false || v === null || v === undefined) continue
    if (k === "class") el.className = v
    else el.setAttribute(k, v === true ? "" : v)
  }
  if (html) el.innerHTML = html
  return el
}

/** Attributes for an InfoTooltip-style trigger. */
export const tipAttrs = (content, title) => `data-tip="${esc(content)}"${title ? ` data-tip-title="${esc(title)}"` : ""}`

// Lucide icon paths (lucide.dev, ISC licence).
const PATHS = {
  search: '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
  x: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
  chevronRight: '<path d="m9 18 6-6-6-6"/>',
  zap: '<path d="M4 14a1 1 0 0 1-.78-1.63l9.9-10.2a.5.5 0 0 1 .86.46l-1.92 6.02A1 1 0 0 0 13 10h7a1 1 0 0 1 .78 1.63l-9.9 10.2a.5.5 0 0 1-.86-.46l1.92-6.02A1 1 0 0 0 11 14z"/>',
  alert: '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
  info: '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/>',
  checkCircle: '<circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/>',
  loader: '<path d="M21 12a9 9 0 1 1-6.219-8.56"/>',
  wallet: '<path d="M19 7V4a1 1 0 0 0-1-1H5a2 2 0 0 0 0 4h15a1 1 0 0 1 1 1v4h-3a2 2 0 0 0 0 4h3a1 1 0 0 0 1-1v-2a1 1 0 0 0-1-1"/><path d="M3 5v14a2 2 0 0 0 2 2h15a1 1 0 0 0 1-1v-4"/>',
  handCoins: '<path d="M11 15h2a2 2 0 1 0 0-4h-3c-.6 0-1.1.2-1.4.6L3 17"/><path d="m7 21 1.6-1.4c.3-.4.8-.6 1.4-.6h4c1.1 0 2.1-.4 2.8-1.2l4.6-4.4a2 2 0 0 0-2.75-2.91l-4.2 3.9"/><path d="m2 16 6 6"/><circle cx="16" cy="9" r="2.9"/><circle cx="6" cy="5" r="3"/>',
  piggyBank: '<path d="M19 5c-1.5 0-2.8 1.4-3 2-3.5-1.5-11-.3-11 5 0 1.8 0 3 2 4.5V20h4v-2h3v2h4v-4c1-.5 1.7-1 2-2h2v-4h-2c0-1-.5-1.5-1-2V5z"/><path d="M2 9v1c0 1.1.9 2 2 2h1"/><path d="M16 11h.01"/>',
  receipt: '<path d="M4 2v20l2-1 2 1 2-1 2 1 2-1 2 1 2-1 2 1V2l-2 1-2-1-2 1-2-1-2 1-2-1-2 1Z"/><path d="M14 8H8"/><path d="M16 12H8"/><path d="M13 16H8"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  clock: '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
  external: '<path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h3"/>',
  copy: '<rect width="14" height="14" x="8" y="8" rx="2" ry="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>',
  arrowRight: '<path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>',
  logOut:'<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" x2="9" y1="12" y2="12"/>',
}

export const icon = (name, cls = "") =>
  `<svg class="svg-icon ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${PATHS[name]}</svg>`

// ---------------------------------------------------------------------------
// Formatting: amounts stay bigint until the last step.
// ---------------------------------------------------------------------------

export function fmtUsd(n) {
  if (n === null || n === undefined || !Number.isFinite(n)) return "--"
  if (n === 0) return "$0.00"
  if (n < 0.01) return "<$0.01"
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(2)}M`
  if (n >= 10_000) return `$${(n / 1_000).toFixed(1)}K`
  return `$${n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

export function fmtPrice(n) {
  if (n === null || n === undefined || !Number.isFinite(n) || n <= 0) return "--"
  if (n >= 1) return `$${n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
  return `$${n.toFixed(4)}`
}

export function fmtPct(n, digits = 2) {
  if (n === null || n === undefined || !Number.isFinite(n)) return "--"
  if (n > 0 && n < 0.005) return "<0.01%"
  return `${n.toFixed(digits)}%`
}

/** Table APY: "--" for zero, compact above 1000%. */
export function fmtRowPct(n) {
  if (!n || n <= 0) return "--"
  if (n < 0.005) return "<0.01%"
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K%`
  return `${n.toFixed(2)}%`
}

export function fmtTvl(n) {
  if (!n || n <= 0) return "--"
  if (n >= 1_000_000_000) return `$${(n / 1_000_000_000).toFixed(2)}B`
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(2)}M`
  if (n >= 1_000) return `$${(n / 1_000).toFixed(2)}K`
  return `$${n.toFixed(2)}`
}

export function fmtBadge(usd) {
  if (usd >= 1000) return `$${(usd / 1000).toFixed(1)}K`
  if (usd >= 0.01) return `$${usd.toFixed(2)}`
  return "<$0.01"
}

/** A token amount with precision that fits the asset: dollars to cents, a $200 stock to six places. */
export function fmtAmount(raw, decimals, symbol) {
  if (raw === null || raw === undefined) return "--"
  const n = Number(formatUnits(raw, decimals))
  const places = decimals <= 6 ? 2 : 6
  let body
  if (raw === 0n) body = "0"
  else if (n > 0 && n < 10 ** -places) body = `<${(10 ** -places).toFixed(places)}`
  else body = n.toLocaleString("en-US", { maximumFractionDigits: places })
  return symbol ? `${body} ${symbol}` : body
}

/** The balance shown in and above the amount field. */
export function fmtBalance(raw, decimals) {
  const n = Number(formatUnits(raw, decimals))
  if (!n || n <= 0) return "0"
  if (n < 0.0001) return n.toExponential(2)
  const fixed = n < 1 ? n.toFixed(6) : n < 100 ? n.toFixed(4) : n.toFixed(2)
  return fixed.includes(".") ? fixed.replace(/0+$/, "").replace(/\.$/, "") : fixed
}

/** Typed text to base units, truncating extra decimals. Anything unparsable is 0. */
export function parseAmount(value, decimals) {
  const v = String(value ?? "").trim()
  if (!/^\d*\.?\d*$/.test(v) || v === "" || v === ".") return 0n
  const [int, frac = ""] = v.split(".")
  try {
    return parseUnits(`${int || "0"}.${frac.slice(0, decimals) || "0"}`, decimals)
  } catch {
    return 0n
  }
}

/** Base units to an input string, at most `places` decimals, rounded down. */
export function toInputString(raw, decimals, places = decimals) {
  const s = formatUnits(raw, decimals)
  if (!s.includes(".")) return s
  const [int, frac] = s.split(".")
  const cut = frac.slice(0, places).replace(/0+$/, "")
  return cut ? `${int}.${cut}` : int
}

export const shortAddress = (a) => (a ? `${a.slice(0, 6)}…${a.slice(-4)}` : "")

/**
 * Part of a boosted market works in the paired liquidity vault. Its result
 * reaches suppliers through the share price, not the supply rate, and it can
 * be negative. Below half a percent the market counts as not boosted.
 */
export const isBoostedShare = (v) => v !== null && v !== undefined && Number.isFinite(v) && v > 0.005

export function boostedHint(vaultShare, vaultPaused) {
  const pct = Math.round(vaultShare * 100)
  const base =
    `About ${pct}% of this market works in Peridot's paired liquidity vault. ` +
    "What the vault earns raises the value of your deposit directly, so it is not part of the supply APY shown here. " +
    "Vault results are not guaranteed and can also be negative."
  return vaultPaused ? `${base} The vault is paused right now, so no new funds go into it.` : base
}
