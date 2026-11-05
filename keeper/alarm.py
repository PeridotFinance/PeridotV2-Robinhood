"""Read-only liveness alarm for the cloud range keeper. It reads public chain data and, only if a
DigitalOcean token or context is configured, the app's status and run logs. It never signs or sends.

`classify` is pure: it turns one observation into one state. A job that exits 0 means "no page",
not "healthy": see the printed state. Chain reads cannot see a dead process while the keeper has no
reason to send (stale feed, paused pair), and only DigitalOcean can separate a dead process from
sends failing before they are mined.

    python3 keeper/alarm.py                   # prints the state, exits 1 on a page
    python3 keeper/alarm.py --notify          # also keeps one GitHub issue up to date (Actions only)
    python3 keeper/alarm.py --notify --drill  # pages without reading anything, to test delivery
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

RPC = 'https://rpc.mainnet.chain.robinhood.com'
CHAIN_ID = 4663
KEEPER = '0x82ABD8eE038c10F5bE00E5200C94265400999f7e'
VAULT = '0x280825b2d856706Ff7E0d6351CcB2e935E1a9A2f'
PAIR = '0xe2050352f4346597cc69d2776d99ae60c9440dfc28f9406ce66be0bbe3fb6b06'
FEED = '0x379EC4f7C378F34a1B47E4F3cbeBCbAC3E8E9F15'
APP_ID = '6dc249f8-8e9d-43d0-9ba1-fcbaaa6b5e9c'
ASSIGNEE = 'joshschcom'
ISSUE_TITLE = 'Keeper alarm: the range keeper needs attention'
ISSUE_AUTHOR = 'github-actions[bot]'

GAS_FLOOR_WEI = 300_000_000_000_000  # worker.MIN_GAS_WEI: below this the worker sends nothing
BURN_WEI_PER_ACTIVE_DAY = 500_000_000_000_000  # measured; override with ALARM_BURN_WEI_PER_DAY
LOW_RUNWAY_DAYS = 3
FEED_MAX_AGE = 43_200  # the guard's 12 h staleness bound
FEED_FRESH_FOR = 1_800  # give the worker time to act once the feed is usable again
CHECKPOINT_MAX_AGE = 7_800  # two missed hourly checkpoints plus ten minutes
HEARTBEAT_MAX_AGE = 900
HEAD_MAX_LAG = 900  # a head older than this means the node (or chain) is not current
SEND_FAILURES_PER_HOUR = 3
LOG_LINES = 400
FEED_ROUNDS_MAX = 64

PAGES = {'GAS_CRITICAL', 'GAS_LOW', 'STALLED', 'FAILED_SENDS', 'PROCESS_DOWN', 'RPC_FAILURE', 'DRILL'}
REQUIRED = ('now', 'head_lag', 'balance_wei', 'nonce', 'last_checkpoint', 'feed_updated_at', 'feed_fresh_for',
            'allocation_paused', 'emergency_mode')


def feed_fresh_for(now, updates):
    """Seconds the feed has been continuously usable. `updates` are updatedAt values, newest first."""
    if not updates or now - updates[0] > FEED_MAX_AGE:
        return 0
    start = updates[0]
    for older in updates[1:]:
        if start - older > FEED_MAX_AGE:
            break
        start = older
    return now - start


def _result(state, message, process, **fields):
    return dict(state=state, page=state in PAGES, message=message, process=process, **fields)


def _process_view(process, wall):
    """(status, why) where status is up, down or unknown. Absence of evidence is never 'up'."""
    if not process.get('configured'):
        return 'unknown', 'process status unknown: no DigitalOcean access configured'
    if process.get('phase') is None:
        return 'unknown', 'process status unknown: the DigitalOcean read failed'
    if process['phase'] != 'ACTIVE':
        return 'down', 'deployment phase is ' + str(process['phase'])
    if not process.get('logs_read'):
        return 'unknown', 'process status unknown: deployment ACTIVE but its logs could not be read'
    heartbeat = process.get('last_heartbeat')
    if heartbeat is None or wall - heartbeat > HEARTBEAT_MAX_AGE:
        return 'down', 'no worker heartbeat in the last ' + str(HEARTBEAT_MAX_AGE) + ' s'
    if process.get('execution_enabled') is False:
        return 'down', 'worker is running with EXECUTE=false, so it cannot send'
    return 'up', 'deployment ACTIVE, heartbeat ' + str(int(wall - heartbeat)) + ' s old'


def classify(o, burn_wei_per_day=BURN_WEI_PER_ACTIVE_DAY, low_days=LOW_RUNWAY_DAYS):
    """The one state for an observation. Pure: no clock, no reads."""
    proc = o.get('process') or {}
    status, why = _process_view(proc, o.get('wall', o.get('now') or 0))
    if proc.get('configured') and proc.get('phase') not in (None, 'ACTIVE'):
        return _result('PROCESS_DOWN', why, status)
    missing = [k for k in REQUIRED if o.get(k) is None]
    if missing or o['head_lag'] > HEAD_MAX_LAG:
        detail = 'reads failed: ' + ','.join(missing) if missing else 'chain head is ' + str(o['head_lag']) + ' s old'
        return _result('RPC_FAILURE', "The alarm's own reads failed, so nothing is known about the keeper: " + detail,
                       status)
    if status == 'down':  # only trusted once the RPC works: the worker's heartbeat needs the same RPC
        return _result('PROCESS_DOWN', why, status)
    failures = proc.get('send_failures_last_hour') or 0
    if failures >= SEND_FAILURES_PER_HOUR:
        return _result('FAILED_SENDS', str(failures) + ' failed sends in the worker log in the last hour', status)

    runway = (o['balance_wei'] - GAS_FLOOR_WEI) / burn_wei_per_day
    fields = {'runwayActiveDays': round(runway, 2)}
    if o['balance_wei'] < GAS_FLOOR_WEI:
        return _result('GAS_CRITICAL', 'Keeper balance is below the worker floor; it sends nothing until funded',
                       status, **fields)
    if runway < low_days:
        return _result('GAS_LOW', 'Under ' + str(low_days) + ' active days of gas left', status, **fields)
    if o['allocation_paused'] or o['emergency_mode']:
        return _result('EXPECTED_PAUSED', 'Pair is paused, so the worker cannot send. A dead process is invisible '
                       'from chain reads during this; ' + why, status, **fields)
    feed_age = o['now'] - o['feed_updated_at']
    if feed_age > FEED_MAX_AGE:
        return _result('EXPECTED_WAIT', 'Feed is ' + str(feed_age) + ' s old: expected oracle refusal. A dead process '
                       'is invisible from chain reads during this; ' + why, status, **fields)
    checkpoint_age = o['now'] - o['last_checkpoint']
    fields['checkpointAge'] = checkpoint_age
    if checkpoint_age > CHECKPOINT_MAX_AGE:
        if o['feed_fresh_for'] < FEED_FRESH_FOR:
            return _result('EXPECTED_WAIT', 'Feed only just became usable; giving the worker time to act; ' + why,
                           status, **fields)
        simulation = o.get('checkpoint_simulation') or 'not read'
        fields['checkpointSimulation'] = simulation
        base = 'Checkpoint is ' + str(checkpoint_age) + ' s old with gas, a fresh feed and no pause (simulation: ' \
               + simulation + '). '
        stall_nonce = o.get('stall_nonce')
        if failures:
            return _result('FAILED_SENDS', base + str(failures) + ' failed sends in the worker log', status, **fields)
        if stall_nonce is not None and o['nonce'] > stall_nonce:
            return _result('FAILED_SENDS', base + 'The nonce moved from ' + str(stall_nonce) + ' to ' + str(o['nonce'])
                           + ' without the checkpoint advancing', status, **fields)
        return _result('STALLED', base + 'The nonce is not moving. Dead process, hung worker or sends failing before '
                       'they are mined; chain reads cannot tell which. ' + why, status, **fields)
    if status == 'up':
        return _result('HEALTHY', 'Checkpoint ' + str(checkpoint_age) + ' s old; ' + why, status, **fields)
    return _result('CHAIN_OK_PROCESS_UNKNOWN', 'Checkpoint ' + str(checkpoint_age) + ' s old, which only shows the '
                   'worker sent recently; ' + why, status, **fields)


# --- reads ------------------------------------------------------------------------------------------------------


class Reverted(Exception):
    def __init__(self, data):
        super().__init__('reverted')
        self.data = data


def curl_post(body):
    # python's urllib stalled against this RPC; it also rejects requests without a curl-like agent.
    done = subprocess.run(['curl', '-s', '-4', '-m', '20', '-A', 'curl/8.0', '-H', 'content-type: application/json',
                           '--data-binary', '@-', RPC], input=body, capture_output=True, text=True, timeout=30)
    return done.stdout


def rpc(method, params, post=curl_post, sleep=time.sleep, attempts=3):
    """Retries transport failures and RPC errors; a contract revert is an answer and is not retried."""
    for attempt in range(attempts):
        try:
            answer = json.loads(post(json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params})))
            if 'error' in answer:
                if answer['error'].get('code') == 3:
                    raise Reverted(str(answer['error'].get('data', ''))[:10])
                raise RuntimeError('rpc error')
            if answer.get('result') is None:
                raise RuntimeError('empty result')
            return answer['result']
        except (ValueError, RuntimeError, OSError, subprocess.SubprocessError, AttributeError):
            if attempt + 1 == attempts:
                raise RuntimeError(method + ' failed') from None
            sleep(2 * (attempt + 1))


def _word(n):
    return format(n, '064x')


def read_chain(wall, post=curl_post, sleep=time.sleep):
    """Every field that could not be read stays None, and classify turns that into RPC_FAILURE."""
    o = {'wall': wall}

    def call(to, data, **extra):
        return rpc('eth_call', [dict(to=to, data=data, **extra), 'latest'], post, sleep)

    def words(answer):
        raw = answer[2:]
        return [int(raw[i:i + 64], 16) for i in range(0, len(raw), 64)]

    def attempt(read):
        try:
            return read()
        except (RuntimeError, Reverted, ValueError, IndexError, KeyError, TypeError):
            return None

    if attempt(lambda: int(rpc('eth_chainId', [], post, sleep), 16)) != CHAIN_ID:
        return o
    head = attempt(lambda: rpc('eth_getBlockByNumber', ['latest', False], post, sleep))
    o['now'] = attempt(lambda: int(head['timestamp'], 16))
    if o['now'] is None:
        return o
    o['head_lag'] = wall - o['now']
    o['balance_wei'] = attempt(lambda: int(rpc('eth_getBalance', [KEEPER, 'latest'], post, sleep), 16))
    o['nonce'] = attempt(lambda: int(rpc('eth_getTransactionCount', [KEEPER, 'latest'], post, sleep), 16))
    o['last_checkpoint'] = attempt(lambda: words(call(VAULT, '0x15977d45' + PAIR[2:]))[5])
    config = attempt(lambda: words(call(VAULT, '0xf1a8151c' + PAIR[2:])))
    if config and len(config) == 18:
        o['allocation_paused'], o['emergency_mode'] = bool(config[14]), bool(config[16])

    def rounds():
        latest = words(call(FEED, '0xfeaf968c'))
        round_id, updates = latest[0], [latest[3]]
        while updates[-1] > o['now'] - FEED_FRESH_FOR and len(updates) < FEED_ROUNDS_MAX:
            round_id -= 1
            try:
                updates.append(words(call(FEED, '0x9a6fc8f5' + _word(round_id)))[3])
            except Reverted:  # first round of this aggregator phase
                break
        return updates
    updates = attempt(rounds)
    if updates:
        o['feed_updated_at'] = updates[0]
        o['feed_fresh_for'] = feed_fresh_for(o['now'], updates)

    def simulation():  # what a send would do right now; explains a stall, decides nothing
        data = '0x52c11eea' + PAIR[2:] + _word(o['now'] + 120)
        try:
            call(VAULT, data, **{'from': KEEPER, 'gas': hex(3_000_000)})
            return 'would succeed'
        except Reverted as error:
            return 'reverts ' + error.data
    o['checkpoint_simulation'] = attempt(simulation)
    return o


def parse_run_logs(text, wall):
    """Only counts and times leave this function: log text is never echoed."""
    seen = {'last_heartbeat': None, 'execution_enabled': None, 'send_failures_last_hour': 0}
    for line in text.splitlines():
        match = re.match(r'\S+ \S+ (\{.*\})\s*$', line)
        if not match:
            continue
        try:
            row = json.loads(match.group(1))
        except ValueError:
            continue
        at = row.get('checkedAtUnix') or row.get('time') or 0
        if row.get('status') == 'heartbeat' and at >= (seen['last_heartbeat'] or 0):
            seen.update(last_heartbeat=at, execution_enabled=row.get('executionEnabled'))
        if str(row.get('action', '')).startswith(('SEND FAILED', 'REVERTED ON-CHAIN')) and wall - at <= 3600:
            seen['send_failures_last_hour'] += 1
    return seen


def read_process(wall, env=os.environ, run=subprocess.run):
    """Uses DIGITALOCEAN_ACCESS_TOKEN (doctl reads it itself) or, locally, ALARM_DOCTL_CONTEXT."""
    context = env.get('ALARM_DOCTL_CONTEXT')
    if not env.get('DIGITALOCEAN_ACCESS_TOKEN') and not context:
        return {'configured': False}
    base = ['doctl'] + (['--context', context] if context else [])

    def doctl(*args):
        done = run(base + list(args), capture_output=True, text=True, timeout=90)
        if done.returncode:
            raise RuntimeError('doctl failed')
        return done.stdout
    process = {'configured': True, 'phase': None, 'logs_read': False}
    try:
        app = json.loads(doctl('apps', 'get', APP_ID, '-o', 'json'))
        app = app[0] if isinstance(app, list) else app
        process['phase'] = (app.get('active_deployment') or {}).get('phase') or 'NONE'
        process.update(parse_run_logs(doctl('apps', 'logs', APP_ID, '--type', 'run', '--tail', str(LOG_LINES)), wall))
        process['logs_read'] = True
    except (RuntimeError, ValueError, OSError, AttributeError, subprocess.SubprocessError):
        pass
    return process


# --- notification -----------------------------------------------------------------------------------------------


def read_marker(issue):
    match = re.search(r'<!-- keeper-alarm (\{.*?\}) -->', (issue or {}).get('body') or '')
    if match:
        try:
            return json.loads(match.group(1))
        except ValueError:
            pass
    return {}


def issue_plan(result, issue, nonce):
    """What to do with the one alarm issue. Pure. A comment goes out only when the state changes."""
    marker = read_marker(issue)
    if not result['page']:
        return {'op': 'close', 'comment': 'Recovered: now ' + result['state'] + '. ' + result['message']} if issue \
            else {'op': 'none'}
    new_marker = {'state': result['state']}
    if result['state'] in ('STALLED', 'FAILED_SENDS'):  # the nonce when the stall was first seen
        stall_nonce = marker.get('stallNonce', nonce)
        if stall_nonce is not None:
            new_marker['stallNonce'] = stall_nonce
    body = '**' + result['state'] + '**: ' + result['message'] + '\n\nProcess: ' + result['process'] \
        + '\n\n<!-- keeper-alarm ' + json.dumps(new_marker) + ' -->\n'
    if not issue:
        return {'op': 'create', 'body': body}
    plan = {'op': 'update', 'body': body}
    if marker.get('state') != result['state']:
        plan['comment'] = 'Now ' + result['state'] + ': ' + result['message']
    return plan


def github_api(method, path, payload=None, env=os.environ):
    request = urllib.request.Request('https://api.github.com/repos/' + env['GITHUB_REPOSITORY'] + path, method=method,
                                     data=json.dumps(payload).encode() if payload is not None else None)
    request.add_header('Authorization', 'Bearer ' + env['GITHUB_TOKEN'])
    request.add_header('Accept', 'application/vnd.github+json')
    with urllib.request.urlopen(request, timeout=30) as answer:
        return json.loads(answer.read() or 'null')


def find_issue(api=github_api):
    """Only an open issue this workflow's own bot opened counts: anyone can open issues on a public repo."""
    for issue in api('GET', '/issues?state=open&creator=' + urllib.request.quote(ISSUE_AUTHOR) + '&per_page=100'):
        if issue.get('title') == ISSUE_TITLE and (issue.get('user') or {}).get('login') == ISSUE_AUTHOR:
            return issue
    return None


