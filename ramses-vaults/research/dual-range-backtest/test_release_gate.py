"""The release's cold-reconstruction gate must accept only FRESH fetches: not failed runs, not runs marked as cache-assisted second passes, and not logs without fetch timings."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "lib"))

import bundle as B  # noqa: E402
import make_release as M  # noqa: E402


def fresh_log(pair="NVDA"):
    lines = [f"  OK   committed input {f}" for f in B.REQUIRED_SMALL_INPUTS]
    lines += [f"  {pair} {k}: 5 logs (7s)" for k in B.REQUIRED_EVENT_STREAMS]
    lines += [f"  OK   {k}: 5 logs" for k in B.REQUIRED_EVENT_STREAMS]
    lines += ["  OK   dataset columns: 5 swaps"]
    return "\n".join(lines) + "\n"


class ColdGate(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "evidence" / "logs").mkdir(parents=True)
        self.saved = M.HERE
        M.HERE = self.tmp

    def tearDown(self):
        M.HERE = self.saved

    def put(self, text, pair="NVDA"):
        (self.tmp / "evidence" / "logs" / f"bundle-rebuild-{pair}.log").write_text(text)

    def test_fresh_complete_log_passes(self):
        self.put(fresh_log())
        self.assertTrue(M.cold_ok("NVDA"))

    def test_missing_log_fails(self):
        self.assertFalse(M.cold_ok("NVDA"))

    def test_failed_dataset_hash_fails(self):
        self.put(fresh_log().replace("OK   dataset columns", "FAIL dataset columns"))
        self.assertFalse(M.cold_ok("NVDA"))

    def test_any_failed_hash_line_fails_even_if_the_dataset_line_is_ok(self):
        self.put(fresh_log() + "  FAIL swap: 4 logs (manifest 5)\n")
        self.assertFalse(M.cold_ok("NVDA"))

    def test_cache_assisted_second_pass_fails(self):
        self.put("# Second pass: reuses the cache populated by the first cold run\n" + fresh_log())
        self.assertFalse(M.cold_ok("NVDA"))

    def test_log_without_fetch_timings_fails(self):
        self.put("\n".join(l for l in fresh_log().splitlines() if "(7s)" not in l) + "\n")
        self.assertFalse(M.cold_ok("NVDA"))

    def test_log_missing_one_stream_fails(self):
        self.put("\n".join(l for l in fresh_log().splitlines() if "swap: 5 logs" not in l) + "\n")
        self.assertFalse(M.cold_ok("NVDA"))

    def test_traceback_fails(self):
        self.put(fresh_log() + "Traceback (most recent call last):\n")
        self.assertFalse(M.cold_ok("NVDA"))


if __name__ == "__main__":
    unittest.main()
