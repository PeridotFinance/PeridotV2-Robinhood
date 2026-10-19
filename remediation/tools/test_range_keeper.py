import unittest
from unittest import mock
import range_keeper as k


def setUpModule():
    # Unit tests never talk to a chain: the price read is mocked everywhere (PriceCheckpointTests re-mocks it).
    patcher = mock.patch.object(k, 'stock_price', return_value=None)
    patcher.start()
    unittest.addModuleCleanup(patcher.stop)


class RangeKeeperTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(k, 'maybe_price_checkpoint', return_value=0)
        patcher.start()
        self.addCleanup(patcher.stop)

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


if __name__ == '__main__':
    unittest.main()


class PriceCheckpointTests(unittest.TestCase):
    def setUp(self):
        k.BASELINE_PRICE = None
        k.LAST_PRICE_CHECKPOINT = 0

    def _run(self, prices, execute=True, simulation=('due', False, ''), gas=(True, 1), sent_status='0x1'):
        fake = mock.Mock(returncode=0, stdout='{"status":"%s"}' % sent_status, stderr='', elapsed=1)
        with mock.patch.object(k, 'stock_price', side_effect=prices), \
                mock.patch.object(k, 'simulate', return_value=simulation) as sim, \
                mock.patch.object(k, 'gas_price_ok', return_value=gas), \
                mock.patch.object(k, 'send', return_value=fake) as send, \
                mock.patch.object(k, 'journal') as journal:
            codes = [k.maybe_price_checkpoint(execute) for _ in prices]
        return codes, sim, send, journal

    def test_first_observation_only_sets_the_baseline(self):
        codes, sim, send, _ = self._run([200 * 10**18])
        self.assertEqual(codes, [0])
        send.assert_not_called()
        self.assertEqual(k.BASELINE_PRICE, 200 * 10**18)

    def test_move_below_threshold_does_nothing_in_both_directions(self):
        base = 200 * 10**18
        down = base - base * 49 // 10000
        up = base + base * 49 // 10000
        codes, sim, send, _ = self._run([base, down, up])
        self.assertEqual(codes, [0, 0, 0])
        send.assert_not_called()

    def test_exact_threshold_checkpoints_down_and_up(self):
        base = 200 * 10**18
        for moved in (base - base * 50 // 10000, base + base * 50 // 10000):
            k.BASELINE_PRICE = None
            k.LAST_PRICE_CHECKPOINT = 0
            codes, sim, send, journal = self._run([base, moved])
            self.assertEqual(codes, [0, 0])
            send.assert_called_once_with('checkpoint')
            journal.assert_called_once()
            self.assertEqual(k.BASELINE_PRICE, moved)

    def test_baseline_is_the_last_checkpoint_so_slow_drift_accumulates(self):
        base = 200 * 10**18
        step1 = base + base * 30 // 10000
        step2 = base + base * 60 // 10000
        codes, sim, send, _ = self._run([base, step1, step2])
        self.assertEqual(send.call_count, 1)
        self.assertEqual(k.BASELINE_PRICE, step2)

    def test_nothing_is_sent_without_execute(self):
        base = 200 * 10**18
        codes, sim, send, _ = self._run([base, base * 2], execute=False)
        self.assertEqual(codes, [0, 0])
        send.assert_not_called()
        self.assertEqual(k.BASELINE_PRICE, base)

    def test_unpriceable_oracle_is_not_a_fault_and_keeps_the_baseline(self):
        base = 200 * 10**18
        codes, sim, send, _ = self._run([base, None, None])
        self.assertEqual(codes, [0, 0, 0])
        send.assert_not_called()
        self.assertEqual(k.BASELINE_PRICE, base)

    def test_waiting_revert_such_as_a_stale_feed_keeps_the_baseline(self):
        base = 200 * 10**18
        codes, sim, send, _ = self._run([base, base * 2], simulation=('StaleOracle', True, 'x'))
        self.assertEqual(codes, [0, 0])
        send.assert_not_called()
        self.assertEqual(k.BASELINE_PRICE, base)

    def test_unexpected_revert_needs_attention(self):
        base = 200 * 10**18
        codes, sim, send, _ = self._run([base, base * 2], simulation=('unknown(0xdeadbeef)', False, 'x'))
        self.assertEqual(codes, [0, 2])
        send.assert_not_called()

    def test_gas_cap_blocks_sending(self):
        base = 200 * 10**18
        codes, sim, send, _ = self._run([base, base * 2], gas=(False, 10**12))
        self.assertEqual(codes, [0, 0])
        send.assert_not_called()

    def test_failed_send_keeps_the_baseline_and_needs_attention(self):
        base = 200 * 10**18
        codes, sim, send, journal = self._run([base, base * 2], sent_status='0x0')
        self.assertEqual(codes, [0, 3])
        self.assertEqual(k.BASELINE_PRICE, base)
        journal.assert_called_once()

    def test_attempts_are_rate_limited(self):
        base = 200 * 10**18
        codes, sim, send, _ = self._run([base, base * 2, base * 2], sent_status='0x0')
        self.assertEqual(send.call_count, 1)
        self.assertEqual(codes, [0, 3, 0])

    def test_price_checkpoint_runs_before_and_does_not_hide_a_step_error(self):
        with mock.patch.object(k, 'maybe_price_checkpoint', return_value=3), \
                mock.patch.object(k, 'simulate', return_value=('RecenterLossTooHigh', False, 'x')):
            self.assertEqual(k.step(True), 2)
        with mock.patch.object(k, 'maybe_price_checkpoint', return_value=3), \
                mock.patch.object(k, '_step', return_value=0):
            self.assertEqual(k.step(True), 3)
