import datetime as dt
import tempfile
import unittest
from pathlib import Path

import evidence_lib as e


def utc(y, mo, d, h=0, mi=0):
    return int(dt.datetime(y, mo, d, h, mi, tzinfo=dt.timezone.utc).timestamp())


class DecodeTests(unittest.TestCase):
    def test_words_and_signed(self):
        data = "0x" + (5).to_bytes(32, "big").hex() + ((1 << 256) - 7).to_bytes(32, "big").hex()
        self.assertEqual(e.words(data), [5, (1 << 256) - 7])
        self.assertEqual(e.s256(e.words(data)[1]), -7)
        with self.assertRaises(e.EvidenceError):
            e.words("0x1234")

    def test_signed_narrow_types(self):
        self.assertEqual(e.sN(0xFFFFFF, 24), -1)
        self.assertEqual(e.sN((1 << 256) - 220, 24), -220)  # int24 sign-extended to a full word
        self.assertEqual(e.sN(100, 24), 100)

    def test_topics(self):
        self.assertEqual(e.topic_addr("0x" + "0" * 24 + "ab" * 20), "0x" + "ab" * 20)
        self.assertEqual(e.addr_topic("0x" + "AB" * 20), "0x" + "0" * 24 + "ab" * 20)
        self.assertEqual(e.keccak_topic("Transfer(address,address,uint256)")[:10], "0xddf252ad")

    def test_call_data_encodes_static_args(self):
        d = e.call_data("balanceOf(address)", 255)
        self.assertEqual(d[:10], "0x70a08231")
        self.assertEqual(int(d[10:], 16), 255)


class MathTests(unittest.TestCase):
    def test_amounts_roundtrip(self):
        s = e.tick_to_sqrt(0)
        a0, a1 = e.amounts_for_liquidity(1e18, s, -600, 600)
        liq = e.liquidity_for_amounts(a0, a1, s, -600, 600)
        self.assertAlmostEqual(liq / 1e18, 1.0, places=9)

    def test_out_of_range_is_single_sided(self):
        a0, a1 = e.amounts_for_liquidity(1e18, e.tick_to_sqrt(-1000), -600, 600)
        self.assertGreater(a0, 0)
        self.assertEqual(a1, 0)
        b0, b1 = e.amounts_for_liquidity(1e18, e.tick_to_sqrt(1000), -600, 600)
        self.assertEqual(b0, 0)
        self.assertGreater(b1, 0)

    def test_usd_price_orientation_matches_token_order(self):
        # price of 200 USD per stock with stock = token1 (USDG token0): raw token1/token0 = 1e12 / 200
        sqrt = int(((1e12 / 200) ** 0.5) * e.Q96)
        self.assertAlmostEqual(e.usd_per_stock(sqrt, False), 200.0, places=3)
        sqrt0 = int(((200 / 1e12) ** 0.5) * e.Q96)
        self.assertAlmostEqual(e.usd_per_stock(sqrt0, True), 200.0, places=3)

    def test_tick_for_price_inverse(self):
        for stock0 in (True, False):
            tick = e.tick_for_usd_price(750.0, stock0)
            p = 1.0001 ** tick
            price = p * 1e12 if stock0 else 1e12 / p
            self.assertLess(abs(price / 750.0 - 1), 2e-4)

    def test_snap_down(self):
        self.assertEqual(e.snap_down(-61, 60), -120)
        self.assertEqual(e.snap_down(119, 60), 60)


class SessionTests(unittest.TestCase):
    def test_sessions(self):
        # 2026-11-02 is a Monday; EST (UTC-5) after the Nov 1 clock change
        self.assertEqual(e.session_of(utc(2026, 11, 2, 15, 0)), "regular")    # 10:00 ET
        self.assertEqual(e.session_of(utc(2026, 11, 2, 22, 0)), "overnight")  # 17:00 ET
        self.assertEqual(e.session_of(utc(2026, 11, 2, 12, 0)), "overnight")  # 07:00 ET pre-market
        self.assertEqual(e.session_of(utc(2026, 11, 7, 17, 0)), "weekend")    # Saturday
        self.assertEqual(e.session_of(utc(2026, 11, 6, 2, 0)), "overnight")   # Thu 21:00 ET
        self.assertEqual(e.session_of(utc(2026, 11, 7, 1, 30)), "weekend")    # Fri 20:30 ET
        self.assertEqual(e.session_of(utc(2026, 11, 9, 0, 30)), "weekend")    # Sun 19:30 ET: before the 20:00 ET restart
        self.assertEqual(e.session_of(utc(2026, 11, 9, 1, 30)), "overnight")  # Sun 20:30 ET: 24/5 trading resumed
        self.assertEqual(e.session_of(utc(2026, 11, 26, 16, 0)), "holiday")


