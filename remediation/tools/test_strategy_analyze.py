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


class CostTests(unittest.TestCase):
    def test_costs_come_from_measured_per_action_averages(self):
        keeper = {"byAction": {"recenter": {"count": 2, "usdCost": 0.2}, "rebalance": {"count": 10, "usdCost": 0.5},
                               "checkpoint": {"count": 20, "usdCost": 1.0}},
                  "lastKeeperActionWithEvents": "x", "activeDays": 10.0}
        costs, per_day, notes = sa.derive_costs(keeper)
        self.assertAlmostEqual(costs.recenter_usd, 0.1)
        self.assertAlmostEqual(costs.rebalance_usd, 0.05)
        self.assertAlmostEqual(per_day, 0.1)  # 20 checkpoints x $0.05 over 10 active days
        self.assertTrue(notes)

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
