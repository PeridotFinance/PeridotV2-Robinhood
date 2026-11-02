import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "lib"))

import engine as E  # noqa: E402

Q = E.Q
WEEKDAY = 1793000000 - (1793000000 % 86400) + 3 * 86400  # some timestamp; session_of is replaced by a stub below


def always_open(ts):
    return "regular"


def make_dataset(prices, stock_is_token0=True, L_tp=2e18, fee_ppm=100, step=10.0, t0=1_000_000.0, oracle_prices=None,
                 oracle_times=None, in_tok=None):
    """Build a Dataset whose pool price follows ``prices`` (USD per stock), one swap per element."""
    m = E.Market(stock_is_token0)
    ts, s_after, tick_after, L_after, itok, iamt, fees = [], [], [], [], [], [], []
    prev = None
    for i, p in enumerate(prices):
        s = m.sqrt_for_usd(p)
        ts.append(t0 + i * step)
        s_after.append(s)
        tick_after.append(int(math.floor(E.sqrt_tick(s))))
        L_after.append(L_tp)
        if in_tok is not None:
            itok.append(in_tok[i])
        else:
            # direction of the swap: token0 in when sqrt price falls
            itok.append(0 if (prev is not None and s < prev) else 1)
        iamt.append(0.0)
        fees.append(fee_ppm)
        prev = s
    ot = oracle_times if oracle_times is not None else [t0 - 10.0]
    op = oracle_prices if oracle_prices is not None else [prices[0]]
    return E.Dataset(ts, s_after, tick_after, L_after, itok, iamt, fees, ot, op, m)


class GeometryTests(unittest.TestCase):
    def test_amounts_roundtrip(self):
        s = E.tick_sqrt(1000)
        L = E.liquidity_for(5e9, 7e9, s, 900, 1100)
        a0, a1 = E.amounts(L, s, 900, 1100)
        self.assertLessEqual(a0, 5e9 * (1 + 1e-9))
        self.assertLessEqual(a1, 7e9 * (1 + 1e-9))
        self.assertTrue(math.isclose(a0, 5e9, rel_tol=1e-9) or math.isclose(a1, 7e9, rel_tol=1e-9))

    def test_new_range_shape_matches_contract(self):
        lo, hi = E.new_range(-209992.4, 100, 1)
        self.assertEqual((lo, hi), (-209993 - 100, -209993 + 100 + 1))
        lo, hi = E.new_range(222987.7, 100, 10)
        self.assertEqual((hi - lo), 2 * 100 + 10)
        self.assertEqual(lo % 10, 0)

    def test_market_price_roundtrip_both_orders(self):
        for flag in (True, False):
            m = E.Market(flag)
            s = m.sqrt_for_usd(763.79)
            self.assertAlmostEqual(m.usd_per_stock(s), 763.79, places=6)


