"""No wallet files, private keys, cloud tools or external RPCs are used by this suite."""
import contextlib
import copy
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import worker as w


def manifest():
    names = ['vault','adapter','guard','reserve','controller','controllerImplementation','delegate','stockMarket',
             'dollarMarket','rate','source','oracle','timelock','pool','gauge','manager','feed','stock','usdg','keeper']
    m = {name:'0x'+format(i+1,'040x') for i,name in enumerate(names)}
    m.update(chainId=4663, pair='0x'+'ab'*32, bootstrapNonce=0, storagePins=[], callPins=[])
    m['codeHashes'] = {m[k]:w.offline('keccak','0x6000') for k in names if k != 'keeper'}
    return m


class Double(w.Chain):
    """Faithful worker-facing state transitions: checkpoint snapshots, atomic calls, receipt/nonces.
    Solidity Launch/Phase2 tests independently exercise real CL math and vault/delegate behavior.
    """
    def __init__(self, m):
        self.m=m; self.keeper=m['keeper']; self.now=2000000000; self.price=100*10**18
        self.checkpoint=self.now-4000; self.pending_nonce=0; self.mined_nonce=0
        self.balance_value=10**18; self.gas_price=100000000; self.chain_id=4663
        self.roles={(m['vault'],w.offline('keccak','KEEPER_ROLE'),self.keeper):1}
        self.codes={a:'0x6000' for a in m['codeHashes']}; self.overrides={}; self.storage={}
        self.submitted=[]; self.receipts={}; self.failures={}; self.mined=True; self.receipt_status=1
        self.ambiguous=False; self.due=False; self.checkpoint_history={}; self.last_action=None

    def nonce(self,tag='pending'): return self.pending_nonce if tag=='pending' else self.mined_nonce
    def balance(self): return self.balance_value
    def head(self): return {'timestamp':hex(self.now),'number':hex(self.mined_nonce+1)}
    def rpc(self, method, params):
        if method=='eth_chainId': return hex(self.chain_id)
        if method=='eth_getCode': return self.codes.get(params[0],'0x')
        if method=='eth_getStorageAt': return self.storage.get(tuple(params[:2]),'0x'+'00'*32)
        if method=='eth_gasPrice': return hex(self.gas_price)
        if method=='eth_getTransactionReceipt': return self.receipts.get(params[0])
        raise AssertionError((method,params))

    def read(self,target,sig,*args,block='latest'):
        if (target,sig) in self.overrides: return self.overrides[(target,sig)]
        m=self.m
        if sig=='hasRole(bytes32,address)': return [self.roles.get((target,*args),0)]
        if sig in ('admin()','pendingAdmin()','pauseGuardian()','borrowCapGuardian()'): return [0]
        if sig=='feedConfig(bytes32)': return [int(m[k],16) for k in ('stock','usdg','feed','pool','gauge')]+[43200,300,600,18,6,8,1]
        if sig=='robinhoodPairId()': return [int(m['pair'],16)]
        if sig=='pricesUSD18(bytes32)':
            if self.failures.get('price'): raise self.failures['price']
            return [self.checkpoint_history.get(block,self.price),10**18]
        if sig=='getRewardTokens()': return [32,2,int(m['stock'],16),int(m['usdg'],16)]
        if sig=='earned(address,uint256)': return [123]
        if sig=='ledger(bytes32)': return [10**18,100*10**6,10**17,10*10**6,0,self.checkpoint]
        if sig=='positionStateAt(bytes32,uint160)': return [1,1000,9*10**17,90*10**6]
        if sig=='referenceSqrtPriceX96(bytes32,bool)': return [2**96]
        if sig=='positionTicks(bytes32)': return [222000,226800,1]
        if sig=='rangeState(bytes32)': return [224400,1,self.now-4000,1]
        if sig=='pairConfig(bytes32)': return [0,0,0,0,1000*10**18]
        getters={'liquidityAdapter()':'adapter','oracleGuard()':'guard','lossReserve()':'reserve',
            'positionManager()':'manager','vault()':'vault','oracle()':'guard','pool()':'pool',
            'nfpManager()':'manager','poolOf(bytes32)':'pool','implementation()':'delegate',
            'peridottroller()':'controller','robinhoodVault()':'vault','vaultOperator()':'keeper',
            'peridottrollerImplementation()':'controllerImplementation'}
        if sig in getters: return [int(m[getters[sig]],16)]
        raise AssertionError((target,sig,args,block))

    def action(self,tx):
        for a in w.GAS:
            sig='rebalanceVault()' if a=='rebalanceVault' else a+'(bytes32,uint256)'
            if tx['data'][:10] == w.offline('sig',sig): return a
        raise AssertionError(tx)

    def simulate(self,tx):
        a=self.action(tx)
        if a in self.failures: raise self.failures[a]
        if a=='recenter' and not self.due: raise revert('RecenterNotNeeded()')
        if a in ('rebalance','rebalanceVault') and self.checkpoint < self.now:
            raise revert('CheckpointStale()')

    def submit(self,tx):
        a=self.action(tx)
        if self.ambiguous: raise RuntimeError('secret must not be logged')
        assert int(tx['nonce'],16)==self.pending_nonce
        self.submitted.append((a,copy.deepcopy(tx))); self.pending_nonce+=1
        h='0x'+format(self.pending_nonce,'064x')
        if self.mined: self.mine(h,tx,a)
        return h

    def mine(self,h,tx,a):
        self.mined_nonce=self.pending_nonce
        block=hex(self.mined_nonce+1)
        if self.receipt_status:
            self.checkpoint=self.now
            self.checkpoint_history[block]=self.price
            if a=='recenter': self.due=False
        self.receipts[h]={'transactionHash':h,'from':self.keeper,'to':tx['to'],
                          'status':hex(self.receipt_status),'blockNumber':block}


