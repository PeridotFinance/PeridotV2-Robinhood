"""Tests for feasibility.py and the engine fields it relies on (explicit trigger, ranged-value cap, reserve cut,
TickMath-exact full range). Each validatePolicy rule has a violating and a passing case."""
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "lib"))

import engine as E  # noqa: E402
import feasibility as F  # noqa: E402
from test_engine import always_open, make_dataset  # noqa: E402


def base(**kw):
    d = dict(name="t", core_half=600, wide_half=0, core_weight=1.0, trigger_ticks=300, cooldown=3600.0, max_per_day=24,
             loss_bps=10, max_ranged_usd=10_000.0, spacing=1, rebalance=False)
    d.update(kw)
    return E.Config(**d)


class PolicyRules(unittest.TestCase):
    def ok(self, **kw):
        spacing = kw.pop("spacing", 1)
        return F.assess(base(spacing=spacing, **kw), spacing)

    def test_baseline_is_feasible_for_every_pool_spacing(self):
        for pair, sp in F.POOL_SPACING.items():
            f = self.ok(spacing=sp)
            self.assertTrue(f.contract_feasible, (pair, f.violations, f.hypothetical))
            self.assertEqual(f.mode, "single_range_policy")

    def test_pool_spacings_are_the_measured_ones(self):
        self.assertEqual(F.POOL_SPACING, {"SPY": 1, "GLD": 1, "NVDA": 10})

    def test_half_width_bounds(self):
        self.assertFalse(self.ok(core_half=500, trigger_ticks=250).contract_feasible)   # the old "single +/-5%"
        self.assertFalse(self.ok(core_half=599, trigger_ticks=299).contract_feasible)
        self.assertTrue(self.ok(core_half=600).contract_feasible)
        self.assertTrue(self.ok(core_half=4800, trigger_ticks=2400).contract_feasible)
        self.assertFalse(self.ok(core_half=4801, trigger_ticks=2400).contract_feasible)

    def test_half_width_must_be_a_multiple_of_the_pools_spacing(self):
        self.assertFalse(self.ok(core_half=605, trigger_ticks=300, spacing=10).contract_feasible)
        self.assertTrue(self.ok(core_half=610, trigger_ticks=300, spacing=10).contract_feasible)
        self.assertTrue(self.ok(core_half=605, trigger_ticks=300, spacing=1).contract_feasible)

    def test_legacy_default_trigger_is_rejected(self):
        # engine default core_trigger_frac = 0.70: 600 ticks -> 420 > 300
        f = F.legacy_result_feasibility("x", dict(core_half=600, wide_half=0, cooldown=3600.0, max_per_day=24))
        self.assertFalse(f.contract_feasible)
        self.assertTrue(any("420" in v for v in f.violations), f.violations)
        # and a Config that leaves trigger_ticks unset is rejected outright
        self.assertFalse(F.assess(base(trigger_ticks=None), 1).contract_feasible)

    def test_trigger_bounds_use_integer_half(self):
        self.assertTrue(self.ok(trigger_ticks=300).contract_feasible)
        self.assertFalse(self.ok(trigger_ticks=301).contract_feasible)
        self.assertTrue(self.ok(core_half=601, trigger_ticks=300).contract_feasible)    # 601 // 2 = 300
        self.assertFalse(self.ok(core_half=601, trigger_ticks=301).contract_feasible)
        self.assertTrue(self.ok(trigger_ticks=1).contract_feasible)
        self.assertFalse(self.ok(trigger_ticks=0).contract_feasible)

    def test_trigger_must_reach_the_tick_spacing(self):
        self.assertFalse(self.ok(trigger_ticks=9, spacing=10, core_half=610).contract_feasible)
        self.assertTrue(self.ok(trigger_ticks=10, spacing=10, core_half=610).contract_feasible)

    def test_cooldown_bounds(self):
        self.assertFalse(self.ok(cooldown=900.0).contract_feasible)
        self.assertFalse(self.ok(cooldown=3599.0).contract_feasible)
        self.assertTrue(self.ok(cooldown=3600.0).contract_feasible)
        self.assertTrue(self.ok(cooldown=7 * 86400.0).contract_feasible)
        self.assertFalse(self.ok(cooldown=7 * 86400.0 + 1).contract_feasible)
        self.assertFalse(self.ok(cooldown=3600.5).contract_feasible)

    def test_rolling_limit_bounds(self):
        self.assertFalse(self.ok(max_per_day=0).contract_feasible)
        self.assertTrue(self.ok(max_per_day=1).contract_feasible)
        self.assertTrue(self.ok(max_per_day=24).contract_feasible)
        self.assertFalse(self.ok(max_per_day=25).contract_feasible)

    def test_loss_bound_and_cap(self):
        self.assertTrue(self.ok(loss_bps=100).contract_feasible)
        self.assertFalse(self.ok(loss_bps=101).contract_feasible)
        self.assertFalse(self.ok(loss_bps=10.5).contract_feasible)
        self.assertFalse(self.ok(max_ranged_usd=0.0).contract_feasible)
        self.assertFalse(self.ok(max_ranged_usd=None).contract_feasible)

    def test_engine_only_behaviour_is_hypothetical(self):
        dual = self.ok(wide_half=800, core_weight=0.7)
        self.assertFalse(dual.contract_feasible)
        self.assertEqual(dual.mode, "dual_layer")
        self.assertTrue(any("ONE ranged position" in h for h in dual.hypothetical))
        swap = self.ok(rebalance=True)
        self.assertFalse(swap.contract_feasible)
        self.assertTrue(any("no swap" in h for h in swap.hypothetical))

    def test_spacing_mismatch_is_rejected(self):
        f = F.assess(base(spacing=1), 10)
        self.assertEqual(f.mode, "invalid")
        self.assertFalse(f.contract_feasible)

    def test_require_feasible_raises_with_the_reasons(self):
        with self.assertRaises(F.InfeasibleConfig) as cm:
            F.require_feasible(base(core_half=500, trigger_ticks=250, cooldown=900.0), 1)
        msg = str(cm.exception)
        self.assertIn("halfWidthTicks 500", msg)
        self.assertIn("minInterval", msg)

    def test_historical_grid_rows_are_all_hypothetical(self):
        for core in (50, 100, 200, 300, 400):
            f = F.legacy_result_feasibility("d", dict(core_half=core, wide_half=500, cooldown=900.0, max_per_day=24))
            self.assertFalse(f.contract_feasible)
        for core in (100, 500):
            f = F.legacy_result_feasibility("s", dict(core_half=core, wide_half=0, cooldown=900.0, max_per_day=24))
            self.assertFalse(f.contract_feasible)


