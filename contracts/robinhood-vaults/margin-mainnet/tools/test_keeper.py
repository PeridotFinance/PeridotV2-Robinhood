import json
import tempfile
import unittest
from pathlib import Path
from keeper import run, loopback


class Backend:
    identity = {'chainId': 4663, 'executor': 'fixture', 'sender': 'keeper'}
    def __init__(self, outcomes=(50,25), healthy_below=30):
        self.debt, self.fresh, self.healthy_below = 100, True, healthy_below
        self.outcomes = list(outcomes)
        self.sent, self.simulations, self.applied = [], 0, set()
        self.pending, self.revert, self.unknown = False, False, False
    def snapshot(self, position):
        return {'positionId': position, 'status': 6 if self.debt == 0 else 2,
                'debt': self.debt, 'fresh': self.fresh, 'block': len(self.applied)}
    def simulate(self, position):
        self.simulations += 1
        if self.debt < self.healthy_below:
            raise RuntimeError('RiskEngine: healthy')
        return {'to':'liquidator','data':str(position),'gas':'0x123'}
    def nonce(self): return len(self.sent)
    def submit(self, tx, nonce):
        self.sent.append((nonce,tx))
        if self.unknown: raise TimeoutError('ambiguous transport failure')
        return 'tx'+str(len(self.sent))
    def receipt(self, transaction_hash):
        if self.pending: return None
        if self.revert: return {'status':'0x0','transactionHash':transaction_hash}
        if transaction_hash not in self.applied:
            self.debt = self.outcomes.pop(0)
            self.applied.add(transaction_hash)
        return {'status':'0x1','transactionHash':transaction_hash}


class KeeperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'journal.json'
    def test_rechecks_and_repeats_partial_liquidations_until_healthy(self):
        b=Backend()
        result=run(b,1,self.path,True)
        self.assertEqual(result['status'],'not_executable_stop')
        self.assertEqual(b.debt,25)
        self.assertEqual([n for n,_ in b.sent],[0,1])
        self.assertEqual(b.simulations,3)
        self.assertTrue(all(a['state']=='confirmed' for a in result['journal']['attempts']))
    def test_full_liquidation_stops_without_second_submission(self):
        b=Backend((0,))
        self.assertEqual(run(b,1,self.path,True)['status'],'resolved')
        self.assertEqual(len(b.sent),1)
    def test_stale_feed_does_not_simulate_or_send(self):
        b=Backend();b.fresh=False
        self.assertEqual(run(b,1,self.path,True)['status'],'stale_or_unavailable_stop')
        self.assertEqual((b.simulations,len(b.sent)),(0,0))
    def test_healthy_position_does_not_send(self):
        b=Backend();b.debt=20
        self.assertEqual(run(b,1,self.path,True)['status'],'not_executable_stop')
        self.assertEqual(len(b.sent),0)
    def test_pending_hash_is_reconciled_before_requoting(self):
        b=Backend((0,));b.pending=True
        self.assertEqual(run(b,1,self.path,True)['status'],'pending')
        self.assertEqual(run(b,1,self.path,True)['status'],'pending')
        self.assertEqual(len(b.sent),1)
        b.pending=False
        self.assertEqual(run(b,1,self.path,True)['status'],'resolved')
        self.assertEqual(len(b.sent),1)
    def test_failed_receipt_never_blindly_retries(self):
        b=Backend();b.revert=True
        self.assertEqual(run(b,1,self.path,True)['status'],'reverted_stop')
        self.assertEqual(run(b,1,self.path,True)['status'],'reverted_stop')
        self.assertEqual(len(b.sent),1)
    def test_ambiguous_submission_stops_across_restarts(self):
        b=Backend();b.unknown=True
        self.assertEqual(run(b,1,self.path,True)['status'],'reconcile_unknown_submission')
        self.assertEqual(run(b,1,self.path,True)['status'],'reconcile_unknown_submission')
        self.assertEqual(len(b.sent),1)
        self.assertEqual(json.loads(self.path.read_text())['attempts'][0]['nonce'],0)
    def test_success_receipt_without_debt_reduction_stops(self):
        b=Backend((100,))
        self.assertEqual(run(b,1,self.path,True)['status'],'no_progress_stop')
        self.assertEqual(run(b,1,self.path,True)['status'],'no_progress_stop')
        self.assertEqual(len(b.sent),1)
    def test_read_only_plan_never_submits(self):
        b=Backend()
        self.assertEqual(run(b,1,self.path)['status'],'executable_plan')
        self.assertEqual(len(b.sent),0)
        self.assertFalse(self.path.exists())
    def test_mismatched_deployment_journal_rejected(self):
        b=Backend((0,));run(b,1,self.path,True)
        b.identity={'chainId':4663,'executor':'different','sender':'keeper'}
        with self.assertRaises(AssertionError): run(b,1,self.path,True)
    def test_execution_url_guard(self):
        self.assertTrue(loopback('http://127.0.0.1:8556'))
        self.assertTrue(loopback('http://[::1]:8556'))
        for url in ['https://rpc.mainnet.chain.robinhood.com','http://127.0.0.1.evil:8556',
                    'http://127.0.0.1@evil:8556','https://127.0.0.1:8556']:
            self.assertFalse(loopback(url))


if __name__ == '__main__': unittest.main()
