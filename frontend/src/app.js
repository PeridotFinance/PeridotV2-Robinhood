/**
 * Robinhood Chain lending: the Peridot app's Expert view, as one static page.
 *
 * Account strip on top, the market table below it, and per market an
 * expanded panel with the market facts and one tab per action (Supply,
 * Withdraw, Borrow, Repay). Reads are live on-chain; every transaction is
 * simulated before the wallet is asked to sign.
 *
 * Rendering is plain DOM. Elements are built once and updated in place, so
 * the transitions (row accent, panel height, capacity bar) run like the app's
 * instead of restarting on every refresh.
 */
import { loadConfig } from "./config.js"
import {
  amountToNumber,
  gasStatus,
  lendingLimit,
  lendingMarkets,
  projectedLimitUsedPct,
  readLendingAccount,
  readLendingMarkets,
  summarizeLending,
  usd18,
  usd18ToNumber,
} from "./lending.js"
import { decodeError, reconcilePending, runAction } from "./flows.js"
import { findProvider, listProviders, onProvidersChanged, requestAccount, silentAccount } from "./wallet.js"
import {
  boostedHint,
  esc,
  fmtAmount,
  fmtBadge,
  fmtBalance,
  fmtPct,
  fmtPrice,
  fmtRowPct,
  fmtTvl,
  fmtUsd,
  h,
  icon,
  isBoostedShare,
  parseAmount,
  setHTML,
  shortAddress,
  tipAttrs,
  toInputString,
} from "./ui.js"

const MARKETS_REFRESH_MS = 20_000
const ACCOUNT_REFRESH_MS = 15_000
const WALLET_KEY = "peridot.rh.wallet"

const S = {
  cfg: null,
  markets: [],
  data: null,
  dataError: null,
  account: null,
  accountLoading: false,
  user: null,
  wallet: null,
  expanded: null,
  search: "",
  busy: false,
}

const store = {
  get(k) {
    try {
      return localStorage.getItem(k)
    } catch {
      return null
    }
  },
  set(k, v) {
    try {
      if (v === null) localStorage.removeItem(k)
      else localStorage.setItem(k, v)
    } catch {
      // Storage blocked: the wallet just will not reconnect on reload.
    }
  },
}

const $ = (sel, root = document) => root.querySelector(sel)

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

async function boot() {
  wireToolbar()
  wireTooltips()
  try {
    S.cfg = await loadConfig()
  } catch (err) {
    $("#strip").replaceChildren(h("div", { class: "boot-error" }, esc(err.message)))
    return
  }
  S.markets = lendingMarkets(S.cfg)
  buildTable()
  render()
  // Market reads do not wait for the wallet; the account read follows once it is back.
  refreshMarkets()
  reconnectWallet()
  setInterval(() => document.hidden || refreshMarkets(), MARKETS_REFRESH_MS)
  setInterval(() => document.hidden || refreshAccount(), ACCOUNT_REFRESH_MS)
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refreshAll()
  })
}

async function refreshMarkets() {
  try {
    S.data = await readLendingMarkets(S.cfg, S.markets)
    S.dataError = null
  } catch (err) {
    S.dataError = err
  }
  render()
}

let accountSeq = 0
async function refreshAccount() {
  const user = S.user
  if (!user) return
  const seq = ++accountSeq
  if (!S.account) {
    S.accountLoading = true
    render()
  }
  try {
    const account = await readLendingAccount(S.cfg, S.markets, user)
    if (seq === accountSeq && S.user === user) S.account = account
  } catch {
    // Keep the last good read; the next tick retries.
  } finally {
    if (seq === accountSeq) S.accountLoading = false
    render()
  }
}

const refreshAll = () => Promise.all([refreshMarkets(), refreshAccount()])

function render() {
  renderWalletPill()
  renderStrip()
  renderTable()
}

// ---------------------------------------------------------------------------
// Toolbar: search and wallet
// ---------------------------------------------------------------------------

function wireToolbar() {
  const box = $("#search")
  const input = $("#search input")
  const sync = () => {
    box.classList.toggle("active", document.activeElement === input || !!input.value)
    box.classList.toggle("has-value", !!input.value)
  }
  input.addEventListener("focus", sync)
  input.addEventListener("blur", sync)
  input.addEventListener("input", () => {
    S.search = input.value
    sync()
    renderTable()
  })
  $("#search .clear").addEventListener("mousedown", (e) => {
    e.preventDefault()
    input.value = ""
    S.search = ""
    sync()
    renderTable()
    input.focus()
  })
  $("#wallet-pill").addEventListener("click", () => (S.user ? openAccountDialog() : openConnectDialog()))
  $("#brand").addEventListener("click", (e) => {
    // A modified click opens a new tab and leaves this page where it is.
    if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return
    e.preventDefault()
    openLeaveDialog(e.currentTarget.href)
  })
}

function openLeaveDialog(href) {
  const host = new URL(href).host
  const body = h(
    "div",
    { class: "leave-box" },
    `<a class="leave-dest" href="${esc(href)}">
       <span class="mark"><img src="./assets/peridot.svg" alt=""></span>
       <span class="grow"><span class="host">${esc(host)}</span><br><span class="what">Peridot website</span></span>
       ${icon("external")}
     </a>
     <p class="leave-text">Your positions stay on Robinhood Chain and nothing is signed by leaving. Come back to this page at any time to manage them.</p>
     ${
       S.busy
         ? `<div class="notice notice-warn">${icon("alert")}<span>A transaction is still running. It continues on chain, but this page stops following it.</span></div>`
         : ""
     }
     <div class="leave-actions">
       <button type="button" class="btn-ghost" data-close>Stay here</button>
       <a class="submit submit-earn" href="${esc(href)}" data-go><span class="inner">Continue${icon("arrowRight")}</span></a>
     </div>`,
  )
  openDialog("Leave this page?", `You are about to visit ${host}.`, body)
  $("[data-go]", body).focus({ preventScroll: true })
}

function renderWalletPill() {
  const pill = $("#wallet-pill")
  setHTML(
    pill,
    S.user
      ? `<span class="dot"></span><span class="mono">${esc(shortAddress(S.user))}</span>`
      : `${icon("wallet")}<span>Connect<span class="pill-label-full"> wallet</span></span>`,
  )
}

// ---------------------------------------------------------------------------
// Wallet
// ---------------------------------------------------------------------------