def apply_plan(plan, issue, api=github_api):
    if plan['op'] == 'create':
        api('POST', '/issues', {'title': ISSUE_TITLE, 'body': plan['body'], 'assignees': [ASSIGNEE]})
        return
    if plan['op'] == 'none':
        return
    number = str(issue['number'])
    if plan.get('comment'):
        api('POST', '/issues/' + number + '/comments', {'body': plan['comment']})
    if plan['op'] == 'update':
        api('PATCH', '/issues/' + number, {'body': plan['body']})
    else:
        api('PATCH', '/issues/' + number, {'state': 'closed'})


def run(argv=None, env=os.environ, reader=read_chain, process_reader=read_process, finder=find_issue,
        applier=apply_plan, clock=time.time, out=sys.stdout):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--notify', action='store_true', help='keep the GitHub issue up to date')
    parser.add_argument('--drill', action='store_true', help='page without reading anything, to test delivery')
    args = parser.parse_args(argv)
    notify_failed = False
    issue = None
    if args.notify:
        try:
            issue = finder()
        except (OSError, ValueError, KeyError):
            notify_failed = True
    if args.drill:
        observation = {}
        result = _result('DRILL', 'Delivery test, not a real alarm. Run the workflow again without drill to close this.',
                         'unknown')
    else:
        wall = int(clock())
        observation = reader(wall)
        observation['process'] = process_reader(wall)
        observation['stall_nonce'] = read_marker(issue).get('stallNonce')
        burn = int(env.get('ALARM_BURN_WEI_PER_DAY') or BURN_WEI_PER_ACTIVE_DAY)
        result = classify(observation, burn_wei_per_day=burn)
    if args.notify and not notify_failed:
        try:
            applier(issue_plan(result, issue, observation.get('nonce')), issue)
        except (OSError, ValueError, KeyError):
            notify_failed = True
    report = {'observation': {k: v for k, v in observation.items() if k != 'process'},
              'process': observation.get('process'), 'result': result, 'notifyFailed': notify_failed}
    print(json.dumps(report, indent=1), file=out)
    print(('::error::' if result['page'] else '::notice::') + result['state'] + ': ' + result['message'], file=out)
    if env.get('GITHUB_STEP_SUMMARY'):
        with open(env['GITHUB_STEP_SUMMARY'], 'a') as summary:
            summary.write('**' + result['state'] + '** (process: ' + result['process'] + ')\n\n' + result['message'] + '\n')
    return 1 if result['page'] or notify_failed else 0


if __name__ == '__main__':
    sys.exit(run())
