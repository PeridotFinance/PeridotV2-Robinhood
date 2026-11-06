"""READ-ONLY provenance evidence for pToken holders and margin owners: on-chain logs and transactions only, up to the balance-sheet pin.
For each address: market Transfer history, first/last activity, sender of the tx that touched it, USDG/NVDA token transfers in and out with counterparties.
Evidence, not identification. Output: holder-evidence.json."""
import json
from bs_lib import *
from bs_snapshot import scan, T_TRANSFER, PN, PU, NVDA, USDG, GOV, AD, KEEPER

S = json.load(open(HERE / "snapshot.json"))
P = S["pin"]["l2"]
KNOWN = {GOV: "deployer/governor (CLAUDE.md, deployments)", AD["marginVault"]: "margin vault (deployments/margin-mainnet-live/addresses.json)", AD["insuranceFund"]: "insurance fund (addresses.json)",
         KEEPER: "LP keeper (earlier evidence actors.json)", AD["flashVault"]: "flash vault (addresses.json)", AD["accountFactory"]: "margin account factory", AD["executor"]: "margin executor", AD["liquidator"]: "margin liquidator",
         AD["swapModule"]: "margin swap module", AD["feeDistributor"]: "fee distributor", "0x16aec17597e5224998e2043c9c83c4a35dd95a86": "dedicated margin keeper wallet (CLAUDE.md)", "0x5f2999abfa24ae7a2b62e33bc5372700b8d68c19": "testnet wallet (CLAUDE.md)",
         "0x254b539ecb1b6d2d00e955d243f19f6f15d88ae4": "retired testnet wallet (CLAUDE.md)", "0x8366a39cc670b4001a1121b8f6a443a643e40951": "Uniswap v4 PoolManager", PN: "pNVDA", PU: "pUSDG"}
TARGETS = ["0xdc48fd39733af117a4ffda35d7b325de9bdedfde", "0x12c1e2c33f63f897e0e4ac1969c29493136f2481", "0x5085037684db1d4a59c6e8a3d22adddefc7a05bc"]


def label(a):
    return KNOWN.get(a, "unlabelled")


def tok_transfers(tok, who):
    t = "0x" + pa(who)
    a = scan({"address": tok, "topics": [T_TRANSFER, t]}, 50_000_000, P)
    b = scan({"address": tok, "topics": [T_TRANSFER, None, t]}, 50_000_000, P)
    rows = []
    for l, d in [(x, "out") for x in a] + [(x, "in") for x in b]:
        cp = addr(int(l["topics"][2 if d == "out" else 1], 16))
        rows.append({"block": int(l["blockNumber"], 16), "tx": l["transactionHash"], "dir": d, "counterparty": cp, "counterpartyLabel": label(cp), "value": int(l["data"], 16)})
    rows.sort(key=lambda r: r["block"])
    return rows


out = {"pin": P, "targets": {}}
txcache = {}


def tx_from(h):
    if h not in txcache:
        t = call("eth_getTransactionByHash", [h])
        txcache[h] = {"from": t["from"], "to": t["to"], "fromLabel": label(t["from"])}
    return txcache[h]


for who in TARGETS:
    RR = S["reads"]
    d = {"code_bytes": (len(RR[f"code.{who}"]) - 2) // 2, "nonce_at_pin": int(RR[f"nonce.{who}"], 16), "ethWei_at_pin": int(RR[f"eth.{who}"], 16)}
    for tn, t in (("USDG", USDG), ("NVDA", NVDA), ("pUSDG", PU), ("pNVDA", PN)):
        d[tn] = tok_transfers(t, who)
    txs = sorted({r["tx"] for tn in ("USDG", "NVDA", "pUSDG", "pNVDA") for r in d[tn]})
    d["txSenders"] = {h: tx_from(h) for h in txs[:40]}
    d["distinctTxSenders"] = sorted({v["from"] for v in d["txSenders"].values()})
    d["distinctTokenCounterparties"] = sorted({r["counterparty"] for tn in ("USDG", "NVDA") for r in d[tn]})
    # was this address ever funded directly by the governor or keeper?
    d["directTokenLinkToKnown"] = sorted({(r["counterparty"], r["counterpartyLabel"]) for tn in ("USDG", "NVDA", "pUSDG", "pNVDA") for r in d[tn] if r["counterparty"] in KNOWN})
    out["targets"][who] = d
json.dump(out, open(HERE / "holder-evidence.json", "w"), indent=1)
for w, d in out["targets"].items():
    print(w, {k: d[k] for k in ("code_bytes", "nonce_at_pin", "ethWei_at_pin", "distinctTxSenders", "distinctTokenCounterparties", "directTokenLinkToKnown")})
    for tn in ("USDG", "NVDA", "pUSDG", "pNVDA"):
        for r in d[tn]:
            print("  ", tn, r["block"], r["dir"], r["value"], r["counterparty"], r["counterpartyLabel"], r["tx"][:14])