async function reconnectWallet() {
  const rdns = store.get(WALLET_KEY)
  if (!rdns) return
  // EIP-6963 announcements arrive asynchronously; give them a moment.
  let entry = findProvider(rdns)
  for (let i = 0; !entry && i < 10; i++) {
    await new Promise((r) => setTimeout(r, 50))
    entry = findProvider(rdns)
  }
  if (!entry) return
  const address = await silentAccount(entry.provider)
  if (address) setWallet(entry, address)
}

function setWallet(entry, address) {
  if (S.wallet?.provider && S.wallet.provider !== entry?.provider) S.wallet.provider.removeListener?.("accountsChanged", onAccountsChanged)
  S.wallet = entry
  S.user = address ?? null
  S.account = null
  if (entry) {
    entry.provider.on?.("accountsChanged", onAccountsChanged)
    store.set(WALLET_KEY, entry.info.rdns)
  } else {
    store.set(WALLET_KEY, null)
  }
  render()
  refreshAccount()
}

function onAccountsChanged(accounts) {
  if (!accounts?.length) setWallet(null, null)
  else if (accounts[0] !== S.user) setWallet(S.wallet, accounts[0])
}

function openDialog(title, sub, body) {
  const overlay = h(
    "div",
    { class: "overlay", role: "dialog", "aria-modal": "true" },
    `<div class="dialog">
      <div class="dialog-head"><h2>${esc(title)}</h2><button type="button" aria-label="Close" data-close>${icon("x")}</button></div>
      <p class="dialog-sub">${esc(sub)}</p>
      <div class="dialog-body"></div>
    </div>`,
  )
  $(".dialog-body", overlay).append(body)
  const close = () => {
    overlay.classList.remove("open")
    document.removeEventListener("keydown", onKey)
    setTimeout(() => overlay.remove(), 200)
  }
  const onKey = (e) => e.key === "Escape" && close()
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay || e.target.closest("[data-close]")) close()
  })
  document.addEventListener("keydown", onKey)
  document.body.append(overlay)
  requestAnimationFrame(() => overlay.classList.add("open"))
  return close
}

function openConnectDialog() {
  const body = h("div")
  const list = h("div", { class: "wallet-list" })
  const error = h("div", { class: "dialog-error" })
  body.append(list, error)
  let close = () => {}

  const draw = () => {
    const providers = listProviders()
    if (providers.length === 0) {
      setHTML(list, `<div class="wallet-empty">No browser wallet found. Install an EVM wallet such as MetaMask or Rabby and reload the page.</div>`)
      return
    }
    list.replaceChildren(
      ...providers.map((p) => {
        const btn = h(
          "button",
          { type: "button", class: "wallet-option" },
          `${p.info.icon ? `<img src="${esc(p.info.icon)}" alt="">` : `<span class="wallet-fallback">${icon("wallet")}</span>`}
           <span class="grow">${esc(p.info.name)}</span>${icon("chevronRight")}`,
        )
        btn.addEventListener("click", async () => {
          setHTML(error, "")
          try {
            const address = await requestAccount(p.provider)
            setWallet(p, address)
            close()
          } catch (err) {
            setHTML(error, `<div class="flow-error">${esc(decodeError(err, S.cfg.abis).message)}</div>`)
          }
        })
        return btn
      }),
    )
  }
  draw()
  const off = onProvidersChanged(draw)
  close = openDialog("Connect a wallet", "Robinhood Chain markets use an EVM wallet. Network fees are paid in ETH.", body)
  const origClose = close
  close = () => {
    off()
    origClose()
  }
}

function openAccountDialog() {
  const url = `${S.cfg.explorer}/address/${S.user}`
  const body = h(
    "div",
    { class: "account-box" },
    `<div class="account-addr">${esc(S.user)}</div>
     <div class="account-actions">
       <button type="button" class="btn-ghost" data-copy>${icon("copy")}<span>Copy</span></button>
       <a class="btn-ghost" href="${esc(url)}" target="_blank" rel="noreferrer">${icon("external")}<span>Explorer</span></a>
       <button type="button" class="btn-ghost" data-disconnect>${icon("logOut")}<span>Disconnect</span></button>
     </div>`,
  )
  const close = openDialog(S.wallet?.info.name ?? "Wallet", "Connected on Robinhood Chain.", body)
  $("[data-copy]", body).addEventListener("click", async (e) => {
    try {
      await navigator.clipboard.writeText(S.user)
      e.currentTarget.querySelector("span").textContent = "Copied"
    } catch {
      // Clipboard blocked; the address is selectable above.
    }
  })
  $("[data-disconnect]", body).addEventListener("click", () => {
    S.wallet?.provider.request?.({ method: "wallet_revokePermissions", params: [{ eth_accounts: {} }] }).catch(() => {})
    setWallet(null, null)
    close()
  })
}

// ---------------------------------------------------------------------------
// Tooltips (InfoTooltip: hover on desktop, tap on touch)
// ---------------------------------------------------------------------------

function wireTooltips() {
  const tip = h("div", { class: "tip", role: "tooltip" })
  document.body.append(tip)
  let anchor = null
  let timer = null

  const show = (el) => {
    clearTimeout(timer)
    anchor = el
    const title = el.getAttribute("data-tip-title")
    tip.innerHTML = `${title ? `<b>${esc(title)}</b>` : ""}${esc(el.getAttribute("data-tip"))}`
    const r = el.getBoundingClientRect()
    const w = tip.offsetWidth
    const left = Math.min(Math.max(16, r.left + r.width / 2 - w / 2), window.innerWidth - w - 16)
    const above = r.top - tip.offsetHeight - 10
    tip.style.left = `${left}px`
    tip.style.top = `${above > 8 ? above : r.bottom + 10}px`
    tip.classList.add("open")
  }
  const hide = () => {
    timer = setTimeout(() => {
      tip.classList.remove("open")
      anchor = null
    }, 120)
  }

  document.addEventListener("mouseover", (e) => {
    const el = e.target.closest?.("[data-tip]")
    if (el && matchMedia("(hover: hover)").matches) show(el)
  })
  document.addEventListener("mouseout", (e) => {
    const el = e.target.closest?.("[data-tip]")
    if (el && !el.contains(e.relatedTarget)) hide()
  })
  tip.addEventListener("mouseenter", () => clearTimeout(timer))
  tip.addEventListener("mouseleave", hide)
  // Taps open the tooltip and stay out of the row's toggle.
  document.addEventListener(
    "click",
    (e) => {
      const el = e.target.closest?.("[data-tip]")
      if (el) {
        e.stopPropagation()
        if (anchor === el && tip.classList.contains("open") && !matchMedia("(hover: hover)").matches) hide()
        else show(el)
      } else if (!tip.contains(e.target)) {
        tip.classList.remove("open")
        anchor = null
      }
    },
    true,
  )
  window.addEventListener("scroll", () => tip.classList.remove("open"), { passive: true })
}

