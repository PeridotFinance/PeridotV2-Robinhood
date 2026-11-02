"""Regression tests: a fork replay is rejected if rows are missing, duplicated, unexpected or mismatched, or if its proof is not bound to its snapshot."""
import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "lib"))

import pool_quote as PQ  # noqa: E402
import replay_check as RC  # noqa: E402
import verify_replays as VR  # noqa: E402
from test_quote_math import USDG, inputs  # noqa: E402

SIZES = [50, 500]
LIMIT = 10.0


def make_inputs(window=False):
    inp = inputs([(-3000, 3000, 10 ** 17)], 0, fee_ppm=1000, full=not window, window=(-400, 400))
    inp["pool"] = "0x" + "11" * 20
    inp["pin"] = {"l2Block": 123456, "hash": "0x" + "ab" * 32, "timestamp": 1, "native": 99}
    inp["tickSpacing"] = 1
    if window:     # a real window scan only reads ticks inside the window; the band's far ticks are not visible to it
        lo, hi = inp["scan"]["fromTick"], inp["scan"]["toTick"]
        inp["ticks"] = {t: v for t, v in inp["ticks"].items() if lo <= int(t) <= hi}
    return inp


def fork_rows_from(inp):
    plan, exp, _l = RC.expected_rows(inp, USDG, SIZES, LIMIT)
    rows = []
    for (kind, i), e in exp.items():
        rows.append({"kind": kind, "index": i, "requestedIn": int(e["requested"]), "consumed": int(round(e["consumed"])),
                     "out": int(round(e["out"] or 0)), "sqrtAfter": int(e["sqrtAfter"] * RC.Q96)})
    return rows


class RowAccounting(unittest.TestCase):
    def setUp(self):
        self.inp = make_inputs()
        self.rows = fork_rows_from(self.inp)

    def check(self, rows):
        return RC.check_rows(self.inp, USDG, SIZES, LIMIT, rows)[1]

    def test_exact_rows_pass(self):
        self.assertEqual(self.check(self.rows), [])
        self.assertEqual(len(self.rows), (len(RC.KINDS) + 1) * len(SIZES))

    def test_every_missing_row_is_rejected(self):
        for i in range(len(self.rows)):
            rows = self.rows[:i] + self.rows[i + 1:]
            self.assertTrue(any("missing row" in e for e in self.check(rows)), self.rows[i])

    def test_duplicate_row_is_rejected(self):
        self.assertTrue(any("duplicate" in e for e in self.check(self.rows + [copy.deepcopy(self.rows[3])])))

    def test_unexpected_row_is_rejected(self):
        extra = dict(self.rows[0], index=9)
        self.assertTrue(any("unexpected" in e for e in self.check(self.rows + [extra])))
        extra = dict(self.rows[0], kind="weird")
        self.assertTrue(any("unexpected" in e for e in self.check(self.rows + [extra])))

    def test_requested_amount_mismatch_is_rejected(self):
        rows = copy.deepcopy(self.rows)
        rows[0]["requestedIn"] += 1
        self.assertTrue(any("requested" in e for e in self.check(rows)))

    def test_output_consumed_and_price_mismatches_are_rejected(self):
        for field in ("out", "consumed"):
            rows = copy.deepcopy(self.rows)
            r = next(x for x in rows if x["kind"] == "buy" and x["index"] == 1)
            r[field] = int(r[field] * 1.01) + 5
            self.assertTrue(self.check(rows), field)
        rows = copy.deepcopy(self.rows)
        r = next(x for x in rows if x["kind"] == "sell")
        r["sqrtAfter"] = int(r["sqrtAfter"] * 1.001)
        self.assertTrue(self.check(rows))

    def test_sequential_mismatch_is_rejected(self):
        rows = copy.deepcopy(self.rows)
        r = next(x for x in rows if x["kind"] == "sequential")
        r["out"] = int(r["out"] * 0.99)
        self.assertTrue(self.check(rows))

    def test_scan_window_exhausted_offline_is_a_lower_bound(self):
        inp = make_inputs(window=True)
        plan, exp, _l = RC.expected_rows(inp, USDG, [50, 5_000_000_000], LIMIT)
        rows = []
        for (kind, i), e in exp.items():
            rows.append({"kind": kind, "index": i, "requestedIn": int(e["requested"]), "consumed": int(round(e["consumed"])), "out": int(round(e["out"] or 0)), "sqrtAfter": int(e["sqrtAfter"] * RC.Q96)})
        lowers = [k for k, e in exp.items() if e["status"] == "scan_window_exhausted"]
        self.assertTrue(lowers, "fixture must contain a window-exhausted row")
        self.assertEqual(RC.check_rows(inp, USDG, [50, 5_000_000_000], LIMIT, rows)[1], [])
        # the deployed pool may fill MORE than the offline lower bound ...
        more = copy.deepcopy(rows)
        for r in more:
            if (r["kind"], r["index"]) in lowers and r["kind"] != "sequential":
                r["consumed"] += 10 ** 6
                r["out"] += 10 ** 4
        self.assertEqual(RC.check_rows(inp, USDG, [50, 5_000_000_000], LIMIT, more)[1], [])
        # ... but never LESS
        less = copy.deepcopy(rows)
        for r in less:
            if (r["kind"], r["index"]) in lowers and r["kind"] != "sequential":
                r["out"] = int(r["out"] * 0.5)
        self.assertTrue(RC.check_rows(inp, USDG, [50, 5_000_000_000], LIMIT, less)[1])