class InputHardening(unittest.TestCase):
    """Negative, non-finite and out-of-width values must be REJECTED (never crash, never pass a comparison)."""
    NAN, INF = float("nan"), float("inf")

    def bad(self, **kw):
        f = F.assess(base(**kw), 1)
        self.assertFalse(f.contract_feasible, kw)
        return f

    def test_negative_values_rejected_for_every_unsigned_field(self):
        for kw in (dict(max_per_day=-1), dict(max_per_day=-24), dict(core_half=-600, trigger_ticks=300), dict(trigger_ticks=-1),
                   dict(cooldown=-3600.0), dict(loss_bps=-1), dict(loss_bps=-0.5), dict(max_ranged_usd=-1.0)):
            f = self.bad(**kw)
            self.assertTrue(f.violations, kw)

    def test_negative_daily_limit_names_the_field(self):
        f = self.bad(max_per_day=-5)
        self.assertTrue(any("maxPerDay -5 is negative" in v for v in f.violations), f.violations)

    def test_nan_and_inf_rejected_without_exceptions(self):
        for field in ("max_per_day", "cooldown", "loss_bps", "max_ranged_usd", "trigger_ticks", "core_half"):
            for value in (self.NAN, self.INF, -self.INF):
                f = self.bad(**{field: value})
                self.assertTrue(any("not a finite number" in v or "must be > 0" in v for v in f.violations), (field, value, f.violations))

    def test_values_above_the_field_width_rejected(self):
        for kw in (dict(core_half=65536, trigger_ticks=300), dict(trigger_ticks=65536), dict(cooldown=2.0 ** 32),
                   dict(max_per_day=256), dict(loss_bps=65536)):
            f = self.bad(**kw)
            self.assertTrue(any("exceeds the field's maximum" in v for v in f.violations), (kw, f.violations))

    def test_huge_cap_and_non_numbers_rejected(self):
        self.bad(max_ranged_usd=1e30)
        self.bad(max_ranged_usd="10000")
        f = self.bad(core_half=True)      # bool is not a tick count
        self.assertTrue(any("not a finite number" in v for v in f.violations), f.violations)
        self.bad(max_per_day=None)
        self.assertEqual(F.validate_policy(600, 300, 3600, 24, 10, 1e6, 0)[0].split()[0], "tickSpacing")
        self.assertTrue(F.validate_policy(600, 300, 3600, 24, 10, 1e6, float("nan")))
        self.assertTrue(F.validate_policy(600, 300, 3600, 24, 10, 1e6, -1))

    def test_boundaries_still_accepted(self):
        for kw in (dict(max_per_day=1), dict(max_per_day=24), dict(loss_bps=0), dict(loss_bps=100), dict(cooldown=3600.0),
                   dict(max_ranged_usd=1e-9), dict(max_ranged_usd=F.MAX_RANGED_USD_E18)):
            self.assertTrue(F.assess(base(**kw), 1).contract_feasible, kw)

    def test_require_feasible_raises_instead_of_crashing_on_nan(self):
        with self.assertRaises(F.InfeasibleConfig):
            F.require_feasible(base(max_per_day=self.NAN), 1)


