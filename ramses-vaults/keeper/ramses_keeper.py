#!/usr/bin/env python3
"""Ramses range keeper: the live range keeper's loop (remediation/tools/range_keeper.py) ported to RamsesBoostedVault.

One step, in order, each call simulated exactly as it would be sent and sent only when the vault accepts it:
  1. recenter   when the contract says a recentre is due (eligibility, cooldown, budget and loss bound are on chain)
  2. rebalance  deploys idle assets; a stale checkpoint is refreshed first (checkpoint collects the pool fees)
  3. harvest    claims gauge rewards, at most every HARVEST_INTERVAL seconds
  4. weekend    optional session-gap rule, OFF by default (WEEKEND_RULE=on), see `session_action`

    python3 ramses_keeper.py --journal <deployment journal> [--execute] [--once]

Addresses come from the deployment journal written by script/DeployRamsesInKind.s.sol. Without --execute nothing is sent.
The keeper wallet holds only KEEPER_ROLE on the vault; it refuses to run if it holds any admin power.
Logs are JSON lines and never contain secrets or transaction bodies.
"""
import datetime
import json
import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

RPC = os.environ.get('RH_RPC', 'https://rpc.mainnet.chain.robinhood.com')
GAS_LIMIT = 3_000_000
DEADLINE_SECONDS = 290
MAX_GAS_PRICE_WEI = int(os.environ.get('MAX_GAS_PRICE_WEI', 100_000_000))  # 0.1 gwei default, as for the other keepers
REBALANCE_MIN_INTERVAL = 3600
HARVEST_INTERVAL = int(os.environ.get('HARVEST_INTERVAL', 6 * 3600))
# Ramses anti-sybil: a claim within 99 s of the position's last change pays nothing (fork-measured 2026-11-06, both gauges);
# from 100 s it pays earned() exactly. Wait this long after any position change before harvest or recenter (both claim).
ANTI_SYBIL_WAIT = int(os.environ.get('ANTI_SYBIL_WAIT', 120))
SHORTFALL_TOPIC = '0x2d781398914aa8cb0d4b37e42c95b14bec56d2691b335b01806cae7b2338d8db'  # keccak256("RewardShortfallBooked(bytes32,address,uint256,uint256,uint256)")
REBALANCE_MIN_VALUE_MULTIPLE = 10.0
REBALANCE_GAS_ESTIMATE = 750_000
ETH_USD_FEED = '0x78F3556b67E17Df817D51Ef5a990cDaF09E8d3A9'
ETH_USD_MAX_AGE = 86_400
STOCK_DECIMALS = 18  # Robinhood stock tokens
USDG_DECIMALS = 6
FORBIDDEN_ROLES = ('0x' + '00' * 32, 'CONFIG_ROLE', 'GUARDIAN_ROLE', 'IN_KIND_OPERATOR_ROLE')

# selector -> waiting. Waiting means "nothing to do right now", not a fault.
REASONS = {
    'RecenterNotNeeded()': True,
    'RecenterCooldown()': True,
    'RecenterRateLimited()': True,
    'AllocationPaused()': True,
    'EmergencyMode()': True,
    'RangePolicyDisabled()': True,
    'AccessControlUnauthorizedAccount(address,bytes32)': True,
    'StaleOracle(address,uint256)': True,
    'StockOraclePaused()': True,
    'PriceDeviation(uint256,uint256)': True,
    'InsufficientLiquidity()': True,
    'CoverageInsufficient(uint256,uint256,uint256)': True,  # the router's coverage gate: new exposure refused
    'RecenterLossTooHigh()': False,
    'DeployLossTooHigh()': False,
    'InvalidConfiguration()': False,
    'InvalidDeadline()': False,
    'CheckpointStale()': False,
    # The gauge paid less than earned() (anti-sybil). Recenter and harvest cannot proceed: a person must decide.
    'RewardClaimMismatch()': False,
    # Above the per-pair forfeiture ceiling: the claim would forfeit too much now (inside the anti-sybil window). Retry later.
    'RewardShortfallTooHigh(address,uint256,uint256)': True,
}
DUST_REBALANCES = ('InvalidConfiguration', 'DeployLossTooHigh')  # nothing deployable yet; see range_keeper.py


