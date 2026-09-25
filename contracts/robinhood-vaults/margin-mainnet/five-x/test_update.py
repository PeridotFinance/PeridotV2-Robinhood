import json
import tempfile
import unittest
from pathlib import Path
from update import broadcast_record


class BroadcastRecordTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run_dir = Path(self.temp.name)
        self.chain = self.run_dir/'broadcast'/'UpdateMainnetFiveX.s.sol'/'4663'
        self.chain.mkdir(parents=True)

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def test_queue_selects_mined_record_with_prior_dry_run(self):
        mined = {'transactions': [{'hash': 'confirmed-queue'}]}
        self.write(self.chain/'queue-latest.json', mined)
        self.write(self.chain/'dry-run'/'queue-latest.json', {'transactions': [{'hash': None}]})
        self.write(self.chain/'run-123.json', {'transactions': []})
        self.assertEqual(broadcast_record(self.run_dir, 'queue'), mined)

    def test_apply_selects_correct_function_despite_other_records(self):
        mined = {'transactions': [{'hash': 'confirmed-apply'}]}
        self.write(self.chain/'applyRisk-latest.json', mined)
        self.write(self.chain/'dry-run'/'applyRisk-latest.json', {'transactions': [{'hash': None}]})
        self.write(self.chain/'queue-latest.json', {'transactions': [{'hash': 'wrong-function'}]})
        self.assertEqual(broadcast_record(self.run_dir, 'apply'), mined)

    def test_dry_run_alone_cannot_be_reconciled(self):
        self.write(self.chain/'dry-run'/'queue-latest.json', {'transactions': [{'hash': None}]})
        with self.assertRaisesRegex(RuntimeError, 'Missing mined broadcast record'):
            broadcast_record(self.run_dir, 'queue')

    def test_wrong_chain_or_script_cannot_be_reconciled(self):
        self.write(self.chain.parent/'46630'/'queue-latest.json', {'transactions': []})
        self.write(self.run_dir/'broadcast'/'Other.s.sol'/'4663'/'queue-latest.json', {'transactions': []})
        with self.assertRaisesRegex(RuntimeError, 'Missing mined broadcast record'):
            broadcast_record(self.run_dir, 'queue')

    def test_unknown_stage_fails_closed(self):
        with self.assertRaises(ValueError):
            broadcast_record(self.run_dir, 'other')


if __name__ == '__main__':
    unittest.main()
