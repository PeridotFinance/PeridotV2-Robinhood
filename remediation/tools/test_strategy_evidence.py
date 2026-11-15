import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

import strategy_evidence as e


N = e.ROLLOUT_BLOCK
BLOCK_HASH = "0x" + "ab" * 32
TX_HASH = "0x" + "cd" * 32
PAIR = e.keccak("NVDA/USDG")


def encode(*values):
    return "0x" + "".join(f"{v % (1 << 256):064x}" for v in values)


def block(number=N, block_hash=BLOCK_HASH):
    return {"number": hex(number), "hash": block_hash, "timestamp": hex(1_800_000_000 + number - N)}


def log(index=0, number=N):
    return {
        "address": e.VAULT, "blockNumber": hex(number), "blockHash": BLOCK_HASH,
        "transactionHash": TX_HASH, "transactionIndex": "0x0", "logIndex": hex(index),
        "topics": [e.keccak("FeesProcessed(bytes32,uint256,uint256,uint256,uint256)"), PAIR],
        "data": encode(100, 200, 20, 40), "removed": False,
    }


class PinnedReadTests(unittest.TestCase):
    def test_keccak_is_ethereum_not_nist_sha3(self):
        self.assertEqual(e.keccak("transfer(address,uint256)")[:10], "0xa9059cbb")
        self.assertEqual(e.keccak(""), "0xc5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470")

    def test_all_network_state_reads_are_hash_pinned(self):
        rpc = Mock()
        rpc.call.side_effect = [block(), encode(123)]
        collector = e.Collector(rpc)
        self.assertEqual(collector.call(e.VAULT, "totalSupply()", (), 1, N), [123])
        self.assertEqual(rpc.call.call_args_list[-1].args[1][1], {"blockHash": BLOCK_HASH, "requireCanonical": True})
        self.assertEqual(collector.reads[0]["blockHash"], BLOCK_HASH)

    def test_archive_read_failure_never_retries_at_latest(self):
        rpc = Mock()
        rpc.call.side_effect = [block(), e.EvidenceError("historical state missing")]
        collector = e.Collector(rpc)
        with self.assertRaises(e.EvidenceError):
            collector.call(e.VAULT, "totalSupply()", (), 1, N)
        self.assertEqual(rpc.call.call_count, 2)
        self.assertEqual(collector.reads, [])

    def test_wrong_chain_fails_before_any_state_reads(self):
        rpc = Mock()
        rpc.call.return_value = "0x1"
        with self.assertRaisesRegex(e.EvidenceError, "Wrong chain"):
            e.Collector(rpc).verify_archive(N, N + 1)
        self.assertEqual(rpc.call.call_count, 1)

    def test_missing_historical_code_is_fatal(self):
        rpc = Mock()
        rpc.call.side_effect = [hex(e.CHAIN_ID), block(), "0x"]
        with self.assertRaisesRegex(e.EvidenceError, "Missing/malformed"):
            e.Collector(rpc).verify_archive(N, N)

    def test_reorg_invalidates_collection(self):
        rpc = Mock()
        rpc.call.side_effect = [block(), block(block_hash="0x" + "ef" * 32)]
        collector = e.Collector(rpc)
        collector.block(N)
        with self.assertRaisesRegex(e.EvidenceError, "Chain changed"):
            collector.verify_canonical()

    def test_malformed_or_empty_abi_is_not_zero(self):
        for data in ("0x", "0x00", encode(1, 2), "garbage"):
            with self.subTest(data=data), self.assertRaises(e.EvidenceError):
                e.words(data, 1)

    def test_sampling_preserves_both_boundaries(self):
        self.assertEqual(e.sample_blocks(N, N + 11, 5), [N, N + 5, N + 10, N + 11])
        for args in ((N - 1, N, 1), (N, N - 1, 1), (N, N + 1, 0)):
            with self.assertRaises(e.EvidenceError):
                e.sample_blocks(*args)

    def test_native_local_cash_is_not_delegate_cash(self):
        collector = e.Collector(Mock())
        collector.blocks[N] = {"number": N, "hash": BLOCK_HASH, "timestamp": 100}
        feed_address = "0x" + "12" * 20
        cfg = [int(e.NVDA, 16), int(e.USDG, 16), 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 18, 6, 0, 0, 0, 1]
        feed = [int(e.NVDA, 16), int(e.USDG, 16), int(feed_address, 16), 0, 0,
                int(e.POOL_ID, 16), 3600, 3600, 300, 18, 6, 8, 0, 1, 1, 600]

        def call(contract, signature, args, count, number):
            fixture = {
                "pairConfig(bytes32)": cfg,
                "poolKey(bytes32)": [int(e.USDG, 16), int(e.NVDA, 16), 3000, 60, 0],
                "token0()": [int(e.USDG, 16)], "token1()": [int(e.NVDA, 16)],
                "ledger(bytes32)": [10, 20, 3, 4, 0, 99],
                "positionState(bytes32)": [1, 200, 6, 7],
                "positionTicks(bytes32)": [-300 % (1 << 256), 300, 1],
                "getSlot0(bytes32)": [1 << 96, 0, 0, 3000],
                "feedConfig(bytes32)": feed,
                "oraclePaused()": [1], "latestRoundData()": [1, 200 * 10**8, 1, 1, 1],
                "rangePolicy(bytes32)": [1, 1200, 300, 3600, 4, 10, 50 * 10**18],
                "getCash()": [500], "balanceOf(address)": [100],
            }
            if signature == "underlying()":
                data = [int(next(value[1] for value in e.MARKETS.values() if value[0] == contract), 16)]
            elif signature == "decimals()":
                data = [{e.NVDA: 18, e.USDG: 6, feed_address: 8}[contract]]
            else:
                data = fixture.get(signature, [1])
            self.assertEqual(len(data), count, signature)
            return data

        with patch.object(collector, "call", side_effect=call):
            snapshot = collector.snapshot(N)
        self.assertEqual(snapshot["markets"]["pNVDA"]["getCash"], 500)
        self.assertEqual(snapshot["markets"]["pNVDA"]["localUnderlyingCash"], 100)
        self.assertTrue(snapshot["oracle"]["stockOraclePaused"])
        self.assertNotIn("priceUsd", snapshot["oracle"])


