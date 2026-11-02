"""Regression: unrelated off-grid header anchors in a (shared) header cache must NOT change the resulting dataset.

The NVDA dataset once differed from its cold reconstruction because a shared header cache held 12 extra anchors (fork pins, evidence end blocks) that
refined the timestamp interpolation. build_from_raw now interpolates on the regular grid only."""
import copy
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "lib"))

import bundle as B  # noqa: E402
import build_dataset as BD  # noqa: E402
from evidence_lib import TimeIndex  # noqa: E402

STEP = 10_000


def w(*vals):
    return "0x" + "".join((v % (1 << 256)).to_bytes(32, "big").hex() for v in vals)


def swap_log(block, idx, a0, a1, sqrtp, liq, tick):
    return {"blockNumber": block, "logIndex": idx, "topics": ["0x0"], "data": w(a0, a1, sqrtp, liq, tick), "tx": "0x"}


class FakeRpc:
    def head(self):
        raise AssertionError("the dataset build must not need the network")

    def block(self, n):
        raise AssertionError("a header was requested although the grid was preloaded")


def raw_logs():
    q96 = 2 ** 96
    swaps = [swap_log(100_500 + 3_700 * i, 0, -(10 ** 6) if i % 2 else 10 ** 6, 10 ** 6 if i % 2 else -(10 ** 6), q96 + i, 10 ** 15, i) for i in range(20)]
    return {"head": 140_000, "init": [], "swap": swaps, "feeadj": [], "proto_b": [{"blockNumber": 90_000, "logIndex": 0, "topics": ["0x0"], "data": w(50_000, 500_000), "tx": "0x"}]}


def grid(extra=()):
    g = {b: (1_000 + int(b * 0.125) + (b // STEP) % 3, b) for b in range(90_000, 150_001, STEP)}
    for b in extra:
        if b % STEP == 0:
            continue    # an extra that coincides with a grid anchor is not off-grid
        # an off-grid anchor from some other script: its timestamp is real, but it is not on the regular grid
        g[b] = (1_000 + int(b * 0.125) + 7, b)
    return g


def build(extra=()):
    ti = TimeIndex(FakeRpc(), step=STEP, path=Path(tempfile.mkdtemp()) / "h.json")
    ti.grid = grid(extra)
    cols, _meta = BD.build_from_raw("NVDA", copy.deepcopy(raw_logs()), ti, limit=140_000)
    return cols


class OffGridAnchors(unittest.TestCase):
    def test_extra_off_grid_anchors_do_not_change_the_dataset(self):
        base = build()
        for extra in ((105_000,), (104_113_250 % 150_000, 113_250), (100_700, 101_300, 120_001, 129_999)):
            with_extra = build(extra)
            self.assertEqual(B.dataset_hash(base), B.dataset_hash(with_extra), extra)
            self.assertEqual(base["ts"], with_extra["ts"])

    def test_the_fixture_would_otherwise_be_sensitive(self):
        # an unfiltered TimeIndex interpolates differently once an off-grid anchor is present: this is what the grid-only rule removes
        a, b = TimeIndex(FakeRpc(), step=STEP), TimeIndex(FakeRpc(), step=STEP)
        a.grid, b.grid = grid(), grid((105_000,))
        self.assertNotEqual(a.ts(104_000), b.ts(104_000))

    def test_the_dataset_is_independent_of_which_other_blocks_were_cached(self):
        h1 = B.dataset_hash(build())
        h2 = B.dataset_hash(build(tuple(range(95_001, 140_000, 4_999))))
        self.assertEqual(h1, h2)

    def test_the_real_local_dataset_matches_the_manifest_when_present(self):
        import pickle
        for pair in ("SPY", "GLD", "NVDA"):
            f = B.RESULTS / f"dataset_{pair}.pkl"
            if f.exists():
                cols, _ = pickle.load(open(f, "rb"))
                self.assertEqual(B.dataset_hash(cols), B.load_manifest()["pairs"][pair]["dataset"]["sha256"], pair)


if __name__ == "__main__":
    unittest.main()