class Keeper:
    def __init__(self, journal_path, sender, signer, state_path, rpc=RPC, clock=time.time, runner=subprocess.run):
        d = json.loads(Path(journal_path).read_text())
        self.vault, self.pair, self.rpc = d['vault'], d['pair'], rpc
        self.sender, self.signer, self.clock, self.runner = sender, signer, clock, runner
        self.state_path = Path(state_path)
        self.last_tx = {}
        text = self.state_path.read_text().strip() if self.state_path.exists() else ''
        self.state = json.loads(text) if text else {}

    # ------------------------------------------------------------------ chain access
    def sh(self, *args, check=True):
        r = self.runner(list(args), capture_output=True, text=True)
        if check and r.returncode:
            raise RuntimeError((r.stderr or r.stdout).strip()[:300])
        return r

    def cast(self, *args, check=True):
        return self.sh('cast', *args, '--rpc-url', self.rpc, check=check)

    def chain_now(self):
        return int(self.cast('block', 'latest', '--field', 'timestamp').stdout.strip())

    def deadline(self):
        return max(self.chain_now(), int(self.clock())) + DEADLINE_SECONDS

    def classify(self, output):
        m = re.search(r'0x[0-9a-fA-F]{8}', output or '')
        if not m:
            return 'unknown', False
        code = m.group(0).lower()
        for sig, waiting in REASONS.items():
            if self.sh('cast', 'sig', sig).stdout.strip().lower() == code:
                return sig.split('(')[0], waiting
        return 'unknown(' + code + ')', False

    def simulate(self, fn):
        r = self.cast('call', self.vault, fn + '(bytes32,uint256)', self.pair, str(self.deadline()),
                      '--from', self.sender, '--gas-limit', str(GAS_LIMIT), check=False)
        if r.returncode == 0:
            return 'due', False
        return self.classify(r.stderr + r.stdout)

    def send(self, fn):
        r = self.cast('send', self.vault, fn + '(bytes32,uint256)', self.pair, str(self.deadline()), *self.signer,
                      '--from', self.sender, '--gas-limit', str(GAS_LIMIT), '--json', check=False)
        if r.returncode:
            return 'SEND FAILED'
        try:
            receipt = json.loads(r.stdout)
        except ValueError:
            return 'UNCONFIRMED'
        self.last_tx = {'tx': receipt.get('transactionHash'), 'gasUsed': int(str(receipt.get('gasUsed', '0x0')), 16)}
        # Section 8a: alert on EVERY booked gauge underpayment (RewardShortfallBooked from the adapter).
        booked = [l for l in receipt.get('logs', []) if (l.get('topics') or [''])[0].lower() == SHORTFALL_TOPIC]
        if booked:
            self.last_tx['alert'] = 'RewardShortfallBooked'
            self.last_tx['shortfallEvents'] = len(booked)
        return 'sent' if str(receipt.get('status')).lower() in ('0x1', '1') else 'REVERTED ON-CHAIN'

    def gas_ok(self):
        return int(self.cast('gas-price').stdout.strip()) <= MAX_GAS_PRICE_WEI

    def save(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self.state, indent=2) + '\n')

    # ------------------------------------------------------------------ safety
    def check_roles(self):
        keeper_role = self.sh('cast', 'keccak', 'KEEPER_ROLE').stdout.strip()
        if self.cast('call', self.vault, 'hasRole(bytes32,address)(bool)', keeper_role, self.sender).stdout.strip() != 'true':
            return 'missing KEEPER_ROLE'
        for name in FORBIDDEN_ROLES:
            role = name if name.startswith('0x') else self.sh('cast', 'keccak', name).stdout.strip()
            if self.cast('call', self.vault, 'hasRole(bytes32,address)(bool)', role, self.sender).stdout.strip() == 'true':
                return 'keeper wallet holds ' + name + '; refusing'
        return None

    # ------------------------------------------------------------------ value gate (as range_keeper.py)
    def rebalance_worth_it(self):
        try:
            led = [int(x.split()[0]) for x in self.cast('call', self.vault,
                   'ledger(bytes32)(uint256,uint256,uint256,uint256,uint256,uint64)', self.pair).stdout.strip().splitlines()]
            guard = self.cast('call', self.vault, 'oracleGuard()(address)').stdout.strip()
            sp, up = [int(x.split()[0]) for x in self.cast('call', guard, 'pricesUSD18(bytes32)(uint256,uint256)', self.pair).stdout.strip().splitlines()]
            sd, ud = STOCK_DECIMALS, USDG_DECIMALS
            feed = [int(x.split()[0]) for x in self.cast('call', ETH_USD_FEED, 'latestRoundData()(uint80,int256,uint256,uint256,uint80)').stdout.strip().splitlines()]
            gas = int(self.cast('gas-price').stdout.strip())
            if min(sp, up, feed[1], gas) <= 0 or self.clock() - feed[3] > ETH_USD_MAX_AGE:
                raise ValueError('unusable price')
            matched = min(led[2] * sp // 10 ** sd, led[3] * up // 10 ** ud)
            cost = REBALANCE_GAS_ESTIMATE * gas * feed[1] // 10 ** 8
        except (RuntimeError, ValueError, IndexError, OSError):
            return False, {'event': 'rebalance_value_read_failed'}
        multiple = float(os.environ.get('REBALANCE_MIN_VALUE_MULTIPLE', REBALANCE_MIN_VALUE_MULTIPLE))
        fields = {'matchedUsd': round(matched / 1e18, 6), 'gasCostUsd': round(cost / 1e18, 6)}
        return matched >= multiple * cost, fields

    # ------------------------------------------------------------------ session-gap rule (off by default)
    def session_action(self, now=None):
        """'park' in the window before the last close of the week (or before a holiday gap),
        'redeploy' once the market has been open REDEPLOY_AFTER_OPEN_MINUTES, else None. OFF unless WEEKEND_RULE=on."""
        if os.environ.get('WEEKEND_RULE', 'off') != 'on' or ZoneInfo is None:
            return None
        ny = datetime.datetime.fromtimestamp(now if now is not None else self.clock(), ZoneInfo('America/New_York'))
        park_before = int(os.environ.get('PARK_BEFORE_CLOSE_MINUTES', 30))
        redeploy_after = int(os.environ.get('REDEPLOY_AFTER_OPEN_MINUTES', 30))
        open_t, close_t = datetime.time(9, 30), datetime.time(16, 0)
        trading = lambda day: day.weekday() < 5 and day.date().isoformat() not in NYSE_HOLIDAYS_2026_2027
        if not trading(ny):
            return 'park' if self.state.get('parked') is False else None
        minutes = ny.hour * 60 + ny.minute
        next_day = ny + datetime.timedelta(days=1)
        last_session_before_gap = not trading(next_day)
        if last_session_before_gap and close_t.hour * 60 - park_before <= minutes < close_t.hour * 60:
            return 'park'
        if self.state.get('parked') and minutes >= open_t.hour * 60 + open_t.minute + redeploy_after:
            return 'redeploy'
        return None

    def park(self, execute):
        # RamsesBoostedVault gives KEEPER_ROLE no way to move live liquidity to idle: clearRange needs an empty position and
        # recenter redeploys at once. emergencyDecrease is guardian-only and sets emergency mode, which only the timelock clears.
        # A keeper park needs a vault capability first (see the keeper report). Until then this logs and does nothing.
        return {'action': 'park_unsupported', 'detail': 'no keeper path removes live liquidity without emergency mode'}

    # ------------------------------------------------------------------ one step
    def step(self, execute):
        out = []
        bad = self.check_roles()
        if bad:
            return [{'status': 'refused', 'detail': bad}], 2
        session = self.session_action()
        if session == 'park':
            out.append(dict(self.park(execute), status='session_gap'))
            return out, 0
        rc = 0
        # The gauge's anti-sybil threshold runs on block time, so the hold does too (wall time can differ, e.g. on a fork).
        since = self.chain_now() - self.state.get('lastModified', 0)
        name, waiting = self.simulate('recenter')
        if name == 'due' and since < ANTI_SYBIL_WAIT:
            out.append({'function': 'recenter', 'action': 'wait (anti-sybil window after the last position change)'})
            name = 'held'
        elif name == 'due':
            res = self.send('recenter') if execute and self.gas_ok() else ('wait (gas price above cap)' if execute else 'due (dry run)')
            if res == 'sent':
                self.state['lastModified'] = self.chain_now()
            out.append(dict(self.last_tx if res in ('sent', 'REVERTED ON-CHAIN') else {}, function='recenter', action=res))
            rc = rc if res in ('sent',) or not execute else 3
        elif name != 'held':
            out.append({'function': 'recenter', 'simulation': name, 'action': 'wait' if waiting else 'ATTENTION'})
            rc = rc if waiting else 2
        name, waiting = self.simulate('rebalance')
        waiting = waiting or name in DUST_REBALANCES
        if name == 'CheckpointStale':
            cname, cwait = self.simulate('checkpoint')
            if cname == 'due' and execute:
                res = self.send('checkpoint')
                out.append(dict(self.last_tx, function='checkpoint', action=res))
                name, waiting = self.simulate('rebalance')
                waiting = waiting or name in DUST_REBALANCES
            else:
                out.append({'function': 'checkpoint', 'simulation': cname, 'action': 'due (dry run)' if cname == 'due' else 'wait'})
        if name == 'due' and self.chain_now() - self.state.get('lastModified', 0) < ANTI_SYBIL_WAIT:
            # Two position changes under the gauge's threshold apart send the reward accrued between them to the Ramses
            # treasury on the next claim (fork-measured). Spacing them out avoids that forfeiture entirely.
            out.append({'function': 'rebalance', 'action': 'wait (anti-sybil window after the last position change)'})
        elif name == 'due':
            worth, fields = self.rebalance_worth_it()
            recent = self.clock() - self.state.get('lastRebalance', 0) < REBALANCE_MIN_INTERVAL
            if worth and execute and not recent and not self.gas_ok():
                out.append(dict(fields, function='rebalance', action='wait (gas price above cap)'))
            elif worth and execute and not recent:
                res = self.send('rebalance')
                if res == 'sent':
                    self.state['lastRebalance'] = int(self.clock())
                    self.state['lastModified'] = self.chain_now()
                out.append(dict(fields, **self.last_tx, function='rebalance', action=res))
            else:
                out.append(dict(fields, function='rebalance', action='wait' if not worth or recent else 'due (dry run)'))
        else:
            out.append({'function': 'rebalance', 'simulation': name, 'action': 'wait' if waiting else 'ATTENTION'})
        if self.chain_now() - self.state.get('lastModified', 0) < ANTI_SYBIL_WAIT:
            out.append({'function': 'harvest', 'action': 'wait (anti-sybil window after the last position change)'})
        elif self.chain_now() - self.state.get('lastHarvest', 0) >= HARVEST_INTERVAL:
            name, waiting = self.simulate('harvest')
            if name == 'due' and execute and not self.gas_ok():
                out.append({'function': 'harvest', 'action': 'wait (gas price above cap)'})
            elif name == 'due' and execute:
                res = self.send('harvest')
                if res == 'sent':
                    self.state['lastHarvest'] = self.chain_now()
                out.append(dict(self.last_tx, function='harvest', action=res))
            else:
                out.append({'function': 'harvest', 'simulation': name, 'action': 'due (dry run)' if name == 'due' else 'wait'})
        if session == 'redeploy':
            self.state['parked'] = False
            out.append({'status': 'session_gap', 'action': 'redeployed by the steps above'})
        self.save()
        return out, rc


# NYSE full-day closures (the session-gap rule treats them like a weekend).
NYSE_HOLIDAYS_2026_2027 = {
    '2026-11-26', '2026-12-25', '2027-01-01', '2027-01-18', '2027-02-15', '2027-03-26', '2027-05-31',
    '2027-06-18', '2027-07-05', '2027-09-06', '2027-11-25', '2027-12-24',
}


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--journal', required=True)
    p.add_argument('--sender', default=os.environ.get('RAMSES_KEEPER'))
    p.add_argument('--keystore', default=os.environ.get('RAMSES_KEEPER_KEYSTORE'))
    p.add_argument('--password-file', default=os.environ.get('RAMSES_KEEPER_PASSWORD_FILE'))
    p.add_argument('--unlocked', action='store_true', help='local fork rehearsal only')
    p.add_argument('--state', default=os.environ.get('RAMSES_KEEPER_STATE', 'ramses-keeper-state.json'))
    p.add_argument('--execute', action='store_true')
    p.add_argument('--once', action='store_true')
    p.add_argument('--interval', type=int, default=int(os.environ.get('INTERVAL_SECONDS', 300)))
    a = p.parse_args(argv)
    signer = ['--unlocked'] if a.unlocked else (['--keystore', a.keystore, '--password-file', a.password_file] if a.keystore else [])
    if a.execute and not signer:
        print(json.dumps({'status': 'refused', 'detail': 'no signer configured'}))
        return 2
    k = Keeper(a.journal, a.sender, signer, a.state)
    while True:
        lines, rc = k.step(a.execute)
        for line in lines:
            print(json.dumps(dict(line, time=int(time.time()))), flush=True)
        if a.once:
            return rc
        time.sleep(a.interval)


if __name__ == '__main__':
    sys.exit(main())
