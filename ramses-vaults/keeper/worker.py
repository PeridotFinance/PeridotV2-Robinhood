"""Ramses adaptation of keeper/worker.py. Operator-run; monitor-only by default.
Only reviewed manifest targets, fixed methods and explicit bounded transactions are sent.
No raw RPC/cast output or signer material is logged. Local tests use unlocked anvil accounts.
"""
import base64
import contextlib
import fcntl
import functools
import hashlib
import json
import os
import re
import signal
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

RPC = 'https://rpc.mainnet.chain.robinhood.com'
MIN_GAS_WEI = 300_000_000_000_000
ZERO = '0x' + '00' * 20
ZERO_ROLE = '0x' + '00' * 32
GAS = {'harvest': 4_000_000, 'checkpoint': 3_000_000, 'rebalanceVault': 6_000_000,
       'rebalance': 5_000_000, 'recenter': 8_000_000}
MAX_CYCLE_GAS = 32_000_000
STOP = False

class SafetyError(RuntimeError):
    pass

class Revert(Exception):
    def __init__(self, data):
        self.data = data


def require(ok, reason):
    if not ok:
        raise SafetyError(reason)


def emit(status, **fields):
    print(json.dumps(dict(status=status, checkedAtUnix=int(time.time()), **fields)), flush=True)


def address(value):
    require(isinstance(value, str) and re.fullmatch(r'0x[0-9a-fA-F]{40}', value) and int(value, 16), 'invalid address')
    return value.lower()


def endpoint(url, local_only=False):
    p = urllib.parse.urlsplit(url)
    local = p.scheme == 'http' and p.hostname in {'localhost', '127.0.0.1', '::1'} and p.path in {'', '/'}
    require(not p.username and not p.password and not p.query and not p.fragment, 'RPC credentials/query forbidden')
    require(local if local_only else (local or url == RPC), 'RPC endpoint not allow-listed')
    return local


@functools.lru_cache(maxsize=2048)
def offline(*args):
    result = subprocess.run(['cast', *map(str, args)], capture_output=True, text=True, timeout=30)
    require(result.returncode == 0, 'offline ABI operation failed')
    return result.stdout.strip()


def words(raw):
    require(isinstance(raw, str) and raw.startswith('0x') and len(raw[2:]) % 64 == 0, 'malformed ABI result')
    return [int(raw[i:i+64], 16) for i in range(2, len(raw), 64)]


def as_address(word):
    return '0x' + format(word, '040x')


def reason(error):
    # Only structured revert bytes; never search arbitrary error prose for a hex substring.
    data = error.data
    if isinstance(data, dict):
        data = data.get('data', '')
    if not isinstance(data, str) or not re.fullmatch('0x[0-9a-fA-F]*', data):
        return 'unknown'
    for signature in ('RecenterNotNeeded()', 'RecenterCooldown()', 'RecenterRateLimited()',
                      'CheckpointStale()', 'DeployLossTooHigh()', 'InvalidConfiguration()',
                      'InsufficientLiquidity()', 'StaleOracle(address,uint256)'):
        if data[:10].lower() == offline('sig', signature).lower():
            return signature.split('(')[0]
    return 'unknown'


def benign(name, action, clean_first=False, dust=False):
    return (name in {'RecenterNotNeeded', 'RecenterCooldown', 'RecenterRateLimited'} and action == 'recenter'
            or name == 'StaleOracle'
            or name == 'DeployLossTooHigh' and action in {'rebalance', 'recenter'}
            or name == 'CheckpointStale' and clean_first
            or name in {'InvalidConfiguration', 'InsufficientLiquidity'} and action == 'rebalance' and dust)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise SafetyError('RPC redirects forbidden')