// ---------------------------------------------------------------------------
// Account strip
// ---------------------------------------------------------------------------

let stripMode = null

function renderStrip() {
  const root = $("#strip")
  const summary = summarizeLending(S.data, S.account)
  const hasPosition = (summary.suppliedUsd18 ?? 0n) > 0n || (summary.borrowedUsd18 ?? 0n) > 0n
  const mode = !S.user ? "connect" : !S.account ? "loading" : hasPosition ? "stats" : "empty"

  if (mode !== stripMode) {
    stripMode = mode
    if (mode === "connect") {
      const btn = h(
        "button",
        { type: "button", class: "strip strip-cta" },
        `<span class="strip-id"><img src="./assets/robinhood-chain.png" alt=""><span>
           <span class="strip-title">Lend and borrow on Robinhood Chain</span>
           <span class="strip-sub">USDG and tokenized NVDA. Connect an EVM wallet to start.</span></span></span>
         <span class="strip-go">Connect ${icon("chevronRight")}</span>`,
      )
      btn.addEventListener("click", openConnectDialog)
      root.replaceChildren(btn)
      return
    }
    if (mode === "loading") {
      root.replaceChildren(h("div", { class: "strip-skeleton skeleton" }))
      return
    }
    const card = h("div", { class: "strip" })
    if (mode === "stats") {
      card.innerHTML = `
        <div class="stats">
          <div class="stat"><span class="label">Supplied</span><span class="stat-value mono" data-k="supplied"></span></div>
          <div class="stat"><span class="label">Borrowed</span><span class="stat-value mono text-amber" data-k="borrowed"></span></div>
          <div class="stat"><span class="label">Borrow limit left</span><span class="stat-value mono" data-k="left"></span></div>
          <div class="stat"><span class="label">Net APY</span><span class="stat-value mono" data-k="net"></span></div>
        </div>
        <div data-k="bar" hidden>
          <div class="limit-bar-head"><span>Borrow limit used</span><span class="mono" data-k="used"></span></div>
          <div class="track"><div class="fill" data-k="fill"></div></div>
        </div>
        <div data-k="alerts"></div>`
    } else {
      card.innerHTML = `
        <div class="strip-id"><img src="./assets/robinhood-chain.png" alt="" style="width:28px;height:28px"><span>
          <span class="strip-title">No positions on Robinhood Chain yet</span>
          <span class="strip-sub">Open a market below to supply USDG or NVDA.</span></span></div>
        <div data-k="alerts"></div>`
    }
    root.replaceChildren(card)
  }
  if (mode === "connect" || mode === "loading") return

  const card = root.firstElementChild
  const k = (name) => card.querySelector(`[data-k="${name}"]`)
  if (mode === "stats") {
    const left =
      summary.borrowLimitUsd18 !== null && summary.borrowedUsd18 !== null
        ? usd18ToNumber(summary.borrowLimitUsd18 > summary.borrowedUsd18 ? summary.borrowLimitUsd18 - summary.borrowedUsd18 : 0n)
        : null
    k("supplied").textContent = fmtUsd(summary.suppliedUsd18 !== null ? usd18ToNumber(summary.suppliedUsd18) : null)
    k("borrowed").textContent = fmtUsd(summary.borrowedUsd18 !== null ? usd18ToNumber(summary.borrowedUsd18) : null)
    k("left").textContent = fmtUsd(left)
    k("net").textContent = fmtPct(summary.netApy)
    k("net").className = `stat-value mono ${(summary.netApy ?? 0) >= 0 ? "text-emerald" : "text-amber"}`
    const used = summary.limitUsedPct
    const showBar = (summary.borrowedUsd18 ?? 0n) > 0n && used !== null
    k("bar").hidden = !showBar
    if (showBar) {
      k("used").textContent = `${used.toFixed(1)}%`
      const fill = k("fill")
      fill.style.width = `${Math.min(100, used)}%`
      fill.className = `fill ${used >= 85 ? "zone-high" : used >= 65 ? "zone-mid" : "zone-safe"}`
    }
  }

  const gas = gasStatus(S.account)
  let alerts = ""
  if (summary.liquidatable) {
    alerts += `<p class="strip-alert text-red">${icon("alert")} This account is below its collateral requirement and can be liquidated. Repay or add collateral.</p>`
  }
  if (gas.blocks && gas.message) alerts += `<p class="strip-alert hint-warn">${icon("alert")} ${esc(gas.message)}</p>`
  setHTML(k("alerts"), alerts)
  k("alerts").hidden = !alerts
}

// ---------------------------------------------------------------------------
// Market table
// ---------------------------------------------------------------------------

const rows = new Map()

function buildTable() {
  const tbody = $("#markets")
  for (const market of S.markets) {
    const tr = h("tr", { class: "market-row", "data-market": market.id })
    tr.innerHTML = `
      <td class="cell-asset"><div class="asset">
        <div class="asset-icon"><img src="${esc(market.icon)}" alt="${esc(market.name)}" width="28" height="28"></div>
        <div style="min-width:0;flex:1">
          <div class="asset-name">${esc(market.name)}</div>
          <div class="asset-symbol">${esc(market.symbol)}</div>
          <div class="badge-row" data-k="badge"></div>
        </div></div></td>
      <td class="cell-apy-mobile mobile-cell" data-k="mobile"></td>
      <td class="cell-center desktop-cell" data-k="supply"></td>
      <td class="cell-center desktop-cell" data-k="borrow"></td>
      <td class="cell-right desktop-cell" data-k="tvl"></td>
      <td class="cell-chev"><div class="chev"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M9 5l7 7-7 7"/></svg></div></td>`
    tr.addEventListener("click", () => toggleMarket(market.id))

    const panelRow = h("tr", { class: "panel-row" })
    const td = h("td", { colspan: "6" })
    const grid = h("div", { class: "panel-grid" })
    const clip = h("div", { class: "panel-clip" })
    grid.append(clip)
    td.append(grid)
    panelRow.append(td)
    tbody.append(tr, panelRow)
    rows.set(market.id, { market, tr, panelRow, grid, clip, panel: null })
  }
  tbody.append(
    h("tr", { class: "table-empty", id: "no-match", hidden: true }, `<td colspan="6"></td>`),
    h("tr", { class: "table-note", id: "chain-silent", hidden: true }, `<td colspan="6">Robinhood Chain did not answer. The table retries on its own.</td>`),
  )
}

