import math
import unittest

from strategy_data import Oracle, Trade
from strategy_sim import Costs, Policy, liq_for, make_range, overlap_fraction, pos_amounts, Pos, simulate


def tr(ts, price, usd_in=10.0, fee_ppm=3000, usd_input=True):
    stock_in = -(usd_in / price) if usd_input else (usd_in / price)
    usd = usd_in if usd_input else -usd_in
    return Trade(block=int(ts), idx=0, ts=float(ts), stock_in=stock_in, usd_in=usd, price=price, tick=0, liq_after=1.0, fee_ppm=fee_ppm, tx="0x")


class GeometryTests(unittest.TestCase):
    def test_amounts_value_conserved_at_the_entry_price(self):
        pa, pb = 90.0, 110.0
        liq = liq_for(1.0, 100.0, 100.0, pa, pb)
        x, y = pos_amounts(Pos(pa, pb, liq, 0), 100.0)
        self.assertLessEqual(x, 1.0 + 1e-9)
        self.assertLessEqual(y, 100.0 + 1e-9)
        self.assertTrue(abs(x - 1.0) < 1e-9 or abs(y - 100.0) < 1e-9)  # one side binds

    def test_out_of_range_is_single_sided(self):
        pos = Pos(90.0, 110.0, 5.0, 0)
        x_hi, y_hi = pos_amounts(pos, 120.0)
        x_lo, y_lo = pos_amounts(pos, 80.0)
        self.assertEqual(x_hi, 0.0)
        self.assertGreater(y_hi, 0.0)
        self.assertEqual(y_lo, 0.0)
        self.assertGreater(x_lo, 0.0)

    def test_make_range_brackets_the_oracle_price(self):
        for stock0 in (True, False):
            pa, pb, c = make_range(200.0, Policy(half_width=1200, spacing=60), stock0)
            self.assertLess(pa, 200.0 < pb and 200.0 or 0)  # pa below price
            self.assertGreater(pb, 200.0)
            self.assertAlmostEqual(math.log(pb / 200.0), math.log(200.0 / pa), delta=0.01)  # roughly symmetric (+-12%)

    def test_stock_input_overlap_uses_the_inverse_sqrt_coordinate(self):
        pos = Pos(100.0, 110.0, 1.0, 0)
        # price falls 105 -> 95 through a [100, 110] range; in 1/sqrt(P) coordinates 0.481245 of the input is inside
        # (hand calculation: (0.1 - 1/sqrt(105)) / (1/sqrt(95) - 1/sqrt(105)))
        self.assertAlmostEqual(overlap_fraction(105.0, 95.0, pos, False), 0.481245, places=5)
        # the same path priced in the USD-input coordinate (sqrt P) would give 0.493747 for the mirrored move; they must differ
        self.assertAlmostEqual(overlap_fraction(95.0, 105.0, pos, True), 0.493747, places=5)

    def test_overlap_fraction(self):
        pos = Pos(100.0, 110.0, 1.0, 0)
        self.assertEqual(overlap_fraction(101.0, 102.0, pos, True), 1.0)
        self.assertEqual(overlap_fraction(120.0, 125.0, pos, True), 0.0)
        part = overlap_fraction(105.0, 115.0, pos, True)
        self.assertTrue(0 < part < 1)
        self.assertEqual(overlap_fraction(100.0, 100.0, pos, True), 1.0)
        # downward move (stock input) mirrors it
        self.assertTrue(0 < overlap_fraction(105.0, 95.0, pos, False) < 1)


