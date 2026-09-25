import json
import unittest
from pathlib import Path
from unittest.mock import patch

import verify_snapshot as snapshot


class SnapshotPortabilityTests(unittest.TestCase):
    def test_every_archived_source_resolves_to_a_pinned_file_inside_clone(self):
        pins = json.loads((snapshot.ROOT / "snapshot/sources.json").read_text())["files"]
        artifacts = json.loads((snapshot.ROOT / "snapshot/artifacts.json").read_text())
        historical_absolute_sources = set()
        for entry in artifacts.values():
            artifact = json.loads((snapshot.ROOT / entry["path"]).read_text())
            for source in artifact["metadata"]["sources"]:
                target = snapshot.source_path(source)
                self.assertTrue(target.is_file(), source)
                self.assertIn(target.relative_to(snapshot.ROOT).as_posix(), pins)
                if source.startswith("/"):
                    historical_absolute_sources.add(source)
                    self.assertNotEqual(target, Path(source))
        self.assertTrue(historical_absolute_sources, "Regression needs historical absolute paths")

    def test_absolute_source_uses_relocated_clone_without_original_checkout(self):
        root = Path("/tmp/unrelated-robinhood-clone")
        with patch.object(snapshot, "ROOT", root):
            source = snapshot.ORIGINAL_PERIDOT + "contracts/contracts/BorrowAccounting.sol"
            target = snapshot.source_path(source)
            self.assertEqual(target, (root / "contracts/peridot-contracts-2-5/contracts/contracts/BorrowAccounting.sol").resolve())

    def test_unrecognized_absolute_and_escaping_paths_are_rejected(self):
        for source in ("/tmp/outside.sol", "../../../outside.sol", snapshot.ORIGINAL_PERIDOT + "../../../outside.sol"):
            with self.subTest(source=source), self.assertRaises(RuntimeError):
                snapshot.source_path(source)


if __name__ == "__main__":
    unittest.main()
