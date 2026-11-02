import copy
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import quote_math as Q  # noqa: E402

USDG = "0xusdg"


def inputs(bands, tick, fee_ppm=3000, full=True, usdg_is_token0=True, window=(-5000, 5000)):
    """bands: list of (lowerTick, upperTick, liquidity). Current tick/price inside or between bands."""
    ticks = {}
    for lo, hi, L in bands:
        for t, net in ((lo, L), (hi, -L)):
            cur = int(ticks.get(str(t), {"liquidityNet": "0"})["liquidityNet"])
            ticks[str(t)] = {"liquidityGross": str(abs(net)), "liquidityNet": str(cur + net)}
    active = sum(L for lo, hi, L in bands if lo <= tick < hi)
    s = Q.tick_sqrt(tick + 0.5)
    return {"slot0": {"tick": tick, "sqrtPriceX96": str(int(s * Q.Q96))}, "liquidity": str(active), "fee_ppm": fee_ppm,
            "scan": {"mode": "full" if full else "window", "fromTick": Q.MIN_TICK if full else window[0], "toTick": Q.MAX_TICK if full else window[1]},
            "ticks": ticks, "dec0": 6 if usdg_is_token0 else 18, "dec1": 18 if usdg_is_token0 else 6,
            "token0": USDG if usdg_is_token0 else "0xstock", "token1": "0xstock" if usdg_is_token0 else USDG}


class SwapMath(unittest.TestCase):
    def test_single_band_matches_the_closed_form(self):
        inp = inputs([(-1000, 1000, 10 ** 20)], 0)
        st = Q.make_state(inp)
        s, L = st["s"], st["L"]
        gross = 1e15
        r = Q.swap(st, gross, True)
        net = gross * (1 - 0.003)
        s_new = 1 / (1 / s + net / L)
        self.assertEqual(r["status"], "filled")
        self.assertTrue(math.isclose(r["out"], L * (s - s_new), rel_tol=1e-12))
        self.assertTrue(math.isclose(st["s"], s_new, rel_tol=1e-12))

    def test_fee_is_taken_from_the_gross_input(self):
        a = Q.swap(Q.make_state(inputs([(-1000, 1000, 10 ** 20)], 0, fee_ppm=0)), 1e15, False)
        b = Q.swap(Q.make_state(inputs([(-1000, 1000, 10 ** 20)], 0, fee_ppm=10000)), 1e15, False)
        self.assertTrue(math.isclose(b["out"] / a["out"], 0.99, rel_tol=1e-3))

    def test_crossing_a_tick_changes_liquidity(self):
        inp = inputs([(-1000, 1000, 10 ** 20), (-100, 100, 10 ** 20)], 50)
        st = Q.make_state(inp)
        self.assertEqual(st["L"], 2e20)
        r = Q.swap(st, 1e30, False, sqrt_limit=Q.tick_sqrt(500))
        self.assertEqual(r["status"], "price_limit_rejection")
        self.assertEqual(st["L"], 1e20)         # crossed the +100 upper tick of the narrow band

    def test_zero_liquidity_gap_is_crossed_for_free(self):
        # band A [-500,-100] and band B [100,500] with nothing in between; the price starts inside A and we sell UP through the gap
        inp = inputs([(-500, -100, 10 ** 20), (100, 500, 10 ** 20)], -200)
        st = Q.make_state(inp)
        # buy enough to leave A, cross the gap, and trade inside B: the fill must continue through the gap
        big = 1e18
        r = Q.swap(st, big, False)
        self.assertEqual(r["status"], "filled", r)
        self.assertGreater(st["s"], Q.tick_sqrt(100))        # price ended inside band B
        self.assertEqual(st["L"], 1e20)
        # input needed to finish A, then B up to the end price, equals the sum of the two band legs; the gap contributed nothing
        sa = Q.tick_sqrt(-200 + 0.5)
        leg_a = 1e20 * (Q.tick_sqrt(-100) - sa) / (1 - 0.003)
        leg_b = big - leg_a
        s_end = Q.tick_sqrt(100) + leg_b * (1 - 0.003) / 1e20
        self.assertTrue(math.isclose(st["s"], s_end, rel_tol=1e-9))

    def test_old_behaviour_would_have_stopped_at_the_gap(self):
        # regression: the fill continues beyond the first band's end, so the consumed input exceeds band A's capacity
        inp = inputs([(-500, -100, 10 ** 20), (100, 500, 10 ** 20)], -200)
        st = Q.make_state(inp)
        cap_a = 1e20 * (Q.tick_sqrt(-100) - st["s"]) / (1 - 0.003)
        r = Q.swap(st, cap_a * 3, False)
        self.assertEqual(r["status"], "filled")
        self.assertAlmostEqual(r["consumed"], cap_a * 3, delta=1.0)

    def test_status_capacity_exhausted_only_on_a_full_scan(self):
        bands = [(-100, 100, 10 ** 18)]
        full = Q.swap(Q.make_state(inputs(bands, 0, full=True)), 1e30, False)
        win = Q.swap(Q.make_state(inputs(bands, 0, full=False, window=(-300, 300))), 1e30, False)
        self.assertEqual(full["status"], "capacity_exhausted")
        self.assertEqual(win["status"], "scan_window_exhausted")
        self.assertLess(full["filledFraction"], 1.0)

    def test_price_limit_rejection_is_distinct(self):
        st = Q.make_state(inputs([(-5000, 5000, 10 ** 18)], 0))
        r = Q.swap(st, 1e30, False, sqrt_limit=Q.tick_sqrt(200))
        self.assertEqual(r["status"], "price_limit_rejection")
        self.assertTrue(math.isclose(st["s"], Q.tick_sqrt(200), rel_tol=1e-12))

    def test_zero_liquidity_with_nothing_beyond_is_capacity_not_a_free_fill(self):
        st = Q.make_state(inputs([(-500, -100, 10 ** 20)], -200))
        r = Q.swap(st, 1e30, False)
        self.assertEqual(r["status"], "capacity_exhausted")


