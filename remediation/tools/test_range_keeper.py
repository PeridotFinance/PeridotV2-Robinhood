import unittest
from unittest import mock
import range_keeper as k


class RangeKeeperTests(unittest.TestCase):
    def test_waiting_reasons_are_not_errors(self):
        for sig in ['RecenterNotNeeded()', 'RecenterCooldown()', 'RecenterRateLimited()', 'AllocationPaused()']:
            code = k.selector(sig)
            name, waiting = k.classify('Error: execution reverted, data: "' + code + '"')
            self.assertEqual(name, sig.split('(')[0])
            self.assertTrue(waiting)

    def test_loss_bound_failures_need_attention(self):
        code = k.selector('RecenterLossTooHigh()')
        name, waiting = k.classify('data: "' + code + '"')
        self.assertEqual(name, 'RecenterLossTooHigh')
        self.assertFalse(waiting)

    def test_unknown_error_needs_attention(self):
        name, waiting = k.classify('data: "0xdeadbeef"')
        self.assertTrue(name.startswith('unknown'))
        self.assertFalse(waiting)
        self.assertEqual(k.classify('')[0], 'unknown')

    def test_nothing_is_sent_without_execute(self):
        with mock.patch.object(k, 'simulate', return_value=('due', False, '')), \
                mock.patch.object(k, 'gas_price_ok', return_value=(True, 1)), \
                mock.patch.object(k, 'send') as send:
            self.assertEqual(k.step(False), 0)
            send.assert_not_called()

    def test_gas_price_cap_blocks_sending(self):
        with mock.patch.object(k, 'simulate', return_value=('due', False, '')), \
                mock.patch.object(k, 'gas_price_ok', return_value=(False, 10**12)), \
                mock.patch.object(k, 'send') as send:
            self.assertEqual(k.step(True), 0)
            send.assert_not_called()

    def test_execute_sends_once_and_journals(self):
        fake = mock.Mock(returncode=0, stdout='{"transactionHash":"0x1"}', stderr='')
        with mock.patch.object(k, 'simulate', return_value=('due', False, '')), \
                mock.patch.object(k, 'gas_price_ok', return_value=(True, 1)), \
                mock.patch.object(k, 'send', return_value=fake) as send, \
                mock.patch.object(k, 'journal') as journal:
            self.assertEqual(k.step(True), 0)
            send.assert_called_once()
            journal.assert_called_once()

    def test_attention_exit_code_for_unexpected_revert(self):
        with mock.patch.object(k, 'simulate', return_value=('RecenterLossTooHigh', False, 'x')):
            self.assertEqual(k.step(True), 2)


if __name__ == '__main__':
    unittest.main()