class LogTests(unittest.TestCase):
    def reader(self, logs):
        rpc = Mock()
        rpc.call.side_effect = lambda method, params: logs if method == "eth_getLogs" else block(int(params[0], 16))
        return e.Collector(rpc)

    def test_decode_preserves_fees_and_reserve_separately(self):
        out = self.reader([log()]).logs(e.VAULT, [None, PAIR], N, N, 1)
        self.assertEqual(out[0]["decoded"], {"event": "FeesProcessed", "stockFees": 100,
                                         "usdgFees": 200, "stockReserved": 20, "usdgReserved": 40})

    def test_duplicate_logs_are_fatal(self):
        with self.assertRaisesRegex(e.EvidenceError, "Duplicate log"):
            self.reader([log(), log()]).logs(e.VAULT, [None, PAIR], N, N, 1)

    def test_removed_out_of_range_and_wrong_pool_are_fatal(self):
        variants = [dict(log(), removed=True), dict(log(), blockNumber=hex(N + 1)),
                    dict(log(), address=e.ADAPTER), dict(log(), blockHash="0x" + "ff" * 32),
                    dict(log(), topics=[log()["topics"][0], "0x" + "00" * 32])]
        for variant in variants:
            with self.subTest(variant=variant), self.assertRaises(e.EvidenceError):
                self.reader([variant]).logs(e.VAULT, [None, PAIR], N, N, 1)

    def test_log_rpc_failure_is_not_an_empty_window(self):
        rpc = Mock()
        rpc.call.side_effect = e.EvidenceError("missing history")
        with self.assertRaises(e.EvidenceError):
            e.Collector(rpc).logs(e.VAULT, None, N, N, 1)

    def test_logs_sorted_in_chain_order(self):
        out = self.reader([log(4), log(1), log(3)]).logs(e.VAULT, [None, PAIR], N, N, 1)
        self.assertEqual([int(x["logIndex"], 16) for x in out], [1, 3, 4])

    def test_empty_provider_response_is_recorded_without_proving_no_activity(self):
        reader = self.reader([])
        self.assertEqual(reader.logs(e.VAULT, None, N, N + 2, 2), [])
        self.assertEqual(reader.log_batches, [
            {"address": e.VAULT, "topics": None, "fromBlock": N, "toBlock": N + 1, "providerReturnedCount": 0},
            {"address": e.VAULT, "topics": None, "fromBlock": N + 2, "toBlock": N + 2, "providerReturnedCount": 0},
        ])

    def test_signed_swap_fields_are_not_unsigned_volume(self):
        item = dict(log(), address=e.MANAGER,
                    topics=[e.keccak("Swap(bytes32,address,int128,int128,uint160,uint128,int24,uint24)"), e.POOL_ID],
                    data=encode(-100, 90, 1 << 96, 1000, -300, 3000))
        out = self.reader([item]).logs(e.MANAGER, [None, e.POOL_ID], N, N, 1)
        self.assertEqual(out[0]["decoded"]["amount0"], -100)
        self.assertEqual(out[0]["decoded"]["tick"], -300)


