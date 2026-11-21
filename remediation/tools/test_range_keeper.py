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

    def test_missing_keeper_role_waits(self):
        code = k.selector('AccessControlUnauthorizedAccount(address,bytes32)')
        name, waiting = k.classify('data: "' + code + '0000"')
        self.assertEqual(name, 'AccessControlUnauthorizedAccount')
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
        fake = mock.Mock(returncode=0, stdout='{"status":"0x1"}', stderr='', elapsed=1)
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


class RebalanceTests(unittest.TestCase):
    def setUp(self):  # the value gate has its own tests in keeper/test_worker.py
        gate = mock.patch.object(k, 'rebalance_value_gate', return_value=(True, {}))
        gate.start()
        self.addCleanup(gate.stop)

    def test_idle_assets_are_deployed_when_the_real_call_succeeds(self):
        fake = mock.Mock(returncode=0, stdout='{"status":"0x1"}', stderr='', elapsed=1)
        with mock.patch.object(k, 'simulate', return_value=('due', False, '')), \
                mock.patch.object(k, 'gas_price_ok', return_value=(True, 1)), \
                mock.patch.object(k, 'last_sent', return_value=0), \
                mock.patch.object(k, 'send', return_value=fake) as send, \
                mock.patch.object(k, 'journal'):
            self.assertEqual(k.maybe_rebalance(True), 0)
            send.assert_called_once_with('rebalance')

    def test_nothing_idle_waits(self):
        with mock.patch.object(k, 'simulate', return_value=('InsufficientLiquidity', True, '')), \
                mock.patch.object(k, 'send') as send:
            self.assertEqual(k.maybe_rebalance(True), 0)
            send.assert_not_called()

    def test_dust_idle_balance_is_not_a_fault(self):
        with mock.patch.object(k, 'simulate', return_value=('InvalidConfiguration', False, '')), \
                mock.patch.object(k, 'send') as send:
            self.assertEqual(k.maybe_rebalance(True), 0)
            send.assert_not_called()

    def test_loss_bound_on_dust_fees_is_not_a_fault(self):
        with mock.patch.object(k, 'simulate', return_value=('DeployLossTooHigh', False, '')), \
                mock.patch.object(k, 'send') as send:
            self.assertEqual(k.maybe_rebalance(True), 0)
            send.assert_not_called()

    def test_stale_checkpoint_is_refreshed_first_then_rebalanced(self):
        sims = iter([('CheckpointStale', False, ''), ('due', False, ''), ('due', False, '')])
        fake = mock.Mock(returncode=0, stdout='{"status":"0x1"}', stderr='', elapsed=1)
        with mock.patch.object(k, 'simulate', side_effect=lambda fn='recenter': next(sims)), \
                mock.patch.object(k, 'gas_price_ok', return_value=(True, 1)), \
                mock.patch.object(k, 'last_sent', return_value=0), \
                mock.patch.object(k, 'send', return_value=fake) as send, \
                mock.patch.object(k, 'journal'):
            self.assertEqual(k.maybe_rebalance(True), 0)
            self.assertEqual([c.args[0] for c in send.call_args_list], ['checkpoint', 'rebalance'])

    def test_rebalance_is_rate_limited_to_once_an_hour(self):
        with mock.patch.object(k, 'simulate', return_value=('due', False, '')), \
                mock.patch.object(k, 'gas_price_ok', return_value=(True, 1)), \
                mock.patch.object(k, 'last_sent', return_value=k.time.time()), \
                mock.patch.object(k, 'send') as send:
            self.assertEqual(k.maybe_rebalance(True), 0)
            send.assert_not_called()

    def test_dry_run_never_sends(self):
        with mock.patch.object(k, 'simulate', return_value=('due', False, '')), \
                mock.patch.object(k, 'gas_price_ok', return_value=(True, 1)), \
                mock.patch.object(k, 'send') as send:
            self.assertEqual(k.maybe_rebalance(False), 0)
            send.assert_not_called()

    def test_recenter_waiting_falls_through_to_rebalance(self):
        with mock.patch.object(k, 'simulate', return_value=('RecenterNotNeeded', True, '')), \
                mock.patch.object(k, 'maybe_rebalance', return_value=0) as rebalance:
            self.assertEqual(k.step(True), 0)
            rebalance.assert_called_once_with(True)