class Quotes(unittest.TestCase):
    def test_sequential_round_trip_is_not_the_sum_of_independent_quotes(self):
        inp = inputs([(-3000, 3000, 10 ** 17)], 0, fee_ppm=1000)
        q = Q.quote_all(inp, USDG, [100, 1000])
        for row in q["rows"]:
            seq, tot = row["sequentialRoundTrip"]["costBps"], row["sumOfIndependentOneWayQuotesBps"]
            self.assertIsNotNone(seq)
            self.assertLess(seq, tot)               # independent quotes each pay the impact of leaving the mid; a round trip gets it back
            self.assertGreater(seq, 2 * 10 - 1)      # but still pays the fee both ways (about 2 x 10 bp)

    def test_quote_is_a_pure_function_of_the_saved_inputs(self):
        inp = inputs([(-3000, 3000, 10 ** 17)], 0)
        self.assertEqual(Q.quote_all(copy.deepcopy(inp), USDG, [50, 500]), Q.quote_all(copy.deepcopy(inp), USDG, [50, 500]))

    def test_token_order_symmetry(self):
        a = Q.quote_all(inputs([(-3000, 3000, 10 ** 17)], 0, usdg_is_token0=True), USDG, [100])["rows"][0]
        b = Q.quote_all(inputs([(-3000, 3000, 10 ** 17)], 0, usdg_is_token0=False), USDG, [100])["rows"][0]
        self.assertGreater(a["buy"]["costBps"], 0)
        self.assertGreater(b["buy"]["costBps"], 0)

    def test_depth_distinguishes_unreachable_targets(self):
        inp = inputs([(-100, 100, 10 ** 18)], 0)       # +-1% of liquidity only
        d = {(x["direction"], x["movePct"]): x for x in Q.depth(inp, USDG)}
        self.assertEqual(d[("price_up_buy_stock", 5.0)]["status"], "capacity_exhausted")
        self.assertIsNone(d[("price_up_buy_stock", 5.0)]["inputUsd"])




class DepthPrecision(unittest.TestCase):
    def test_depth_input_matches_the_closed_form_and_is_not_lost_to_float_cancellation(self):
        inp = inputs([(-5000, 5000, 10 ** 18)], 0, fee_ppm=3000, usdg_is_token0=False)   # stock is token0, USDG token1
        st = Q.make_state(inp)
        d = {(x["direction"], x["movePct"]): x for x in Q.depth(inp, USDG)}
        up1 = d[("price_up_buy_stock", 1.0)]
        # buying the stock raises sqrt price by sqrt(1.01); net input = L * (s1 - s0); gross = net / (1 - fee); USDG has 6 decimals
        s0 = st["s"]
        expect = 1e18 * (s0 * (math.sqrt(1.01) - 1)) / (1 - 0.003) / 1e6
        self.assertGreater(up1["inputUsd"], 0)
        self.assertTrue(math.isclose(up1["inputUsd"], expect, rel_tol=1e-9), (up1, expect))

    def test_consumed_is_accumulated_not_gross_minus_remainder(self):
        st = Q.make_state(inputs([(-100, 100, 10 ** 18)], 0))
        r = Q.swap(st, 1e40, False)
        self.assertEqual(r["status"], "capacity_exhausted")
        self.assertGreater(r["consumed"], 0)
        self.assertLess(r["consumed"], 1e25)


if __name__ == "__main__":
    unittest.main()