function toggleMarket(id) {
  S.expanded = S.expanded === id ? null : id
  for (const [mid, r] of rows) {
    const open = S.expanded === mid
    r.tr.classList.toggle("expanded", open)
    r.grid.classList.toggle("open", open)
    if (open && !r.panel) {
      r.panel = new MarketPanel(r.market)
      r.clip.append(r.panel.el)
      r.panel.update()
    }
    if (open) setTimeout(() => r.grid.scrollIntoView({ behavior: "smooth", block: "nearest" }), 80)
  }
  renderTable()
}

function rateCell(apy, kind, symbol, loading) {
  if (loading) return null
  if (apy === null) {
    return `<span class="explained text-muted" ${tipAttrs("This rate could not be read from the network right now. It will show again on the next refresh.")}>--</span>`
  }
  if (kind === "supply" && !(apy > 0)) {
    return `<span class="explained" ${tipAttrs(`${symbol} pays no supply yield right now because nobody borrows from this market yet. That's a live rate, not missing data; it rises as the market gets used.`)}>--</span>`
  }
  return esc(fmtRowPct(apy))
}

function renderTable() {
  if (!S.markets.length) return
  const q = S.search.trim().toLowerCase()
  const loading = !S.data && !S.dataError
  let visible = 0

  for (const [id, r] of rows) {
    const { market } = r
    const match = !q || market.name.toLowerCase().includes(q) || market.symbol.toLowerCase().includes(q)
    r.tr.hidden = !match
    r.panelRow.hidden = !match
    if (!match) continue
    visible++

    const state = S.data?.markets.find((m) => m.market.id === id)
    const k = (name) => r.tr.querySelector(`[data-k="${name}"]`)
    setHTML(k("badge"), positionBadge(state))

    const boosted = state && isBoostedShare(state.vaultShare)
      ? `<span class="boosted" ${tipAttrs(boostedHint(state.vaultShare, state.vaultPaused), "Boosted")}>${icon("zap")}Boosted</span>`
      : ""
    const supply = rateCell(state ? state.supplyApy : null, "supply", market.symbol, loading)
    const borrow = rateCell(state ? state.borrowApy : null, "borrow", market.symbol, loading)

    setHTML(
      k("mobile"),
      loading
        ? `<div class="apy-stack"><div class="skeleton" style="height:16px;width:56px"></div><div class="skeleton" style="height:12px;width:40px;opacity:.5"></div></div>`
        : `<div class="apy-stack"><span class="apy-supply-line">${boosted}<span class="apy-supply">${supply}</span></span><span class="apy-borrow-small">${borrow}</span></div>`,
    )
    setHTML(
      k("supply"),
      loading
        ? `<div class="skeleton" style="height:16px;width:64px;margin:0 auto"></div>`
        : `<div class="apy-col"><span class="apy-big text-emerald">${supply}</span>${boosted}</div>`,
    )
    setHTML(k("borrow"), loading ? `<div class="skeleton" style="height:16px;width:64px;margin:0 auto"></div>` : `<span class="apy-big text-amber">${borrow}</span>`)
    setHTML(k("tvl"), loading ? `<div class="skeleton" style="height:16px;width:80px;margin-left:auto"></div>` : `<span class="tvl">${esc(fmtTvl(state?.tvlUsd ?? 0))}</span>`)

    r.panel?.update()
  }
  const noMatch = $("#no-match")
  noMatch.hidden = visible > 0
  setHTML(noMatch.firstElementChild, q ? `No assets match "${esc(S.search)}"` : "No assets available on this network")
  $("#chain-silent").hidden = !(S.dataError && !S.data)
}

/** "↑ supplied ↓ borrowed", like the app's badge. */
function positionBadge(state) {
  const pos = S.account?.positions.find((p) => p.market.id === state?.market.id)
  const price = state?.referencePriceUsd18
  if (!pos || !price) return ""
  const hasSupply = (pos.supplied ?? 0n) > 0n
  const hasBorrow = (pos.borrowed ?? 0n) > 0n
  if (!hasSupply && !hasBorrow) return ""
  let out = ""
  if (hasSupply) out += `<span class="badge-supply">↑ ${fmtBadge(usd18ToNumber(usd18(pos.supplied, price, pos.market.decimals)))}</span>`
  if (hasBorrow) out += `<span class="badge-borrow">↓ ${fmtBadge(usd18ToNumber(usd18(pos.borrowed, price, pos.market.decimals)))}</span>`
  if (pos.isCollateral && hasSupply) out += `<span class="badge-coll">Collateral</span>`
  return out
}

// ---------------------------------------------------------------------------
// Expanded panel
// ---------------------------------------------------------------------------

const TABS = [
  { key: "supply", label: "Supply" },
  { key: "withdraw", label: "Withdraw" },
  { key: "borrow", label: "Borrow" },
  { key: "repay", label: "Repay" },
]

class MarketPanel {
  constructor(market) {
    this.market = market
    this.tab = "supply"
    this.forms = new Map()
    this.el = h("div", { class: "panel-content" })
    this.facts = h("div")
    this.tabsEl = h("div", { class: "tabs", role: "tablist" })
    this.body = h("div", { class: "tab-body" })
    for (const t of TABS) {
      const btn = h("button", { type: "button", class: "tab", role: "tab", "data-tab": t.key }, t.label)
      btn.addEventListener("click", () => this.select(t.key))
      this.tabsEl.append(btn)
    }
    this.el.append(this.facts, this.tabsEl, this.body)
    this.select("supply")
  }

