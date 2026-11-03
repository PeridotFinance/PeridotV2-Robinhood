import bisect
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import alarm as a  # noqa: E402

NOW = 1_800_000_000
ETH = 10 ** 18
GOOD_BALANCE = 4 * ETH // 1000
LOW_LINE = a.GAS_FLOOR_WEI + a.LOW_RUNWAY_DAYS * a.BURN_WEI_PER_ACTIVE_DAY


def observed(**changes):
    base = dict(wall=NOW, now=NOW, head_lag=5, balance_wei=GOOD_BALANCE, nonce=180, last_checkpoint=NOW - 600,
                feed_updated_at=NOW - 3000, feed_fresh_for=90_000, allocation_paused=False, emergency_mode=False,
                checkpoint_simulation='would succeed', process={'configured': False})
    base.update(changes)
    return base


def up(**changes):
    base = dict(configured=True, phase='ACTIVE', logs_read=True, last_heartbeat=NOW - 100, execution_enabled=True,
                send_failures_last_hour=0)
    base.update(changes)
    return base


STUCK = dict(last_checkpoint=NOW - 7801)


class ClassifyTests(unittest.TestCase):
    def assertState(self, state, page, **changes):
        result = a.classify(observed(**changes))
        self.assertEqual((result['state'], result['page']), (state, page), result['message'])
        return result

    def test_healthy_needs_process_evidence(self):
        self.assertState('HEALTHY', False, process=up())

    def test_without_digitalocean_it_never_claims_healthy(self):
        result = self.assertState('CHAIN_OK_PROCESS_UNKNOWN', False)
        self.assertEqual(result['process'], 'unknown')
        self.assertIn('process status unknown', result['message'])

    def test_failed_digitalocean_read_is_unknown_not_healthy(self):
        self.assertState('CHAIN_OK_PROCESS_UNKNOWN', False, process={'configured': True, 'phase': None})
        self.assertState('CHAIN_OK_PROCESS_UNKNOWN', False, process=up(logs_read=False))

    def test_gas_critical(self):
        result = self.assertState('GAS_CRITICAL', True, balance_wei=a.GAS_FLOOR_WEI - 1)
        result = a.classify(observed(balance_wei=a.GAS_FLOOR_WEI // 2))
        self.assertLess(result['runwayActiveDays'], 0)

    def test_gas_boundaries(self):
        self.assertState('GAS_LOW', True, balance_wei=a.GAS_FLOOR_WEI)
        self.assertState('GAS_LOW', True, balance_wei=LOW_LINE - 1)
        self.assertState('CHAIN_OK_PROCESS_UNKNOWN', False, balance_wei=LOW_LINE)

    def test_burn_is_configurable(self):
        balance = a.GAS_FLOOR_WEI + 3 * a.BURN_WEI_PER_ACTIVE_DAY // 2
        self.assertEqual(a.classify(observed(balance_wei=balance))['state'], 'GAS_LOW')
        lower = a.BURN_WEI_PER_ACTIVE_DAY // 4
        self.assertEqual(a.classify(observed(balance_wei=balance), burn_wei_per_day=lower)['state'],
                         'CHAIN_OK_PROCESS_UNKNOWN')

    def test_expected_oracle_refusal_is_quiet(self):
        result = self.assertState('EXPECTED_WAIT', False, feed_updated_at=NOW - a.FEED_MAX_AGE - 1, feed_fresh_for=0,
                                  last_checkpoint=NOW - 200_000)
        self.assertIn('invisible', result['message'])

    def test_feed_age_boundary(self):
        self.assertState('CHAIN_OK_PROCESS_UNKNOWN', False, feed_updated_at=NOW - a.FEED_MAX_AGE)
        self.assertState('EXPECTED_WAIT', False, feed_updated_at=NOW - a.FEED_MAX_AGE - 1)

    def test_low_gas_still_pages_while_the_feed_is_stale(self):
        self.assertState('GAS_LOW', True, balance_wei=LOW_LINE - 1, feed_updated_at=NOW - a.FEED_MAX_AGE - 1)
        self.assertState('GAS_CRITICAL', True, balance_wei=1, feed_updated_at=NOW - a.FEED_MAX_AGE - 1)

    def test_grace_after_the_feed_returns(self):
        self.assertState('EXPECTED_WAIT', False, feed_fresh_for=a.FEED_FRESH_FOR - 1, **STUCK)
        self.assertState('STALLED', True, feed_fresh_for=a.FEED_FRESH_FOR, **STUCK)

    def test_stalled_execution_and_its_checkpoint_boundary(self):
        result = self.assertState('STALLED', True, **STUCK)
        self.assertIn('chain reads cannot tell which', result['message'])
        self.assertEqual(result['checkpointSimulation'], 'would succeed')
        self.assertState('CHAIN_OK_PROCESS_UNKNOWN', False, last_checkpoint=NOW - 7800)

    def test_stall_reports_why_a_send_would_fail(self):
        result = self.assertState('STALLED', True, checkpoint_simulation='reverts 0x12345678', **STUCK)
        self.assertIn('reverts 0x12345678', result['message'])

    def test_dead_process_without_digitalocean_is_a_stall(self):
        result = self.assertState('STALLED', True, **STUCK)
        self.assertEqual(result['process'], 'unknown')
        self.assertIn('Dead process', result['message'])

    def test_dead_process_with_digitalocean(self):
        self.assertState('PROCESS_DOWN', True, process=up(phase='ERROR'))
        self.assertState('PROCESS_DOWN', True, process=up(phase='NONE'))
        self.assertState('PROCESS_DOWN', True, process=up(last_heartbeat=NOW - a.HEARTBEAT_MAX_AGE - 1))
        self.assertState('PROCESS_DOWN', True, process=up(last_heartbeat=None))

    def test_heartbeat_boundary(self):
        self.assertState('HEALTHY', False, process=up(last_heartbeat=NOW - a.HEARTBEAT_MAX_AGE))

    def test_monitor_mode_worker_cannot_send(self):
        result = self.assertState('PROCESS_DOWN', True, process=up(execution_enabled=False))
        self.assertIn('EXECUTE=false', result['message'])

    def test_dead_process_is_seen_with_digitalocean_during_quiet_states(self):
        self.assertState('PROCESS_DOWN', True, process=up(phase='ERROR'), feed_updated_at=NOW - 200_000)
        self.assertState('PROCESS_DOWN', True, process=up(phase='ERROR'), allocation_paused=True)

    def test_deliberate_pause_is_reported_not_paged(self):
        for flags in ({'allocation_paused': True}, {'emergency_mode': True}):
            result = self.assertState('EXPECTED_PAUSED', False, **flags, **STUCK)
            self.assertIn('invisible', result['message'])

    def test_pause_does_not_hide_gas(self):
        self.assertState('GAS_LOW', True, allocation_paused=True, balance_wei=LOW_LINE - 1)

    def test_rpc_failure_for_each_read_is_distinct_and_never_healthy(self):
        for key in a.REQUIRED:
            with self.subTest(key):
                result = self.assertState('RPC_FAILURE', True, process=up(), **{key: None})
                self.assertIn(key, result['message'])

    def test_rpc_failure_is_not_blamed_on_the_worker_heartbeat(self):
        self.assertState('RPC_FAILURE', True, now=None, process=up(last_heartbeat=NOW - 5000))

    def test_a_failed_deployment_is_down_even_when_the_rpc_is_down_too(self):
        self.assertState('PROCESS_DOWN', True, now=None, process=up(phase='ERROR'))

    def test_lagging_head_is_an_rpc_failure(self):
        self.assertState('RPC_FAILURE', True, head_lag=a.HEAD_MAX_LAG + 1)
        self.assertState('CHAIN_OK_PROCESS_UNKNOWN', False, head_lag=a.HEAD_MAX_LAG)

    def test_failed_sends_from_chain_nonce_moves_but_checkpoint_does_not(self):
        result = self.assertState('FAILED_SENDS', True, stall_nonce=170, nonce=171, **STUCK)
        self.assertIn('170', result['message'])
        self.assertState('STALLED', True, stall_nonce=171, nonce=171, **STUCK)

    def test_failed_sends_from_logs_with_a_stale_checkpoint(self):
        self.assertState('FAILED_SENDS', True, process=up(send_failures_last_hour=1), **STUCK)

    def test_failed_sends_from_logs_while_the_checkpoint_is_still_fresh(self):
        self.assertState('HEALTHY', False, process=up(send_failures_last_hour=a.SEND_FAILURES_PER_HOUR - 1))
        self.assertState('FAILED_SENDS', True, process=up(send_failures_last_hour=a.SEND_FAILURES_PER_HOUR))


class FeedFreshnessTests(unittest.TestCase):
    def test_continuous_run_is_measured_back_to_the_last_long_gap(self):
        gap = a.FEED_MAX_AGE
        self.assertEqual(a.feed_fresh_for(NOW, [NOW - 100, NOW - 500, NOW - 500 - gap - 1]), 500)
        self.assertEqual(a.feed_fresh_for(NOW, [NOW - 100, NOW - 500, NOW - 500 - gap]), 500 + gap)

    def test_stale_or_empty_is_zero(self):
        self.assertEqual(a.feed_fresh_for(NOW, [NOW - a.FEED_MAX_AGE - 1]), 0)
        self.assertEqual(a.feed_fresh_for(NOW, [NOW - a.FEED_MAX_AGE]), a.FEED_MAX_AGE)
        self.assertEqual(a.feed_fresh_for(NOW, []), 0)


class ReplayTests(unittest.TestCase):
    """The real 10-18 to 11-03 history on the workflow's 20-minute schedule."""

    @classmethod
    def setUpClass(cls):
        data = json.loads((Path(__file__).parent / 'testdata' / 'alarm_replay_2026-10-18_11-03.json').read_text())
        balances, checkpoints, feed = data['balances'], data['checkpoints'], data['feedUpdates']
        times = [row[0] for row in balances]
        cls.rows = []
        for t in range(data['window'][0], data['window'][1], 1200):
            i = bisect.bisect_right(feed, t)
            updates = feed[:i][::-1]
            o = observed(wall=t, now=t, balance_wei=balances[bisect.bisect_right(times, t) - 1][1],
                         last_checkpoint=checkpoints[bisect.bisect_right(checkpoints, t) - 1], nonce=0,
                         feed_updated_at=updates[0], feed_fresh_for=a.feed_fresh_for(t, updates))
            cls.rows.append((t, o, a.classify(o)))
        cls.gas_out = next(r[0] for r in balances if r[1] < a.GAS_FLOOR_WEI)

    def first(self, state):
        return next(t for t, _, r in self.rows if r['state'] == state)

    def test_gas_low_fired_days_before_the_gas_ran_out(self):
        self.assertGreater(self.gas_out - self.first('GAS_LOW'), 72 * 3600)

    def test_gas_critical_fired_within_a_cycle_of_running_out(self):
        self.assertLess(self.first('GAS_CRITICAL') - self.gas_out, 1200)

    def test_no_false_page_while_the_feed_is_stale(self):
        stale = [(o, r) for _, o, r in self.rows if o['now'] - o['feed_updated_at'] > a.FEED_MAX_AGE]
        self.assertGreater(len(stale), 30)
        for o, r in stale:
            if o['balance_wei'] >= LOW_LINE:
                self.assertEqual(r['state'], 'EXPECTED_WAIT')
            else:
                self.assertIn(r['state'], ('GAS_LOW', 'GAS_CRITICAL'))

    def test_nothing_but_gas_ever_paged(self):
        self.assertEqual({r['state'] for _, _, r in self.rows if r['page']}, {'GAS_LOW', 'GAS_CRITICAL'})

    def test_recovered_after_funding(self):
        self.assertFalse(self.rows[-1][2]['page'])


class FakeRpc:
    def __init__(self, **overrides):
        config = [0] * 18
        config[17] = 1
        self.config = config
        self.answers = {'eth_chainId': hex(a.CHAIN_ID), 'eth_getBalance': hex(GOOD_BALANCE),
                        'eth_getTransactionCount': hex(180)}
        self.answers.update(overrides)
        self.calls = []
        self.broken = set()
        self.checkpoint_data = None

    @staticmethod
    def encode(values):
        return '0x' + ''.join(format(v, '064x') for v in values)

    def __call__(self, body):
        request = json.loads(body)
        method = request['method']
        self.calls.append(method)
        if method in self.broken:
            return 'not json'
        if method == 'eth_getBlockByNumber':
            return json.dumps({'result': {'timestamp': hex(NOW)}})
        if method != 'eth_call':
            return json.dumps({'result': self.answers[method]})
        call = request['params'][0]
        data = call['data']
        if data[:10] in self.broken:
            return json.dumps({'error': {'code': -32000, 'message': 'boom'}})
        if data.startswith('0x15977d45'):
            return json.dumps({'result': self.encode([1, 2, 3, 4, 5, NOW - 600])})
        if data.startswith('0xf1a8151c'):
            return json.dumps({'result': self.encode(self.config)})
        if data.startswith('0xfeaf968c'):
            return json.dumps({'result': self.encode([50, 7, NOW - 100, NOW - 100, 50])})
        if data.startswith('0x9a6fc8f5'):
            return json.dumps({'result': self.encode([49, 7, NOW - 4000, NOW - 4000, 49])})
        if data.startswith('0x52c11eea'):
            self.checkpoint_data = call
            if self.checkpoint_data.get('from') != a.KEEPER:
                raise AssertionError('simulate as the keeper')
            return json.dumps({'error': {'code': 3, 'message': 'execution reverted', 'data': '0x12345678abcd'}})
        raise AssertionError(data)


class ReadTests(unittest.TestCase):
    def read(self, fake):
        return a.read_chain(NOW + 5, post=fake, sleep=lambda _: None)

    def test_decodes_every_field(self):
        o = self.read(FakeRpc())
        self.assertEqual((o['now'], o['head_lag'], o['balance_wei'], o['nonce'], o['last_checkpoint']),
                         (NOW, 5, GOOD_BALANCE, 180, NOW - 600))
        self.assertEqual((o['allocation_paused'], o['emergency_mode']), (False, False))
        self.assertEqual((o['feed_updated_at'], o['feed_fresh_for']), (NOW - 100, 4000))
        self.assertEqual(o['checkpoint_simulation'], 'reverts 0x12345678')

    def test_pause_flags_come_from_the_right_words(self):
        fake = FakeRpc()
        fake.config[15] = 1  # swapsPaused is live and must not read as a pause
        self.assertEqual(self.read(fake)['allocation_paused'], False)
        fake.config[14] = 1
        self.assertEqual(self.read(fake)['allocation_paused'], True)
        fake.config[14], fake.config[16] = 0, 1
        self.assertEqual(self.read(fake)['emergency_mode'], True)

    def test_a_failed_read_becomes_rpc_failure(self):
        for broken in ('eth_getBalance', 'eth_getTransactionCount', 'eth_getBlockByNumber', 'eth_chainId',
                       '0x15977d45', '0xf1a8151c', '0xfeaf968c'):
            with self.subTest(broken):
                fake = FakeRpc()
                fake.broken.add(broken)
                self.assertEqual(a.classify(self.read(fake))['state'], 'RPC_FAILURE')

    def test_wrong_chain_is_an_rpc_failure(self):
        self.assertEqual(a.classify(self.read(FakeRpc(eth_chainId='0x1')))['state'], 'RPC_FAILURE')

    def test_retries_then_succeeds(self):
        sleeps = []
        answers = iter(['', 'not json', json.dumps({'result': '0x1'})])
        self.assertEqual(a.rpc('m', [], post=lambda _: next(answers), sleep=sleeps.append), '0x1')
        self.assertEqual(sleeps, [2, 4])

    def test_gives_up_after_three_attempts(self):
        count = []

        def broken(_):
            count.append(1)
            return ''
        with self.assertRaises(RuntimeError):
            a.rpc('m', [], post=broken, sleep=lambda _: None)
        self.assertEqual(len(count), 3)

    def test_a_revert_is_an_answer_and_is_not_retried(self):
        count = []

        def reverted(_):
            count.append(1)
            return json.dumps({'error': {'code': 3, 'data': '0xabcdef0011'}})
        with self.assertRaises(a.Reverted):
            a.rpc('m', [], post=reverted, sleep=lambda _: None)
        self.assertEqual(len(count), 1)


LOGS = '\n'.join([
    'range-keeper 2026-11-03T12:49:52.7Z {"status": "heartbeat", "checkedAtUnix": %d, "executionEnabled": true}' % (NOW - 700),
    'range-keeper 2026-11-03T12:49:53.5Z {"time": %d, "simulation": "RecenterNotNeeded", "action": "wait"}' % (NOW - 699),
    'range-keeper 2026-11-03T12:49:55.0Z {"time": %d, "rebalance": "CheckpointStale", "action": "SEND FAILED (checkpoint)"}' % (NOW - 650),
    'range-keeper 2026-11-03T12:49:55.0Z {"time": %d, "action": "REVERTED ON-CHAIN"}' % (NOW - 640),
    'range-keeper 2026-11-03T10:00:00.0Z {"time": %d, "action": "SEND FAILED"}' % (NOW - 3601),
    'range-keeper 2026-11-03T12:54:52.7Z {"status": "heartbeat", "checkedAtUnix": %d, "executionEnabled": false}' % (NOW - 100),
    'garbage line',
    'range-keeper 2026-11-03T12:54:52.7Z {broken',
])


class ProcessReadTests(unittest.TestCase):
    def test_parses_the_real_log_format(self):
        self.assertEqual(a.parse_run_logs(LOGS, NOW), {'last_heartbeat': NOW - 100, 'execution_enabled': False,
                                                       'send_failures_last_hour': 2})

    def doctl(self, app='{"active_deployment": {"phase": "ACTIVE"}}', logs=LOGS, code=0):
        def run(args, **_):
            return mock.Mock(returncode=code, stdout=logs if 'logs' in args else app)
        return run

    def test_no_token_means_not_configured_and_runs_nothing(self):
        run = mock.Mock()
        self.assertEqual(a.read_process(NOW, env={}, run=run), {'configured': False})
        run.assert_not_called()

    def test_token_reads_status_and_logs(self):
        process = a.read_process(NOW, env={'DIGITALOCEAN_ACCESS_TOKEN': 't'}, run=self.doctl())
        self.assertEqual((process['phase'], process['logs_read'], process['last_heartbeat']),
                         ('ACTIVE', True, NOW - 100))

    def test_doctl_failure_is_unknown(self):
        process = a.read_process(NOW, env={'DIGITALOCEAN_ACCESS_TOKEN': 't'}, run=self.doctl(code=1))
        self.assertEqual(a.classify(observed(process=process))['state'], 'CHAIN_OK_PROCESS_UNKNOWN')

    def test_no_active_deployment_is_down(self):
        process = a.read_process(NOW, env={'DIGITALOCEAN_ACCESS_TOKEN': 't'}, run=self.doctl(app='{}'))
        self.assertEqual(a.classify(observed(process=process))['state'], 'PROCESS_DOWN')

    def test_failed_sends_flow_through_from_logs(self):
        process = up(**a.parse_run_logs(LOGS, NOW))
        process['execution_enabled'] = True
        self.assertEqual(a.classify(observed(process=process, **STUCK))['state'], 'FAILED_SENDS')


class IssueTests(unittest.TestCase):
    PAGE = {'state': 'GAS_LOW', 'page': True, 'message': 'm', 'process': 'unknown'}
    QUIET = {'state': 'EXPECTED_WAIT', 'page': False, 'message': 'm', 'process': 'unknown'}

    def issue(self, marker):
        return {'number': 7, 'body': '<!-- keeper-alarm ' + json.dumps(marker) + ' -->'}

    def test_first_page_creates_one_issue(self):
        self.assertEqual(a.issue_plan(self.PAGE, None, 1)['op'], 'create')

    def test_same_state_updates_silently(self):
        plan = a.issue_plan(self.PAGE, self.issue({'state': 'GAS_LOW'}), 1)
        self.assertEqual(plan['op'], 'update')
        self.assertNotIn('comment', plan)

    def test_state_change_comments(self):
        plan = a.issue_plan(self.PAGE, self.issue({'state': 'STALLED'}), 1)
        self.assertIn('GAS_LOW', plan['comment'])

    def test_quiet_closes_an_open_issue_and_otherwise_does_nothing(self):
        self.assertEqual(a.issue_plan(self.QUIET, self.issue({'state': 'GAS_LOW'}), 1)['op'], 'close')
        self.assertEqual(a.issue_plan(self.QUIET, None, 1), {'op': 'none'})

    def test_stall_nonce_is_remembered_from_the_first_stall(self):
        stalled = dict(self.PAGE, state='STALLED')
        plan = a.issue_plan(stalled, None, 180)
        self.assertEqual(a.read_marker(plan), {'state': 'STALLED', 'stallNonce': 180})
        plan = a.issue_plan(dict(stalled, state='FAILED_SENDS'), self.issue({'state': 'STALLED', 'stallNonce': 180}), 190)
        self.assertEqual(a.read_marker(plan)['stallNonce'], 180)

    def test_only_the_workflows_own_issue_counts(self):
        spoof = {'title': a.ISSUE_TITLE, 'user': {'login': 'someone'}, 'number': 1}
        own = {'title': a.ISSUE_TITLE, 'user': {'login': a.ISSUE_AUTHOR}, 'number': 2}
        other = {'title': 'x', 'user': {'login': a.ISSUE_AUTHOR}, 'number': 3}
        self.assertEqual(a.find_issue(lambda *_: [spoof, other, own])['number'], 2)
        self.assertIsNone(a.find_issue(lambda *_: [spoof, other]))

    def test_create_assigns_the_operator(self):
        api = mock.Mock()
        a.apply_plan({'op': 'create', 'body': 'b'}, None, api)
        self.assertEqual(api.call_args.args[2]['assignees'], [a.ASSIGNEE])

    def test_close_comments_then_closes(self):
        api = mock.Mock()
        a.apply_plan({'op': 'close', 'comment': 'c'}, {'number': 7}, api)
        self.assertEqual([c.args[:2] for c in api.call_args_list], [('POST', '/issues/7/comments'), ('PATCH', '/issues/7')])
        self.assertEqual(api.call_args.args[2], {'state': 'closed'})


class RunTests(unittest.TestCase):
    def go(self, argv, observation, process=None, finder=None, applier=None, env=None):
        out = io.StringIO()
        code = a.run(argv, env=env or {}, reader=lambda _: dict(observation), process_reader=lambda _: process or {'configured': False},
                     finder=finder or (lambda: None), applier=applier or mock.Mock(), clock=lambda: NOW, out=out)
        return code, out.getvalue()

    def test_page_exits_nonzero_and_quiet_exits_zero(self):
        self.assertEqual(self.go([], observed(balance_wei=1))[0], 1)
        self.assertEqual(self.go([], observed())[0], 0)

    def test_rpc_failure_exits_nonzero(self):
        code, out = self.go([], {'wall': NOW})
        self.assertEqual(code, 1)
        self.assertIn('RPC_FAILURE', out)

    def test_nothing_is_notified_without_the_flag(self):
        finder, applier = mock.Mock(), mock.Mock()
        self.go([], observed(balance_wei=1), finder=finder, applier=applier)
        finder.assert_not_called()
        applier.assert_not_called()

    def test_notify_failure_fails_the_job_even_when_quiet(self):
        def broken():
            raise OSError('down')
        self.assertEqual(self.go(['--notify'], observed(), finder=broken)[0], 1)

    def test_stall_nonce_comes_from_the_open_issue(self):
        issue = {'number': 1, 'body': '<!-- keeper-alarm {"state": "STALLED", "stallNonce": 170} -->'}
        code, out = self.go(['--notify'], observed(**STUCK), finder=lambda: issue)
        self.assertIn('FAILED_SENDS', out)

    def test_drill_pages_without_reading(self):
        reader, applier = mock.Mock(), mock.Mock()
        out = io.StringIO()
        code = a.run(['--notify', '--drill'], env={}, reader=reader, process_reader=reader, finder=lambda: None,
                     applier=applier, clock=lambda: NOW, out=out)
        self.assertEqual(code, 1)
        reader.assert_not_called()
        self.assertEqual(applier.call_args.args[0]['op'], 'create')

    def test_burn_env_is_used(self):
        balance = a.GAS_FLOOR_WEI + a.BURN_WEI_PER_ACTIVE_DAY
        self.assertEqual(self.go([], observed(balance_wei=balance))[0], 1)
        self.assertEqual(self.go([], observed(balance_wei=balance),
                                 env={'ALARM_BURN_WEI_PER_DAY': str(a.BURN_WEI_PER_ACTIVE_DAY // 4)})[0], 0)

    def test_summary_is_written_for_actions(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'summary')
            self.go([], observed(), env={'GITHUB_STEP_SUMMARY': path})
            self.assertIn('CHAIN_OK_PROCESS_UNKNOWN', Path(path).read_text())


if __name__ == '__main__':
    unittest.main()
