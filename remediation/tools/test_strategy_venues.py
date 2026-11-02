import unittest

from strategy_venues import capacity_sentence, week_chunks
from strategy_data import Trade


def row(size, net_usd, net_apr, fees_apr=10.0):
    return {"size_usd": size, "net_after_ops_usd": net_usd, "net_after_ops_apr_pct": net_apr, "fees_gross_apr_pct": fees_apr, "avg_deployed_fraction": 0.55}


class SentenceTests(unittest.TestCase):
    weekly = {"sizeUsd": 1000, "low": -5.0, "high": 12.0}

    def test_positive_case_names_the_largest_positive_size_and_the_edge(self):
        rows = [row(10, -1, -50), row(1000, 5, 6.0), row(10000, 40, 4.0), row(50000, -20, -1.0)]
        s = capacity_sentence("V", "P", rows, self.weekly, 24 * 86400, -11.5)
        self.assertIn("about $10,000", s)
        self.assertIn("4.0% net", s)
        self.assertIn("negative by $50,000", s)
        self.assertIn("-11.5%", s)
        self.assertIn("one window is not a forecast", s)

    def test_never_positive_names_the_best_case_and_does_not_blame_the_strategy_for_keeper_cost(self):
        rows = [row(10, -4, -640), row(1000, -3, -40), row(10000, -20, -18), row(50000, -80, -17.6)]
        s = capacity_sentence("V", "P", rows, self.weekly, 24 * 86400, None)
        self.assertIn("supports NO tested size", s)
        self.assertIn("-17.6% a year at $50,000", s)
        self.assertIn("keeper cost alone", s)
        self.assertIn("-640%", s)
        self.assertIn("ranged -5% to 12%", s)
        self.assertIn("55% of capital deployed", s)

    def test_weekly_chunks_split_on_time_and_drop_tiny_tails(self):
        trades = [Trade(block=i, idx=0, ts=float(i * 3600), stock_in=1.0, usd_in=-1.0, price=1.0, tick=0, liq_after=1.0, fee_ppm=1, tx="0x") for i in range(0, 24 * 22)]  # 22 days of hourly trades
        chunks = week_chunks(trades, [1.0] * len(trades))
        self.assertEqual(len(chunks), 3)  # three full weeks; the 1-day tail is dropped, never scored as a week
        self.assertTrue(all(len(c[0]) == len(c[1]) for c in chunks))


if __name__ == "__main__":
    unittest.main()
