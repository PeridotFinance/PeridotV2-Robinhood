import unittest

from evidence_lib import addr_topic
from strategy_data import BalanceSeries, Oracle, StepFunction, Trade, decode_v4_swap, fee_function, ramses_trades
from evidence_lib import EvidenceError


def w(*vals):
    return "0x" + "".join((v % (1 << 256)).to_bytes(32, "big").hex() for v in vals)


def log(data, block=1, idx=0):
    return {"blockNumber": block, "logIndex": idx, "topics": ["0x0"], "data": data, "tx": "0xaa"}


class FakeTimeIndex:
    def ts(self, block):
        return float(block)


Q = 1 << 96


class DecodeTests(unittest.TestCase):
    def test_v4_swap_is_flipped_to_the_pools_side(self):
        # swapper pays 100 USDG (negative) and receives 0.5 NVDA (positive): the POOL received +100 USDG and paid -0.5 NVDA
        raw = decode_v4_swap(log(w(-100_000_000, 500_000_000_000_000_000, Q, 7, 5, 3000)))
        self.assertEqual((raw[1], raw[2]), (100_000_000, -500_000_000_000_000_000))
        self.assertEqual(raw[6], 3000)

    def test_ramses_swap_amounts_are_already_pool_side_and_token_order_is_applied(self):
        # token0 = USDG, token1 = NVDA: pool received 100 USDG and paid out 0.5 NVDA
        l = log(w(100_000_000, -500_000_000_000_000_000, int(((1e12 / 200) ** 0.5) * Q), 9, 11))
        tr = ramses_trades([l], FakeTimeIndex(), lambda b, i: 500, False)[0]
        self.assertAlmostEqual(tr.usd_in, 100.0)
        self.assertAlmostEqual(tr.stock_in, -0.5)
        self.assertAlmostEqual(tr.price, 200.0, places=2)
        self.assertEqual(tr.fee_ppm, 500)
        # stock-first pool: the same raw words mean the opposite assignment
        sw = ramses_trades([l], FakeTimeIndex(), lambda b, i: 500, True)[0]
        self.assertAlmostEqual(sw.stock_in, 100_000_000 / 1e18)

    def test_the_exact_raw_liquidity_and_sqrt_price_are_kept_for_state_checks(self):
        l = log(w(1, -1, Q + 7, 50434080898269761, 0))
        tr = ramses_trades([l], FakeTimeIndex(), lambda b, i: 1, False)[0]
        self.assertEqual(tr.liq_raw, 50434080898269761)   # a float round trip would lose the last digit
        self.assertEqual(tr.sqrt_x96, Q + 7)

    def test_liquidity_is_scaled_to_human_units(self):
        l = log(w(1, -1, Q, 2_000_000_000_000, 0))
        self.assertAlmostEqual(ramses_trades([l], FakeTimeIndex(), lambda b, i: 1, False)[0].liq_after, 2.0)


class FeeAndTradeTests(unittest.TestCase):
    def test_fee_function_starts_with_the_old_value_of_the_first_change(self):
        evs = [log(w(100, 175), 10, 0), log(w(175, 300), 20, 3)]
        f = fee_function(evs, head_fee=300)
        self.assertEqual(f(5, 0), 100)     # before the first adjustment: its "old" fee
        self.assertEqual(f(10, 0), 175)
        self.assertEqual(f(20, 2), 175)
        self.assertEqual(f(20, 3), 300)
        self.assertEqual(fee_function([], 123)(1, 1), 123)

    def test_balance_series_orders_by_log_index_inside_a_block(self):
        b = BalanceSeries([(5, 1, 10), (5, 4, -3), (9, 0, 7)])
        self.assertEqual(b.at(5, 0), 0)
        self.assertEqual(b.at(5, 1), 10)
        self.assertEqual(b.at(5, 3), 10)
        self.assertEqual(b.at(5, 4), 7)
        self.assertEqual(b.at(9), 14)

    def test_fee_is_charged_on_the_input_leg_and_swapper_fee_includes_protocol(self):
        t = Trade(1, 0, 1.0, stock_in=-0.5, usd_in=100.0, price=200.0, tick=0, liq_after=1.0, fee_ppm=3000, tx="0x", total_fee_ppm=3499)
        self.assertAlmostEqual(t.fee_usd, 0.3)               # USDG in: 100 x 3000 ppm
        self.assertAlmostEqual(t.swapper_fee_usd, 0.3499)
        s = Trade(1, 0, 1.0, stock_in=0.5, usd_in=-99.0, price=200.0, tick=0, liq_after=1.0, fee_ppm=3000, tx="0x")
        self.assertAlmostEqual(s.fee_usd, 0.5 * 198.0 * 0.003)  # stock in: valued at its execution price
        self.assertAlmostEqual(s.swapper_fee_usd, s.fee_usd)

    def test_a_swap_with_no_input_leg_pays_no_fee_not_a_negative_one(self):
        t = Trade(1, 0, 1.0, stock_in=-0.001, usd_in=0.0, price=150.0, tick=0, liq_after=1.0, fee_ppm=3000, tx="0x")
        self.assertEqual(t.fee_usd, 0.0)
        self.assertEqual(t.swapper_fee_usd, 0.0)

    def test_dust_swap_falls_back_to_the_pool_price(self):
        t = Trade(1, 0, 1.0, stock_in=-0.001, usd_in=0.0, price=150.0, tick=0, liq_after=1.0, fee_ppm=1, tx="0x")
        self.assertEqual(t.exec_price, 150.0)


class OracleTests(unittest.TestCase):
    def test_never_returns_a_future_value(self):
        o = Oracle([(100, 10.0), (200, 11.0)])
        self.assertEqual(o.price(150), 10.0)
        self.assertEqual(o.price(200), 11.0)
        with self.assertRaises(EvidenceError):
            o.price(50)
        with self.assertRaises(EvidenceError):
            o.age(50)


if __name__ == "__main__":
    unittest.main()