class SimulationTests(unittest.TestCase):
    def flat_market(self, n=400):
        # price oscillates +-0.1% around 100: always in range, no recenter needed
        out = []
        for i in range(n):
            out.append(tr(i * 60, 100.0 + (0.1 if i % 2 else -0.1), usd_in=20.0, usd_input=bool(i % 2)))
        return out

    def test_flat_market_earns_fees_and_never_recenters(self):
        trades = self.flat_market()
        others = [1000.0] * len(trades)
        res = simulate(trades, others, Oracle([(0, 100.0)]), 1000.0, Policy(), True, 1.0, 0.2, Costs(recenter_usd=1.0))
        self.assertEqual(res.recenters, 0)
        self.assertGreater(res.fees_gross_usd, 0)
        self.assertGreater(res.time_in_range, 0.99)
        self.assertEqual(res.gas_usd, 0.0)

    def test_dilution_lowers_fees_per_dollar(self):
        trades = self.flat_market()
        others = [1000.0] * len(trades)
        small = simulate(trades, others, Oracle([(0, 100.0)]), 100.0, Policy(), True)
        big = simulate(trades, others, Oracle([(0, 100.0)]), 100_000.0, Policy(), True)
        self.assertGreater(small.fees_gross_usd / small.size_usd, big.fees_gross_usd / big.size_usd)

    def test_trend_forces_recenters_subject_to_limits(self):
        trades = []
        price = 100.0
        for i in range(2000):
            price *= 1.0004
            trades.append(tr(i * 120, price, usd_in=20.0, usd_input=True))
        oracle = Oracle([(i * 120, 100.0 * 1.0004 ** (i + 1)) for i in range(2000)])
        res = simulate(trades, [1000.0] * len(trades), oracle, 1000.0, Policy(trigger=300, cooldown_s=3600, max_per_day=4), True, costs=Costs(recenter_usd=0.1))
        self.assertGreaterEqual(res.recenters, 1)
        days = res.seconds / 86400
        self.assertLessEqual(res.recenters, math.ceil(4 * days) + 1)  # the rolling daily cap holds
        self.assertAlmostEqual(res.gas_usd, 0.1 * res.recenters)

    def test_reserve_cut_removes_that_share_of_fees_from_the_net_result(self):
        trades = self.flat_market()
        others = [1000.0] * len(trades)
        none = simulate(trades, others, Oracle([(0, 100.0)]), 1000.0, Policy(), True, 1.0, 0.0)
        cut = simulate(trades, others, Oracle([(0, 100.0)]), 1000.0, Policy(), True, 1.0, 0.2)
        self.assertAlmostEqual(none.net_vs_hold_usd - cut.net_vs_hold_usd, 0.2 * none.fees_gross_usd, places=9)
        self.assertAlmostEqual(cut.fees_net_usd, 0.8 * cut.fees_gross_usd, places=9)
        self.assertAlmostEqual(none.fees_net_usd, none.fees_gross_usd, places=9)

    def test_start_split_changes_the_hold_benchmark(self):
        trades = [tr(i * 60, 100.0 * (1 - 0.001 * i), usd_in=1.0, fee_ppm=0) for i in range(100)]
        oracle = Oracle([(i * 60, 100.0 * (1 - 0.001 * i)) for i in range(100)])
        a = simulate(trades, [1000.0] * 100, oracle, 1000.0, Policy(trigger=10_000), True, start_stock_fraction=0.5)
        b = simulate(trades, [1000.0] * 100, oracle, 1000.0, Policy(trigger=10_000), True, start_stock_fraction=0.2)
        self.assertLess(a.hold_pnl, b.hold_pnl)   # more stock held in a falling market loses more
        self.assertAlmostEqual(a.hold_pnl / b.hold_pnl, 0.5 / 0.2, places=6)

    def test_stale_oracle_blocks_recentering(self):
        trades = []
        price = 100.0
        for i in range(600):
            price *= 1.001
            trades.append(tr(i * 120, price, usd_in=20.0))
        res = simulate(trades, [1000.0] * len(trades), Oracle([(0, 100.0)]), 1000.0, Policy(max_oracle_age_s=3600), True)
        self.assertEqual(res.recenters, 0)
        self.assertGreater(res.blocked_stale, 0)

    def test_dust_swap_with_a_zero_leg_does_not_divide_by_zero(self):
        trades = self.flat_market(50)
        trades[10] = Trade(block=600, idx=0, ts=600.0, stock_in=-0.0, usd_in=0.0, price=100.0, tick=0, liq_after=1.0, fee_ppm=3000, tx="0x")
        trades[11] = Trade(block=660, idx=0, ts=660.0, stock_in=-0.001, usd_in=0.0, price=100.0, tick=0, liq_after=1.0, fee_ppm=3000, tx="0x")
        res = simulate(trades, [1000.0] * 50, Oracle([(0, 100.0)]), 1000.0, Policy(), True)
        self.assertGreaterEqual(res.fees_gross_usd, 0.0)
        self.assertEqual(trades[11].exec_price, 100.0)

    def test_inventory_accounting_is_consistent_with_hold_for_a_pure_price_move_without_fees(self):
        # zero fee: the vault must lose to holding when price drifts away within the range (impermanent loss)
        trades = [tr(i * 60, 100.0 * (1 + 0.0005 * i), usd_in=1.0, fee_ppm=0) for i in range(200)]
        oracle = Oracle([(i * 60, 100.0 * (1 + 0.0005 * i)) for i in range(200)])
        res = simulate(trades, [1000.0] * 200, oracle, 1000.0, Policy(trigger=10_000), True)
        self.assertLess(res.pnl_before_fees_vs_hold, 0.0)
        self.assertEqual(res.fees_gross_usd, 0.0)


if __name__ == "__main__":
    unittest.main()