class ProofBinding(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.saved = (PQ.POOLS_DIR, VR.POOLS_DIR)
        PQ.POOLS_DIR = VR.POOLS_DIR = self.tmp
        self.inp = make_inputs()
        pin = self.inp["pin"]
        self.rel = f"inputs/test/{pin['l2Block']}-{pin['hash'][2:10]}.json"
        (self.tmp / "inputs" / "test").mkdir(parents=True)
        self.snap = self.tmp / self.rel
        self.snap.write_text(json.dumps(self.inp, indent=1))
        self.rows = fork_rows_from(self.inp)
        (self.tmp / "fork-replay").mkdir()
        self.write_log(self.rows)
        self.doc = {"pool": "test", "usdg": USDG, "sizesUsd": SIZES, "limitPct": LIMIT, "pin": pin, "inputsFile": self.rel,
                    "inputsSha256": hashlib.sha256(self.snap.read_bytes()).hexdigest(), "forgeLogFile": "fork-replay/t.forge.log",
                    "forgeLogSha256": PQ.file_sha256(self.log), "expectedRowCount": len(self.rows), "forkRows": self.rows}
        self.docpath = self.tmp / "fork-replay" / "test@123456.json"
        self.write_doc(self.doc)

    def tearDown(self):
        PQ.POOLS_DIR, VR.POOLS_DIR = self.saved

    def write_log(self, rows):
        self.log = self.tmp / "fork-replay" / "t.forge.log"
        self.log.write_text("Logs:\n" + "".join(f"  REPLAY,{r['kind']},{r['index']},{r['requestedIn']},{r['consumed']},{r['out']},{r['sqrtAfter']}\n" for r in rows))

    def write_doc(self, doc):
        self.docpath.write_text(json.dumps(doc))

    def test_consistent_proof_verifies(self):
        self.assertEqual(VR.verify_doc(self.docpath), [])

    def test_modified_snapshot_is_rejected(self):
        inp = copy.deepcopy(self.inp)
        inp["liquidity"] = str(int(inp["liquidity"]) + 1)
        self.snap.write_text(json.dumps(inp, indent=1))
        self.assertTrue(any("sha256" in e for e in VR.verify_doc(self.docpath)))

    def test_snapshot_with_a_different_pin_is_rejected_even_if_the_hash_is_updated(self):
        inp = copy.deepcopy(self.inp)
        inp["pin"]["l2Block"] += 1
        self.snap.write_text(json.dumps(inp, indent=1))
        d = dict(self.doc, inputsSha256=hashlib.sha256(self.snap.read_bytes()).hexdigest())
        self.write_doc(d)
        self.assertTrue(any("pin" in e for e in VR.verify_doc(self.docpath)))

    def test_snapshot_file_name_must_carry_the_block_and_hash(self):
        new = self.tmp / "inputs" / "test" / "latest.json"
        new.write_bytes(self.snap.read_bytes())
        self.write_doc(dict(self.doc, inputsFile="inputs/test/latest.json"))
        self.assertTrue(any("file name" in e for e in VR.verify_doc(self.docpath)))

    def test_missing_snapshot_or_log_is_rejected(self):
        self.snap.unlink()
        self.assertTrue(any("missing" in e for e in VR.verify_doc(self.docpath)))
        self.snap.write_text(json.dumps(self.inp, indent=1))
        self.log.unlink()
        self.assertTrue(any("forge log missing" in e for e in VR.verify_doc(self.docpath)))

    def test_log_and_stored_rows_must_agree(self):
        rows = copy.deepcopy(self.rows)
        rows[2]["out"] += 1
        self.write_log(rows)                       # log changed, doc not updated: sha differs
        errs = VR.verify_doc(self.docpath)
        self.assertTrue(any("forge log sha256" in e for e in errs))
        d = dict(self.doc, forgeLogSha256=PQ.file_sha256(self.log))
        self.write_doc(d)                          # sha repaired but stored rows still differ from the log
        self.assertTrue(any("stored fork rows" in e for e in VR.verify_doc(self.docpath)))

    def test_expected_row_count_must_match(self):
        self.write_doc(dict(self.doc, expectedRowCount=len(self.rows) - 1))
        self.assertTrue(any("row count" in e for e in VR.verify_doc(self.docpath)))

    def test_missing_duplicate_and_wrong_rows_in_the_stored_proof_are_rejected(self):
        for rows in (self.rows[:-1], self.rows + [self.rows[0]]):
            self.write_log(rows)
            d = dict(self.doc, forkRows=rows, forgeLogSha256=PQ.file_sha256(self.log))
            self.write_doc(d)
            self.assertTrue(VR.verify_doc(self.docpath))


if __name__ == "__main__":
    unittest.main()