class FullRange(unittest.TestCase):
    def test_ticks_match_tickmath_usable_bounds(self):
        # Uniswap/Ramses TickMath: minUsableTick = (MIN_TICK / spacing) * spacing with truncation toward zero
        self.assertEqual(E.full_range_ticks(1), (-887272, 887272))
        self.assertEqual(E.full_range_ticks(10), (-887270, 887270))
        self.assertEqual(E.full_range_ticks(60), (-887220, 887220))
        for sp in (1, 10, 60, 200):
            lo, hi = E.full_range_ticks(sp)
            self.assertEqual((lo % sp, hi % sp), (0, 0))
            self.assertGreaterEqual(lo, -887272)
            self.assertLessEqual(hi, 887272)
            self.assertLess(-887272 - lo + sp, sp + 1)

    def test_full_range_assessment_per_pool(self):
        for pair, sp in F.POOL_SPACING.items():
            f = F.assess(E.Config(name="fr", full_range=True, core_half=0, wide_half=0, spacing=sp), sp)
            self.assertEqual(f.mode, "full_range")
            self.assertTrue(f.contract_feasible, (pair, f.violations))

    def test_full_range_violations_catch_the_legacy_off_by_one(self):
        # the legacy engine used floor(MIN)+spacing .. floor(MAX)-spacing, which is NOT the contract's range
        self.assertTrue(F.full_range_violations(-887271, 887271, 1))
        self.assertTrue(F.full_range_violations(-887260, 887260, 10))
        self.assertFalse(F.full_range_violations(-887270, 887270, 10))

    def test_engine_full_range_uses_the_exact_ticks(self):
        ds = make_dataset([100.0] * 20, stock_is_token0=False)
        cfg = E.Config(name="fr", capital_usd=10_000.0, gas_usd=0.0, ram_usd=0.0, full_range=True, core_half=0, wide_half=0,
                       core_weight=1.0, spacing=10)
        sim = E.Sim(ds, cfg, always_open).run()
        ly = sim.layers["core"]
        self.assertEqual((ly.lo, ly.hi), (-887270, 887270))


