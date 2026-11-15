"""Pinned, read-only input collection for handoff 08 Phase 1.

This first checkpoint collects evidence; it deliberately does not certify profitability.
It never substitutes current state for missing history, invents fee attribution, or
annualizes an incomplete window. See strategy_evidence.md for remaining analysis gates.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

CHAIN_ID = 4663
ROLLOUT_BLOCK = 84_107_567
SOURCE_COMMIT = "a41096221409403cef84d2c7c4cf62195bcca81d"
VAULT = "0x280825b2d856706ff7e0d6351ccb2e935e1a9a2f"
ADAPTER = "0xada73211711e4790bc83b5d6b39f47fe04d276f3"
GUARD = "0xae4d4ddb8dd646951d54fe9b13be23dcb61c6741"
MANAGER = "0x8366a39cc670b4001a1121b8f6a443a643e40951"
STATE_VIEW = "0xf3334192d15450cdd385c8b70e03f9a6bd9e673b"
RAMSES = "0xdac1904d823f7d6bcaaF431ceee40c934fed321b".lower()
NVDA = "0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec"
USDG = "0x5fc5360d0400a0fd4f2af552add042d716f1d168"
POOL_ID = "0x3bb34a44f1b2b5f32c034c38a53065a521a47b199700fa9bd19d60985ff24bf1"
MARKETS = {
    "pNVDA": ("0xa155cccb986774ae818b3f10f07d01d1b7a47b26", NVDA, 18),
    "pUSDG": ("0x55aed0569c8f0d166d71face57b57c2f2624a563", USDG, 6),
}
READ_METHODS = frozenset({
    "eth_chainId", "eth_getBlockByNumber", "eth_getCode", "eth_call",
    "eth_getLogs", "eth_getTransactionReceipt", "eth_getTransactionByHash",
})
HEX = re.compile(r"0x[0-9a-fA-F]*\Z")
EVENTS = {
    "FeesProcessed(bytes32,uint256,uint256,uint256,uint256)":
        ("FeesProcessed", ("stockFees", "usdgFees", "stockReserved", "usdgReserved")),
    "PairCheckpoint(bytes32,uint256,uint256,uint256,int256,uint256,uint256)":
        ("PairCheckpoint", ("stockAssets", "usdgAssets", "benchmarkUSDG", "pnlUSDG", "stockAccounted", "usdgAccounted")),
    "Deposited(bytes32,address,address,uint256)": ("Deposited", ("amount",)),
    "Withdrawal(bytes32,address,address,uint256,uint256,uint256)":
        ("Withdrawal", ("requested", "returned", "realizedLoss")),
    "LiquidityRebalanced(bytes32,uint256,uint256,uint128)":
        ("LiquidityRebalanced", ("stockUsed", "usdgUsed", "liquidityAdded")),
    "AccrueInterest(uint256,uint256,uint256,uint256)":
        ("AccrueInterest", ("cashPrior", "interestAccumulated", "borrowIndex", "totalBorrows")),
    "Mint(address,uint256,uint256)": ("Mint", ("minter", "mintAmount", "mintTokens")),
    "Redeem(address,uint256,uint256)": ("Redeem", ("redeemer", "redeemAmount", "redeemTokens")),
    "VaultWithdrawal(uint256,uint256,uint256)":
        ("VaultWithdrawal", ("requested", "returned", "realizedLoss")),
    "Swap(bytes32,address,int128,int128,uint160,uint128,int24,uint24)":
        ("UniswapSwap", ("amount0", "amount1", "sqrtPriceX96", "liquidity", "tick", "fee")),
    "Swap(address,address,int256,int256,uint160,uint128,int24)":
        ("RamsesSwap", ("amount0", "amount1", "sqrtPriceX96", "liquidity", "tick")),
}
SIGNED_FIELDS = {"pnlUSDG", "amount0", "amount1", "tick"}


class EvidenceError(RuntimeError):
    """A failed required read makes the entire collection fail."""


def keccak(text: str) -> str:
    try:
        from Crypto.Hash import keccak as ethereum_keccak
    except ImportError as exc:
        raise EvidenceError("Install remediation/tools/strategy_evidence_requirements.txt") from exc
    return "0x" + ethereum_keccak.new(digest_bits=256, data=text.encode()).hexdigest()


def quantity(value, name="quantity") -> int:
    if not isinstance(value, str) or not HEX.fullmatch(value) or len(value) <= 2:
        raise EvidenceError(f"Malformed {name}")
    return int(value, 16)


def hash32(value, name="hash") -> str:
    if not isinstance(value, str) or not HEX.fullmatch(value) or len(value) != 66:
        raise EvidenceError(f"Malformed {name}")
    return value.lower()


def address(value) -> str:
    if not isinstance(value, str) or not HEX.fullmatch(value) or len(value) != 42:
        raise EvidenceError("Malformed contract address")
    return value.lower()


def words(data, count) -> list[int]:
    if not isinstance(data, str) or not HEX.fullmatch(data) or len(data) != 2 + 64 * count:
        raise EvidenceError(f"ABI length mismatch: expected {count} words")
    return [int(data[i:i + 64], 16) for i in range(2, len(data), 64)]


def word_address(value: int) -> str:
    if value < 0 or value >= 1 << 160:
        raise EvidenceError("Non-canonical ABI address")
    return f"0x{value:040x}"


def signed(value: int) -> int:
    return value - (1 << 256) if value >= 1 << 255 else value


def sample_blocks(start, end, step):
    if start < ROLLOUT_BLOCK or end < start or step <= 0:
        raise EvidenceError("Require rollout <= from-block <= to-block and positive sample-step")
    return sorted(set(range(start, end + 1, step)) | {end})


class Rpc:
    def __init__(self, url, timeout=30):
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise EvidenceError("An HTTP(S) archive RPC URL is required")
        self.url, self.timeout, self.request_id = url, timeout, 0

    def call(self, method, params):
        if method not in READ_METHODS:
            raise EvidenceError(f"RPC method is not read-only allowlisted: {method}")
        self.request_id += 1
        payload = {"jsonrpc": "2.0", "id": self.request_id, "method": method, "params": params}
        req = Request(self.url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        try:
            with urlopen(req, timeout=self.timeout) as response:
                result = json.load(response)
        except HTTPError as exc:
            raise EvidenceError(f"{method}: HTTP {exc.code}; no fallback performed") from exc
        except (URLError, TimeoutError, ValueError, OSError) as exc:
            # Do not put credential-bearing RPC URLs or upstream exception text in reports.
            raise EvidenceError(f"{method}: transport/JSON failure; no fallback performed") from exc
        if not isinstance(result, dict) or result.get("id") != self.request_id:
            raise EvidenceError(f"{method}: mismatched JSON-RPC response")
        if "error" in result or "result" not in result or result["result"] is None:
            raise EvidenceError(f"{method}: required RPC read failed; archive/EIP-1898 support required")
        return result["result"]


class Collector:
    def __init__(self, rpc):
        self.rpc = rpc
        self.blocks = {}
        self.reads = []
        self.log_batches = []
        self.event_schema = {keccak(signature): item for signature, item in EVENTS.items()}

    def block(self, number):
        if number not in self.blocks:
            raw = self.rpc.call("eth_getBlockByNumber", [hex(number), False])
            if not isinstance(raw, dict) or quantity(raw.get("number")) != number:
                raise EvidenceError("Wrong block returned")
            self.blocks[number] = {
                "number": number, "hash": hash32(raw.get("hash")),
                "timestamp": quantity(raw.get("timestamp")),
            }
        return self.blocks[number]

    def call(self, contract, signature, args, count, number):
        block = self.block(number)
        encoded = "".join(f"{int(a, 16) if isinstance(a, str) else a:064x}" for a in args)
        data = keccak(signature)[:10] + encoded
        raw = self.rpc.call("eth_call", [
            {"to": address(contract), "data": data},
            {"blockHash": block["hash"], "requireCanonical": True},
        ])
        values = words(raw, count)
        self.reads.append({"block": number, "blockHash": block["hash"], "to": address(contract),
                           "signature": signature, "data": data, "result": raw})
        return values

    def verify_archive(self, start, end):
        if quantity(self.rpc.call("eth_chainId", []), "chain ID") != CHAIN_ID:
            raise EvidenceError("Wrong chain; Robinhood chain ID 4663 required")
        contracts = [VAULT, ADAPTER, GUARD, MANAGER, STATE_VIEW, RAMSES, NVDA, USDG]
        contracts.extend(v[0] for v in MARKETS.values())
        code = []
        for number in sorted({start, end}):
            block = self.block(number)
            for contract in contracts:
                raw = self.rpc.call("eth_getCode", [contract, {"blockHash": block["hash"], "requireCanonical": True}])
                if not isinstance(raw, str) or not HEX.fullmatch(raw) or len(raw) <= 2 or len(raw) % 2:
                    raise EvidenceError(f"Missing/malformed historical code at block {number}")
                code.append({"block": number, "address": contract,
                             "bytecodeSha256": hashlib.sha256(bytes.fromhex(raw[2:])).hexdigest()})
        return code

    def snapshot(self, number):
        pid = keccak("NVDA/USDG")
        get = lambda contract, signature, args=(), count=1: self.call(contract, signature, args, count, number)
        config = get(VAULT, "pairConfig(bytes32)", [pid], 18)
        if [word_address(v) for v in config[:2]] != [NVDA, USDG] or config[12:14] != [18, 6]:
            raise EvidenceError("Vault token identity or decimal mismatch")
        key = get(ADAPTER, "poolKey(bytes32)", [pid], 5)
        if {word_address(key[0]), word_address(key[1])} != {NVDA, USDG}:
            raise EvidenceError("Uniswap pool token mismatch")
        ramses_tokens = [word_address(get(RAMSES, f"token{i}()")[0]) for i in (0, 1)]
        if set(ramses_tokens) != {NVDA, USDG}:
            raise EvidenceError("Ramses pool token mismatch")
        ledger = get(VAULT, "ledger(bytes32)", [pid], 6)
        position = get(ADAPTER, "positionState(bytes32)", [pid], 4)
        ticks = get(ADAPTER, "positionTicks(bytes32)", [pid], 3)
        slot0 = get(STATE_VIEW, "getSlot0(bytes32)", [POOL_ID], 4)
        lower, upper, tick = signed(ticks[0]), signed(ticks[1]), signed(slot0[1])
        feed = get(GUARD, "feedConfig(bytes32)", [pid], 16)
        oracle = {"feedConfigWords": feed, "stockOraclePaused": bool(get(NVDA, "oraclePaused()")[0]),
                  "stockRoundWords": get(word_address(feed[2]), "latestRoundData()", (), 5),
                  "stockFeedDecimals": get(word_address(feed[2]), "decimals()")[0]}
        # Read raw feed state during closed sessions too. Never label last price as executable.
        for name, index in (("usdg", 3), ("sequencer", 4)):
            if feed[index]:
                oracle[name + "RoundWords"] = get(word_address(feed[index]), "latestRoundData()", (), 5)
        markets = {}
        for name, (market, underlying, decimals) in MARKETS.items():
            if word_address(get(market, "underlying()")[0]) != underlying:
                raise EvidenceError(f"{name} underlying mismatch")
            if get(underlying, "decimals()")[0] != decimals:
                raise EvidenceError(f"{name} token decimals mismatch")
            values = {s: get(market, s + "()")[0] for s in (
                "exchangeRateStored", "totalSupply", "totalBorrows", "totalReserves", "borrowIndex",
                "reserveFactorMantissa", "supplyRatePerBlock", "borrowRatePerBlock", "vaultAccountedAssets",
                "vaultWithdrawableAssets", "cumulativeVaultLoss", "vaultBufferMantissa", "getCash",
            )}
            # getCash includes strategy-withdrawable assets in this delegate, not only local cash.
            values["localUnderlyingCash"] = get(underlying, "balanceOf(address)", [market])[0]
            values["underlyingDecimals"] = decimals
            markets[name] = values
        return {
            **self.block(number), "pairConfigWords": config, "rangePolicyWords": get(VAULT, "rangePolicy(bytes32)", [pid], 7),
            "ledgerWords": ledger, "oracle": oracle, "markets": markets,
            "uniswap": {"poolKeyWords": key, "slot0Words": slot0,
                        "activeLiquidity": get(STATE_VIEW, "getLiquidity(bytes32)", [POOL_ID])[0],
                        "positionWords": position, "tickLower": lower, "tickUpper": upper,
                        "inRange": bool(position[1] and lower <= tick < upper),
                        "amountValuation": "raw spot-composition quantities; no oracle USD valuation yet"},
            "ramses": {"tokens": ramses_tokens, "liquidity": get(RAMSES, "liquidity()")[0]},
        }

    def logs(self, contract, topics, start, end, step):
        out, seen = [], set()
        for left in range(start, end + 1, step):
            right = min(end, left + step - 1)
            request = {"address": address(contract), "fromBlock": hex(left), "toBlock": hex(right)}
            if topics is not None:
                request["topics"] = topics
            logs = self.rpc.call("eth_getLogs", [request])
            if not isinstance(logs, list):
                raise EvidenceError("Malformed log response")
            self.log_batches.append({"address": address(contract), "topics": topics,
                                     "fromBlock": left, "toBlock": right,
                                     "providerReturnedCount": len(logs)})
            for log in logs:
                n = quantity(log.get("blockNumber"))
                if not left <= n <= right or address(log.get("address")) != address(contract) or log.get("removed"):
                    raise EvidenceError("Removed, out-of-range or wrong-contract log")
                if hash32(log.get("blockHash")) != self.block(n)["hash"]:
                    raise EvidenceError("Log block hash is not canonical")
                log_topics = log.get("topics")
                if not isinstance(log_topics, list) or not log_topics:
                    raise EvidenceError("Malformed topics")
                log_topics = [hash32(t, "topic") for t in log_topics]
                if topics:
                    for index, wanted in enumerate(topics):
                        if wanted is not None and (len(log_topics) <= index or log_topics[index] != wanted.lower()):
                            raise EvidenceError("Provider returned log outside requested topics")
                tx = hash32(log.get("transactionHash"))
                idx = quantity(log.get("logIndex"))
                identity = (tx, idx)
                if identity in seen:
                    raise EvidenceError("Duplicate log; provider pagination is not trustworthy")
                seen.add(identity)
                row = dict(log, timestamp=self.block(n)["timestamp"])
                if log_topics[0] in self.event_schema:
                    name, fields = self.event_schema[log_topics[0]]
                    values = words(log.get("data"), len(fields))
                    row["decoded"] = {"event": name, **{f: signed(v) if f in SIGNED_FIELDS else v for f, v in zip(fields, values)}}
                out.append(row)
        return sorted(out, key=lambda x: (quantity(x["blockNumber"]), quantity(x["transactionIndex"]), quantity(x["logIndex"])))

    def receipt(self, tx_hash):
        receipt = self.rpc.call("eth_getTransactionReceipt", [tx_hash])
        tx = self.rpc.call("eth_getTransactionByHash", [tx_hash])
        if not isinstance(receipt, dict) or not isinstance(tx, dict):
            raise EvidenceError("Malformed transaction receipt")
        number = quantity(receipt.get("blockNumber"))
        block_hash = self.block(number)["hash"]
        if any(hash32(x.get("blockHash")) != block_hash for x in (receipt, tx)):
            raise EvidenceError("Receipt/transaction block hash mismatch")
        if hash32(receipt.get("transactionHash")) != tx_hash or hash32(tx.get("hash")) != tx_hash:
            raise EvidenceError("Receipt/transaction hash mismatch")
        return {"receipt": receipt, "transaction": tx,
                "gasChargeWei": str(quantity(receipt.get("gasUsed")) * quantity(receipt.get("effectiveGasPrice")))}

    def verify_canonical(self):
        for n, expected in self.blocks.items():
            current = self.rpc.call("eth_getBlockByNumber", [hex(n), False])
            if not isinstance(current, dict) or hash32(current.get("hash")) != expected["hash"]:
                raise EvidenceError(f"Chain changed during collection at block {n}")


def exchange_rate_changes(snapshots):
    if len(snapshots) < 2:
        return None
    first, last = snapshots[0], snapshots[-1]
    out = {}
    for name in MARKETS:
        start = first["markets"][name]["exchangeRateStored"]
        end = last["markets"][name]["exchangeRateStored"]
        if start <= 0:
            raise EvidenceError("Cannot calculate growth from zero exchange rate")
        with localcontext() as context:
            context.prec = 60
            out[name] = {"storedExchangeRateChangePercent": str((Decimal(end) / start - 1) * 100),
                         "fromBlock": first["number"], "toBlock": last["number"],
                         "windowSeconds": last["timestamp"] - first["timestamp"],
                         "annualizedApy": None,
                         "note": "Stored accounting change only; not fully accrued yield, TWR, IRR or LP P&L."}
    return out


def collect(rpc, start, end, sample_step, log_step):
    sampled = sample_blocks(start, end, sample_step)
    if log_step <= 0:
        raise EvidenceError("log-step must be positive")
    c = Collector(rpc)
    code = c.verify_archive(start, end)
    snapshots = [c.snapshot(n) for n in sampled]
    pid = keccak("NVDA/USDG")
    sources = {"vault": (VAULT, [None, pid]), "adapter": (ADAPTER, [None, pid]),
               "uniswap": (MANAGER, [None, POOL_ID]), "ramses": (RAMSES, None)}
    sources.update({name: (value[0], None) for name, value in MARKETS.items()})
    logs = {name: c.logs(contract, topics, start, end, log_step) for name, (contract, topics) in sources.items()}
    # Transactions emitting vault/adapter events: successful observed actions, not all keeper attempts.
    tx_hashes = sorted({x["transactionHash"].lower() for name in ("vault", "adapter") for x in logs[name]})
    receipts = {tx: c.receipt(tx) for tx in tx_hashes}
    c.verify_canonical()
    return {
        "schemaVersion": 1, "collectionStatus": "requests_completed", "phase1AnalysisStatus": "incomplete",
        "createdAt": datetime.now(timezone.utc).isoformat(), "chainId": CHAIN_ID,
        "sourceCommit": SOURCE_COMMIT, "window": {"fromBlock": start, "toBlock": end, "sampleStep": sample_step},
        "blocks": sorted(c.blocks.values(), key=lambda x: x["number"]), "codeAtBoundaries": code,
        "snapshots": snapshots, "historicalCalls": c.reads, "logs": logs, "observedActionReceipts": receipts,
        "logBatches": c.log_batches,
        "logCompleteness": {
            "status": "not_independently_verified",
            "provenance": "eth_getLogs responses from one provider; requested ranges and returned counts recorded",
            "limitation": "Silent truncation or omitted logs cannot be detected from these responses alone",
            "requiredBeforeVolumeOrYieldClaims": "Cross-check against independently enumerated complete block receipts or a verified independent source",
        },
        "storedExchangeRateChanges": exchange_rate_changes(snapshots),
        "profitableCapacity": None,
        "unproven": [
            "Historical log completeness: one-provider responses may silently omit or truncate logs",
            "Supplier interest/LP fee/loss/reserve decomposition and flow-adjusted TWR/IRR",
            "Oracle-priced active/inactive capital including validity and closed-session policy",
            "Realized LP fee entitlement on Ramses and Uniswap, including protocol cuts",
            "Portfolio ledger and delta-matched benchmark; +1m/+1h swap markouts",
            "Keeper failed/waiting attempts: require the keeper journal or full transaction enumeration",
            "Dilution and liquidation capacity at $1k/$10k/$50k; net return/capacity sentence",
        ],
    }


def write_atomic(path, report):
    path = Path(path)
    if path.exists():
        raise EvidenceError("Output already exists; choose a new evidence filename")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".evidence-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(report, handle, indent=2)
            handle.write("\n")
        # Exclusive link prevents accidental replacement by another concurrent run.
        os.link(temporary, path)
    finally:
        os.unlink(temporary)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rpc-env", default="ROBINHOOD_ARCHIVE_RPC_URL", help="Environment variable holding archive RPC URL")
    parser.add_argument("--from-block", type=int, default=ROLLOUT_BLOCK)
    parser.add_argument("--to-block", type=int, required=True, help="Explicit finalized end block; no implicit latest")
    parser.add_argument("--sample-step", type=int, default=10000)
    parser.add_argument("--log-step", type=int, default=2000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        sample_blocks(args.from_block, args.to_block, args.sample_step)
        if args.output.exists():
            raise EvidenceError("Output already exists; choose a new evidence filename")
        url = os.environ.get(args.rpc_env)
        if not url:
            raise EvidenceError(f"Set {args.rpc_env} to an archive RPC; no default/pruned fallback")
        report = collect(Rpc(url), args.from_block, args.to_block, args.sample_step, args.log_step)
        write_atomic(args.output, report)
        print(json.dumps({"collectionStatus": "requests_completed", "phase1AnalysisStatus": "incomplete", "output": str(args.output)}))
        return 0
    except EvidenceError as exc:
        print(json.dumps({"collectionStatus": "failed", "error": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