def revert(sig): return w.Revert(w.offline('sig',sig))


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.m=manifest(); self.c=Double(self.m)
        self.j=w.Journal(Path(self.tmp.name)/'journal.json','test',self.c,0)
        self.worker=w.Worker(self.c,self.m,self.j,True)
        self.logs=io.StringIO(); self.output=contextlib.redirect_stdout(self.logs); self.output.__enter__()
        self.addCleanup(self.output.__exit__,None,None,None)

    def test_order_and_noop(self):
        self.assertEqual(self.worker.cycle(),'complete')
        self.assertEqual([a for a,_ in self.c.submitted],['harvest','checkpoint','rebalanceVault','rebalanceVault','rebalance'])
        self.assertEqual(self.worker.cycle(),'noop')
        self.assertIn('heartbeat',self.logs.getvalue())

    def test_initial_range_does_not_mint_interim_full_range_nft(self):
        self.c.due=True
        self.c.overrides[(self.m['adapter'],'positionTicks(bytes32)')]=[0,0,0]
        self.assertEqual(self.worker.cycle(),'complete')
        actions=[a for a,_ in self.c.submitted]
        self.assertNotIn('rebalance',actions)
        self.assertEqual(actions[-1],'recenter')
        self.assertEqual(actions[:2],['harvest','checkpoint'])

    def test_due_recenter_does_not_increase_old_nft(self):
        self.c.due=True
        self.assertEqual(self.worker.cycle(),'complete')
        self.assertEqual([a for a,_ in self.c.submitted],['harvest','checkpoint','rebalanceVault','rebalanceVault','recenter'])

    def test_price_trigger_exactly_50bps_both_directions(self):
        self.worker.cycle()
        for factor in (10050,9950):
            self.c.now+=300
            baseline=self.j.data['checkpointPrice']
            self.c.price=baseline*factor//10000
            before=len(self.c.submitted)
            self.assertEqual(self.worker.cycle(),'complete')
            self.assertGreater(len(self.c.submitted),before)
            self.assertEqual(self.j.data['checkpointPrice'],self.c.price)

    def test_49bps_no_trigger(self):
        self.worker.cycle(); self.c.now+=300; self.c.price=self.c.price*10049//10000
        self.assertEqual(self.worker.cycle(),'noop')

    def test_rate_limit_price_moves(self):
        self.worker.cycle(); self.c.now+=59; self.c.price*=2
        self.assertEqual(self.worker.cycle(),'rate_limited')

    def test_periodic_checkpoint(self):
        self.worker.cycle(); self.c.now+=3600
        self.assertEqual(self.worker.cycle(),'complete')

    def test_monitor_never_sends(self):
        self.worker.execute=False
        self.assertEqual(self.worker.cycle(),'monitor'); self.assertFalse(self.c.submitted)

    def test_configured_gas_and_rate_limit_bounds(self):
        for price in (0,2000000001):
            with self.assertRaises(w.SafetyError): w.Worker(self.c,self.m,self.j,max_gas_price=price)
        with self.assertRaises(w.SafetyError): w.Worker(self.c,self.m,self.j,minimum=59)

    def test_gas_price_cap_no_sends(self):
        self.c.gas_price=self.worker.max_gas_price+1
        self.assertEqual(self.worker.cycle(),'wait'); self.assertFalse(self.c.submitted)

    def test_gas_price_is_explicit_not_only_simulated(self):
        self.worker.cycle()
        for a,tx in self.c.submitted:
            self.assertEqual(int(tx['gasPrice'],16),self.c.gas_price)
            self.assertEqual(int(tx['gas'],16),w.GAS[a])

    def test_cycle_gas_cap(self):
        self.worker.gas_used=w.MAX_CYCLE_GAS
        with self.assertRaisesRegex(w.SafetyError,'cycle gas'): self.worker.act('harvest',self.m['vault'])
        self.assertFalse(self.c.submitted)

    def test_low_balance(self):
        self.c.balance_value=0
        self.assertEqual(self.worker.cycle(),'awaiting_gas'); self.assertFalse(self.c.submitted)

    def test_deadline_later_chain_or_wall(self):
        for wall,head in ((1000,2000),(2000,1000)):
            self.c.now=head
            with mock.patch.object(w.time,'time',return_value=wall): tx=self.worker.transaction('harvest',self.m['vault'])
            self.assertEqual(int(tx['data'][-64:],16),2120)

    def test_checkpoint_failure_prevents_all_rebalancing(self):
        self.c.failures['checkpoint']=revert('Surprise()')
        with self.assertRaises(w.SafetyError): self.worker.cycle()
        self.assertEqual([a for a,_ in self.c.submitted],['harvest'])

    def test_harvest_failure_prevents_recenter(self):
        self.c.failures['harvest']=revert('RewardClaimMismatch()')
        with self.assertRaises(w.SafetyError): self.worker.cycle()
        self.assertFalse(self.c.submitted)

    def test_named_benign_reverts(self):
        for a,s in [('recenter','RecenterNotNeeded()'),('recenter','RecenterCooldown()'),
                    ('rebalance','DeployLossTooHigh()'),('harvest','StaleOracle(address,uint256)')]:
            self.c.failures={a:revert(s)}
            self.assertEqual(self.worker.act(a,self.m['vault']),'wait')

    def test_clean_first_checkpoint_stale_only(self):
        self.assertTrue(w.benign('CheckpointStale','harvest',clean_first=True))
        self.assertFalse(w.benign('CheckpointStale','checkpoint'))

    def test_dust_errors_require_tiny_idle_and_rebalance(self):
        self.c.failures['rebalance']=revert('InvalidConfiguration()')
        self.assertEqual(self.worker.act('rebalance',self.m['vault'],dust=True),'wait')
        with self.assertRaises(w.SafetyError): self.worker.act('rebalance',self.m['vault'],dust=False)

    def test_unmatched_idle_is_dust_but_material_matched_failure_alerts(self):
        self.c.failures['rebalance']=revert('InsufficientLiquidity()')
        self.c.overrides[(self.m['vault'],'ledger(bytes32)')]=[10**18,100000000,10**18,0,0,self.c.now]
        self.assertEqual(self.worker.act('rebalance',self.m['vault']),'wait')
        self.c.overrides[(self.m['vault'],'ledger(bytes32)')][3]=100000000
        with self.assertRaises(w.SafetyError): self.worker.act('rebalance',self.m['vault'])

    def test_closed_market_waits(self):
        self.c.failures['price']=revert('StaleOracle(address,uint256)')
        self.assertEqual(self.worker.cycle(),'wait'); self.assertFalse(self.c.submitted)

    def test_mined_revert_with_zero_exit_is_not_success(self):
        self.c.receipt_status=0
        with self.assertRaisesRegex(w.SafetyError,'mined revert'): self.worker.cycle()
        self.assertIsNotNone(self.j.data['pending'])
        self.assertEqual(len(self.c.submitted),1)

    def test_successful_receipt_with_caught_delegate_failure_alerts(self):
        self.c.checkpoint=self.c.now
        original=self.c.mine
        def mine(h,tx,a):
            original(h,tx,a)
            self.c.receipts[h]['logs']=[{'address':tx['to'],'topics':[w.offline('keccak','VaultDepositFailed(uint256)')]}]
        with mock.patch.object(self.c,'mine',side_effect=mine):
            with self.assertRaisesRegex(w.SafetyError,'caught vault failure'):
                self.worker.act('rebalanceVault',self.m['stockMarket'])
        self.assertIsNotNone(self.j.data['pending'])

    def test_receipt_identity_mismatch_stops(self):
        p={'hash':'0x'+'aa'*32,'target':self.m['vault'],'nonce':0,'action':'harvest'}
        self.j.data['pending']=p
        self.c.receipts[p['hash']]={'transactionHash':p['hash'],'from':self.m['guard'],'to':p['target'],'status':'0x1','blockNumber':'0x1'}
        with self.assertRaisesRegex(w.SafetyError,'receipt identity'): self.worker.recover()

    def test_intent_persisted_before_submit(self):
        real=self.c.submit; intents=[]
        def check(tx):
            intents.append(json.loads(self.j.path.read_text())['pending'])
            return real(tx)
        with mock.patch.object(self.c,'submit',side_effect=check): self.worker.cycle()
        self.assertTrue(intents)
        for intent in intents:
            self.assertIsNotNone(intent)
            self.assertIsNone(intent['hash'])

    def test_ambiguous_submission_stops_on_restart(self):
        self.c.ambiguous=True
        with self.assertRaisesRegex(w.SafetyError,'ambiguous'): self.worker.cycle()
        restarted=w.Journal(self.j.path,'test',self.c,0)
        with self.assertRaisesRegex(w.SafetyError,'ambiguous'): w.Worker(self.c,self.m,restarted,True).cycle()
        self.assertNotIn('secret must',self.logs.getvalue())

    def test_pending_tx_recovery_no_duplicate(self):
        self.c.mined=False
        with mock.patch.object(w.time,'monotonic',side_effect=[0,0,31]), mock.patch.object(w.time,'sleep'):
            self.assertEqual(self.worker.act('harvest',self.m['vault']),'pending')
        restarted=w.Journal(self.j.path,'test',self.c,0)
        worker=w.Worker(self.c,self.m,restarted,True)
        self.assertEqual(worker.cycle(),'pending'); self.assertEqual(len(self.c.submitted),1)
        p=restarted.data['pending']; a,tx=self.c.submitted[0]; self.c.mine(p['hash'],tx,a)
        self.assertTrue(worker.recover()); self.assertEqual(len(self.c.submitted),1)

    def test_checkpoint_price_uses_receipt_block(self):
        self.c.mined=False
        with mock.patch.object(w.time,'monotonic',side_effect=[0,0,31]), mock.patch.object(w.time,'sleep'):
            self.worker.act('harvest',self.m['vault'])
        p=self.j.data['pending']; a,tx=self.c.submitted[0]; self.c.mine(p['hash'],tx,a)
        original=self.c.price; self.c.price*=2
        self.worker.recover(); self.assertEqual(self.j.data['checkpointPrice'],original)

    def test_journal_loss_used_signer_refuses(self):
        self.worker.cycle(); self.j.path.unlink()
        with self.assertRaisesRegex(w.SafetyError,'journal missing'): w.Journal(self.j.path,'test',self.c,0)

    def test_journal_identity_and_nonce_must_match(self):
        with self.assertRaisesRegex(w.SafetyError,'identity'): w.Journal(self.j.path,'other',self.c,0)
        self.c.pending_nonce=1
        with self.assertRaisesRegex(w.SafetyError,'nonce'): self.worker.cycle()

    def test_lock_independent_of_state_path(self):
        with w.signer_lock(self.c.keeper):
            with self.assertRaisesRegex(w.SafetyError,'lock held'):
                with w.signer_lock(self.c.keeper): pass

    def test_privileged_roles_rejected_even_monitor(self):
        for key,names in [('vault',['CONFIG_ROLE','GUARDIAN_ROLE','']),('guard',['CONFIG_ROLE','']),
                          ('reserve',['CONFIG_ROLE','']),('timelock',['PROPOSER_ROLE','EXECUTOR_ROLE','CANCELLER_ROLE',''])]:
            for name in names:
                role=w.offline('keccak',name) if name else w.ZERO_ROLE
                self.c.roles[(self.m[key],role,self.c.keeper)]=1
                with self.assertRaises(w.SafetyError): w.preflight(self.c,self.m,False)
                del self.c.roles[(self.m[key],role,self.c.keeper)]

    def test_execute_requires_role_and_operator(self):
        self.c.roles={}
        self.assertFalse(w.preflight(self.c,self.m,False))
        with self.assertRaisesRegex(w.SafetyError,'KEEPER_ROLE'): w.preflight(self.c,self.m,True)
        self.c.roles={(self.m['vault'],w.offline('keccak','KEEPER_ROLE'),self.c.keeper):1}
        self.c.overrides[(self.m['stockMarket'],'vaultOperator()')]=[0]
        with self.assertRaisesRegex(w.SafetyError,'operator'): w.preflight(self.c,self.m,True)

    def test_wrong_chain(self):
        self.c.chain_id=1
        with self.assertRaisesRegex(w.SafetyError,'chain'): self.worker.cycle()

    def test_every_code_hash_pin(self):
        for target in self.m['codeHashes']:
            self.c.codes[target]='0x6001'
            with self.assertRaisesRegex(w.SafetyError,'code hash'): w.preflight(self.c,self.m,False)
            self.c.codes[target]='0x6000'

    def test_missing_required_pin(self):
        del self.m['codeHashes'][self.m['gauge']]
        with self.assertRaisesRegex(w.SafetyError,'missing required'): w.preflight(self.c,self.m,False)

    def test_storage_and_call_pins(self):
        self.m['storagePins']=[{'address':self.m['feed'],'slot':'0x0','value':'0x01'}]
        with self.assertRaisesRegex(w.SafetyError,'storage pin'): w.preflight(self.c,self.m,False)
        self.m['storagePins']=[]
        self.m['callPins']=[{'address':self.m['gauge'],'signature':'pool()','words':[123]}]
        with self.assertRaisesRegex(w.SafetyError,'call pin'): w.preflight(self.c,self.m,False)

    def test_gauge_and_guard_identity(self):
        self.c.overrides[(self.m['guard'],'feedConfig(bytes32)')]=[int(self.m['stock'],16)]*5
        with self.assertRaisesRegex(w.SafetyError,'feed/gauge'): w.preflight(self.c,self.m,False)

    def test_controller_and_market_admin_refused(self):
        for key in ('controller','stockMarket','dollarMarket'):
            self.c.overrides[(self.m[key],'admin()')]=[int(self.c.keeper,16)]
            with self.assertRaisesRegex(w.SafetyError,'admin'): w.preflight(self.c,self.m,False)
            self.c.overrides.clear()

    def test_preflight_repeated_before_send(self):
        self.c.codes[self.m['vault']]='0x6001'
        with self.assertRaisesRegex(w.SafetyError,'code hash'): self.worker.act('harvest',self.m['vault'])
        self.assertFalse(self.c.submitted)

    def test_endpoint_parser_rejects_prefix_tricks(self):
        for url in ('http://localhost.evil','http://127.0.0.1.evil','http://localhost@evil','http://evil/localhost',
                    w.RPC+'?key=secret','https://elsewhere','http://localhost:8545/redirect'):
            with self.assertRaises(w.SafetyError): w.endpoint(url)
        self.assertTrue(w.endpoint('http://127.0.0.1:8545'))
        self.assertTrue(w.endpoint('http://[::1]:8545'))
        self.assertFalse(w.endpoint(w.RPC))
        with self.assertRaises(w.SafetyError): w.endpoint(w.RPC,local_only=True)

    def test_signer_not_read_before_privilege_check(self):
        env={'KEEPER_ADDRESS':self.c.keeper,'RAMSES_MANIFEST_JSON':json.dumps(self.m),'EXECUTE':'true'}
        with mock.patch.dict(os.environ,env), mock.patch.object(w,'Chain',return_value=self.c), mock.patch.object(w,'prepare_signer') as prepare:
            self.c.roles[(self.m['vault'],w.ZERO_ROLE,self.c.keeper)]=1
            with self.assertRaises(w.SafetyError): w.main()
            prepare.assert_not_called()


if __name__=='__main__': unittest.main()