class ReportingTests(unittest.TestCase):
    def test_successful_requests_do_not_certify_completeness_or_profitability(self):
        with patch.object(e, "Collector") as factory:
            collector = factory.return_value
            collector.verify_archive.return_value = []
            collector.snapshot.return_value = {"number": N}
            collector.logs.return_value = []
            collector.blocks = {}
            collector.reads = []
            collector.log_batches = []
            report = e.collect(Mock(), N, N, 1, 1)
        self.assertEqual(report["collectionStatus"], "requests_completed")
        self.assertEqual(report["logCompleteness"]["status"], "not_independently_verified")
        self.assertEqual(report["phase1AnalysisStatus"], "incomplete")
        self.assertIsNone(report["profitableCapacity"])

    def test_stored_growth_is_not_annualized_or_called_twr(self):
        snapshots = [{"number": N + i, "timestamp": 100 + i,
                      "markets": {name: {"exchangeRateStored": rate} for name in e.MARKETS}}
                     for i, rate in enumerate((10**18, 10**18 - 1))]
        result = e.exchange_rate_changes(snapshots)
        self.assertEqual(result["pNVDA"]["storedExchangeRateChangePercent"], "-1.00E-16")
        self.assertIsNone(result["pNVDA"]["annualizedApy"])
        self.assertIn("not fully accrued", result["pNVDA"]["note"])

    def test_existing_evidence_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "evidence.json"
            e.write_atomic(path, {"original": True})
            with self.assertRaises(e.EvidenceError):
                e.write_atomic(path, {"replacement": True})
            self.assertEqual(json.loads(path.read_text()), {"original": True})

    def test_failure_leaves_no_success_artifact(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"TEST_RPC": "https://rpc.example"}):
            path = Path(tmp) / "evidence.json"
            with patch.object(e, "collect", side_effect=e.EvidenceError("archive missing")), contextlib.redirect_stdout(io.StringIO()) as stdout:
                result = e.main(["--rpc-env", "TEST_RPC", "--to-block", str(N + 1), "--output", str(path)])
            self.assertEqual(result, 1)
            self.assertFalse(path.exists())
            self.assertEqual(json.loads(stdout.getvalue())["collectionStatus"], "failed")

    def test_no_rpc_default_or_current_state_fallback(self):
        with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(io.StringIO()) as stdout:
            result = e.main(["--to-block", str(N + 1), "--output", "/tmp/not-written-evidence.json"])
        self.assertEqual(result, 1)
        self.assertIn("no default/pruned fallback", stdout.getvalue())


class RpcSafetyTests(unittest.TestCase):
    def test_send_transaction_is_rejected_before_network(self):
        with patch.object(e, "urlopen") as network, self.assertRaises(e.EvidenceError):
            e.Rpc("https://rpc.example").call("eth_sendRawTransaction", ["0x00"])
        network.assert_not_called()

    def test_http_error_redacts_credentials(self):
        url = "https://user:secret@rpc.example/private-key"
        with patch.object(e, "urlopen", side_effect=HTTPError(url, 403, "blocked " + url, {}, None)):
            with self.assertRaises(e.EvidenceError) as context:
                e.Rpc(url).call("eth_chainId", [])
        self.assertNotIn("secret", str(context.exception))
        self.assertNotIn("private-key", str(context.exception))
        self.assertIn("HTTP 403", str(context.exception))

    def test_rpc_error_does_not_leak_upstream_details(self):
        response = io.BytesIO(json.dumps({"id": 1, "error": {"message": "secret"}}).encode())
        with patch.object(e, "urlopen", return_value=response), self.assertRaises(e.EvidenceError) as context:
            e.Rpc("https://rpc.example").call("eth_chainId", [])
        self.assertNotIn("secret", str(context.exception))


if __name__ == "__main__":
    unittest.main()