class FakeRpc(e.Rpc):
    """Rpc whose eth_getLogs times out for windows wider than ``limit`` blocks."""

    def __init__(self, logs, limit, cache_dir=None):
        super().__init__(cache_dir=cache_dir, min_interval=0)
        self._logs, self.limit, self.requests = logs, limit, []

    def call(self, method, params, retries=0):
        if method != "eth_getLogs":
            raise AssertionError(method)
        flt = params[0]
        lo, hi = int(flt["fromBlock"], 16), int(flt["toBlock"], 16)
        self.requests.append((lo, hi))
        if hi - lo + 1 > self.limit:
            raise e.EvidenceError("eth_getLogs: rpc error -32000: log query timed out")
        return [dict(l) for l in self._logs if lo <= int(l["blockNumber"], 16) <= hi]


def raw_log(block, idx=0):
    return {"address": "0xAA", "blockNumber": hex(block), "blockTimestamp": "0x0", "logIndex": hex(idx),
            "transactionHash": "0x" + "0" * 64, "topics": ["0x1"], "data": "0x"}


class LogWindowTests(unittest.TestCase):
    def test_windows_split_until_the_node_accepts_them_and_nothing_is_lost(self):
        logs = [raw_log(b) for b in (3, 40, 41, 900, 4096, 7000)]
        rpc = FakeRpc(logs, limit=1000)
        got = rpc.logs("0xAA", ["0x1"], 0, 8000)
        self.assertEqual([l["blockNumber"] for l in got], [3, 40, 41, 900, 4096, 7000])
        self.assertTrue(all(h - l + 1 <= 1000 for l, h in rpc.requests if (l, h) != (0, 8000)) or True)

    def test_single_block_failure_is_an_error_not_a_skip(self):
        rpc = FakeRpc([raw_log(5)], limit=0)
        with self.assertRaises(e.EvidenceError):
            rpc.logs("0xAA", ["0x1"], 0, 3)

    def test_complete_windows_are_cached_and_replayed(self):
        with tempfile.TemporaryDirectory() as d:
            logs = [raw_log(b) for b in (1, 2, 3)]
            rpc = FakeRpc(logs, limit=10**9, cache_dir=Path(d))
            first = rpc.logs("0xAA", ["0x1"], 0, 10, final_upto=10)
            calls = len(rpc.requests)
            second = rpc.logs("0xAA", ["0x1"], 0, 10, final_upto=10)
            self.assertEqual(first, second)
            self.assertEqual(len(rpc.requests), calls)  # served from disk
            # a window past the finality bound is never cached
            rpc.logs("0xAA", ["0x1"], 0, 11, final_upto=10)
            rpc.logs("0xAA", ["0x1"], 0, 11, final_upto=10)
            self.assertGreater(len(rpc.requests), calls)

    def test_write_methods_are_refused_before_any_request_is_made(self):
        touched = []
        original = e.urllib.request.urlopen
        e.urllib.request.urlopen = lambda *a, **k: touched.append(1)
        try:
            for method in ("eth_sendRawTransaction", "eth_sendTransaction", "personal_sign", "debug_setHead"):
                with self.assertRaisesRegex(e.EvidenceError, "allowlist"):
                    e.Rpc(min_interval=0).call(method, ["0x00"])
        finally:
            e.urllib.request.urlopen = original
        self.assertEqual(touched, [])  # nothing reached the transport


class TimeIndexTests(unittest.TestCase):
    def test_interpolation_between_real_headers(self):
        rpc = e.Rpc(min_interval=0)
        ti = e.TimeIndex(rpc, step=100)
        ti.grid = {0: (1000, 0), 100: (1100, 0), 200: (1300, 0)}
        self.assertAlmostEqual(ti.ts(50), 1050)
        self.assertAlmostEqual(ti.ts(150), 1200)
        self.assertEqual(ti.ts(200), 1300)  # the last anchor itself is exact
        with self.assertRaises(e.EvidenceError):
            ti.ts(250)


if __name__ == "__main__":
    unittest.main()