class Chain:
    def __init__(self, url, keeper, signer=None, unlocked=False):
        self.local = endpoint(url, unlocked)
        self.url, self.keeper, self.signer, self.unlocked = url, address(keeper), signer, unlocked
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        if unlocked:
            require('anvil' in self.rpc('web3_clientVersion', []).lower(), 'unlocked requires anvil')

    def rpc(self, method, params):
        request = urllib.request.Request(self.url, json.dumps({'jsonrpc':'2.0', 'id':1, 'method':method, 'params':params}).encode(), {'Content-Type':'application/json','User-Agent':'ramses-keeper/1.0'})
        try:
            with self.http.open(request, timeout=30) as response:
                result = json.load(response)
        except Exception:
            raise SafetyError('RPC transport failure') from None
        if 'error' in result:
            raise Revert(result['error'].get('data', ''))
        return result['result']

    def read(self, target, signature, *args, block='latest'):
        data = offline('calldata', signature, *args)
        return words(self.rpc('eth_call', [{'to':target, 'data':data}, block]))

    def uint(self, target, signature, *args, block='latest'):
        return self.read(target, signature, *args, block=block)[0]

    def addr(self, target, signature, *args):
        return as_address(self.uint(target, signature, *args))

    def head(self):
        return self.rpc('eth_getBlockByNumber', ['latest', False])

    def nonce(self, tag='pending'):
        return int(self.rpc('eth_getTransactionCount', [self.keeper, tag]), 16)

    def balance(self):
        return int(self.rpc('eth_getBalance', [self.keeper, 'latest']), 16)

    def simulate(self, tx):
        self.rpc('eth_call', [tx, 'latest'])

    def submit(self, tx):
        if self.unlocked:
            # Chain constructor validates exact loopback URL AND anvil client before this path.
            return self.rpc('eth_sendTransaction', [tx])
        require(self.signer is not None, 'signer not provisioned')
        key, secret = self.signer
        args = ['cast', 'send', tx['to'], '--data', tx['data'], '--rpc-url', self.url,
                '--keystore', key, '--password-file', secret, '--from', self.keeper,
                '--nonce', str(int(tx['nonce'],16)), '--gas-limit', str(int(tx['gas'],16)),
                '--gas-price', str(int(tx['gasPrice'],16)), '--legacy', '--async']
        result = subprocess.run(args, capture_output=True, text=True, timeout=90)
        require(result.returncode == 0, 'submission ambiguous; inspect journal')
        return result.stdout.strip()


