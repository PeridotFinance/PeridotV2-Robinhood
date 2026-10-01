/**
 * Everything the page knows about the deployment comes from the pinned
 * handout next to the contracts: addresses, decimals, RPC and explorer from
 * manifest.json, call shapes from the ABI files beside it. Nothing here
 * re-types an address, so a new handout is a file swap.
 */
import { createPublicClient, defineChain, fallback, http } from "../vendor/viem-2.33.2.min.js"

const HANDOUT = new URL("../../contracts/robinhood-vaults/frontend/margin-mainnet/", import.meta.url)

async function loadJson(name) {
  const res = await fetch(new URL(name, HANDOUT))
  if (!res.ok) throw new Error(`Could not load ${name} (${res.status}). Serve the repository root, see frontend/README.md.`)
  const json = await res.json()
  return Array.isArray(json) ? json : json.abi ?? json
}

/** Canonical Multicall3, deployed on 4663. */
const MULTICALL3 = "0xcA11bde05977b3631167028862bE2a173976CA11"

/** Optional override, e.g. a paid endpoint: ?rpc=https://... or localStorage "peridot.rh.rpc". */
function rpcOverride() {
  try {
    const fromQuery = new URLSearchParams(location.search).get("rpc")
    if (fromQuery) return fromQuery
    return localStorage.getItem("peridot.rh.rpc")
  } catch {
    return null
  }
}

export async function loadConfig() {
  const [manifest, pToken, erc20, oracle] = await Promise.all([
    loadJson("manifest.json"),
    loadJson("RobinhoodBoostedDelegate.abi.json"),
    loadJson("IERC20.abi.json"),
    loadJson("RobinhoodMarginPriceOracle.abi.json"),
  ])
  if (manifest.chainId !== 4663) throw new Error(`Manifest chainId ${manifest.chainId} is not Robinhood Chain (4663).`)

  const override = rpcOverride()
  const rpcUrls = override && override !== manifest.rpcURL ? [override, manifest.rpcURL] : [manifest.rpcURL]

  const chain = defineChain({
    id: manifest.chainId,
    name: "Robinhood Chain",
    nativeCurrency: { name: "Ether", symbol: manifest.gasSymbol, decimals: 18 },
    rpcUrls: { default: { http: rpcUrls } },
    blockExplorers: { default: { name: "Robinhood Blockscout", url: manifest.explorerURL } },
    contracts: { multicall3: { address: MULTICALL3 } },
  })

  const transports = rpcUrls.map((url) => http(url, { batch: { batchSize: 50, wait: 16 }, timeout: 15_000, retryCount: 1 }))
  const client = createPublicClient({
    chain,
    transport: transports.length === 1 ? transports[0] : fallback(transports, { rank: false }),
    batch: { multicall: { batchSize: 2_048, wait: 16 } },
  })

  const a = manifest.existingAddresses
  return {
    manifest,
    chain,
    client,
    rpcUrls,
    explorer: manifest.explorerURL.replace(/\/$/, ""),
    abis: { pToken, erc20, oracle },
    tokens: { USDG: a.usd, NVDA: a.stock, pUSDG: a.pUsd, pNVDA: a.pStock, controller: a.controller },
    marginOracle: manifest.marginAddresses.oracle,
    decimals: { USDG: manifest.decimals.usd, NVDA: manifest.decimals.stock, pToken: manifest.decimals.pToken },
  }
}
