import types
import unittest

from strategy_data import Oracle
from strategy_metrics import liquidity_scenario
from strategy_returns import delta_matched_pnl, exchange_rate, lvr_shortfall, mark_to_end

P = 200.0
E18 = 10 ** 18


def ctx(stock_principal, usdg_principal, stock_idle, usdg_idle, liquidity=0, pending=(0, 0), reserve_bps=2000, last_ckpt=0, now=1000):
    snap = {"timestamp": now,
            "vault": {"ledger": {"stockPrincipal": stock_principal, "usdgPrincipal": usdg_principal, "stockIdle": stock_idle,
                                 "usdgIdle": usdg_idle, "lastCheckpoint": last_ckpt}, "reserveFeeBps": reserve_bps, "maxCheckpointAge": 3600},
            "position": {"liquidity": liquidity, "tickLower": 0, "tickUpper": 60, "pendingFeesUsdgRaw": pending[0], "pendingFeesStockRaw": pending[1]}}
    return types.SimpleNamespace(snap=snap, oracle=Oracle([(0, P)]), t1=now)


class MarkToEndTests(unittest.TestCase):
    def test_a_shortfall_scales_both_principals_pro_rata(self):
        # principals worth 1 NVDA ($200) + $200 = $400; assets are only 0.9 NVDA + $180 = $360 -> scale 0.9
        c = ctx(1 * E18, 200_000_000, 9 * 10 ** 17, 180_000_000)
        m = mark_to_end(c)
        self.assertEqual(m["outcome"], "loss_recognised")
        self.assertAlmostEqual(m["shortfallUsd"], 40.0)
        self.assertAlmostEqual(m["shortfallPctOfBenchmark"], 10.0)
        self.assertAlmostEqual(m["stockPrincipalMarked"] / E18, 0.9, places=9)
        self.assertAlmostEqual(m["usdgPrincipalMarked"] / 1e6, 180.0, places=6)

    def test_checkpoint_age_flag(self):
        self.assertFalse(mark_to_end(ctx(E18, 200_000_000, E18, 200_000_000, last_ckpt=0, now=3000))["checkpointOverdue"])
        self.assertTrue(mark_to_end(ctx(E18, 200_000_000, E18, 200_000_000, last_ckpt=0, now=4000))["checkpointOverdue"])

    def test_pending_fees_enter_net_of_the_reserve_cut_on_both_sides(self):
        # fully covered: assets = principals + 80% of the pending fees on each side, so a gain is recognised
        c = ctx(E18, 200_000_000, E18, 200_000_000, pending=(1_000_000, 10 ** 16))
        m = mark_to_end(c)
        self.assertEqual(m["outcome"], "gain_recognised")
        self.assertEqual(m["usdgPrincipalMarked"], 200_000_000 + 800_000)   # 1 USDG pending x 80%
        self.assertEqual(m["stockPrincipalMarked"], E18 + 8 * 10 ** 15)

    def test_no_change_when_assets_equal_principals_with_no_fees(self):
        m = mark_to_end(ctx(E18, 200_000_000, E18, 200_000_000))
        self.assertEqual(m["outcome"], "gain_recognised")  # nothing to add: principals are set to assets, which are equal
        self.assertEqual(m["stockPrincipalMarked"], E18)


class BenchmarkTests(unittest.TestCase):
    def sample(self, stock, price):
        return {"idle": [stock, 0.0], "lpIn": [0.0, 0.0], "lpOut": [0.0, 0.0], "pOracle": price}

    def test_delta_matched_uses_the_quantity_at_the_start_of_each_interval(self):
        s = [self.sample(1.0, 100.0), self.sample(2.0, 110.0), self.sample(2.0, 99.0)]
        # interval 1 earns 1.0 x (110-100) = 10 ; interval 2 earns 2.0 x (99-110) = -22
        self.assertAlmostEqual(delta_matched_pnl(s), -12.0)

    def test_sign_matters_a_falling_price_with_a_long_book_is_a_loss(self):
        self.assertLess(delta_matched_pnl([self.sample(1.0, 100.0), self.sample(1.0, 90.0)]), 0)


class ShortfallTests(unittest.TestCase):
    def test_lvr_is_positive_when_the_pair_earned_less_than_the_benchmark(self):
        self.assertAlmostEqual(lvr_shortfall(benchmark_pnl=5.0, inventory_pnl_ex_fees=-2.0), 7.0)
        self.assertAlmostEqual(lvr_shortfall(benchmark_pnl=-3.0, inventory_pnl_ex_fees=-1.0), -2.0)  # earned more: negative shortfall


class ExchangeRateTests(unittest.TestCase):
    def test_formula(self):
        # (cash 10 + claim 20 + borrows 5 - reserves 1) = 34 underlying; over 34 pTokens the rate is exactly 1e18, over 17 it is 2e18
        self.assertEqual(exchange_rate(10, 20, 5, 1, 34), 10 ** 18)
        self.assertEqual(exchange_rate(10, 20, 5, 1, 17), 2 * 10 ** 18)


class FloorTests(unittest.TestCase):
    def test_borrowing_is_blocked_only_when_cash_falls_below_the_ten_percent_floor(self):
        self.assertFalse(liquidity_scenario(100, 0.5, 0.4, 0.1, 0)["newBorrowingBlocked"])    # cash 10 vs floor 10: not below
        self.assertTrue(liquidity_scenario(100, 0.5, 0.4, 0.1, 5)["newBorrowingBlocked"])     # cash 5 vs floor 9.5


if __name__ == "__main__":
    unittest.main()