class Journal:
    """Intent is fsynced before submission. Lost journal refuses reuse of a used signer."""
    def __init__(self, path, identity, chain, bootstrap_nonce):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.exists():
            self.data = json.loads(self.path.read_text())
            require(self.data['identity'] == identity, 'journal identity mismatch')
        else:
            require(chain.nonce() == chain.nonce('latest') == bootstrap_nonce, 'journal missing with used/pending signer')
            self.data = {'identity':identity, 'pending':None, 'nextNonce':bootstrap_nonce,
                         'checkpointPrice':0, 'checkpointAt':0, 'lastAttempt':0, 'lastCycle':0}
            self.save()

    def save(self):
        fd, temporary = tempfile.mkstemp(prefix='.journal-', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w') as out:
                json.dump(self.data, out, sort_keys=True)
                out.flush(); os.fsync(out.fileno())
            os.replace(temporary, self.path)
            fd = os.open(self.path.parent, os.O_RDONLY)
            try: os.fsync(fd)
            finally: os.close(fd)
        finally:
            if os.path.exists(temporary): os.unlink(temporary)


@contextlib.contextmanager
def signer_lock(keeper):
    # Independent of journal pathname, so two processes cannot bypass by changing STATE_DIRECTORY.
    path = Path(tempfile.gettempdir()) / ('ramses-signer-' + address(keeper) + '.lock')
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise SafetyError('one-signer lock held') from None
        yield
    finally:
        os.close(fd)


def preflight(chain, m, execute):
    require(int(chain.rpc('eth_chainId', []), 16) == 4663 == m['chainId'], 'wrong chain')
    required = {'vault','adapter','guard','reserve','controller','controllerImplementation','delegate',
                'stockMarket','dollarMarket','rate','source','oracle','timelock','pool','gauge','manager','feed','stock','usdg'}
    require(required <= m.keys(), 'incomplete manifest')
    pins = m['codeHashes']
    require({address(m[k]) for k in required} <= set(pins), 'missing required code pin')
    for target, expected in pins.items():
        code = chain.rpc('eth_getCode', [target, 'latest'])
        require(code != '0x' and offline('keccak', code).lower() == expected.lower(), 'runtime code hash changed')
    for pin in m['storagePins']:
        require(chain.rpc('eth_getStorageAt', [pin['address'], pin['slot'], 'latest']).lower() == pin['value'].lower(), 'proxy storage pin changed')
    for pin in m['callPins']:
        require(chain.read(pin['address'], pin['signature']) == pin['words'], 'dependency call pin changed')
    pair = m['pair']
    for contract, sig, expected in [('vault','liquidityAdapter()',m['adapter']), ('vault','oracleGuard()',m['guard']),
            ('vault','lossReserve()',m['reserve']), ('adapter','positionManager()',m['manager']),
            ('adapter','vault()',m['vault']), ('adapter','oracle()',m['guard']),
            ('gauge','pool()',m['pool']), ('gauge','nfpManager()',m['manager'])]:
        require(chain.addr(m[contract], sig) == expected.lower(), 'contract wiring changed')
    require(chain.addr(m['adapter'], 'poolOf(bytes32)', pair) == m['pool'].lower(), 'pool changed')
    feed = chain.read(m['guard'], 'feedConfig(bytes32)', pair)
    require([as_address(x) for x in feed[:5]] == [m[k].lower() for k in ('stock','usdg','feed','pool','gauge')], 'guard feed/gauge changed')
    keeper = chain.keeper
    for key in ('vault','guard','reserve'):
        for role in (ZERO_ROLE, offline('keccak','CONFIG_ROLE'), offline('keccak','GUARDIAN_ROLE')):
            require(not chain.uint(m[key], 'hasRole(bytes32,address)', role, keeper), 'keeper has privileged role')
    for role in (ZERO_ROLE, offline('keccak','PROPOSER_ROLE'), offline('keccak','EXECUTOR_ROLE'), offline('keccak','CANCELLER_ROLE')):
        require(not chain.uint(m['timelock'], 'hasRole(bytes32,address)', role, keeper), 'keeper has timelock role')
    for key in ('stockMarket','dollarMarket','controller'):
        for sig in ('admin()', 'pendingAdmin()'):
            require(chain.addr(m[key], sig) != keeper, 'keeper is market/controller admin')
    for sig in ('pauseGuardian()', 'borrowCapGuardian()'):
        require(chain.addr(m['controller'],sig) != keeper, 'keeper is controller guardian')
    holds = bool(chain.uint(m['vault'], 'hasRole(bytes32,address)', offline('keccak','KEEPER_ROLE'), keeper))
    require(not execute or holds, 'wallet lacks KEEPER_ROLE')
    for key in ('stockMarket','dollarMarket'):
        require(chain.addr(m[key], 'implementation()') == m['delegate'].lower(), 'market implementation changed')
        require(chain.addr(m[key], 'peridottroller()') == m['controller'].lower(), 'market controller changed')
        require(chain.addr(m[key], 'robinhoodVault()') == m['vault'].lower(), 'market vault changed')
        require(chain.uint(m[key], 'robinhoodPairId()') == int(pair,16), 'market pair changed')
        require(not execute or chain.addr(m[key], 'vaultOperator()') == keeper, 'wallet lacks market operator')
    require(chain.addr(m['controller'], 'peridottrollerImplementation()') == m['controllerImplementation'].lower(), 'controller implementation changed')
    return holds


class Worker:
    def __init__(self, chain, manifest, journal, execute=False, max_gas_price=100_000_000, cadence=3600, minimum=60):
        require(0 < max_gas_price <= 2_000_000_000, 'invalid gas price cap')
        require(cadence >= 60 and minimum >= 60, 'checkpoint rate limit below 60 seconds')
        self.c, self.m, self.j, self.execute = chain, manifest, journal, execute
        self.max_gas_price, self.cadence, self.minimum = max_gas_price, cadence, minimum
        self.gas_used = 0

    def snapshot(self):
        c,m = self.c,self.m
        head = c.head(); now = int(head['timestamp'],16)
        ledger = c.read(m['vault'], 'ledger(bytes32)', m['pair'])
        price = c.uint(m['guard'], 'pricesUSD18(bytes32)', m['pair'])
        ref = c.read(m['guard'], 'referenceSqrtPriceX96(bytes32,bool)', m['pair'], 'false')[0]
        pos = c.read(m['adapter'], 'positionStateAt(bytes32,uint160)', m['pair'], ref)
        gross = (ledger[2] + pos[2]) * price // 10**18 + (ledger[3] + pos[3]) * 10**12
        benchmark = ledger[0]*price//10**18 + ledger[1]*10**12
        tokens = c.read(m['gauge'], 'getRewardTokens()')[2:]
        accrual = {as_address(t): str(c.uint(m['gauge'], 'earned(address,uint256)', as_address(t), pos[0]) if pos[0] else 0) for t in tokens}
        return dict(chainTime=now, checkpointAge=max(0, now-ledger[5]), checkpointAt=ledger[5], price=price,
                    unrecognisedLossUSD18=str(max(0, benchmark-gross)),
                    ticks=c.read(m['adapter'], 'positionTicks(bytes32)', m['pair']),
                    rangeState=c.read(m['vault'], 'rangeState(bytes32)', m['pair']),
                    pairConfigHex='0x' + format(c.read(m['vault'], 'pairConfig(bytes32)', m['pair'])[4], 'x'),
                    dust=(min(ledger[2]*price//10**18, ledger[3]*10**12) < 10**16), tokenId=pos[0], rewardAccrualRaw=accrual)

    def recover(self):
        p = self.j.data['pending']
        if not p:
            require(self.c.nonce() == self.c.nonce('latest') == self.j.data['nextNonce'], 'unexpected signer nonce/pending transaction')
            return True
        require(p.get('hash'), 'ambiguous submission; operator must reconcile journal')
        receipt = self.c.rpc('eth_getTransactionReceipt', [p['hash']])
        if receipt is None:
            emit('pending', action=p['action'], transactionHash=p['hash'])
            return False
        require(receipt.get('transactionHash','').lower() == p['hash'].lower()
                and receipt.get('from','').lower() == self.c.keeper
                and receipt.get('to','').lower() == p['target'].lower(), 'receipt identity mismatch')
        require(int(receipt['status'],16) == 1, 'mined revert; operator must acknowledge journal')
        failed_topics = {offline('keccak','VaultDepositFailed(uint256)'), offline('keccak','VaultWithdrawalFailed(uint256)')}
        require(not any(log.get('address','').lower() == p['target'].lower() and log.get('topics', [''])[0] in failed_topics for log in receipt.get('logs', [])), 'market reported caught vault failure')
        # Read the actual checkpoint price at the receipt block, not the later head price.
        block = receipt['blockNumber']
        price = self.c.uint(self.m['guard'], 'pricesUSD18(bytes32)', self.m['pair'], block=block)
        ledger = self.c.read(self.m['vault'], 'ledger(bytes32)', self.m['pair'], block=block)
        self.j.data.update(checkpointPrice=price, checkpointAt=ledger[5], nextNonce=p['nonce']+1, pending=None)
        self.j.save()
        emit('sent', action=p['action'], transactionHash=p['hash'])
        return True

    def transaction(self, action, target):
        now = max(int(self.c.head()['timestamp'],16), int(time.time()))
        deadline = now + 120
        signature = 'rebalanceVault()' if action == 'rebalanceVault' else action+'(bytes32,uint256)'
        args = [] if action == 'rebalanceVault' else [self.m['pair'], deadline]
        return {'from':self.c.keeper, 'to':target, 'data':offline('calldata', signature, *args), 'gas':hex(GAS[action])}

    def act(self, action, target, dust=None):
        tx = self.transaction(action, target)
        try: self.c.simulate(tx)
        except Revert as error:
            name = reason(error)
            if dust is None and action == 'rebalance' and name in {'InvalidConfiguration', 'InsufficientLiquidity'}:
                # Re-read AFTER checkpoint and both delegate rebalances. Only the matched
                # native budget is deployable; a large unmatched side alone is not liquidity.
                ledger = self.c.read(self.m['vault'], 'ledger(bytes32)', self.m['pair'])
                try: price = self.c.uint(self.m['guard'], 'pricesUSD18(bytes32)', self.m['pair'])
                except Revert as feed_error:
                    if reason(feed_error) == 'StaleOracle': return 'wait'
                    raise SafetyError('dust valuation failed') from None
                dust = min(ledger[2]*price//10**18, ledger[3]*10**12) < 10**16
            emit('wait' if benign(name, action, clean_first=(action == 'harvest' and self.gas_used == 0), dust=dust) else 'attention', action=action, reason=name)
            if not benign(name, action, clean_first=(action == 'harvest' and self.gas_used == 0), dust=dust): raise SafetyError('unrecognised or unsafe action revert')
            return 'wait'
        if not self.execute: return 'monitor'
        preflight(self.c, self.m, True) # governance/code changes between transactions fail closed
        price = int(self.c.rpc('eth_gasPrice',[]),16)
        if price > self.max_gas_price:
            emit('wait', reason='gas cap', gasPriceWei=price); return 'wait'
        require(self.gas_used + GAS[action] <= MAX_CYCLE_GAS, 'cycle gas budget exceeded')
        require(self.j.data['pending'] is None, 'pending transaction blocks send')
        nonce = self.c.nonce()
        require(nonce == self.c.nonce('latest') == self.j.data['nextNonce'], 'signer nonce changed')
        tx.update(nonce=hex(nonce), gasPrice=hex(price))
        require(self.c.balance() >= GAS[action] * price, 'insufficient gas balance')
        self.j.data['pending'] = {'action':action, 'nonce':nonce, 'target':target, 'hash':None}
        self.j.save() # durable intent BEFORE any possible submission
        try: txhash = self.c.submit(tx)
        except Exception: raise SafetyError('ambiguous submission; inspect journal') from None
        require(isinstance(txhash,str) and re.fullmatch('0x[0-9a-fA-F]{64}',txhash), 'ambiguous submission hash')
        self.j.data['pending']['hash'] = txhash
        self.j.save()
        self.gas_used += GAS[action]
        end = time.monotonic()+30
        while time.monotonic()<end:
            if self.recover(): return 'sent'
            time.sleep(1)
        return 'pending'

    def cycle(self):
        self.gas_used = 0
        holds = preflight(self.c, self.m, self.execute)
        balance = self.c.balance()
        emit('heartbeat', executionEnabled=self.execute, keeper=self.c.keeper,
             holdsKeeperRole=holds, gasBalanceWei=str(balance), gasReady=balance >= MIN_GAS_WEI)
        if not self.recover(): return 'pending'
        if self.execute and balance < MIN_GAS_WEI: return 'awaiting_gas'
        try: state = self.snapshot()
        except Revert as error:
            if reason(error) == 'StaleOracle':
                ledger = self.c.read(self.m['vault'], 'ledger(bytes32)', self.m['pair'])
                emit('wait', reason='StaleOracle', checkpointAge=max(0,int(self.c.head()['timestamp'],16)-ledger[5]), unrecognisedLossUSD18=None)
                return 'wait'
            raise SafetyError('status read failed') from None
        baseline = self.j.data['checkpointPrice']
        moved = baseline == 0 or abs(state['price']-baseline)*10000 >= baseline*50
        now = max(state['chainTime'], int(time.time()))
        emit('status', **state, priceMoved50Bps=moved)
        due = moved or state['checkpointAge'] >= self.cadence or now-self.j.data['lastCycle'] >= self.cadence
        if not due: return 'noop'
        if now-self.j.data['lastAttempt'] < self.minimum: return 'rate_limited'
        if not self.execute:
            emit('monitor', action='harvest then checkpoint then rebalance then recenter'); return 'monitor'
        self.j.data['lastAttempt'] = now; self.j.save()
        # Harvest checkpoints internally too; still require explicit successful checkpoint before rebalancing.
        for action, target in [('harvest',self.m['vault']), ('checkpoint',self.m['vault']),
                               ('rebalanceVault',self.m['stockMarket']), ('rebalanceVault',self.m['dollarMarket']),
                               ('rebalance',self.m['vault']), ('recenter',self.m['vault'])]:
            if action == 'rebalance':
                # Preview only, after a fresh checkpoint. Do not add to an NFT that is due
                # to be removed: an increase can reset the upstream reward-validator age.
                try:
                    self.c.simulate(self.transaction('recenter', self.m['vault']))
                except Revert as error:
                    name = reason(error)
                    if name not in {'RecenterNotNeeded', 'RecenterCooldown', 'RecenterRateLimited'}:
                        if benign(name, 'recenter'):
                            emit('wait', action='recenter', reason=name)
                            return 'wait'
                        emit('attention', action='recenter', reason=name)
                        raise SafetyError('recenter preview failed') from None
                else:
                    emit('wait', action='rebalance', reason='recenter due; retain idle for new range')
                    continue
            outcome = self.act(action, target)
            if outcome != 'sent':
                # A benign rebalance dust/loss wait may still allow a due recenter, but only after checkpoint.
                if action == 'rebalance' and outcome == 'wait': continue
                if action == 'recenter' and outcome == 'wait': break
                return outcome
        self.j.data['lastCycle'] = now; self.j.save()
        return 'complete'


def prepare_signer(directory, keeper):
    encoded = os.environ.pop('KEYSTORE_JSON_BASE64', '')
    password = os.environ.pop('KEYSTORE_PASSWORD', '')
    require(encoded and password, 'signer is not provisioned')
    raw = base64.b64decode(encoded, validate=True)
    data = json.loads(raw)
    require(data.get('version') == 3 and (data.get('crypto') or data.get('Crypto')), 'expected encrypted V3 keystore')
    key, secret = Path(directory)/'keystore', Path(directory)/'password'
    key.write_bytes(raw); key.chmod(0o600)
    secret.write_text(password); secret.chmod(0o600)
    result = subprocess.run(['cast','wallet','address','--keystore',str(key),'--password-file',str(secret)], capture_output=True, text=True, timeout=60)
    require(result.returncode == 0 and result.stdout.strip().lower() == keeper.lower(), 'keystore does not belong to keeper')
    return str(key), str(secret)


def main():
    os.umask(0o077)
    def stop(*_):
        global STOP
        STOP = True
    signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
    keeper = address(os.environ.get('KEEPER_ADDRESS',''))
    m = json.loads(os.environ['RAMSES_MANIFEST_JSON'])
    require(keeper == m['keeper'].lower(), 'manifest keeper mismatch')
    execute = os.environ.get('EXECUTE','false') == 'true'
    interval = max(60, int(os.environ.get('INTERVAL_SECONDS','300')))
    c = Chain(os.environ.get('RPC_URL', RPC), keeper)
    # Refuse privileged identities BEFORE decoding any provisioned keystore.
    preflight(c, m, execute)
    with signer_lock(keeper), tempfile.TemporaryDirectory(prefix='ramses-keeper-') as directory:
        if execute: c.signer = prepare_signer(directory, keeper)
        state = Path(os.environ.get('STATE_DIRECTORY', '/state'))
        identity = hashlib.sha256(json.dumps(m, sort_keys=True).encode()).hexdigest()
        j = Journal(state/'journal.json', identity, c, m['bootstrapNonce'])
        w = Worker(c, m, j, execute, int(os.environ.get('MAX_GAS_PRICE_WEI','100000000')))
        emit('started', executionEnabled=execute, keeper=keeper)
        while not STOP:
            # Any unsafe error stops the signer; benign business waits return normally.
            w.cycle()
            for _ in range(interval):
                if STOP: break
                time.sleep(1)


if __name__ == '__main__':
    try: main()
    except Exception as error:
        emit('fatal', errorType=type(error).__name__, reason=str(error) if isinstance(error,SafetyError) else 'redacted')
        raise SystemExit(2)
