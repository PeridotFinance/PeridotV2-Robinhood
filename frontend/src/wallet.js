/**
 * Browser wallets via EIP-6963 (every injected wallet announces itself), with
 * the legacy `window.ethereum` as a fallback. The connected wallet signs every
 * transaction; the page never holds a key.
 */

const providers = new Map()
const listeners = new Set()

function announce(detail) {
  if (!detail?.info?.uuid || !detail.provider) return
  providers.set(detail.info.uuid, detail)
  listeners.forEach((fn) => fn(listProviders()))
}

window.addEventListener("eip6963:announceProvider", (e) => announce(e.detail))
window.dispatchEvent(new Event("eip6963:requestProvider"))

export function listProviders() {
  const list = [...providers.values()]
  if (list.length === 0 && window.ethereum) {
    list.push({ info: { uuid: "injected", name: "Browser wallet", icon: null, rdns: "injected" }, provider: window.ethereum })
  }
  return list
}

export function onProvidersChanged(fn) {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

export function findProvider(rdns) {
  return listProviders().find((p) => p.info.rdns === rdns) ?? null
}

/** Ask for accounts. Returns the first address. */
export async function requestAccount(provider) {
  const accounts = await provider.request({ method: "eth_requestAccounts" })
  if (!accounts?.length) throw new Error("The wallet returned no account.")
  return accounts[0]
}

/** Accounts the wallet already granted, without a prompt. */
export async function silentAccount(provider) {
  try {
    const accounts = await provider.request({ method: "eth_accounts" })
    return accounts?.[0] ?? null
  } catch {
    return null
  }
}

/** Switch the wallet to 4663, adding the chain first when the wallet does not know it. */
export async function ensureChain(provider, chain) {
  const hex = `0x${chain.id.toString(16)}`
  const current = await provider.request({ method: "eth_chainId" })
  if (typeof current === "string" && parseInt(current, 16) === chain.id) return
  try {
    await provider.request({ method: "wallet_switchEthereumChain", params: [{ chainId: hex }] })
  } catch (err) {
    const code = err?.code ?? err?.data?.originalError?.code
    if (code !== 4902 && !/unrecognized|not added|unknown chain/i.test(String(err?.message))) throw err
    await provider.request({
      method: "wallet_addEthereumChain",
      params: [
        {
          chainId: hex,
          chainName: chain.name,
          nativeCurrency: chain.nativeCurrency,
          // The public RPC only: a wallet keeps what it is given here.
          rpcUrls: [chain.rpcUrls.default.http.at(-1)],
          blockExplorerUrls: [chain.blockExplorers.default.url],
        },
      ],
    })
  }
  const after = await provider.request({ method: "eth_chainId" })
  if (parseInt(after, 16) !== chain.id) throw Object.assign(new Error("Switch the wallet to Robinhood Chain to continue."), { code: 4001 })
}
