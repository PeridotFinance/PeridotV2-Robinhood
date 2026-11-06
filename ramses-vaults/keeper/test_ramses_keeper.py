import calendar
import datetime
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import ramses_keeper as rk


def utc(y, mo, d, h, mi):
    return calendar.timegm(datetime.datetime(y, mo, d, h, mi).timetuple())


class FakeChain:
    """Answers `cast` calls; records every send. Selectors are real (cast sig), everything else scripted."""

    def __init__(self, reverts=None, roles=None, gas_price=20_000_000):
        self.reverts = reverts or {}  # function name -> error signature
        self.roles = {'KEEPER_ROLE'} if roles is None else roles
        self.gas_price = gas_price
        self.now = 1793900000  # block timestamp
        self.sent = []

    def __call__(self, args, capture_output=True, text=True):
        real = subprocess.run
        if args[:2] in (['cast', 'sig'], ['cast', 'keccak']):
            return real(args, capture_output=True, text=True)
        out = lambda s, rc=0, err='': subprocess.CompletedProcess(args, rc, s, err)
        if args[1] == 'block':
            return out('%d\n' % self.now)
        if args[1] == 'gas-price':
            return out(str(self.gas_price) + '\n')
        if args[1] == 'call' and 'hasRole(bytes32,address)(bool)' in args:
            role = args[args.index('hasRole(bytes32,address)(bool)') + 1]
            names = {real(['cast', 'keccak', n], capture_output=True, text=True).stdout.strip(): n
                     for n in ('KEEPER_ROLE', 'CONFIG_ROLE', 'GUARDIAN_ROLE', 'IN_KIND_OPERATOR_ROLE')}
            names['0x' + '00' * 32] = 'DEFAULT_ADMIN'
            return out('true\n' if names.get(role) in self.roles else 'false\n')
        if args[1] == 'call':
            fn = args[3].split('(')[0]
            if fn in self.reverts:
                sel = real(['cast', 'sig', self.reverts[fn]], capture_output=True, text=True).stdout.strip()
                return out('', 1, 'execution reverted, data: "' + sel + '"')
            return out('0x\n')
        if args[1] == 'send':
            self.sent.append(args[3].split('(')[0])
            return out(json.dumps({'status': '0x1', 'transactionHash': '0x' + 'ab' * 32, 'gasUsed': '0x10'}))
        return out('')


def keeper(chain, state=None, clock=lambda: 1793900000):
    d = tempfile.mkdtemp()
    j = Path(d) / 'journal.json'
    j.write_text(json.dumps({'vault': '0x' + '11' * 20, 'pair': '0x' + '22' * 32}))
    s = Path(d) / 'state.json'
    if state is not None:
        s.write_text(state)
    return rk.Keeper(j, '0x' + '33' * 20, ['--unlocked'], s, rpc='http://x', clock=clock, runner=chain)


