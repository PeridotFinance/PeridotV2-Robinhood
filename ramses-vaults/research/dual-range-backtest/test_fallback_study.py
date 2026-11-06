import unittest

from fallback_study import Twap, q, summarize


class TwapTests(unittest.TestCase):
    def setUp(self):
        # tick 100 until t=1000, then 300 until t=2000, then 100 again
        self.tw = Twap("SPY", cols={"ts": [0, 1000, 2000], "tick_after": [100, 300, 100]}, stock_is_token0=True)

    def test_constant_window(self):
        self.assertAlmostEqual(self.tw.avg_tick(900, 600), 100)

    def test_step_is_time_weighted(self):
        # window [1500-1000, 1500] = 500 s at 100 + 500 s at 300
        self.assertAlmostEqual(self.tw.avg_tick(1500, 1000), 200)

    def test_short_spike_barely_moves_the_mean(self):
        tw = Twap("SPY", cols={"ts": [0, 1000, 1012], "tick_after": [0, 1000, 0]}, stock_is_token0=True)
        self.assertAlmostEqual(tw.avg_tick(1800, 1800), 1000 * 12 / 1800)

    def test_window_clamped_to_history_start(self):
        self.assertAlmostEqual(self.tw.avg_tick(500, 10_000), 100)

    def test_stats_helpers(self):
        self.assertEqual(q([3, 1, 2], 0.5), 2)
        s = summarize([-0.02, 0.01, 0.0])
        self.assertAlmostEqual(s["max"], 0.02)
        self.assertEqual(s["n"], 3)


if __name__ == "__main__":
    unittest.main()