class EngineFields(unittest.TestCase):
    def cfg(self, **kw):
        b = dict(capital_usd=10_000.0, protocol_share=0.5, gas_usd=0.0, ram_usd=0.0, max_stale=12 * 3600.0, cooldown=900.0, wide_half=0,
                 core_weight=1.0)
        b.update(kw)
        return E.Config(**b)

    def test_explicit_trigger_replaces_the_fractional_default(self):
        # the oracle moves 4% (about 392 ticks): legacy 0.70 x 600 = 420 does NOT fire, an explicit 300 does
        prices = [100.0] * 10 + [104.0] * 40
        t0 = 1_000_000.0
        ds = make_dataset(prices, oracle_times=[t0 - 10, t0 + 5], oracle_prices=[100.0, 104.0], stock_is_token0=False)
        legacy = E.Sim(ds, self.cfg(core_half=600), always_open).run()
        explicit = E.Sim(ds, self.cfg(core_half=600, trigger_ticks=300, gate_bps=1000.0), always_open).run()
        self.assertEqual(legacy.recenters["core"], 0)
        self.assertGreater(explicit.recenters["core"], 0)

    def test_keeper_polling_delays_and_can_suppress_recenters(self):
        # oracle jumps 4% at t0+5; the pool follows immediately; swaps continue for 400 s every 10 s
        prices = [100.0] * 5 + [104.0] * 60
        t0 = 1_000_000.0
        ds = make_dataset(prices, oracle_times=[t0 - 10, t0 + 5], oracle_prices=[100.0, 104.0], stock_is_token0=False)
        instant = E.Sim(ds, self.cfg(core_half=600, trigger_ticks=300, gate_bps=1000.0), always_open).run()
        slow = E.Sim(ds, self.cfg(core_half=600, trigger_ticks=300, gate_bps=1000.0, poll_s=10_000.0), always_open).run()
        self.assertGreater(instant.recenters["core"], 0)
        self.assertEqual(slow.recenters["core"], 0)         # the only evaluation inside the window happens before the move matters
        mid = E.Sim(ds, self.cfg(core_half=600, trigger_ticks=300, gate_bps=1000.0, poll_s=100.0), always_open).run()
        first_instant = instant.recenter_log[0][0]
        first_mid = mid.recenter_log[0][0]
        self.assertGreaterEqual(first_mid, first_instant)

    def test_fee_stress_scales_or_replaces_the_fee_in_force(self):
        prices = [100.0] + [100.0 + 0.3 * math.sin(i / 3.0) for i in range(120)]
        base_ = E.Sim(make_dataset(prices, fee_ppm=100), self.cfg(core_half=600), always_open).run()
        x2 = E.Sim(make_dataset(prices, fee_ppm=100), self.cfg(core_half=600, fee_mult=2.0), always_open).run()
        fixed = E.Sim(make_dataset(prices, fee_ppm=100), self.cfg(core_half=600, fee_fixed_ppm=300.0), always_open).run()
        self.assertAlmostEqual(x2.fees_usd_cum / base_.fees_usd_cum, 2.0, delta=0.01)
        self.assertAlmostEqual(fixed.fees_usd_cum / base_.fees_usd_cum, 3.0, delta=0.02)
        mixed = E.Sim(make_dataset(prices, fee_ppm=900), self.cfg(core_half=600, fee_fixed_ppm=300.0), always_open).run()
        self.assertAlmostEqual(mixed.fees_usd_cum, fixed.fees_usd_cum, delta=fixed.fees_usd_cum * 1e-9)

    def test_ranged_value_cap_limits_deployment(self):
        prices = [100.0] * 20
        ds = make_dataset(prices, stock_is_token0=False)
        free = E.Sim(ds, self.cfg(core_half=600), always_open).run()
        capped = E.Sim(ds, self.cfg(core_half=600, max_ranged_usd=2_000.0), always_open).run()
        v_free = free.m.value(*E.amounts(free.layers["core"].L, free.s_end, free.layers["core"].lo, free.layers["core"].hi), 100.0)
        v_cap = capped.m.value(*E.amounts(capped.layers["core"].L, capped.s_end, capped.layers["core"].lo, capped.layers["core"].hi), 100.0)
        self.assertAlmostEqual(v_cap, 2_000.0, delta=20.0)
        self.assertGreater(v_free, 9_000.0)
        self.assertLess(capped.result()["deployed_frac_avg"], free.result()["deployed_frac_avg"])

    def test_reserve_cut_splits_fees_exactly(self):
        prices = [100.0] + [100.0 + 0.3 * math.sin(i / 3.0) for i in range(120)]
        a = E.Sim(make_dataset(prices), self.cfg(core_half=600, reserve_bps=0), always_open).run()
        b = E.Sim(make_dataset(prices), self.cfg(core_half=600, reserve_bps=2000), always_open).run()
        total_a = a.fees_usd_cum + a.reserve_usd_cum
        total_b = b.fees_usd_cum + b.reserve_usd_cum
        self.assertEqual(a.reserve_usd_cum, 0.0)
        self.assertAlmostEqual(total_a, total_b, delta=total_a * 1e-3)   # same gross fees (tiny redeploy difference)
        self.assertAlmostEqual(b.reserve_usd_cum / total_b, 0.20, places=3)
        self.assertLess(b.result()["pnl"], a.result()["pnl"])

    def test_result_reports_window_and_reserve(self):
        ds = make_dataset([100.0] * 30)
        r = E.Sim(ds, self.cfg(core_half=600, reserve_bps=2000), always_open).run().result()
        for k in ("reserve_usd", "fees_before_reserve_usd", "start_ts", "end_ts"):
            self.assertIn(k, r)


if __name__ == "__main__":
    unittest.main()