class Tests(unittest.TestCase):
    def test_empty_state_file_is_no_state(self):
        self.assertEqual(keeper(FakeChain(), state='').state, {})

    def test_refuses_when_keeper_holds_admin_power(self):
        for extra in ('CONFIG_ROLE', 'GUARDIAN_ROLE', 'IN_KIND_OPERATOR_ROLE', 'DEFAULT_ADMIN'):
            chain = FakeChain(roles={'KEEPER_ROLE', extra})
            lines, rc = keeper(chain).step(True)
            self.assertEqual(rc, 2, extra)
            self.assertEqual(chain.sent, [])

    def test_refuses_without_keeper_role(self):
        lines, rc = keeper(FakeChain(roles=set())).step(True)
        self.assertEqual((rc, lines[0]['detail']), (2, 'missing KEEPER_ROLE'))

    def test_waiting_reasons_send_nothing(self):
        chain = FakeChain(reverts={'recenter': 'RecenterNotNeeded()', 'rebalance': 'AllocationPaused()',
                                   'harvest': 'StaleOracle(address,uint256)'})
        lines, rc = keeper(chain).step(True)
        self.assertEqual(rc, 0)
        self.assertEqual(chain.sent, [])

    def test_coverage_gate_is_a_wait_not_a_fault(self):
        chain = FakeChain(reverts={'recenter': 'CoverageInsufficient(uint256,uint256,uint256)',
                                   'rebalance': 'CoverageInsufficient(uint256,uint256,uint256)', 'harvest': 'RecenterNotNeeded()'})
        lines, rc = keeper(chain).step(True)
        self.assertEqual(rc, 0)
        self.assertEqual(lines[0]['simulation'], 'CoverageInsufficient')

    def test_reward_claim_mismatch_needs_a_person(self):
        chain = FakeChain(reverts={'recenter': 'RewardClaimMismatch()', 'rebalance': 'RecenterNotNeeded()', 'harvest': 'RewardClaimMismatch()'})
        lines, rc = keeper(chain).step(True)
        self.assertEqual(rc, 2)
        self.assertEqual(lines[0]['action'], 'ATTENTION')
        self.assertEqual(chain.sent, [])

    def test_due_recenter_is_sent_only_with_execute(self):
        chain = FakeChain(reverts={'rebalance': 'RecenterNotNeeded()', 'harvest': 'RecenterNotNeeded()'})
        keeper(chain).step(False)
        self.assertEqual(chain.sent, [])
        keeper(chain).step(True)
        self.assertEqual(chain.sent, ['recenter'])

    def test_gas_cap_blocks_every_send(self):
        chain = FakeChain(gas_price=10**12, reverts={'rebalance': 'RecenterNotNeeded()'})
        lines, _ = keeper(chain).step(True)
        self.assertEqual(chain.sent, [])
        self.assertIn('wait (gas price above cap)', [l.get('action') for l in lines])

    def test_harvest_respects_its_interval(self):
        chain = FakeChain(reverts={'recenter': 'RecenterNotNeeded()', 'rebalance': 'RecenterNotNeeded()'})
        k = keeper(chain)
        k.step(True)
        self.assertEqual(chain.sent, ['harvest'])
        k.step(True)
        self.assertEqual(chain.sent, ['harvest'], 'second harvest inside HARVEST_INTERVAL')

    def test_session_rule_is_off_by_default(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('WEEKEND_RULE', None)
            self.assertIsNone(keeper(FakeChain()).session_action(utc(2026, 11, 13, 20, 45)))

    def test_session_rule_parks_before_the_last_close_and_redeploys_after_the_open(self):
        with mock.patch.dict(os.environ, {'WEEKEND_RULE': 'on'}):
            k = keeper(FakeChain())
            self.assertEqual(k.session_action(utc(2026, 11, 13, 20, 45)), 'park')  # Fri 15:45 New York, standard time
            self.assertIsNone(k.session_action(utc(2026, 11, 12, 20, 45)), 'Thursday is not before a gap')
            self.assertIsNone(k.session_action(utc(2026, 11, 13, 20, 15)), 'too early on Friday')
            k.state['parked'] = True
            self.assertIsNone(k.session_action(utc(2026, 11, 16, 14, 45)), 'Monday 9:45: market open only 15 minutes')
            self.assertEqual(k.session_action(utc(2026, 11, 16, 15, 5)), 'redeploy')

    def test_holiday_is_a_gap(self):
        with mock.patch.dict(os.environ, {'WEEKEND_RULE': 'on'}):
            # Wed 25 Nov 2026 before Thanksgiving (26 Nov closed): park before the close.
            self.assertEqual(keeper(FakeChain()).session_action(utc(2026, 11, 25, 20, 45)), 'park')

    def test_park_is_inert_until_the_vault_can_do_it(self):
        chain = FakeChain()
        with mock.patch.dict(os.environ, {'WEEKEND_RULE': 'on'}):
            k = keeper(chain, clock=lambda: utc(2026, 11, 13, 20, 45))
            lines, rc = k.step(True)
        self.assertEqual(lines[0]['action'], 'park_unsupported')
        self.assertEqual(chain.sent, [], 'no transaction: no keeper path removes liquidity without emergency mode')

    def test_harvest_and_recenter_wait_out_the_anti_sybil_window(self):
        # Wall time stays put: the hold must follow block time, which is what the gauge's threshold uses.
        chain = FakeChain(reverts={'rebalance': 'RecenterNotNeeded()'})
        k = keeper(chain, clock=lambda: 1793900000)
        k.step(True)  # recenter sent; harvest must not follow within 120 s
        self.assertEqual(chain.sent, ['recenter'])
        chain.now += 60
        lines, _ = k.step(True)
        self.assertEqual(chain.sent, ['recenter'], 'recenter and harvest both held inside the window')
        self.assertIn('wait (anti-sybil window after the last position change)', [l.get('action') for l in lines])
        chain.now += 120
        chain.reverts['recenter'] = 'RecenterNotNeeded()'  # no new change this time, so the claim is safe
        k.step(True)
        self.assertEqual(chain.sent, ['recenter', 'harvest'])

    def test_shortfall_above_ceiling_is_a_wait(self):
        chain = FakeChain(reverts={'recenter': 'RewardShortfallTooHigh(address,uint256,uint256)', 'rebalance': 'RecenterNotNeeded()',
                                   'harvest': 'RewardShortfallTooHigh(address,uint256,uint256)'})
        lines, rc = keeper(chain).step(True)
        self.assertEqual(rc, 0)
        self.assertEqual(chain.sent, [])

    def test_every_booked_shortfall_raises_an_alert(self):
        chain = FakeChain(reverts={'rebalance': 'RecenterNotNeeded()', 'harvest': 'RecenterNotNeeded()'})
        real_call = chain.__call__
        topic = rk.SHORTFALL_TOPIC
        def with_logs(args, capture_output=True, text=True):
            r = real_call(args)
            if args[1] == 'send':
                r.stdout = json.dumps({'status': '0x1', 'transactionHash': '0x' + 'cd' * 32, 'gasUsed': '0x10',
                                       'logs': [{'topics': [topic]}, {'topics': ['0x' + '00' * 32]}]})
            return r
        lines, _ = keeper(with_logs).step(True)
        sent = [l for l in lines if l.get('function') == 'recenter'][0]
        self.assertEqual((sent.get('alert'), sent.get('shortfallEvents')), ('RewardShortfallBooked', 1))


if __name__ == '__main__':
    unittest.main()