  select(key) {
    this.tab = key
    for (const btn of this.tabsEl.children) btn.classList.toggle("active", btn.dataset.tab === key)
    // Tabs mount on first open and stay alive, so a typed amount survives a tab switch.
    if (!this.forms.has(key)) {
      const form = new ActionForm(this.market, key)
      this.forms.set(key, form)
      this.body.append(form.el)
    }
    for (const [k, f] of this.forms) f.el.hidden = k !== key
    this.forms.get(key).update()
  }

  update() {
    const state = S.data?.markets.find((m) => m.market.id === this.market.id)
    setHTML(this.facts, state ? marketFacts(state) : `<div class="facts-skeleton"><div></div><div></div><div></div><div></div></div>`)
    this.forms.get(this.tab)?.update()
  }
}

function metric(label, value, tooltip, valueClass = "", badge = "") {
  return `<div class="metric">
    <span class="label" ${tipAttrs(tooltip, label)}>${esc(label)}</span>
    <div class="metric-value ${valueClass}">${esc(value)}${badge}</div>
  </div>`
}

function marketFacts(state) {
  const { market } = state
  const cashUsd = state.cash !== null && state.referencePriceUsd18 ? usd18ToNumber(usd18(state.cash, state.referencePriceUsd18, market.decimals)) : null
  const cf = state.collateralFactor !== null ? Number(state.collateralFactor) / 1e16 : null
  const capUsed =
    state.borrowCap && state.borrowCap > 0n && state.totalBorrows !== null ? (Number(state.totalBorrows) / Number(state.borrowCap)) * 100 : null
  // Undervalued by the controller: supplied here earns but backs nothing.
  const undervalued =
    state.mispriced === true &&
    state.controllerPrice !== null &&
    state.referencePriceUsd18 !== null &&
    state.controllerPrice * 10n ** BigInt(market.decimals) < state.referencePriceUsd18 * 10n ** 18n

  let chips = ""
  if (cf !== null) chips += `<span class="chip">Collateral factor ${cf.toFixed(0)}%</span>`
  if (state.borrowCap !== null && state.borrowCap > 0n) {
    chips += `<span class="chip">Borrow cap ${esc(fmtAmount(state.borrowCap, market.decimals, market.symbol))}${
      capUsed !== null ? ` · ${capUsed.toFixed(capUsed < 1 ? 1 : 0)}% used` : ""
    }</span>`
  }
  if (isBoostedShare(state.vaultShare)) {
    chips += `<button type="button" class="chip" ${tipAttrs(boostedHint(state.vaultShare, state.vaultPaused))}>Boosted · ${(state.vaultShare * 100).toFixed(0)}% in paired vault</button>`
  }
  chips += `<button type="button" class="chip" ${tipAttrs("Network fees on Robinhood Chain are paid in ETH.")}>Fees in ETH</button>`

  let notices = ""
  if (state.priceable === false) {
    notices += notice(
      "warn",
      `The ${market.symbol} price is unavailable right now. Borrowing waits for a fresh price; supplying, repaying and withdrawing collateral that backs no loan still work.`,
    )
  }
  if (state.mintPaused || state.borrowPaused) {
    notices += notice(
      "warn",
      state.mintPaused && state.borrowPaused
        ? "Supplying and borrowing are paused in this market."
        : state.mintPaused
          ? "Supplying is paused in this market."
          : "Borrowing is paused in this market.",
    )
  }
  if (undervalued) {
    notices += notice(
      "info",
      `${market.symbol} supplied here earns interest but does not raise your borrow limit right now. Borrowing ${market.symbol} is limited to what your other collateral covers.`,
    )
  }

  return `<div class="facts">
    <div class="metric-grid">
      ${metric("Utilization", fmtPct(state.utilizationPct), "Share of the market currently borrowed. High utilization raises rates and leaves less to withdraw or borrow.", (state.utilizationPct ?? 0) > 80 ? "text-amber" : "")}
      ${metric("TVL", fmtUsd(state.tvlUsd), "Everything supplied to this market, including the part working in the paired liquidity vault.")}
      ${metric("Available", fmtUsd(cashUsd), "What the market can pay out or lend right now. Withdrawals above this wait for repayments or for the paired vault to return funds.", "text-emerald-soft")}
      ${metric(`${market.symbol} price`, fmtPrice(state.priceUsd), "Live price from the Robinhood Chain oracle, used to value collateral and loans on this page.", "", state.priceable ? '<span class="live-dot"></span>' : "")}
    </div>
    <div class="chips">${chips}</div>
    ${notices}
  </div>`
}

const notice = (tone, text) => `<div class="notice notice-${tone}">${icon(tone === "warn" ? "alert" : "info")}<span>${esc(text)}</span></div>`

// ---------------------------------------------------------------------------
// Action form
// ---------------------------------------------------------------------------

const VERB = { supply: "Supply", withdraw: "Withdraw", borrow: "Borrow", repay: "Repay" }
const PAST = { supply: "Supplied", withdraw: "Withdrew", borrow: "Borrowed", repay: "Repaid" }
const MAX_LABEL = { supply: "Wallet", withdraw: "Supplied", borrow: "Available", repay: "Borrowed" }
const MAX_ICON = { supply: "wallet", withdraw: "piggyBank", borrow: "handCoins", repay: "receipt" }
const PHASE_COPY = {
  pending: "Waiting",
  switching: "Switching network",
  simulating: "Checking",
  signing: "Confirm in your wallet",
  submitted: "Processing",
  confirmed: "Done",
  failed: "Failed",
  unconfirmed: "Not confirmed yet",
}
const PCTS = [25, 50, 75, 100]

/** One flow's progress: steps, error, status. Shared by the form and the collateral switch. */
class TxState {
  constructor() {
    this.reset()
  }
  reset() {
    this.status = "idle"
    this.steps = []
    this.error = null
  }
  merge(u) {
    const i = this.steps.findIndex((s) => s.id === u.id)
    if (i === -1) this.steps = [...this.steps, u]
    else this.steps = this.steps.map((s, j) => (j === i ? { ...s, ...u } : s))
  }
  get running() {
    return this.status === "running"
  }
}

