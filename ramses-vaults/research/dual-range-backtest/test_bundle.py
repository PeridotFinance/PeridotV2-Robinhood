"""Regression tests: bundle verification must FAIL when a required input or event stream is missing or modified, independently of what the
manifest itself lists; rebuild must verify its inputs before fetching anything."""
import contextlib
import copy
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


LOG = {"blockNumber": 1, "logIndex": 0, "topics": ["0x0"], "data": "0x"}


class BundleVerification(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.here = self.tmp / "here"
        self.inputs = self.here / "evidence" / "bundle" / "inputs"
        self.inputs.mkdir(parents=True)
        self.results = self.tmp / "results"
        self.results.mkdir()
        self.cols = {k: [1, 2, 3] for k in B.DATASET_COLS}
        for f in B.REQUIRED_SMALL_INPUTS:
            (self.inputs / f).write_text(json.dumps({"x": f}))
        h = B.canon_hash([LOG], 10)[1]
        self.raw = {k: [LOG] for k in B.REQUIRED_EVENT_STREAMS}
        self.manifest = {
            "schema": "x", "version": 1,
            "pairs": {"SPY": {"pool": B.ADDR["SPY"]["pool"], "toBlock": 10,
                              "events": {k: {"signature": B.SIGS[k], "topic0": B.T(B.SIGS[k]), "count": 1, "sha256": h} for k in B.REQUIRED_EVENT_STREAMS},
                              "dataset": {"swaps": 3, "sha256": B.dataset_hash(self.cols), "gridLimitBlock": None}}},
            "smallInputs": {f: {"sha256": B.file_sha(self.inputs / f), "committedAt": f"{B.SMALL_INPUT_DIR}/{f}"} for f in B.REQUIRED_SMALL_INPUTS},
        }
        self.saved = (B.MANIFEST, B.HERE, B.RESULTS)
        B.MANIFEST = self.tmp / "manifest.json"
        B.HERE, B.RESULTS = self.here, self.results
        self.write_manifest(self.manifest)
        pickle.dump((self.cols, {}), open(self.results / "dataset_SPY.pkl", "wb"))

    def tearDown(self):
        B.MANIFEST, B.HERE, B.RESULTS = self.saved

    def write_manifest(self, m):
        B.MANIFEST.write_text(json.dumps(m))

    def raw_file(self, raw):
        p = self.tmp / "raw.json"
        p.write_text(json.dumps(raw))
        return str(p)

    # ---- the happy path
    def test_complete_inputs_pass(self):
        self.assertEqual(quiet(B.verify, "SPY", self.raw_file(self.raw))[0], 0)

    # ---- datasets
    def test_missing_dataset_fails(self):
        (self.results / "dataset_SPY.pkl").unlink()
        rc, out = quiet(B.verify, "SPY", None)
        self.assertNotEqual(rc & 2, 0)
        self.assertIn("MISSING", out)

    def test_modified_dataset_fails(self):
        pickle.dump((dict(self.cols, block=[9, 9, 9]), {}), open(self.results / "dataset_SPY.pkl", "wb"))
        self.assertNotEqual(quiet(B.verify, "SPY", None)[0] & 2, 0)

    # ---- small inputs: the required set is fixed in code
    def test_missing_small_input_fails(self):
        (self.inputs / "gauge_notify.json").unlink()
        rc, out = quiet(B.verify, "SPY", None)
        self.assertNotEqual(rc & 4, 0)
        self.assertIn("MISSING", out)

    def test_removing_a_required_input_from_disk_AND_manifest_still_fails(self):
        (self.inputs / "gauge_notify.json").unlink()
        m = copy.deepcopy(self.manifest)
        del m["smallInputs"]["gauge_notify.json"]
        self.write_manifest(m)
        rc, out = quiet(B.verify, "SPY", None)
        self.assertNotEqual(rc & 4, 0)
        self.assertIn("not listed in the manifest", out)

    def test_removing_every_small_input_from_manifest_fails(self):
        m = copy.deepcopy(self.manifest)
        m["smallInputs"] = {}
        self.write_manifest(m)
        self.assertNotEqual(quiet(B.verify, "SPY", None)[0] & 4, 0)
        m.pop("smallInputs")
        self.write_manifest(m)
        self.assertNotEqual(quiet(B.verify, "SPY", None)[0] & 4, 0)

    def test_modified_small_input_fails(self):
        (self.inputs / "answer_updated.json").write_text('{"tampered":1}')
        self.assertNotEqual(quiet(B.verify, "SPY", None)[0] & 4, 0)

    def test_manifest_pointing_at_a_non_canonical_path_fails(self):
        other = self.tmp / "elsewhere.json"
        other.write_text(json.dumps({"x": "answer_updated.json"}))
        m = copy.deepcopy(self.manifest)
        m["smallInputs"]["answer_updated.json"]["committedAt"] = "elsewhere.json"
        (self.here / "elsewhere.json").write_text(other.read_text())
        self.write_manifest(m)
        rc, out = quiet(B.verify, "SPY", None)
        self.assertNotEqual(rc & 4, 0)
        self.assertIn("non-canonical", out)

    # ---- event streams: the required list is fixed in code
    def test_manifest_missing_an_event_stream_fails(self):
        for stream in B.REQUIRED_EVENT_STREAMS:
            m = copy.deepcopy(self.manifest)
            del m["pairs"]["SPY"]["events"][stream]
            self.write_manifest(m)
            rc, out = quiet(B.verify, "SPY", self.raw_file(self.raw))
            self.assertNotEqual(rc & 5, 0, stream)
            self.assertIn(stream, out)

    def test_verify_raw_itself_requires_every_stream_even_if_the_manifest_lacks_it(self):
        # called directly, without manifest_violations: the raw check must not trust the manifest's own list of streams
        for stream in B.REQUIRED_EVENT_STREAMS:
            m = copy.deepcopy(self.manifest)
            del m["pairs"]["SPY"]["events"][stream]
            ok = quiet(B.verify_raw, "SPY", self.raw, m)[0]
            self.assertFalse(ok, stream)

    def test_manifest_with_wrong_signature_or_topic_fails(self):
        m = copy.deepcopy(self.manifest)
        m["pairs"]["SPY"]["events"]["swap"]["signature"] = "Swap(uint256)"
        self.write_manifest(m)
        self.assertNotEqual(quiet(B.verify, "SPY", None)[0] & 5, 0)
        m = copy.deepcopy(self.manifest)
        m["pairs"]["SPY"]["events"]["mint"]["topic0"] = "0x" + "11" * 32
        self.write_manifest(m)
        self.assertNotEqual(quiet(B.verify, "SPY", None)[0] & 5, 0)

    def test_manifest_for_the_wrong_pool_fails(self):
        m = copy.deepcopy(self.manifest)
        m["pairs"]["SPY"]["pool"] = "0x" + "22" * 20
        self.write_manifest(m)
        self.assertNotEqual(quiet(B.verify, "SPY", None)[0] & 5, 0)

    def test_raw_file_missing_a_stream_fails(self):
        for stream in B.REQUIRED_EVENT_STREAMS:
            raw = {k: v for k, v in self.raw.items() if k != stream}
            rc, out = quiet(B.verify, "SPY", self.raw_file(raw))
            self.assertNotEqual(rc & 1, 0, stream)
            self.assertIn("MISSING", out)

    def test_raw_extra_stream_is_ignored_but_required_ones_must_match(self):
        raw = dict(self.raw, extra=[LOG])
        self.assertEqual(quiet(B.verify, "SPY", self.raw_file(raw))[0] & 1, 0)

    def test_missing_raw_file_fails_when_requested(self):
        rc, out = quiet(B.verify, "SPY", str(self.tmp / "nope.json"))
        self.assertNotEqual(rc & 1, 0)
        self.assertIn("MISSING", out)

    def test_raw_hash_mismatch_fails(self):
        raw = dict(self.raw, swap=[dict(LOG, data="0xdead")])
        self.assertNotEqual(quiet(B.verify, "SPY", self.raw_file(raw))[0] & 1, 0)

    def test_unknown_pair_fails(self):
        pickle.dump((self.cols, {}), open(self.results / "dataset_GLD.pkl", "wb"))   # present on disk but absent from the manifest
        rc, out = quiet(B.verify, "GLD", None)
        self.assertEqual(rc, 3)
        self.assertIn("not in the manifest", out)

    # ---- rebuild
    def test_rebuild_refuses_before_any_fetch_when_a_required_input_is_missing(self):
        (self.inputs / "answer_updated.json").unlink()
        called = []
        orig = B.fetch_raw
        B.fetch_raw = lambda *a, **k: called.append(1) or ({}, None)
        try:
            rc, out = quiet(B.rebuild, "SPY", str(self.tmp / "c"), str(self.tmp / "o"))
        finally:
            B.fetch_raw = orig
        self.assertEqual(rc, 4)
        self.assertEqual(called, [])

    def test_rebuild_refuses_before_any_fetch_when_the_manifest_lacks_a_stream(self):
        m = copy.deepcopy(self.manifest)
        del m["pairs"]["SPY"]["events"]["feeadj"]
        self.write_manifest(m)
        called = []
        orig = B.fetch_raw
        B.fetch_raw = lambda *a, **k: called.append(1) or ({}, None)
        try:
            rc, out = quiet(B.rebuild, "SPY", str(self.tmp / "c"), str(self.tmp / "o"))
        finally:
            B.fetch_raw = orig
        self.assertEqual(rc, 5)
        self.assertEqual(called, [])

    def test_canonical_hash_ignores_logs_after_to_block(self):
        a = [dict(LOG)]
        b = a + [dict(LOG, blockNumber=99)]
        self.assertEqual(B.canon_hash(a, 10), B.canon_hash(b, 10))
        self.assertNotEqual(B.canon_hash(a, 10), B.canon_hash(b, 100))

    def test_the_real_manifest_describes_every_required_stream_and_input(self):
        real = json.loads(Path(B.__file__).with_name("evidence").joinpath("bundle", "manifest-v1.json").read_text())
        for pair in ("SPY", "GLD", "NVDA"):
            self.assertEqual(B.manifest_violations(real, pair), [], pair)
        self.assertEqual(sorted(real["smallInputs"]), sorted(B.REQUIRED_SMALL_INPUTS))


if __name__ == "__main__":
    unittest.main()