class OutcomeTests(unittest.TestCase):
    def _r(self, code, out, elapsed=1):
        r = mock.Mock(returncode=code, stdout=out, stderr='')
        r.elapsed = elapsed
        return r

    def test_mined_success(self):
        self.assertEqual(k.outcome_of(self._r(0, '{"status":"0x1"}')), 'sent')

    def test_mined_revert_is_not_sent_even_though_cast_exits_zero(self):
        self.assertTrue(k.outcome_of(self._r(0, '{"status":"0x0"}')).startswith('REVERTED ON-CHAIN'))

    def test_slow_password_entry_is_called_out(self):
        text = k.outcome_of(self._r(0, '{"status":"0x0"}', elapsed=400))
        self.assertIn('password prompt', text)

    def test_submission_failure_and_garbage(self):
        self.assertEqual(k.outcome_of(self._r(1, '')), 'SEND FAILED')
        self.assertEqual(k.outcome_of(self._r(0, 'not json')), 'UNCONFIRMED')

    def test_deadline_stays_within_the_vault_limit(self):
        self.assertLessEqual(k.DEADLINE_SECONDS, 300)



RETIRED = '0x94696d767e65a75581145646960FA0eC886cE5d2'
DEDICATED = '0x82ABD8eE038c10F5bE00E5200C94265400999f7e'


class SenderTests(unittest.TestCase):
    """There is no default sender, and the retired deployer is refused in every casing."""

    def setUp(self):
        self.saved = (k.SENDER, k.ACCOUNT, k.KEYSTORE, k.UNLOCKED)
        k.SENDER, k.ACCOUNT, k.KEYSTORE, k.UNLOCKED = None, None, None, False

    def tearDown(self):
        k.SENDER, k.ACCOUNT, k.KEYSTORE, k.UNLOCKED = self.saved

    def test_no_default_sender(self):
        with self.assertRaisesRegex(RuntimeError, 'sender is required'):
            k.sender()

    def test_simulation_without_a_sender_fails_before_any_call(self):
        with mock.patch.object(k, 'chain_deadline', return_value=1), mock.patch.object(k, 'cast') as cast:
            with self.assertRaisesRegex(RuntimeError, 'sender is required'):
                k.simulate()
            cast.assert_not_called()

    def test_retired_signer_is_refused_in_any_casing(self):
        for address in (RETIRED, RETIRED.lower(), '0x' + RETIRED[2:].upper()):
            with self.assertRaisesRegex(RuntimeError, 'retired signer'):
                k.configure(sender=address)
            self.assertIsNone(k.SENDER)
            with self.assertRaisesRegex(RuntimeError, 'retired signer'):
                k.check_sender(address)

    def test_retired_signer_set_directly_is_still_refused(self):
        k.SENDER = RETIRED
        with self.assertRaisesRegex(RuntimeError, 'retired signer'):
            k.sender()

    def test_malformed_sender_is_refused(self):
        for bad in ('', '0x1234', 'not-an-address', None):
            with self.assertRaises(RuntimeError):
                k.check_sender(bad)

    def test_dedicated_sender_is_used(self):
        k.configure(sender=DEDICATED)
        self.assertEqual(k.sender(), DEDICATED)

    def test_send_without_keystore_or_account_fails_before_any_call(self):
        k.configure(sender=DEDICATED)
        with mock.patch.object(k, 'chain_deadline', return_value=1), mock.patch.object(k, 'cast') as cast:
            with self.assertRaisesRegex(RuntimeError, 'account is required'):
                k.send()
            cast.assert_not_called()

    def test_cli_requires_sender(self):
        with mock.patch('sys.argv', ['range_keeper.py']), mock.patch.object(k, 'cast') as cast, \
                mock.patch('sys.stderr'):
            with self.assertRaises(SystemExit):
                k.main()
            cast.assert_not_called()

    def test_cli_refuses_retired_sender_and_account(self):
        for argv in (['--sender', RETIRED], ['--sender', DEDICATED, '--account', 'robinhood-deployer']):
            with mock.patch('sys.argv', ['range_keeper.py', *argv]), mock.patch.object(k, 'cast') as cast:
                with self.assertRaisesRegex(SystemExit, 'retired'):
                    k.main()
                cast.assert_not_called()

    def test_cli_execute_requires_an_account(self):
        with mock.patch('sys.argv', ['range_keeper.py', '--sender', DEDICATED, '--execute']), \
                mock.patch.object(k, 'cast') as cast:
            with self.assertRaisesRegex(SystemExit, '--account'):
                k.main()
            cast.assert_not_called()

    def test_no_runtime_default_address_remains(self):
        self.assertFalse(hasattr(k, 'GOVERNOR'))
        self.assertIsNone(k.ACCOUNT)


if __name__ == '__main__':
    unittest.main()
