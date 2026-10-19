#!/usr/bin/env python3
"""Read-only Phase 3 verification against a reviewed local build and deployment journal.
Never sends transactions, reads wallets, or uses private credentials.
"""
import argparse, functools, json, pathlib, subprocess, urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / 'ramses-vaults/out-v3'
ZERO = '0x' + '00' * 20
POOL = '0xdac1904d823f7d6bcaaf431ceee40c934fed321b'
MANAGER = '0x2ebd7b85a4e08d5b508b04ba147976c94afe6590'
GAUGE = '0x93c6a733fb3165d94774b3a5fc4319d29bd7b6ce'
FEED = '0x379ec4f7c378f34a1b47e4f3cbebcbac3e8e9f15'
FORBIDDEN = {'0x6148183676e304dbe63a85c350c208da3ceac39c', '0xa155cccb986774ae818b3f10f07d01d1b7a47b26', '0x55aed0569c8f0d166d71face57b57c2f2624a563'}
# Snapshot contains the older system's vault, adapter, oracle, margin contracts and libraries.
FORBIDDEN |= {x['address'].lower() for x in json.loads((ROOT/'snapshot/mainnet-check.json').read_text())['runtimeChecks'].values()}

@functools.lru_cache(None)
def cast(*args):
    return subprocess.check_output(['cast', *map(str, args)], text=True, timeout=30).strip()

def addr(word): return '0x' + word[-40:].lower()
def words(data):
    data = data.removeprefix('0x')
    assert len(data) % 64 == 0, 'Malformed ABI response'
    return [data[i:i+64] for i in range(0, len(data), 64)]

class Reader:
    def __init__(self, url, block='latest'):
        self.url, self.block = url, block
    def rpc(self, method, params):
        assert method in {'eth_chainId', 'eth_getCode', 'eth_getStorageAt', 'eth_call', 'eth_getLogs', 'eth_blockNumber', 'web3_clientVersion'}
        req = urllib.request.Request(self.url, json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params}).encode(), {'Content-Type':'application/json'})
        with urllib.request.urlopen(req, timeout=30) as r: result = json.load(r)
        if 'error' in result: raise RuntimeError(result['error'])
        return result['result']
    def read(self, target, signature, *args):
        data = cast('calldata', signature, *args)
        return words(self.rpc('eth_call', [{'to':target,'data':data}, self.block]))
    def uint(self, target, signature, *args): return int(self.read(target, signature, *args)[0], 16)
    def address(self, target, signature, *args): return addr(self.read(target, signature, *args)[0])
    def code(self, target): return self.rpc('eth_getCode', [target, self.block])

