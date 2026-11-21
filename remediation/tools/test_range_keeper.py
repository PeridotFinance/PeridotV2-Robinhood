import unittest
from unittest import mock
import range_keeper as k

REAL_PAIR_LIQUIDITY = k.pair_liquidity  # captured before setUpModule mocks it


def setUpModule():
    # Unit tests never talk to a chain: the price read is mocked everywhere (PriceCheckpointTests re-mocks it).
    patcher = mock.patch.object(k, 'stock_price', return_value=None)
    patcher.start()
    unittest.addModuleCleanup(patcher.stop)
    liquidity = mock.patch.object(k, 'pair_liquidity', return_value=1)  # PriceCheckpointTests re-mock it
    liquidity.start()
    unittest.addModuleCleanup(liquidity.stop)


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


class PriceCheckpointTests(unittest.TestCase):
    def setUp(self):
        k.BASELINE_PRICES.clear()
        k.LAST_PRICE_CHECKPOINTS.clear()
        k.LIQUIDITY_SKIP.clear()

    def _run(self, prices, execute=True, simulation=('due', False, ''), gas=(True, 1), sent_status='0x1',
             liquidity=10**12):
        fake = mock.Mock(returncode=0, stdout='{"status":"%s"}' % sent_status, stderr='', elapsed=1)
        with mock.patch.object(k, 'stock_price', side_effect=prices), \
                mock.patch.object(k, 'pair_liquidity', return_value=liquidity), \
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
        self.assertEqual(k.BASELINE_PRICES.get(k.PAIR.lower()), 200 * 10**18)

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
            k.BASELINE_PRICES.clear()
            k.LAST_PRICE_CHECKPOINTS.clear()
            codes, sim, send, journal = self._run([base, moved])
            self.assertEqual(codes, [0, 0])
            send.assert_called_once_with('checkpoint')
            journal.assert_called_once()
            self.assertEqual(k.BASELINE_PRICES.get(k.PAIR.lower()), moved)

    def test_baseline_is_the_last_checkpoint_so_slow_drift_accumulates(self):
        base = 200 * 10**18
        step1 = base + base * 30 // 10000
        step2 = base + base * 60 // 10000
        codes, sim, send, _ = self._run([base, step1, step2])
        self.assertEqual(send.call_count, 1)
        self.assertEqual(k.BASELINE_PRICES.get(k.PAIR.lower()), step2)

    def test_nothing_is_sent_without_execute(self):
        base = 200 * 10**18
        codes, sim, send, _ = self._run([base, base * 2], execute=False)
        self.assertEqual(codes, [0, 0])
        send.assert_not_called()
        self.assertEqual(k.BASELINE_PRICES.get(k.PAIR.lower()), base)

    def test_unpriceable_oracle_is_not_a_fault_and_keeps_the_baseline(self):
        base = 200 * 10**18
        codes, sim, send, _ = self._run([base, None, None])
        self.assertEqual(codes, [0, 0, 0])
        send.assert_not_called()
        self.assertEqual(k.BASELINE_PRICES.get(k.PAIR.lower()), base)

    def test_waiting_revert_such_as_a_stale_feed_keeps_the_baseline(self):
        base = 200 * 10**18
        codes, sim, send, _ = self._run([base, base * 2], simulation=('StaleOracle', True, 'x'))
        self.assertEqual(codes, [0, 0])
        send.assert_not_called()
        self.assertEqual(k.BASELINE_PRICES.get(k.PAIR.lower()), base)

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
        self.assertEqual(k.BASELINE_PRICES.get(k.PAIR.lower()), base)
        journal.assert_called_once()

    def test_attempts_are_rate_limited(self):
        base = 200 * 10**18
        codes, sim, send, _ = self._run([base, base * 2, base * 2], sent_status='0x0')
        self.assertEqual(send.call_count, 1)
        self.assertEqual(codes, [0, 3, 0])

    def test_no_open_liquidity_never_checkpoints_even_on_a_2_percent_move(self):
        base = 200 * 10**18
        codes, sim, send, journal = self._run([base, base * 102 // 100, base * 104 // 100], liquidity=0)
        self.assertEqual(codes, [0, 0, 0])
        send.assert_not_called()
        sim.assert_not_called()
        journal.assert_called_once()  # the skip is journalled once, not every cycle
        self.assertIsNone(k.BASELINE_PRICES.get(k.PAIR.lower()))

    def test_failed_liquidity_read_skips_and_is_journalled(self):
        base = 200 * 10**18
        codes, sim, send, journal = self._run([base, base * 2], liquidity=None)
        self.assertEqual(codes, [0, 0])
        send.assert_not_called()
        journal.assert_called_once()
        self.assertEqual(journal.call_args[0][0]['reason'], 'liquidity read failed')

    def test_open_liquidity_checkpoints_on_50_bp(self):
        base = 200 * 10**18
        codes, sim, send, journal = self._run([base, base + base * 50 // 10000], liquidity=1)
        send.assert_called_once_with('checkpoint')

    def test_pair_liquidity_parses_the_second_field_and_fails_safe(self):
        ok = mock.Mock(returncode=0, stdout='4238052 [4.238e6]\n777\n0\n0\n')
        with mock.patch.object(k, 'cast', return_value=ok):
            self.assertEqual(self._real_liquidity(), 777)
        for bad in (mock.Mock(returncode=1, stdout=''), mock.Mock(returncode=0, stdout='garbage')):
            with mock.patch.object(k, 'cast', return_value=bad):
                self.assertIsNone(self._real_liquidity())
        with mock.patch.object(k, 'cast', side_effect=RuntimeError('rpc failed')):
            self.assertIsNone(self._real_liquidity())

    def _real_liquidity(self):
        return REAL_PAIR_LIQUIDITY()

    def test_two_pairs_on_the_same_feed_keep_independent_baselines(self):
        a, b = k.PAIR, '0x' + '2b' * 32
        base = 200 * 10**18
        fake = mock.Mock(returncode=0, stdout='{"status":"0x1"}', stderr='', elapsed=1)
        price = {'p': base}
        clock = {'t': 1_800_000_000}
        with mock.patch.object(k, 'stock_price', side_effect=lambda: price['p']), \
                mock.patch.object(k, 'pair_liquidity', return_value=1), \
                mock.patch.object(k, 'simulate', return_value=('due', False, '')), \
                mock.patch.object(k, 'gas_price_ok', return_value=(True, 1)), \
                mock.patch.object(k, 'send', return_value=fake) as send, \
                mock.patch.object(k, 'journal'), \
                mock.patch.object(k.time, 'time', side_effect=lambda: clock['t']):
            try:
                for pair in (a, b):  # both record the same baseline
                    k.PAIR = pair
                    k.maybe_price_checkpoint(True)
                price['p'] = base + base * 60 // 10000
                k.PAIR = a
                k.maybe_price_checkpoint(True)  # pair a checkpoints and moves its baseline
                self.assertEqual(send.call_count, 1)
                k.PAIR = b
                k.maybe_price_checkpoint(True)  # pair b still sees the move from its own baseline
                self.assertEqual(send.call_count, 2)
                self.assertEqual(k.BASELINE_PRICES[a.lower()], price['p'])
                self.assertEqual(k.BASELINE_PRICES[b.lower()], price['p'])
            finally:
                k.PAIR = a

    def test_price_checkpoint_runs_before_and_does_not_hide_a_step_error(self):
        with mock.patch.object(k, 'maybe_price_checkpoint', return_value=3), \
                mock.patch.object(k, 'simulate', return_value=('RecenterLossTooHigh', False, 'x')):
            self.assertEqual(k.step(True), 2)
        with mock.patch.object(k, 'maybe_price_checkpoint', return_value=3), \
                mock.patch.object(k, '_step', return_value=0):
            self.assertEqual(k.step(True), 3)