class SimTests(unittest.TestCase):
    def cfg(self, **kw):
        base = dict(capital_usd=10_000.0, protocol_share=0.5, gas_usd=0.0, ram_usd=0.0, max_stale=12 * 3600.0, cooldown=900.0)
        base.update(kw)
        return E.Config(**base)

    def test_flat_price_conserves_equity(self):
        ds = make_dataset([100.0] * 50)
        sim = E.Sim(ds, self.cfg(), always_open).run()
        r = sim.result()
        self.assertAlmostEqual(r["equity_end"], 10_000.0, delta=1.0)
        self.assertEqual(r["recenters_core"] + r["recenters_wide"], 0)

    def test_fee_accrual_matches_analytic_formula(self):
        # one swap that pushes the price up 0.2% inside the core range: token1 (USDG) is the input
        prices = [100.0, 100.0, 100.2]
        ds = make_dataset(prices, in_tok=[1, 1, 1])
        cfg = self.cfg(core_weight=1.0, wide_half=0)
        sim = E.Sim(ds, cfg, always_open).run()
        s0, s1 = ds.s_after[1], ds.s_after[2]
        Lo = sim.layers["core"].L
        L_tp = 2e18
        expected_vol = L_tp * (s1 - s0)
        expected_fee = expected_vol * (Lo / (L_tp + Lo)) * 100e-6 * 0.5
        self.assertTrue(math.isclose(sim.fee1, expected_fee, rel_tol=1e-9), (sim.fee1, expected_fee))
        self.assertEqual(sim.fee0, 0.0)

    def test_fee_scales_with_capital_and_dilutes(self):
        prices = [100.0] + [100.0 + 0.3 * math.sin(i / 3.0) for i in range(300)]
        small = E.Sim(make_dataset(prices), self.cfg(capital_usd=1_000.0, wide_half=0, core_weight=1.0), always_open).run()
        big = E.Sim(make_dataset(prices), self.cfg(capital_usd=1_000_000.0, wide_half=0, core_weight=1.0), always_open).run()
        f_small = small.fees_usd_cum / 1_000.0
        f_big = big.fees_usd_cum / 1_000_000.0
        self.assertGreater(f_small, 0)
        self.assertLess(f_big, f_small * 0.8, "fees per dollar must fall when our liquidity is a large share of the pool")

    def test_protocol_share_is_removed_from_fees(self):
        prices = [100.0] + [100.0 + 0.3 * math.sin(i / 3.0) for i in range(100)]
        a = E.Sim(make_dataset(prices), self.cfg(protocol_share=0.5, wide_half=0, core_weight=1.0), always_open).run()
        b = E.Sim(make_dataset(prices), self.cfg(protocol_share=0.0, wide_half=0, core_weight=1.0), always_open).run()
        z = E.Sim(make_dataset(prices), self.cfg(protocol_share=1.0, wide_half=0, core_weight=1.0), always_open).run()
        self.assertAlmostEqual(a.fees_usd_cum * 2.0, b.fees_usd_cum, delta=b.fees_usd_cum * 1e-6)
        self.assertEqual(z.fees_usd_cum, 0.0)

    def test_stale_oracle_blocks_recenter(self):
        prices = [100.0] * 5 + [104.0] * 20  # +4% jump, far beyond core and trigger
        t0 = 1_000_000.0
        fresh = make_dataset(prices, oracle_times=[t0 - 10, t0 + 40], oracle_prices=[100.0, 104.0])
        stale = make_dataset(prices, oracle_times=[t0 - 10], oracle_prices=[100.0])
        a = E.Sim(fresh, self.cfg(max_stale=45.0), always_open).run()
        b = E.Sim(stale, self.cfg(max_stale=45.0), always_open).run()  # feed never updates: stale from t0+35
        self.assertGreater(a.recenters["core"], 0)
        self.assertEqual(b.recenters["core"] + b.recenters["wide"], 0)

    def test_closed_session_blocks_recenter(self):
        prices = [100.0] * 5 + [104.0] * 20
        t0 = 1_000_000.0
        ds = make_dataset(prices, oracle_times=[t0 - 10, t0 + 40], oracle_prices=[100.0, 104.0])
        sim = E.Sim(ds, self.cfg(), lambda ts: "weekend" if ts >= t0 + 40 else "regular").run()
        self.assertEqual(sim.recenters["core"], 0)

    def test_gate_blocks_when_pool_far_from_oracle(self):
        prices = [100.0] * 5 + [104.0] * 20
        t0 = 1_000_000.0
        # oracle says 101.0 (fresh, moved 1% > trigger) but the pool is 3% away: gate is 150 bps
        ds = make_dataset(prices, oracle_times=[t0 - 10, t0 + 55], oracle_prices=[100.0, 101.0])
        sim = E.Sim(ds, self.cfg(), always_open).run()
        self.assertEqual(sim.recenters["core"], 0)
        self.assertGreater(sim.blocked["gate"], 0)

    def test_cooldown_limits_recenters(self):
        # oracle zigzags beyond the trigger every 100 s; pool follows; cooldown 900 s allows far fewer recenters
        n = 200
        prices = [100.0 + (1.5 if (i // 10) % 2 else 0.0) for i in range(n)]
        t0 = 1_000_000.0
        otimes = [t0 - 10] + [t0 + 100 * k for k in range(1, 20)]
        oprices = [100.0] + [100.0 + (1.5 if k % 2 else 0.0) for k in range(1, 20)]
        ds = make_dataset(prices, oracle_times=otimes, oracle_prices=oprices)
        fast = E.Sim(ds, self.cfg(cooldown=1.0, wide_half=0, core_weight=1.0), always_open).run()
        slow = E.Sim(ds, self.cfg(cooldown=900.0, wide_half=0, core_weight=1.0), always_open).run()
        self.assertGreater(fast.recenters["core"], slow.recenters["core"])
        self.assertLessEqual(slow.recenters["core"], 3)

    def test_rolling_budget_caps_recenters(self):
        n = 400
        prices = [100.0 + (1.5 if (i // 5) % 2 else 0.0) for i in range(n)]
        t0 = 1_000_000.0
        otimes = [t0 - 10] + [t0 + 10 * k for k in range(1, 40)]
        oprices = [100.0] + [100.0 + (1.5 if (k // 1) % 2 else 0.0) for k in range(1, 40)]
        ds = make_dataset(prices, oracle_times=otimes, oracle_prices=oprices)
        sim = E.Sim(ds, self.cfg(cooldown=1.0, max_per_day=3, wide_half=0, core_weight=1.0), always_open).run()
        self.assertLessEqual(sim.recenters["core"], 3)
        self.assertGreater(sim.blocked["budget"], 0)

    def test_recenter_keeps_value_and_leaves_unmatched_idle(self):
        # price steps up 3%: the position is all USDG, the recenter can pair only part of it
        prices = [100.0] * 5 + [103.0] * 40
        t0 = 1_000_000.0
        ds = make_dataset(prices, oracle_times=[t0 - 10, t0 + 45], oracle_prices=[100.0, 103.0])
        sim = E.Sim(ds, self.cfg(wide_half=0, core_weight=1.0, loss_bps=10.0), always_open).run()
        self.assertEqual(sim.recenters["core"], 1)
        price = sim.m.usd_per_stock(sim.s_end)
        a0, a1 = sim.holdings(sim.s_end)
        eq = sim.m.value(a0, a1, price)
        self.assertAlmostEqual(eq, sim.equity0 * 1.0, delta=sim.equity0 * 0.03)  # within IL of a 3% step
        # unmatched remainder is idle, not swapped away: idle stock is zero (the stock sold out of the range)
        stock_idle = sim.idle0 if sim.m.stock_is_token0 else sim.idle1
        usdg_idle = sim.idle1 if sim.m.stock_is_token0 else sim.idle0
        self.assertGreater(usdg_idle, 100e6, "unmatched USDG stays idle")
        self.assertLess(stock_idle / 1e18 * price, 1.0)

    def test_il_nonnegative_without_fees(self):
        # round trip of the price with fees switched off: LP can not beat holding (no free lunch)
        up = [100.0 + i * 0.02 for i in range(100)]
        prices = [100.0] + up + up[::-1]
        ds = make_dataset(prices)
        sim = E.Sim(ds, self.cfg(protocol_share=1.0, wide_half=0, core_weight=1.0), always_open).run()
        r = sim.result()
        self.assertLessEqual(r["pnl_vs_hold"], 1e-6 * 10_000)

    def test_token_order_mirror_equivalence(self):
        prices = [100.0] + [100.0 + 0.8 * math.sin(i / 7.0) + i * 0.01 for i in range(400)]
        otimes = [1_000_000.0 - 10]
        a = make_dataset(prices, stock_is_token0=True, oracle_times=otimes, oracle_prices=[100.0])
        # mirror: swap direction flips with the token order
        in_tok_b = [1 - x for x in a.in_tok]
        b = make_dataset(prices, stock_is_token0=False, oracle_times=otimes, oracle_prices=[100.0], in_tok=in_tok_b)
        ra = E.Sim(a, self.cfg(), always_open).run().result()
        rb = E.Sim(b, self.cfg(), always_open).run().result()
        self.assertTrue(math.isclose(ra["equity_end"], rb["equity_end"], rel_tol=2e-4), (ra["equity_end"], rb["equity_end"]))
        self.assertTrue(math.isclose(ra["fees_usd"], rb["fees_usd"], rel_tol=5e-3), (ra["fees_usd"], rb["fees_usd"]))

    def test_full_range_never_recenters(self):
        prices = [100.0] * 3 + [130.0] * 30
        t0 = 1_000_000.0
        ds = make_dataset(prices, oracle_times=[t0 - 10, t0 + 30], oracle_prices=[100.0, 130.0])
        sim = E.Sim(ds, self.cfg(full_range=True, wide_half=0, core_half=0), always_open).run()
        self.assertEqual(sum(sim.recenters.values()), 0)
        self.assertGreater(sim.layers["core"].L, 0)

    def test_gas_is_charged_per_layer_recenter(self):
        prices = [100.0] * 5 + [103.0] * 40
        t0 = 1_000_000.0
        ds = make_dataset(prices, oracle_times=[t0 - 10, t0 + 45], oracle_prices=[100.0, 103.0])
        sim = E.Sim(ds, self.cfg(gas_usd=0.5, wide_half=0, core_weight=1.0), always_open).run()
        self.assertAlmostEqual(sim.gas, 0.5 * sim.recenters["core"], places=9)

    def test_ram_accrues_only_while_in_range_and_by_share(self):
        prices = [100.0] * 60
        ds = make_dataset(prices)
        ds.ram_rate = lambda t: 2.0  # RAM per second
        sim = E.Sim(ds, self.cfg(wide_half=0, core_weight=1.0), always_open).run()
        Lo = sim.layers["core"].L
        dt = 10.0 * 59
        expected = 2.0 * dt * Lo / (2e18 + Lo)
        self.assertTrue(math.isclose(sim.ram, expected, rel_tol=1e-9), (sim.ram, expected))
        out = make_dataset([100.0] * 5 + [110.0] * 55, oracle_times=[1_000_000.0 - 10], oracle_prices=[100.0])  # oracle stale after 12h? no: fresh, but
        out.ram_rate = lambda t: 2.0
        sim2 = E.Sim(out, self.cfg(wide_half=0, core_weight=1.0, gate_bps=1.0), always_open).run()
        self.assertLess(sim2.ram, sim.ram * 0.2, "out of range time earns no RAM")


if __name__ == "__main__":
    unittest.main()
