"""Tests of fallback_replay.py on a synthetic pool/feed: every refusal reason of poke()/price() is produced by construction and counted under its own
name, and the two outcomes (oracle returns a price / the vault's removal guard also passes) are separate."""
import unittest

import fallback_replay as R


class FakeTw:
    """Piecewise-constant tick path; price(tick) = 1.0001**tick."""
    def __init__(self, ticks):          # list of (ts, tick)
        self.ts = [t for t, _ in ticks]
        self.tick = [k for _, k in ticks]

    def _at(self, t):
        i = max(j for j, x in enumerate(self.ts) if x <= t)
        return self.tick[i]

    def avg_tick(self, t, w):
        pts = [t - w + (w * k) // 59 for k in range(60)]
        return sum(self._at(x) for x in pts) / 60

    def price(self, tick):
        return 1.0001 ** tick

    def spot_tick(self, t):
        return self._at(t)


T0 = 1_800_000_000


def feed(*updates):
    return [{"updatedAt": t, "answer": int(p * 1e8)} for t, p in updates]


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.tw = FakeTw([(T0 - 10 ** 6, 0), (T0 + 20 * 3600, 150)])        # +1.5% move at T0+20h
        self.f = feed((T0, 1.0), (T0 + 100 * 3600, 1.0))
        self.p = [x["answer"] / 1e8 for x in self.f]
        self.ts = [x["updatedAt"] for x in self.f]

    def _row(self, hours, closed=False):
        return (0, int(hours * 3600), closed, None, T0 + int(hours * 3600))

    def _policy(self, anchored, reason, atick, rows, d_open=24 * 3600, d_closed=72 * 3600, L=0.02, Rb=0.06, mode="anchored"):
        return R.run_policy(self.tw, self.ts, self.p, anchored, reason, atick, rows, 1800, d_open, d_closed, L, Rb, "as-written", mode)

    def test_anchor_reasons(self):
        tw, ts, p, an, rs, at = R.build("X", self.f, 1800, 0.01, "ideal", tw=self.tw, history=24 * 3600)
        self.assertTrue(an[0])
        tw, ts, p, an, rs, at = R.build("X", self.f, 1800, 0.01, "ideal", tw=self.tw, history=1000)
        self.assertEqual(rs[0], "pool history too short")
        R.MAX_POKE_DELAY, old = 100, R.MAX_POKE_DELAY
        try:
            tw, ts, p, an, rs, at = R.build("X", self.f, 1800, 0.01, "keeper1h", tw=self.tw, history=24 * 3600)
            self.assertEqual(rs[0], "poke too late (maxPokeDelay)")
        finally:
            R.MAX_POKE_DELAY = old
        wide = FakeTw([(T0 - 10 ** 6, 500), (T0 + 200 * 3600, 500)])           # pool 5% away from the 1.0 feed: refused at poke
        tw, ts, p, an, rs, at = R.build("X", self.f, 1800, 0.01, "ideal", tw=wide, history=24 * 3600)
        self.assertEqual(rs[0], "basis too wide")
        tw, ts, p, an, rs, at = R.build("X", self.f, 1800, 0.01, "miss30pct", seed=1, tw=self.tw, history=24 * 3600)
        self.assertIn(rs[0], ("missed poke", "ok"))

    def test_price_refusals_are_counted_by_name(self):
        an, rs, at = {0: True}, {0: "ok"}, {0: 0}
        rows = [self._row(13), self._row(30), self._row(30, closed=True), self._row(80, closed=True), self._row(25)]
        o = self._policy(an, rs, at, rows)
        self.assertEqual(o["fail"].get("FallbackExpired"), 3)       # 30 h open, 80 h closed, 25 h open
        self.assertEqual(o["returns"], 2)                            # 13 h open (pool unmoved: +0%), 30 h closed
        o2 = self._policy({0: False}, {0: "missed poke"}, {}, rows)
        self.assertEqual(o2["fail"], {"AnchorMissing: missed poke": 5})

    def test_move_limit_and_the_two_outcomes_are_separate(self):
        an, rs, at = {0: True}, {0: "ok"}, {0: 0}
        late = [self._row(22)]                                        # after the +1.5% move: inside L = 2%
        o = self._policy(an, rs, at, late, d_open=24 * 3600)
        self.assertEqual(o["returns"], 1)
        o = self._policy(an, rs, at, late, L=0.01)
        self.assertEqual(o["fail"], {"MoveExceedsBound": 1})
        # hold mode returns the anchor price (1.0) while the pool spot is 1.5% above it: a removal bound of 1% rejects the EXIT although a price was returned
        o = self._policy(an, rs, at, late, Rb=0.01, mode="hold")
        self.assertEqual((o["returns"], o["completes"]), (1, 0))
        self.assertIn("removal guard rejects (pool spot vs returned price)", o["fail"])
        # anchored mode follows the pool, so the same bound passes
        o = self._policy(an, rs, at, late, Rb=0.01, mode="anchored")
        self.assertEqual((o["returns"], o["completes"]), (1, 1))


if __name__ == "__main__":
    unittest.main()