async function runFlow(tx, kind, input, onChange) {
  if (S.busy) return false
  if (!S.user || !S.wallet) {
    openConnectDialog()
    return false
  }
  S.busy = true
  tx.reset()
  tx.status = "running"
  renderTable()
  try {
    const open = await reconcilePending(S.cfg, S.user)
    if (open.length > 0) throw Object.assign(new Error(), { flowMessage: "An earlier transaction is still waiting for confirmation. Check it before sending another." })
    const ctx = { cfg: S.cfg, provider: S.wallet.provider, user: S.user, markets: S.markets }
    await runAction(ctx, kind, input, (u) => {
      tx.merge(u)
      onChange()
    })
    tx.status = "success"
    return true
  } catch (err) {
    tx.status = "error"
    tx.error = decodeError(err, S.cfg.abis).message
    return false
  } finally {
    S.busy = false
    // Whatever happened is chain state now, an approval included.
    refreshAll()
    renderTable()
  }
}

function stepsHTML(steps) {
  return steps
    .map((s, i) => {
      const cls = s.phase === "confirmed" ? "ok" : s.phase === "failed" ? "bad" : s.phase === "unconfirmed" ? "wait" : ""
      const glyph =
        s.phase === "confirmed"
          ? icon("check", "text-emerald")
          : s.phase === "failed"
            ? icon("x", "text-red")
            : s.phase === "unconfirmed"
              ? `<span style="color:#facc15;display:flex">${icon("clock")}</span>`
              : s.phase === "pending"
                ? '<span class="pending-dot"></span>'
                : `<span style="color:hsl(var(--primary));display:flex">${icon("loader", "spin")}</span>`
      const link = s.hash ? `<a href="${esc(`${S.cfg.explorer}/tx/${s.hash}`)}" target="_blank" rel="noreferrer" aria-label="View on explorer">${icon("external")}</a>` : ""
      return `<li class="step"><span class="n">${i + 1}</span><span class="what">${esc(s.label)}</span><span class="phase ${cls}">${PHASE_COPY[s.phase] ?? s.phase}</span>${link}${glyph}</li>`
    })
    .join("")
}

function runningLabel(steps) {
  const active = [...steps].reverse().find((s) => s.phase !== "confirmed" && s.phase !== "failed")
  if (!active) return "Preparing"
  if (active.phase === "signing") return "Confirm in wallet"
  if (active.phase === "switching") return "Switching network"
  if (active.phase === "submitted") return `${active.label}...`
  return "Checking"
}

function zone(pct) {
  return pct >= 85 ? "high" : pct >= 65 ? "mid" : "safe"
}

class ActionForm {
  constructor(market, action) {
    this.market = market
    this.action = action
    this.amount = ""
    this.useAsCollateral = true
    this.done = null
    this.tx = new TxState()
    this.collTx = new TxState()
    this.mode = null
    this.el = h("div", { class: "tab-pane", role: "tabpanel" })
  }

  get earn() {
    return this.action === "supply" || this.action === "withdraw"
  }

  update() {
    if (this.el.hidden) return
    const mode = !S.user ? "gate" : !S.data || (!S.account && S.accountLoading) || !S.account ? "loading" : "form"
    if (mode !== this.mode) {
      this.mode = mode
      if (mode === "gate") this.buildGate()
      else if (mode === "loading") this.el.innerHTML = `<div class="form-skeleton"><div style="height:56px"></div><div style="height:32px;border-radius:8px"></div><div style="height:48px"></div></div>`
      else this.buildForm()
    }
    if (mode === "form") this.refresh()
  }

  buildGate() {
    this.el.innerHTML = `<div class="gate">
      <div class="gate-icon">${icon("wallet")}</div>
      <div class="gate-copy"><p>Connect an EVM wallet</p><p>Robinhood Chain markets use an EVM wallet. Network fees are paid in ETH.</p></div>
      <button type="button" class="btn-primary">Connect</button>
    </div>`
    $("button", this.el).addEventListener("click", openConnectDialog)
  }

  buildForm() {
    const { symbol, decimals } = this.market
    this.el.innerHTML = `<div class="form">
      <div class="amount">
        <div class="amount-head"><span class="label">Amount</span><span class="bal" data-k="bal"></span></div>
        <div class="amount-field" data-k="field">
          <input type="text" inputmode="decimal" autocomplete="off" placeholder="0.00" aria-label="Amount in ${esc(symbol)}">
          <button type="button" class="max-btn" data-k="max">${icon(MAX_ICON[this.action])}<span></span></button>
        </div>
        <div class="pct-chips">${PCTS.map((p) => `<button type="button" class="pct" data-pct="${p}">${p === 100 ? "MAX" : `${p}%`}</button>`).join("")}</div>
        <div class="slider" data-k="slider"><input type="range" min="0" max="100" step="1" value="0" aria-label="Amount as a share of ${MAX_LABEL[this.action].toLowerCase()}"></div>
        <p class="base-units" data-k="units"></p>
      </div>
      <div class="rows" data-k="rows"></div>
      <div class="health" data-k="health" hidden>
        <div class="health-head"><span>Capacity used</span><span class="health-value" data-k="hval"></span></div>
        <div class="health-bar">
          <div class="health-track">
            <div class="health-current" data-k="hcur"></div>
            <div class="health-delta" data-k="hdelta" hidden></div>
            <div class="health-tick" style="left:65%"></div><div class="health-tick" style="left:85%"></div>
          </div>
          <div class="health-marker" data-k="hmark" hidden></div>
        </div>
        <div class="health-warn" data-k="hwarn"></div>
      </div>
      ${this.action === "supply" ? `<label class="toggle-card" data-k="coll-new" hidden>
          <span class="toggle-text"><b>Use as collateral</b><span>Lets this supply back a loan. One extra signature.</span></span>
          <button type="button" class="switch" role="switch" aria-checked="true" data-k="coll-new-switch"></button>
        </label>` : ""}
      <p class="hint" data-k="reason"></p>
      <button type="button" class="submit ${this.earn ? "submit-earn" : "submit-cost"}" data-k="submit"></button>
      <p class="hint hint-warn" data-k="blocker"></p>
      <ol class="steps" data-k="steps"></ol>
      <div data-k="error"></div>
      <div data-k="done"></div>
      ${this.action === "supply" ? `<div class="collateral-block" data-k="coll" hidden>
          <div class="toggle-card">
            <span class="toggle-text"><b>Collateral</b><span data-k="coll-copy"></span></span>
            <span class="toggle-side"><span data-k="coll-spin"></span><button type="button" class="switch" role="switch" data-k="coll-switch" aria-label="Use ${esc(symbol)} as collateral"></button></span>
          </div>
          <p class="hint" data-k="coll-hint"></p>
          <ol class="steps" data-k="coll-steps"></ol>
          <div data-k="coll-error"></div>
        </div>` : ""}
    </div>`

    const input = $("input[type=text]", this.el)
    input.value = this.amount
    input.addEventListener("input", () => {
      // Accept a comma as decimal separator; keep only digits and one dot.
      const cleaned = input.value.replace(",", ".").replace(/[^\d.]/g, "").replace(/(\..*)\./g, "$1")
      if (cleaned !== input.value) input.value = cleaned
      this.setAmount(cleaned, false)
    })
    $('[data-k="max"]', this.el).addEventListener("click", () => this.fillPct(100))
    for (const b of this.el.querySelectorAll(".pct")) b.addEventListener("click", () => this.fillPct(Number(b.dataset.pct)))
    $('[data-k="slider"] input', this.el).addEventListener("input", (e) => this.fillPct(Number(e.target.value)))
    $('[data-k="submit"]', this.el).addEventListener("click", () => this.submit())

    const newSwitch = this.el.querySelector('[data-k="coll-new-switch"]')
    newSwitch?.closest("label").addEventListener("click", (e) => {
      e.preventDefault()
      if (this.tx.running) return
      this.useAsCollateral = !this.useAsCollateral
      this.refresh()
    })
    this.el.querySelector('[data-k="coll-switch"]')?.addEventListener("click", () => this.toggleCollateral())
  }