class Verifier:
    def __init__(self, reader, manifest):
        self.r, self.m, self.checked = reader, manifest, {}
    def runtime(self, target, contract, allowed_immutables=()):
        target = target.lower()
        assert target not in FORBIDDEN, 'Uniswap runtime/module/library reused'
        if target in self.checked:
            assert self.checked[target]['contract'] == contract
            return
        artifact = json.loads((OUT / (contract + '.sol') / (contract + '.json')).read_text())
        deployed = artifact['deployedBytecode']
        expected = deployed['object'].removeprefix('0x')
        actual = self.r.code(target).removeprefix('0x').lower()
        assert actual and len(actual) == len(expected), f'{contract}: size mismatch'
        assert len(actual) // 2 <= 24576, f'{contract}: EIP170'
        expected = list(expected)
        observed = set()
        allowed = {x.removeprefix('0x').lower().zfill(64) for x in allowed_immutables}
        for key, refs in deployed.get('immutableReferences', {}).items():
            values = {actual[x['start']*2:(x['start']+x['length'])*2] for x in refs}
            assert len(values) == 1, f'{contract}: inconsistent immutable {key}'
            value = next(iter(values))
            assert value in allowed, f'{contract}: unapproved immutable {value}'
            observed.add(value)
            for x in refs: expected[x['start']*2:(x['start']+x['length'])*2] = value
        assert observed == allowed, f'{contract}: missing expected immutable'
        self.checked[target] = {'contract':contract, 'runtimeHash':cast('keccak', '0x'+actual), 'bytes':len(actual)//2}
        for libs in deployed.get('linkReferences', {}).values():
            for name, refs in libs.items():
                addresses = {addr(actual[x['start']*2:(x['start']+x['length'])*2]) for x in refs}
                assert len(addresses) == 1, f'{contract}: inconsistent library link'
                lib = addresses.pop()
                assert lib not in FORBIDDEN
                lib_artifact = json.loads((OUT/(name+'.sol')/(name+'.json')).read_text())
                self.runtime(lib, name, [lib] if lib_artifact['deployedBytecode'].get('immutableReferences') else [])
                for x in refs: expected[x['start']*2:(x['start']+x['length'])*2] = lib[2:]
        assert ''.join(expected).lower() == actual, f'{contract}: runtime differs from reviewed build'
    def eq_address(self, target, signature, expected, *args):
        assert self.r.address(target, signature, *args) == expected.lower(), signature
    def eq(self, target, signature, expected, *args):
        assert self.r.uint(target, signature, *args) == expected, signature
    def run(self, configured=True):
        r, m = self.r, self.m
        assert int(r.rpc('eth_chainId', []),16) == m['chainId'] == 4663
        keys = ['guard','vault','reserve','adapter','controllerImplementation','controller','delegate','rate','stockMarket','dollarMarket','source','oracle']
        assert len({m[k].lower() for k in keys}) == len(keys), 'Shared contract address'
        assert not {m[k].lower() for k in keys} & FORBIDDEN, 'Uniswap contamination'
        assert m['timelock'].lower() not in {m['sender'].lower(), m['keeper'].lower()}
        assert r.code(m['timelock']) != '0x' and r.uint(m['timelock'],'getMinDelay()') >= 3600
        module = r.address(m['delegate'],'borrowAccountingModule()')
        self.runtime(module, 'BorrowAccountingModule', [module])
        for key, contract, imm in [
            ('guard','RamsesOracleGuard',[]), ('vault','RamsesBoostedVault',[m['sender']]),
            ('reserve','RamsesLossReserve',[m['sender']]), ('adapter','RamsesVaultAdapter',[m['vault'],MANAGER,m['guard']]),
            ('controllerImplementation','RamsesPeridottroller',[]), ('controller','RamsesUnitroller',[]),
            ('delegate','RamsesBoostedDelegate',[module]), ('rate','RamsesInterestRateModel',[]),
            ('stockMarket','PErc20Delegator',[]), ('dollarMarket','PErc20Delegator',[]),
            ('source','RamsesLendingPriceSource',[m['guard'],m['pair']]),
            ('oracle','RobinhoodLendingPriceAdapter',[m['source'],m['stockMarket'],m['dollarMarket'],m['stock'],m['usdg']])]:
            self.runtime(m[key], contract, imm)
        self.eq_address(m['controllerImplementation'],'admin()',m['timelock'])
        self.eq_address(m['controller'],'admin()',m['timelock'])
        self.eq_address(m['controller'],'pendingAdmin()',ZERO)
        self.eq_address(m['controller'],'pendingPeridottrollerImplementation()',ZERO)
        self.eq_address(m['controller'],'peridottrollerImplementation()',m['controllerImplementation'])
        # Unitroller is Compound storage, NOT EIP1967: slots 0 admin, 2 implementation.
        assert addr(r.rpc('eth_getStorageAt',[m['controller'],'0x0',r.block])) == m['timelock'].lower()
        assert addr(r.rpc('eth_getStorageAt',[m['controller'],'0x2',r.block])) == m['controllerImplementation'].lower()
        layout = json.loads((OUT/'PErc20Delegator.sol/PErc20Delegator.json').read_text())['storageLayout']['storage']
        for key, token in [('stockMarket','stock'),('dollarMarket','usdg')]:
            market = m[key]
            self.eq_address(market,'admin()',m['timelock']); self.eq_address(market,'pendingAdmin()',ZERO)
            self.eq_address(market,'implementation()',m['delegate']); self.eq_address(market,'peridottroller()',m['controller'])
            self.eq_address(market,'underlying()',m[token]); self.eq_address(market,'interestRateModel()',m['rate'])
            self.eq(market,'decimals()',8)
            for label, want in [('admin',m['timelock']), ('implementation',m['delegate'])]:
                entry = next(x for x in layout if x['label']==label)
                packed = int(r.rpc('eth_getStorageAt',[market,hex(int(entry['slot'])),r.block]),16)
                stored = (packed >> (8*entry['offset'])) & (2**160-1)
                assert stored == int(want,16), f'Packed proxy slot {label}'
        self.roles(configured)
        if not configured: return self.checked
        pair = m['pair']; c = m['controller']
        self.eq_address(c,'oracle()',m['oracle']); self.eq(c,'closeFactorMantissa()',5*10**17)
        self.eq(c,'liquidationIncentiveMantissa()',108*10**16)
        self.eq_address(c,'isolatedMarginRiskHook()',ZERO); self.eq_address(c,'isolatedMarginRegistrar()',ZERO)
        self.eq_address(c,'pauseGuardian()',ZERO); self.eq_address(c,'borrowCapGuardian()',ZERO)
        self.eq(c,'transferGuardianPaused()',0); self.eq(c,'seizeGuardianPaused()',0)
        markets = r.read(c,'getAllMarkets()')
        assert int(markets[1],16)==2 and {addr(x) for x in markets[2:]} == {m['stockMarket'].lower(),m['dollarMarket'].lower()}
        for key, prefix in [('stockMarket','stock'),('dollarMarket','dollar')]:
            market=m[key]
            listed=r.read(c,'markets(address)',market)
            assert int(listed[0],16)==1 and int(listed[1],16)==0, 'Launch collateral factor must be zero'
            self.eq(c,'borrowGuardianPaused(address)',1,market)
            self.eq(c,'mintGuardianPaused(address)',0,market)
            self.eq(c,'borrowCaps(address)',m[prefix+'BorrowCap'],market)
            self.eq(c,'peridotSupplySpeeds(address)',0,market); self.eq(c,'peridotBorrowSpeeds(address)',0,market)
            self.eq(market,'supplyCap()',m[prefix+'SupplyCap'])
            self.eq(market,'vaultBufferMantissa()',5*10**17); self.eq(market,'vaultPaused()',0)
            self.eq(market,'reserveFactorMantissa()',2*10**17); self.eq(market,'flashLoansPaused()',1)
            self.eq_address(market,'robinhoodVault()',m['vault']); self.eq_address(market,'vaultOperator()',m['keeper'])
            self.eq(market,'robinhoodPairId()',int(pair,16))
            self.eq(market,'balanceOf(address)',10**8,'0x000000000000000000000000000000000000dead')
            assert r.uint(market,'totalSupply()') >= 10**8
        # Complete launch table and auxiliary policy, in struct ABI order.
        config=r.read(m['vault'],'pairConfig(bytes32)',pair)
        assert [addr(x) for x in config[:4]] == [m[x].lower() for x in ['stock','usdg','stockMarket','dollarMarket']]
        assert [int(x,16) for x in config[4:]] == [1000*10**18,1000*10**18,86400,0,300,2000,100,200,18,6,0,0,0,1]
        feed=r.read(m['guard'],'feedConfig(bytes32)',pair)
        assert [addr(x) for x in feed[:5]] == [m['stock'].lower(),m['usdg'].lower(),FEED,POOL,GAUGE]
        assert [int(x,16) for x in feed[5:]] == [43200,300,600,18,6,8,1]
        assert [int(x,16) for x in r.read(m['vault'],'rangePolicy(bytes32)',pair)] == [1,2400,600,3600,4,10,2**128-1]
        self.eq(m['vault'],'deployLossBps(bytes32)',10,pair)
        self.eq(m['vault'],'aggregateUsdgDepositCap(address)',1000*10**6,m['usdg'])
        self.eq(m['adapter'],'allowRewardForfeiture(bytes32)',0,pair)
        self.eq(m['adapter'],'removalTolerance(bytes32)',400,pair)
        reserve = r.read(m['reserve'],'reserveConfig(bytes32)',pair)
        assert [addr(x) for x in reserve[:2]] == [m['stock'].lower(),m['usdg'].lower()]
        assert [int(x,16) for x in reserve[2:]] == [1000*10**18,1000*10**18,10000,0,1]
        self.eq_address(m['adapter'],'vault()',m['vault']); self.eq_address(m['adapter'],'oracle()',m['guard'])
        self.eq_address(m['adapter'],'positionManager()',MANAGER)
        self.eq_address(m['source'],'guard()',m['guard']); self.eq(m['source'],'pair()',int(pair,16))
        self.eq_address(m['vault'],'oracleGuard()',m['guard']); self.eq_address(m['vault'],'liquidityAdapter()',m['adapter']); self.eq_address(m['vault'],'lossReserve()',m['reserve'])
        prices=r.read(m['guard'],'pricesUSD18(bytes32)',pair)
        self.eq(m['oracle'],'getUnderlyingPrice(address)',int(prices[0],16),m['stockMarket'])
        self.eq(m['oracle'],'getUnderlyingPrice(address)',10**30,m['dollarMarket'])
        return self.checked
    def roles(self, configured):
        m,r=self.m,self.r
        roles={'admin':'0x'+'00'*32,'config':cast('keccak','CONFIG_ROLE'),'keeper':cast('keccak','KEEPER_ROLE'),'guardian':cast('keccak','GUARDIAN_ROLE'),'vault':cast('keccak','VAULT_ROLE')}
        grant=cast('keccak','RoleGranted(bytes32,address,address)'); revoke=cast('keccak','RoleRevoked(bytes32,address,address)')
        for key in ['vault','reserve','guard']:
            live={}
            logs=r.rpc('eth_getLogs',[{'address':m[key],'fromBlock':hex(m['deploymentBlock']),'toBlock':r.block,'topics':[[grant,revoke]]}])
            for event in logs:
                topic,role,account=event['topics'][:3]; account=addr(account)
                if topic==grant: live.setdefault(role,set()).add(account)
                else: live.setdefault(role,set()).discard(account)
            expected={roles['admin']:{m['timelock'].lower()},roles['config']:{m['timelock'].lower()}}
            if key=='vault':
                expected[roles['guardian']]={m['timelock'].lower()}
                expected[roles['keeper']]={m['keeper'].lower()} | ({m['stockMarket'].lower(),m['dollarMarket'].lower()} if configured else set())
            if key=='reserve': expected[roles['vault']]={m['vault'].lower()}
            assert {k:v for k,v in live.items() if v}==expected, f'{key}: unexpected role holder'
            for role,accounts in expected.items():
                for account in accounts: self.eq(m[key],'hasRole(bytes32,address)',1,role,account)

def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--rpc-url',required=True); p.add_argument('--journal',required=True); p.add_argument('--unconfigured',action='store_true'); args=p.parse_args()
    m=json.loads(pathlib.Path(args.journal).read_text()); r=Reader(args.rpc_url)
    r.block=r.rpc('eth_blockNumber',[])
    results=Verifier(r,m).run(not args.unconfigured)
    print(json.dumps({'verified':True,'block':int(r.block,16),'contracts':results},indent=2))
if __name__=='__main__': main()
