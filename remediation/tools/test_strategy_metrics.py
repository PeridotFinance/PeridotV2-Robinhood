import math
import unittest

from evidence_lib import EvidenceError
from strategy_data import Oracle, Trade
from strategy_metrics import YEAR_S, apr, apy, irr, liquidity_scenario, markouts, twr


def tr(ts, price, stock_in, usd_in, fee_ppm=3000):
    return Trade(block=int(ts), idx=0, ts=float(ts), stock_in=stock_in, usd_in=usd_in, price=price, tick=0, liq_after=1.0, fee_ppm=fee_ppm, tx="0x")


class ReturnTests(unittest.TestCase):
    def test_apr_and_apy(self):
        self.assertAlmostEqual(apr(0.01, YEAR_S / 12), 0.12)
        self.assertAlmostEqual(apy(0.01, YEAR_S / 12), 1.01 ** 12 - 1)
        self.assertEqual(apy(-1.5, 100), -1.0)
        with self.assertRaises(EvidenceError):
            apr(0.1, 0)

    def test_twr_chains_subperiods(self):
        self.assertAlmostEqual(twr([1.0, 1.1, 1.21]), 0.21)
        with self.assertRaises(EvidenceError):
            twr([1.0])

    def test_irr_matches_closed_form_and_differs_from_twr_with_flows(self):
        # one deposit of 100 growing 10% in exactly a year
        self.assertAlmostEqual(irr([(0.0, 100.0)], YEAR_S, 110.0), 0.10, places=6)
        # money-weighted return penalises a late deposit made just before a loss: TWR can be 0, IRR negative
        flows = [(0.0, 100.0), (YEAR_S / 2, 100.0)]
        r = irr(flows, YEAR_S, 200.0 - 5.0)
        self.assertLess(r, 0.0)

    def test_irr_requires_bracket(self):
        with self.assertRaises(EvidenceError):
            irr([(0.0, 100.0)], YEAR_S, -1.0)


class MarkoutTests(unittest.TestCase):
    def test_signs_and_horizon(self):
        # LP buys 1 stock (swapper sold stock) leaving the pool at 100; a minute later the pool is at 101 -> +1 USD
        trades = [tr(0, 100.0, 1.0, -100.0), tr(60, 101.0, -0.5, 50.5), tr(4000, 90.0, 0.1, -9.0)]
        row = markouts(trades, (60,))["60"]["total"]
        self.assertEqual(row["trades"], 2)  # the last trade has no full horizon of history after it
        # trade0: +1 * (101 - 100) * 1 = +1 ; trade1: LP sold 0.5, nothing traded after it -> mark == its own price -> 0
        self.assertAlmostEqual(row["pool_markout_usd"], 1.0)

    def test_the_fee_is_not_counted_inside_the_markout(self):
        # fee-inclusive execution price is 100.3 but the pool mid stays 100.0 afterwards: the markout must be zero
        trades = [tr(0, 100.0, 1.0, -100.3), tr(10, 100.0, 0.0, 0.0), tr(4000, 100.0, 0.0, 0.0)]
        r = markouts(trades, (60,))["60"]["total"]
        self.assertAlmostEqual(r["pool_markout_usd"], 0.0)
        self.assertGreater(r["fee_usd"], 0.0)

    def test_oracle_markout_is_the_oracle_move_after_the_trade(self):
        oracle = Oracle([(0, 100.0), (30, 99.0)])
        trades = [tr(0, 100.0, 1.0, -100.0), tr(100, 100.0, 0.0, 0.0)]
        out = markouts(trades, (60,), oracle)
        self.assertAlmostEqual(out["60"]["total"]["oracle_markout_usd"], -1.0)
        # a frozen oracle shows no information arriving
        frozen = markouts(trades, (60,), Oracle([(0, 100.0)]))
        self.assertAlmostEqual(frozen["60"]["total"]["oracle_markout_usd"], 0.0)

    def test_adverse_selection_shows_against_fee(self):
        # swapper sells 1 stock; the pool mid then drops to 99.9: the LP bought before the fall and loses 0.1
        trades = [tr(0, 100.0, 1.0, -100.0), tr(10, 99.9, 0.0, 0.0), tr(4000, 99.9, 0.0, 0.0)]
        r = markouts(trades, (60,))["60"]["total"]
        self.assertAlmostEqual(r["pool_markout_usd"], -0.1)


class ScenarioTests(unittest.TestCase):
    def test_worked_example_from_the_shared_model(self):
        r = liquidity_scenario(100, 0.5, 0.4, 0.1, 25)
        self.assertEqual(r["status"], "paid")
        self.assertAlmostEqual(r["fromCash"], 10)
        self.assertAlmostEqual(r["fromStrategy"], 15)
        self.assertAlmostEqual(r["after"]["strategy"], 35)
        self.assertAlmostEqual(r["after"]["cash"], 0)
        self.assertAlmostEqual(r["after"]["loans"], 40)
        self.assertTrue(r["newBorrowingBlocked"])

    def test_strategy_shortfall_reverts_with_balances_intact(self):
        r = liquidity_scenario(100, 0.5, 0.4, 0.1, 25, strategy_returns=12)
        self.assertEqual(r["status"], "reverted")
        self.assertEqual(r["before"], r["after"])

    def test_bad_percentages_fail(self):
        with self.assertRaises(EvidenceError):
            liquidity_scenario(100, 0.5, 0.4, 0.2, 1)


if __name__ == "__main__":
    unittest.main()
