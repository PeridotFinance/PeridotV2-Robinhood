#!/usr/bin/env python3
"""Read-only, pinned-block comparison with the archived Robinhood deployment."""
from concurrent.futures import ThreadPoolExecutor
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
DEPLOYMENTS = ROOT / "contracts/robinhood-vaults/deployments"
URL = "https://rpc.mainnet.chain.robinhood.com"
IMPLEMENTATION_SLOT = "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"


def rpc(method, params):
    if method not in {"eth_chainId", "eth_getBlockByNumber", "eth_getCode", "eth_getStorageAt", "eth_call"}:
        raise ValueError("Read-only method allowlist")
    for attempt in range(4):
        try:
            body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
            req = urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json", "User-Agent": "curl/8.0"})
            with urllib.request.urlopen(req, timeout=30) as response:
                data = json.load(response)
            if "error" in data:
                raise RuntimeError(str(data["error"]))
            return data["result"]
        except Exception:
            if attempt == 3:
                raise
            time.sleep(attempt + 1)


def cast(*args):
    return subprocess.check_output(["cast", *args], text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "snapshot/mainnet-check.json")
    args = parser.parse_args()
    if int(rpc("eth_chainId", []), 16) != 4663:
        raise RuntimeError("Wrong chain")
    tip = rpc("eth_getBlockByNumber", ["latest", False])
    block = rpc("eth_getBlockByNumber", [hex(int(tip["number"], 16) - 2), False])
    tag = block["number"]
    historical = json.loads((DEPLOYMENTS / "margin-mainnet-live/active-verification.json").read_text())
    upgrade = json.loads((DEPLOYMENTS / "robinhood-mainnet.oracle-priced-loss-upgrade.json").read_text())
    manifest = json.loads((ROOT / "contracts/robinhood-vaults/frontend/margin-mainnet/manifest.json").read_text())
    targets = {key: {"address": value["address"], "expected": value["runtimeCodeHash"]}
               for key, value in historical["contracts"].items()}
    for key, value in upgrade["replacementImplementations"].items():
        targets["vault_" + key] = {"address": value["implementation"], "expected": value["runtimeCodeHash"]}
    targets["SettlementLib"] = {"address": upgrade["library"]["address"], "expected": upgrade["library"]["runtimeCodeHash"]}

    def check_code(item):
        key, target = item
        code = rpc("eth_getCode", [target["address"], tag])
        actual = cast("keccak", code)
        if code == "0x" or actual.lower() != target["expected"].lower():
            raise RuntimeError("On-chain code changed: " + key)
        return key, {"address": target["address"], "runtimeCodeHash": actual, "matchesHistorical": True}

    with ThreadPoolExecutor(max_workers=3) as pool:
        codes = dict(pool.map(check_code, targets.items()))
    print(f"{len(codes)} runtime hashes match at block {int(tag, 16)}", flush=True)
    proxies = {}
    for role in ("config", "executor", "liquidator", "riskEngine", "marginVault", "insuranceFund", "feeDistributor"):
        proxy = historical["contracts"][role]["address"]
        expected = historical["contracts"][role + "Implementation"]["address"]
        proxies[role] = (proxy, expected)
    for role, value in upgrade["replacementImplementations"].items():
        proxies["vault_" + role] = (value["proxy"], value["implementation"])
    original = json.loads((DEPLOYMENTS / "robinhood-mainnet.vault-system.json").read_text())
    reserve = original["components"]["reserve"]
    proxies["strategyReserve"] = (reserve["proxy"], reserve["implementation"])
    slots = {}
    for role, (proxy, expected) in proxies.items():
        actual = "0x" + rpc("eth_getStorageAt", [proxy, IMPLEMENTATION_SLOT, tag])[-40:]
        if actual.lower() != expected.lower():
            raise RuntimeError("Implementation changed: " + role)
        slots[role] = {"proxy": proxy, "implementation": actual, "matchesHistorical": True}
    def call(address, signature, *args):
        return rpc("eth_call", [{"to": address, "data": cast("calldata", signature, *args)}, tag])
    markets = {}
    for role in ("pUsd", "pStock"):
        target = manifest["existingAddresses"][role]
        actual = "0x" + call(target, "implementation()")[-40:]
        expected = historical["contracts"]["replacementDelegate"]["address"]
        if actual.lower() != expected.lower():
            raise RuntimeError("Market delegate changed: " + role)
        markets[role] = {"address": target, "implementation": actual}
    expected_risk = json.loads((DEPLOYMENTS / "margin-mainnet-5x-live/status.json").read_text())
    risks = {}
    for direction, pair in manifest["pairs"].items():
        encoded = call(manifest["marginAddresses"]["config"], "getPairRisk(address,address,address)",
                       pair["marginPToken"], pair["positionPToken"], pair["debtPToken"])[2:]
        risk = [int(encoded[i:i+64], 16) for i in range(0, len(encoded), 64)]
        if risk != expected_risk["directions"][direction]["risk"]:
            raise RuntimeError("Directional risk changed: " + direction)
        risks[direction] = risk
    canonical = rpc("eth_getBlockByNumber", [tag, False])
    if canonical["hash"] != block["hash"]:
        raise RuntimeError("Verification block is no longer canonical")
    result = {"status": "MATCHES_RECORDED_MAINNET_DEPLOYMENT", "checkedAtUtc": datetime.now(timezone.utc).isoformat(),
              "chainId": 4663, "block": int(tag, 16), "blockHash": block["hash"],
              "runtimeChecks": codes, "proxyImplementationChecks": slots, "markets": markets, "risk": risks,
              "scope": "Read-only pinned-block code, implementation targets and risk. Does not attest to oracle availability, keeper health, current balances, governance safety or frontend operation."}
    target = args.output
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, indent=2) + "\n")
    target.with_suffix(".sha256").write_text(hashlib.sha256(target.read_bytes()).hexdigest() + "  " + target.name + "\n")
    print(f"Verified {len(slots)} proxy targets, two market delegates and both 5x risk tuples.")


if __name__ == "__main__":
    main()
