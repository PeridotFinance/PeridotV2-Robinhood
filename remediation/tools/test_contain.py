import contextlib
import io
import unittest
from unittest.mock import patch
import contain


class ContainmentTests(unittest.TestCase):
    def test_plan_only_pauses_two_markets_and_seizure(self):
        actions = contain.plan()
        self.assertEqual(len(actions), 3)
        self.assertEqual([a[1] for a in actions], ['_setBorrowPaused(address,bool)'] * 2 + ['_setSeizePaused(bool)'])
        self.assertTrue(all(a[2][-1] == 'true' for a in actions))

    def test_default_is_read_only(self):
        state = {'admin': contain.GOVERNOR, 'pauseGuardian': contain.GOVERNOR}
        with patch('sys.argv', ['contain.py']), patch('contain.read_state', return_value=state), \
             patch('contain.call', return_value='0x1'), patch('contain.cast', return_value='0xfixture'), \
             patch('contain.subprocess.run') as send, contextlib.redirect_stdout(io.StringIO()):
            contain.main()
        send.assert_not_called()

    def test_broadcast_requires_users_interactive_terminal(self):
        state = {'admin': contain.GOVERNOR, 'pauseGuardian': contain.GOVERNOR}
        with patch('sys.argv', ['contain.py', '--broadcast']), patch('contain.read_state', return_value=state), \
             patch('contain.call', return_value='0x1'), patch('contain.cast', return_value='0xfixture'), \
             patch('sys.stdin.isatty', return_value=False), patch('contain.subprocess.run') as send, \
             contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(RuntimeError, 'yourself'):
            contain.main()
        send.assert_not_called()

    def test_ambiguous_intent_cannot_be_retried(self):
        with patch('contain.rpc') as rpc, self.assertRaisesRegex(RuntimeError, 'Ambiguous'):
            contain.reconcile({'nonce': 5, 'data': '0x1234'})
        rpc.assert_not_called()

    def test_reconciliation_rejects_changed_transaction_or_reorg(self):
        item = {'hash': '0xhash', 'nonce': 5, 'data': '0x1234'}
        receipt = {'blockNumber': '0x10', 'blockHash': 'canonical', 'status': '0x1'}
        tx = {'from': contain.GOVERNOR, 'to': contain.CONTROLLER, 'input': '0x1234',
              'value': '0x0', 'nonce': '0x5', 'chainId': hex(4663)}
        for key, value in [('from', '0xwrong'), ('to', '0xwrong'), ('input', '0xwrong'),
                           ('value', '0x1'), ('nonce', '0x6'), ('chainId', '0x1')]:
            with self.subTest(key=key), patch('contain.rpc', side_effect=[receipt, dict(tx, **{key: value})]), \
                 self.assertRaisesRegex(RuntimeError, 'identity'):
                contain.reconcile(dict(item))
        with patch('contain.rpc', side_effect=[receipt, tx, {'hash': 'reorg'}]), \
             self.assertRaisesRegex(RuntimeError, 'noncanonical'):
            contain.reconcile(dict(item))

    def test_verified_receipt_marks_intent_confirmed(self):
        item = {'hash': '0xhash', 'nonce': 5, 'data': '0x1234'}
        receipt = {'blockNumber': '0x10', 'blockHash': 'canonical', 'status': '0x1'}
        tx = {'from': contain.GOVERNOR, 'to': contain.CONTROLLER, 'input': '0x1234',
              'value': '0x0', 'nonce': '0x5', 'chainId': hex(4663)}
        with patch('contain.rpc', side_effect=[receipt, tx, {'hash': 'canonical'}]):
            contain.reconcile(item)
        self.assertEqual(item['state'], 'confirmed')


if __name__ == '__main__':
    unittest.main()
