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

    # ---- exact stream names, not line counts
    def swap_lines(self, text, kind):
        pat = "(7s)" if kind == "fetch" else "OK   swap"
        return [l for l in text.splitlines() if "swap: 5 logs" in l and (pat in l)]

    def test_swap_fetch_replaced_by_a_duplicate_mint_fetch_fails(self):
        text = fresh_log()
        fetch_swap = self.swap_lines(text, "fetch")[0]
        mint_fetch = next(l for l in text.splitlines() if "NVDA mint: 5 logs (7s)" in l)
        self.put(text.replace(fetch_swap, mint_fetch))     # same number of fetch lines, but swap is missing and mint appears twice
        self.assertEqual(sum(1 for l in (self.tmp / "evidence/logs/bundle-rebuild-NVDA.log").read_text().splitlines() if "(7s)" in l), 12)
        self.assertFalse(M.cold_ok("NVDA"))

    def test_swap_hash_line_replaced_by_a_duplicate_mint_hash_line_fails(self):
        text = fresh_log()
        hash_swap = self.swap_lines(text, "hash")[0]
        mint_hash = next(l for l in text.splitlines() if l.strip() == "OK   mint: 5 logs")
        self.put(text.replace(hash_swap, mint_hash))
        self.assertFalse(M.cold_ok("NVDA"))

    def test_extra_duplicate_lines_fail_even_when_every_required_stream_is_present(self):
        text = fresh_log()
        self.put(text + "  NVDA mint: 5 logs (7s)\n")
        self.assertFalse(M.cold_ok("NVDA"))
        self.put(text + "  OK   mint: 5 logs\n")
        self.assertFalse(M.cold_ok("NVDA"))

    def test_unknown_stream_name_fails(self):
        text = fresh_log()
        self.put(text.replace("NVDA flash: 5 logs (7s)", "NVDA other: 5 logs (7s)"))
        self.assertFalse(M.cold_ok("NVDA"))
        self.put(text.replace("  OK   flash: 5 logs", "  OK   other: 5 logs"))
        self.assertFalse(M.cold_ok("NVDA"))

    def test_small_inputs_must_each_appear_exactly_once(self):
        text = fresh_log()
        self.put(text.replace("  OK   committed input gauge_notify.json\n", ""))
        self.assertFalse(M.cold_ok("NVDA"))
        self.put(text + "  OK   committed input gauge_notify.json\n")
        self.assertFalse(M.cold_ok("NVDA"))

    def test_a_small_input_replaced_by_a_duplicate_of_the_other_fails(self):
        text = fresh_log()
        self.put(text.replace("  OK   committed input gauge_notify.json\n", "  OK   committed input answer_updated.json\n"))
        self.assertFalse(M.cold_ok("NVDA"))

    def test_the_dataset_line_must_appear_exactly_once(self):
        text = fresh_log()
        self.put(text + "  OK   dataset columns: 5 swaps\n")
        self.assertFalse(M.cold_ok("NVDA"))
        self.put(text.replace("  OK   dataset columns: 5 swaps\n", ""))
        self.assertFalse(M.cold_ok("NVDA"))

    def test_the_real_logs_pass_when_present(self):
        real = Path(__file__).parent / "evidence" / "logs"
        for pair in ("SPY", "GLD", "NVDA"):
            f = real / f"bundle-rebuild-{pair}.log"
            if f.exists():
                self.put(f.read_text(), pair)
                self.assertTrue(M.cold_ok(pair), pair)

    def test_traceback_fails(self):
        self.put(fresh_log() + "Traceback (most recent call last):\n")
        self.assertFalse(M.cold_ok("NVDA"))


if __name__ == "__main__":
    unittest.main()
