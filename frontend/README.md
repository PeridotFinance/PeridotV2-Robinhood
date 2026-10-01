# Frontend: Robinhood Chain lending

A single static page for the USDG and NVDA lending markets on Robinhood Chain (4663). It looks and behaves like the Robinhood Chain markets in the Expert view of the Peridot app (`/app`): account strip, market table, and per market an expanded panel with the market facts and Supply / Withdraw / Borrow / Repay tabs. No header, no footer, no build step.

## Run locally

The page reads the pinned manifest and ABIs from `contracts/robinhood-vaults/frontend/margin-mainnet/` at runtime, so serve the **repository root**, not this folder:

```sh
make frontend
# same as: python3 -m http.server 5173 --bind 127.0.0.1
# then open http://127.0.0.1:5173/frontend/
```

Any static file server works. Opening `index.html` from disk does not: ES modules and `fetch` need http.

Nothing to install. There is no package manager, lockfile or `.env`: the one runtime dependency is vendored, and the only setting is optional (below).

## Configuration

| What | How |
| --- | --- |
| RPC | Public RPC from `manifest.json` by default. Put a paid endpoint first with `?rpc=https://…` in the URL, or `localStorage.setItem("peridot.rh.rpc", "https://…")`. The public one stays as fallback. |
| Addresses, decimals, explorer | `manifest.json` in the handout folder. Nothing is re-typed here. |

## Deploy

Upload the repository root (or at least `frontend/` and `contracts/robinhood-vaults/frontend/margin-mainnet/` with their relative paths intact) to any static host and point it at `/frontend/`. If the host sets a CSP, `connect-src` needs the RPC (`https://rpc.mainnet.chain.robinhood.com` plus any override) and `style-src`/`font-src` need Google Fonts.

## Files

| Path | Purpose |
| --- | --- |
| `index.html` | Page shell: toolbar, account strip, market table |
| `styles.css` | The app's dark design tokens, spacing and animations, without Tailwind |
| `src/config.js` | Loads the manifest and ABIs, builds the chain and the multicall read client |
| `src/lending.js` | Reads, math and per-action limits (port of the app's `lib/robinhood/lending.ts`) |
| `src/flows.js` | Supply, withdraw, borrow, repay, collateral switch, error decoding (port of `lib/robinhood/lending-flows.ts`) |
| `src/wallet.js` | EIP-6963 wallet discovery, chain switch / add for 4663 |
| `src/app.js`, `src/ui.js` | Rendering, formatting, icons |
| `vendor/viem-2.33.2.min.js` | viem 2.33.2 as one tree-shaken ES module (see below) |
| `assets/` | Peridot mark, USDG, NVDA and Robinhood Chain logos |

## How it behaves

- **Reads are live on-chain**, batched through Multicall3, markets every 20 s and the account every 15 s while the tab is visible. A read that fails shows `--`, never `$0` or `0 available`.
- **Two prices.** Values use the margin oracle (USD per whole token). Borrow and withdraw limits are the controller's own, less 0.5% headroom and bounded by cash and borrow cap, because that is what the contract will accept.
- **APY** uses the interest-rate model's `blocksPerYear()`: `block.number` here is the L1 block (about 12 s), not the L2 block.
- **Every transaction is simulated first.** Compound-style calls return error codes without reverting, so a nonzero code blocks the signature, and a confirmed receipt must carry the market's own event (Mint / Redeem / Borrow / RepayBorrow) to count.
- **"Max" withdraw** redeems every share; **"max" repay** uses `repayBorrow(uint256.max)` with a 0.1% allowance buffer, so no dust is left behind.
- **A sent but unconfirmed hash** is remembered in `localStorage` and checked before anything new is sent.
- Network fees are ETH. A wallet without enough ETH sees that before it tries to sign.
- The Peridot mark top left links to peridot.finance, behind a "Leave this page?" confirmation (a cmd or ctrl click opens a new tab directly).

The page uses the connected wallet for every signature. It holds no key, talks to no backend and stores nothing but the last wallet choice and pending hashes in the browser.

## Checked

Against Robinhood Chain mainnet on 2026-10-01, with a read-only test wallet that rejects every signature: market values match the app's Expert view; supply (approve), withdraw-all (`redeem`) and the collateral switch (`enterMarkets`) pass their on-chain simulation and reach the wallet prompt; a rejected prompt shows "The request was declined in the wallet."; borrow without collateral is blocked with the reason. No real transaction has been sent from this page yet.

## Rebuilding the viem bundle

```sh
mkdir viembuild && cd viembuild
npm init -y && npm i viem@2.33.2 esbuild@0.24.0
cat > entry.js <<'EOF'
export {
  createPublicClient, createWalletClient, custom, http, fallback, defineChain,
  parseAbi, formatUnits, parseUnits, decodeErrorResult, parseEventLogs,
  maxUint256, encodeFunctionData, getAddress, BaseError,
} from "viem"
EOF
npx esbuild entry.js --bundle --format=esm --minify --target=es2020 --outfile=viem-2.33.2.min.js
```

SHA-256 of the committed file: `78cc10357f5aeee0dfd451ce63aa8e5533dc28c35816dd057a80927e71f3cb19`.

## Not covered

Margin (deposit, open, manage, close, in-kind exit) is not part of this page; it is the lending view only. The margin flow is specified in the [implementation guide](../contracts/robinhood-vaults/frontend/margin-mainnet/FRONTEND_IMPLEMENTATION_GUIDE.md).
