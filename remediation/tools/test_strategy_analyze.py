import json
import tempfile
import unittest
from pathlib import Path

import strategy_analyze as sa
from evidence_lib import EvidenceError


class SectionTests(unittest.TestCase):
    def test_an_evidence_error_becomes_a_failed_section_not_a_crash(self):
        res = {}
        sa.section(res, "x", lambda: (_ for _ in ()).throw(EvidenceError("read failed")))
        self.assertEqual(res["x"]["status"], "FAILED")
        self.assertIn("read failed", res["x"]["reason"])

    def test_an_unexpected_bug_is_also_a_failed_section_with_a_trace(self):
        res = {}
        sa.section(res, "y", lambda: 1 / 0)
        self.assertEqual(res["y"]["status"], "FAILED")
        self.assertIn("ZeroDivisionError", res["y"]["reason"])
        self.assertIn("trace", res["y"])

    def test_partial_results_are_written_after_every_section(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "run.partial.json"
            sa._PARTIAL["path"] = path
            try:
                res = {}
                sa.section(res, "a", lambda: {"ok": 1})
                self.assertEqual(json.loads(path.read_text())["a"], {"ok": 1})
                sa.section(res, "b", lambda: {"ok": 2})
                self.assertEqual(set(json.loads(path.read_text())), {"a", "b"})
            finally:
                sa._PARTIAL["path"] = None


class VerdictTests(unittest.TestCase):
    def test_valid_only_when_both_the_vault_and_the_ramses_checks_pass(self):
        ok = {"validation": {"ok": True}, "ramsesFeesAndVolume": {"validation": {"ok": True}}}
        self.assertTrue(sa.overall_valid(ok))
        self.assertFalse(sa.overall_valid({"validation": {"ok": True}, "ramsesFeesAndVolume": {"validation": {"ok": False}}}))
        self.assertFalse(sa.overall_valid({"validation": {"ok": False}, "ramsesFeesAndVolume": {"validation": {"ok": True}}}))

    def test_a_failed_or_missing_ramses_section_is_not_valid(self):
        self.assertFalse(sa.overall_valid({"validation": {"ok": True}, "ramsesFeesAndVolume": {"status": "FAILED"}}))
        self.assertFalse(sa.overall_valid({"validation": {"ok": True}}))


class CostTests(unittest.TestCase):
    def test_costs_come_from_measured_averages_and_checkpoints_never_fall_below_the_hourly_requirement(self):
        keeper = {"byAction": {"recenter": {"count": 2, "usdCost": 0.2}, "rebalance": {"count": 10, "usdCost": 0.5},
                               "checkpoint": {"count": 20, "usdCost": 1.0}},
                  "lastKeeperActionWithEvents": "x", "activeDays": 10.0}
        costs, per_day, notes = sa.derive_costs(keeper)
        self.assertAlmostEqual(costs.recenter_usd, 0.1)
        self.assertAlmostEqual(costs.rebalance_usd, 0.05)
        # observed: 20 x $0.05 over 10 days = $0.10/day ; required: 24 hourly checkpoints x $0.05 = $1.20/day -> the larger
        self.assertAlmostEqual(per_day, 1.2)
        self.assertTrue(any("hourly cadence" in n for n in notes))

    def test_when_the_keeper_was_busier_than_hourly_the_observed_cost_is_used(self):
        keeper = {"byAction": {"checkpoint": {"count": 500, "usdCost": 5.0}}, "lastKeeperActionWithEvents": "x", "activeDays": 10.0}
        _, per_day, _ = sa.derive_costs(keeper)
        self.assertAlmostEqual(per_day, 0.5)   # 500 x $0.01 over 10 days = $0.50 > 24 x $0.01 = $0.24

    def test_missing_keeper_data_is_stated_not_silently_zero(self):
        costs, per_day, notes = sa.derive_costs({"status": "FAILED"})
        self.assertEqual((costs.recenter_usd, per_day), (0.0, 0.0))
        self.assertIn("unavailable", notes[0])

    def test_existing_output_files_are_never_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "run.json"
            out.write_text("{}")
            with self.assertRaises(SystemExit):
                sa.main(["--out", str(out)])


if __name__ == "__main__":
    unittest.main()
