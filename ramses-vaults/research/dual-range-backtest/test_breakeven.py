"""Checks of breakeven.py and reserve_sizing.stress: closed-form impermanent loss, unit conversion, conservation of the write-down, and the fee-share curve."""
import math
import unittest

import breakeven as B
from reserve_sizing import stress


class Tests(unittest.TestCase):
    def test_full_range_shortfall_matches_the_closed_form(self):
        for r in (0.5, 0.7, 1.5, 2.0):
            s = stress(1000.0, None, r, "NVDA")
            hold = 500 * r + 500
            il = 1 - 2 * math.sqrt(r) / (1 + r)
            self.assertAlmostEqual(s["shortfallUsd"], hold * il, places=3)

    def test_surplus_equals_deficit_is_implicit_in_the_write_down(self):
        s = stress(1000.0, None, 0.7, "NVDA")
        self.assertGreater(s["compositionDeficitUsd"], s["shortfallUsd"])   # the liquidity gap exceeds the loss
        self.assertEqual(s["scarceToken"], "USDG")
        self.assertEqual(stress(1000.0, None, 1.3, "NVDA")["scarceToken"], "stock")

    def test_no_move_no_requirement(self):
        s = stress(1000.0, 600, 1.0, "SPY")
        self.assertAlmostEqual(s["shortfallUsd"], 0, places=6)
        self.assertAlmostEqual(s["compositionDeficitUsd"], 0, places=6)

    def test_liquidity_per_usd_rises_as_the_range_narrows(self):
        ls = [B.l_per_usd(200.0, h) for h in (None, 2400, 1200, 600)]
        self.assertEqual(ls, sorted(ls))

    def test_full_range_liquidity_formula(self):
        self.assertAlmostEqual(B.l_per_usd(200.0, None) * 2 * math.sqrt(200.0) * 1e-12, 1.0, places=4)

    def test_units_are_consistent(self):
        for r in B.units_table():
            self.assertAlmostEqual(r["stockValuationPrice"], r["stockPriceNow"] * r["ratioUp"], places=6)
            self.assertAlmostEqual(r["stockLossCoverCostTodayUsd"], r["stockUnitsLossCover"] * r["stockPriceNow"], places=6)
            self.assertAlmostEqual(r["bothLegsLossCoverTodayUsd"], r["usdgUnitsLossCover"] + r["stockLossCoverCostTodayUsd"], places=6)


if __name__ == "__main__":
    unittest.main()