  k(name) {
    return this.el.querySelector(`[data-k="${name}"]`)
  }

  limit() {
    return lendingLimit(this.action, this.market.id, S.data, S.account)
  }

  setAmount(value, writeInput = true) {
    this.amount = value
    if (writeInput) $("input[type=text]", this.el).value = value
    if (this.done) this.done = null
    if (this.tx.status === "error") this.tx.reset()
    this.refresh()
  }

  /** Chips and slider: MAX is the exact limit; the others are rounded down to 8 places. */
  fillPct(pct) {
    const { max } = this.limit()
    if (max <= 0n) return
    const raw = pct >= 100 ? max : (max * BigInt(pct)) / 100n
    this.setAmount(pct >= 100 ? toInputString(raw, this.market.decimals) : toInputString(raw, this.market.decimals, Math.min(this.market.decimals, 8)))
  }

  refresh() {
    if (this.mode !== "form") return
    const { market, action } = this
    const { symbol, decimals } = market
    const state = S.data.markets.find((m) => m.market.id === market.id)
    const pos = S.account.positions.find((p) => p.market.id === market.id)
    const limit = this.limit()
    const summary = summarizeLending(S.data, S.account)
    const gas = gasStatus(S.account)
    const raw = parseAmount(this.amount, decimals)
    const running = this.tx.running
    const locked = running || S.busy

    // "Everything" uses redeem(all shares) / repayBorrow(max), so no dust stays behind.
    const all =
      limit.max > 0n &&
      raw >= limit.max &&
      ((action === "withdraw" && limit.boundBy === "supplied") || (action === "repay" && limit.boundBy === "debt"))
    const over = raw > limit.max && !all
    this.all = all
    this.raw = raw

    // Amount field
    setHTML(this.k("bal"), `${MAX_LABEL[action]}: <b>${esc(fmtBalance(limit.max, decimals))} ${esc(symbol)}</b>`)
    const maxBtn = this.k("max")
    maxBtn.querySelector("span").textContent = fmtBalance(limit.max, decimals)
    maxBtn.title = `${MAX_LABEL[action]}: ${fmtBalance(limit.max, decimals)} ${symbol}`
    maxBtn.disabled = locked || limit.max === 0n
    this.k("field").classList.toggle("disabled", running || limit.blocked)
    for (const b of this.el.querySelectorAll(".pct")) {
      const pct = Number(b.dataset.pct)
      b.disabled = locked || limit.max === 0n
      const target = pct >= 100 ? limit.max : (limit.max * BigInt(pct)) / 100n
      const targetStr = pct >= 100 ? toInputString(target, decimals) : toInputString(target, decimals, Math.min(decimals, 8))
      b.classList.toggle("active", limit.max > 0n && this.amount !== "" && this.amount === targetStr)
    }
    const slider = this.k("slider")
    slider.hidden = limit.max === 0n
    const range = slider.querySelector("input")
    const pctOfMax = limit.max > 0n && raw > 0n ? Math.min(100, Number((raw * 10_000n) / limit.max) / 100) : 0
    range.value = String(Math.round(pctOfMax))
    range.style.setProperty("--pct", `${pctOfMax}%`)
    range.disabled = locked
    this.k("units").textContent = raw > 0n ? `Sends ${raw.toString()} base units (${decimals} decimals)` : ""
    this.k("units").hidden = raw === 0n

    // Info rows
    const apy = this.earn ? state.supplyApy : state.borrowApy
    const priceUsd = state.priceUsd ?? 0
    const valueUsd = priceUsd > 0 && raw > 0n ? amountToNumber(raw, decimals) * priceUsd : null
    const row = (label, value, cls = "") => `<div class="row"><span>${esc(label)}</span><span class="${cls}">${esc(value)}</span></div>`
    let rowsHtml = row(this.earn ? "Supply APY" : "Borrow APY", fmtPct(apy), this.earn ? "text-emerald" : "text-amber")
    if (valueUsd !== null) rowsHtml += row("Value", fmtUsd(valueUsd))
    if (action === "supply") rowsHtml += row("In wallet", fmtAmount(pos?.walletBalance, decimals, symbol))
    if (this.earn) rowsHtml += row("Supplied", fmtAmount(pos?.supplied, decimals, symbol))
    if (!this.earn) rowsHtml += row("Borrowed", fmtAmount(pos?.borrowed, decimals, symbol))
    if (action === "repay") rowsHtml += row("In wallet", fmtAmount(pos?.walletBalance, decimals, symbol))
    if (action === "borrow") {
      rowsHtml += row(
        "Borrow limit left",
        summary.borrowLimitUsd18 !== null && summary.borrowedUsd18 !== null
          ? fmtUsd(usd18ToNumber(summary.borrowLimitUsd18 > summary.borrowedUsd18 ? summary.borrowLimitUsd18 - summary.borrowedUsd18 : 0n))
          : "--",
      )
    }
    setHTML(this.k("rows"), rowsHtml)

    // Capacity bar
    const hasDebt = (summary.borrowedUsd18 ?? 0n) > 0n
    const showCapacity = summary.borrowLimitUsd18 !== null && (action === "borrow" || hasDebt) && (summary.borrowLimitUsd18 > 0n || hasDebt)
    this.k("health").hidden = !showCapacity
    if (showCapacity) this.renderHealth(summary.limitUsedPct ?? 0, projectedLimitUsedPct(action, market.id, raw, S.data, S.account))

    // New-supply collateral switch
    const collNew = this.k("coll-new")
    if (collNew) {
      collNew.hidden = !(pos && !pos.isCollateral)
      const sw = this.k("coll-new-switch")
      sw.setAttribute("aria-checked", String(this.useAsCollateral))
      sw.disabled = running
    }

    // Reason, button, blocker
    const reason = this.k("reason")
    reason.textContent = limit.reason && !limit.blocked && !over ? limit.reason : limit.blocked && raw === 0n && limit.reason ? limit.reason : ""
    reason.hidden = !reason.textContent
    const blocker =
      (limit.blocked && limit.reason) ||
      (gas.blocks && gas.message) ||
      (over ? `The most you can ${VERB[action].toLowerCase()} right now is ${fmtAmount(limit.max, decimals, symbol)}.` : null)
    const submit = this.k("submit")
    submit.disabled = locked || raw <= 0n || !!blocker
    setHTML(
      submit,
      running
        ? `<span class="inner">${icon("loader", "spin")}${esc(runningLabel(this.tx.steps))}</span><span class="btn-progress"></span>`
        : esc(`${VERB[action]} ${all ? `all ${symbol}` : symbol}`),
    )
    const blockerEl = this.k("blocker")
    blockerEl.textContent = blocker && raw > 0n && !running ? blocker : ""
    blockerEl.hidden = !blockerEl.textContent

    // Flow state
    setHTML(this.k("steps"), stepsHTML(this.tx.steps))
    setHTML(this.k("error"), this.tx.status === "error" && this.tx.error ? `<div class="flow-error" role="alert">${esc(this.tx.error)}</div>` : "")
    setHTML(this.k("done"), this.done && this.tx.status === "success" ? `<div class="done">${icon("checkCircle")}${esc(this.done)}</div>` : "")

    // Collateral control, once something is supplied
    const coll = this.k("coll")
    if (coll) {
      const show = !!pos && (pos.supplied ?? 0n) > 0n
      coll.hidden = !show
      if (show) {
        const on = !!pos.isCollateral
        const debtHere = (pos.borrowed ?? 0n) > 0n
        this.k("coll-copy").textContent = on
          ? `Your ${symbol} supply backs loans in both markets.`
          : `Your ${symbol} supply earns interest but backs no loan.`
        const sw = this.k("coll-switch")
        sw.setAttribute("aria-checked", String(on))
        sw.disabled = this.collTx.running || S.busy || (on && debtHere)
        setHTML(this.k("coll-spin"), this.collTx.running ? `<span class="text-muted" style="display:flex">${icon("loader", "spin")}</span>` : "")
        const hint = this.k("coll-hint")
        hint.textContent = on && debtHere ? `Repay the ${symbol} debt before turning this off.` : ""
        hint.hidden = !hint.textContent
        const showSteps = this.collTx.status === "running" || this.collTx.status === "error"
        setHTML(this.k("coll-steps"), showSteps ? stepsHTML(this.collTx.steps) : "")
        setHTML(this.k("coll-error"), this.collTx.status === "error" && this.collTx.error ? `<div class="flow-error" role="alert">${esc(this.collTx.error)}</div>` : "")
      }
    }
  }

