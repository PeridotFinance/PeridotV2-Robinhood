import math
import unittest

from evidence_lib import EvidenceError
from strategy_data import Trade
from strategy_pool import SwapRecord, depth_usd, fee_share, range_overlap_fraction, replay_v4, tick_to_usd_price


INIT = {"tick": 0, "sqrt": 1 << 96}


def trade(block, idx, tick, liq_after_human, fee_ppm=3000, usd_in=100.0, stock_in=-0.5):
    return Trade(block=block, idx=idx, ts=float(block), stock_in=stock_in, usd_in=usd_in, price=200.0, tick=tick,
                 liq_after=liq_after_human, fee_ppm=fee_ppm, tx="0x")


def raw(t, liq_raw):
    return (None, 0, 0, 1 << 96, liq_raw, t.tick, t.fee_ppm)


def mod(block, idx, lower, upper, delta, salt=0):
    return {"block": block, "idx": idx, "sender": "0xpm", "lower": lower, "upper": upper, "delta": delta, "salt": salt, "tx": "0x"}


class ReplayTests(unittest.TestCase):
    def test_active_liquidity_and_our_share(self):
        mods = [mod(1, 0, -100, 100, 1000), mod(1, 1, -50, 50, 100, salt=7)]
        s1 = trade(2, 0, 10, 0)
        s2 = trade(3, 0, 80, 0)
        raws = [raw(s1, 1100), raw(s2, 1000)]
        recs, book = replay_v4(INIT, [s1, s2], raws, mods, {7})
        recs = [r for r in recs if r.kind == "swap"]
        # first swap: pool holds 1000 + our 100, tick 0 is inside both ranges, path 0 -> 10 inside ours
        self.assertEqual(recs[0].liq_before_raw, 1100)
        self.assertEqual(recs[0].our_liq_raw, 100)
        self.assertAlmostEqual(recs[0].our_overlap, 1.0)
        self.assertAlmostEqual(fee_share(recs[0]), 100 / 1100)
        # second swap: tick 10 -> 80; only 10..50 of the 70-tick path is inside our range
        self.assertAlmostEqual(recs[1].our_overlap, 40 / 70)
        self.assertAlmostEqual(fee_share(recs[1]), (40 / 70) * 100 / 1100)  # liq_before comes from the previous swap event
        self.assertEqual(len(book.live()), 1)

    def test_out_of_range_modification_does_not_change_active_liquidity(self):
        mods = [mod(1, 0, -100, 100, 1000), mod(1, 1, 500, 600, 9999)]
        s1 = trade(2, 0, 5, 0)
        recs, _ = replay_v4(INIT, [s1], [raw(s1, 1000)], mods, set())
        self.assertEqual(recs[0].liq_before_raw, 1000)
        self.assertEqual(recs[0].our_liq_raw, 0)
        self.assertEqual(fee_share(recs[0]), 0.0)

    def test_position_changes_are_recorded_as_samples_with_the_last_price(self):
        mods = [mod(1, 0, -100, 100, 1000), mod(1, 1, -50, 50, 100, salt=7), mod(5, 0, -50, 50, -100, salt=7)]
        s1 = trade(2, 0, 10, 0)
        recs, book = replay_v4(INIT, [s1], [raw(s1, 1100)], mods, {7}, record_from_block=2)
        kinds = [(r.block, r.kind, len(r.positions)) for r in recs]
        self.assertEqual(kinds, [(2, "swap", 1), (5, "position", 0)])  # the block-1 add is before the window
        self.assertEqual(recs[1].sqrt_after, 1.0)
        self.assertEqual(book.live(), [])

    def test_scaling_dilutes_the_share(self):
        rec = SwapRecord(1, 0, 1.0, 0, 5, liq_before_raw=1100, our_liq_raw=100, our_overlap=1.0, fee_usd=1.0, price=200.0, notional=100.0)
        base = fee_share(rec, 1.0)
        big = fee_share(rec, 10.0)
        self.assertAlmostEqual(base, 100 / 1100)
        self.assertAlmostEqual(big, 1000 / (1000 + 1000))
        self.assertLess(big / 10.0, base)  # per unit of our liquidity the income falls as we grow

    def test_length_mismatch_fails(self):
        with self.assertRaises(EvidenceError):
            replay_v4(INIT, [trade(1, 0, 0, 0)], [], [], set())

    def test_overlap_fraction_edges(self):
        self.assertEqual(range_overlap_fraction(0, 0, -1, 1), 1.0)
        self.assertEqual(range_overlap_fraction(5, 5, -1, 1), 0.0)
        self.assertAlmostEqual(range_overlap_fraction(-10, 10, -5, 5), 0.5)
        self.assertEqual(range_overlap_fraction(20, 30, -5, 5), 0.0)
        self.assertAlmostEqual(range_overlap_fraction(10, -10, -5, 5), 0.5)


class DepthTests(unittest.TestCase):
    def test_depth_formula(self):
        up, down = depth_usd(100.0, 1000.0, 0.01)
        self.assertAlmostEqual(up, 1000 * (math.sqrt(101) - 10), places=6)
        self.assertAlmostEqual(down, 1000 * (1 / math.sqrt(99) - 1 / 10) * 100, places=6)
        self.assertEqual(depth_usd(100.0, 0.0), (0.0, 0.0))

    def test_tick_price_orientation(self):
        # stock token1 (USDG token0): price falls as the tick rises
        self.assertGreater(tick_to_usd_price(100, False), tick_to_usd_price(200, False))
        self.assertLess(tick_to_usd_price(100, True), tick_to_usd_price(200, True))


if __name__ == "__main__":
    unittest.main()
