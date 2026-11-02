"""Regression tests: bundle verification must FAIL when a required input is missing or modified; rebuild must verify small inputs."""
import contextlib
import io
import json
import pickle
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "lib"))

import bundle as B  # noqa: E402


def quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()) as buf:
        rc = fn(*a, **k)
    return rc, buf.getvalue()


class BundleVerification(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.here = self.tmp / "here"
        (self.here / "evidence" / "bundle" / "inputs").mkdir(parents=True)
        self.results = self.tmp / "results"
        self.results.mkdir()
        self.cols = {k: [1, 2, 3] for k in B.DATASET_COLS}
        (self.here / "evidence" / "bundle" / "inputs" / "answer_updated.json").write_text('{"a":1}')
        (self.here / "evidence" / "bundle" / "inputs" / "gauge_notify.json").write_text('{"g":1}')
        self.manifest = {
            "schema": "x", "version": 1,
            "pairs": {"SPY": {"pool": "0x1", "toBlock": 10, "events": {"swap": {"signature": "S", "topic0": "0x0", "count": 1, "sha256": B.canon_hash([{"blockNumber": 1, "logIndex": 0, "topics": ["0x0"], "data": "0x"}], 10)[1]}},
                              "dataset": {"swaps": 3, "sha256": B.dataset_hash(self.cols), "gridLimitBlock": None}}},
            "smallInputs": {f: {"sha256": B.file_sha(self.here / "evidence" / "bundle" / "inputs" / f), "committedAt": f"evidence/bundle/inputs/{f}"} for f in B.SMALL_INPUTS},
        }
        self.saved = (B.MANIFEST, B.HERE, B.RESULTS)
        B.MANIFEST = self.tmp / "manifest.json"
        B.MANIFEST.write_text(json.dumps(self.manifest))
        B.HERE, B.RESULTS = self.here, self.results
        pickle.dump((self.cols, {}), open(self.results / "dataset_SPY.pkl", "wb"))

    def tearDown(self):
        B.MANIFEST, B.HERE, B.RESULTS = self.saved

    def test_complete_inputs_pass(self):
        self.assertEqual(quiet(B.verify, "SPY", None)[0], 0)

    def test_missing_dataset_fails(self):
        (self.results / "dataset_SPY.pkl").unlink()
        rc, out = quiet(B.verify, "SPY", None)
        self.assertNotEqual(rc & 2, 0)
        self.assertIn("MISSING", out)

    def test_modified_dataset_fails(self):
        bad = dict(self.cols, block=[9, 9, 9])
        pickle.dump((bad, {}), open(self.results / "dataset_SPY.pkl", "wb"))
        self.assertNotEqual(quiet(B.verify, "SPY", None)[0] & 2, 0)

    def test_missing_small_input_fails(self):
        (self.here / "evidence" / "bundle" / "inputs" / "gauge_notify.json").unlink()
        rc, out = quiet(B.verify, "SPY", None)
        self.assertNotEqual(rc & 4, 0)
        self.assertIn("MISSING", out)

    def test_modified_small_input_fails(self):
        (self.here / "evidence" / "bundle" / "inputs" / "answer_updated.json").write_text('{"a":2}')
        self.assertNotEqual(quiet(B.verify, "SPY", None)[0] & 4, 0)

    def test_missing_raw_file_fails_when_requested(self):
        rc, out = quiet(B.verify, "SPY", str(self.tmp / "nope.json"))
        self.assertNotEqual(rc & 1, 0)
        self.assertIn("MISSING", out)

    def test_raw_hash_mismatch_fails(self):
        raw = self.tmp / "raw.json"
        raw.write_text(json.dumps({"swap": [{"blockNumber": 1, "logIndex": 0, "topics": ["0x0"], "data": "0xdead"}]}))
        self.assertNotEqual(quiet(B.verify, "SPY", str(raw))[0] & 1, 0)
        raw.write_text(json.dumps({"swap": [{"blockNumber": 1, "logIndex": 0, "topics": ["0x0"], "data": "0x"}]}))
        self.assertEqual(quiet(B.verify, "SPY", str(raw))[0] & 1, 0)

    def test_unknown_pair_fails(self):
        pickle.dump((self.cols, {}), open(self.results / "dataset_GLD.pkl", "wb"))   # present on disk but absent from the manifest
        rc, out = quiet(B.verify, "GLD", None)
        self.assertEqual(rc, 3)
        self.assertIn("not in the manifest", out)

    def test_empty_small_input_list_fails(self):
        m = dict(self.manifest, smallInputs={})
        B.MANIFEST.write_text(json.dumps(m))
        self.assertNotEqual(quiet(B.verify, "SPY", None)[0] & 4, 0)

    def test_rebuild_refuses_before_any_fetch_when_small_input_missing(self):
        (self.here / "evidence" / "bundle" / "inputs" / "answer_updated.json").unlink()
        called = []
        orig = B.fetch_raw
        B.fetch_raw = lambda *a, **k: called.append(1) or ({}, None)
        try:
            rc, out = quiet(B.rebuild, "SPY", str(self.tmp / "c"), str(self.tmp / "o"))
        finally:
            B.fetch_raw = orig
        self.assertEqual(rc, 4)
        self.assertEqual(called, [])

    def test_canonical_hash_ignores_logs_after_to_block(self):
        a = [{"blockNumber": 1, "logIndex": 0, "topics": [], "data": "0x"}]
        b = a + [{"blockNumber": 99, "logIndex": 0, "topics": [], "data": "0x"}]
        self.assertEqual(B.canon_hash(a, 10), B.canon_hash(b, 10))
        self.assertNotEqual(B.canon_hash(a, 10), B.canon_hash(b, 100))


if __name__ == "__main__":
    unittest.main()