  renderHealth(currentPct, hypoPct) {
    const current = Math.min(Math.max(currentPct ?? 0, 0), 100)
    const hypo = hypoPct !== null && hypoPct !== undefined ? Math.min(Math.max(hypoPct, 0), 100) : null
    const showDelta = hypo !== null && hypo - current > 0.5
    const display = hypo ?? current
    const z = zone(display)
    const cls = z === "high" ? "text-red" : z === "mid" ? "text-amber" : "text-emerald"
    const label = z === "high" ? "High risk" : z === "mid" ? "Moderate risk" : "Safe"

    setHTML(
      this.k("hval"),
      showDelta
        ? `${current.toFixed(1)}% <span style="opacity:.4">→</span> <span class="${cls}">${hypo.toFixed(1)}%</span> · <span class="${cls}">${label}</span>`
        : `${current.toFixed(1)}% · <span class="${cls}">${label}</span>`,
    )
    this.k("hval").className = `health-value ${cls}`
    const cur = this.k("hcur")
    cur.style.width = `${current}%`
    cur.className = `health-current zone-${zone(current)}`
    cur.style.opacity = showDelta ? "0.6" : "0.9"
    const delta = this.k("hdelta")
    delta.hidden = !showDelta
    const mark = this.k("hmark")
    mark.hidden = !showDelta
    if (showDelta) {
      delta.style.left = `${current}%`
      delta.style.width = `${hypo - current}%`
      delta.className = `health-delta zone-${zone(hypo)}-light`
      mark.style.left = `calc(${hypo}% - 1px)`
    }
    const warn = this.k("hwarn")
    warn.textContent = display >= 85 ? "Liquidation risk, reduce exposure" : display >= 65 ? "Approaching liquidation threshold" : ""
    warn.className = `health-warn ${display >= 85 ? "text-red" : "text-amber"}`
    warn.hidden = !warn.textContent
  }

  async submit() {
    this.done = null
    const raw = this.raw
    const all = this.all
    const ok = await runFlow(
      this.tx,
      this.action,
      { market: this.market, amount: raw, all, enableCollateral: this.action === "supply" ? this.useAsCollateral : undefined },
      () => this.refresh(),
    )
    if (ok) {
      this.done = `${PAST[this.action]} ${all ? "everything" : fmtAmount(raw, this.market.decimals, this.market.symbol)}.`
      this.amount = ""
      const input = $("input[type=text]", this.el)
      if (input) input.value = ""
    }
    this.refresh()
  }

  async toggleCollateral() {
    const pos = S.account?.positions.find((p) => p.market.id === this.market.id)
    if (!pos) return
    await runFlow(this.collTx, pos.isCollateral ? "collateral-off" : "collateral-on", { market: this.market }, () => this.refresh())
    this.refresh()
  }
}

boot()
