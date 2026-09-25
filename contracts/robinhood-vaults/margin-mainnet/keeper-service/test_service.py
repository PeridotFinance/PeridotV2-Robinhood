import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from service import SignedBackend,cycle,unresolved,GOVERNOR,LIQUIDATOR

class Fake:
    def __init__(self):self.position_id=None
    def read(self,target,sig,*args):return [3 if sig=='nextPositionId()' else 0]

class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name)
    def test_unknown_send_blocks_all_other_positions(self):
        (self.path/'position-2.json').write_text(json.dumps({'positionId':2,'attempts':[{'state':'unknown_submission'}]}))
        with patch('service.run',return_value={'status':'reconcile_unknown_submission'}) as run:
            r=cycle(Fake(),self.path,True)
        self.assertEqual(r['status'],'operator_attention');self.assertEqual(run.call_count,1);self.assertEqual(run.call_args[0][1],2)
    def test_pending_receipt_is_waited_without_scanning_other_positions(self):
        (self.path/'position-1.json').write_text(json.dumps({'positionId':1,'attempts':[{'state':'submitted','hash':'hash'}]}))
        with patch('service.run',return_value={'status':'pending'}) as run:
            r=cycle(Fake(),self.path,True)
        self.assertEqual(r['status'],'waiting_receipt');self.assertEqual(run.call_count,1)

    def test_position_scan_is_bounded(self):
        with patch('service.run') as run:
            with self.assertRaises(RuntimeError):cycle(Fake(),self.path,True,max_positions=1)
        run.assert_not_called()
    def test_stale_oracle_degrades_health_without_sending(self):
        with patch('service.run',return_value={'status':'stale_or_unavailable_stop'}):r=cycle(Fake(),self.path)
        self.assertEqual(r['status'],'degraded_oracle')
    def test_healthy_simulation_revert_is_normal_monitoring(self):
        with patch('service.run',return_value={'status':'not_executable_stop','snapshot':{'account':'account'}}):r=cycle(Fake(),self.path)
        self.assertEqual(r['status'],'monitoring')
    def test_liquidatable_but_not_executable_requires_operator(self):
        b=Fake();b.read=lambda *a:[1 if a[1]=='isLiquidatable(address)' else 2]
        with patch('service.run',return_value={'status':'not_executable_stop','snapshot':{'account':'account'}}):r=cycle(b,self.path,True)
        self.assertEqual(r['status'],'operator_attention')
    def test_submit_rejects_changed_recipient_calldata_value_target_or_gas(self):
        b=SignedBackend.__new__(SignedBackend);b.position_id=1;b.max_gas=8_000_000
        b.transaction=lambda p:{'data':'0x1234'}
        good={'from':GOVERNOR,'to':LIQUIDATOR,'data':'0x1234','value':'0x0','gas':'0x10000'}
        for field,value in [('from',LIQUIDATOR),('to',GOVERNOR),('data','0xabcd'),('value','0x1'),('gas',hex(8_000_001))]:
            with self.subTest(field=field):
                with self.assertRaises(ValueError):b.submit(dict(good,**{field:value}),0)
    def test_production_signer_command_is_bounded_and_uses_keystore(self):
        b=SignedBackend.__new__(SignedBackend);b.position_id=1;b.max_gas=8_000_000
        b.transaction=lambda p:{'data':'0x1234'};b.nonce=lambda:7;b.local_unlocked=False
        b.url='https://rpc.mainnet.chain.robinhood.com';b.gas_price=100_000_000;b.password_file=None
        tx={'from':GOVERNOR,'to':LIQUIDATOR,'data':'0x1234','value':'0x0','gas':'0x10000'}
        with patch('service.subprocess.run') as run:
            run.return_value.stdout='0x'+'a'*64+'\n'
            self.assertEqual(b.submit(tx,7),'0x'+'a'*64)
        cmd=run.call_args[0][0]
        for flag,value in [('--account','robinhood-deployer'),('--nonce','7'),('--gas-limit','65536'),('--gas-price','100000000'),('--priority-gas-price','0')]:
            self.assertEqual(cmd[cmd.index(flag)+1],value)
        self.assertIn('--async',cmd);self.assertNotIn('--private-key',cmd)

    def test_receipt_rejects_wrong_nonce_and_noncanonical_block(self):
        b=SignedBackend.__new__(SignedBackend);b.position_id=1;b.state=self.path;b.url='fixture';b.local_unlocked=True
        h='0x'+'a'*64
        (self.path/'position-1.json').write_text(json.dumps({'attempts':[{'hash':h,'nonce':7,'transaction':{'data':'0x1234'}}]}))
        receipt={'status':'0x1','blockNumber':'0x10','blockHash':'canonical'}
        sent={'from':GOVERNOR,'to':LIQUIDATOR,'input':'0x1234','nonce':'0x8','value':'0x0','chainId':hex(4663)}
        with patch('service.RpcBackend.receipt',return_value=receipt),patch('service.rpc',return_value=sent):
            with self.assertRaisesRegex(RuntimeError,'identity mismatch'):b.receipt(h)
        sent['nonce']='0x7'
        with patch('service.RpcBackend.receipt',return_value=receipt),patch('service.rpc',side_effect=[sent,{'hash':'reorg'}]):
            with self.assertRaisesRegex(RuntimeError,'canonical'):b.receipt(h)

    def test_nonce_change_prevents_submission(self):
        b=SignedBackend.__new__(SignedBackend);b.position_id=1;b.max_gas=8_000_000;b.transaction=lambda p:{'data':'0x1234'};b.nonce=lambda:2
        with self.assertRaises(RuntimeError):b.submit({'from':GOVERNOR,'to':LIQUIDATOR,'data':'0x1234','value':'0x0','gas':'0x10000'},1)

if __name__=='__main__':unittest.main()
