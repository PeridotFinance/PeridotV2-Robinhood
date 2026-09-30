"""Scope limits, mined-event identity and margin exit accounting regressions."""
import copy
import json
import unittest
from unittest.mock import patch
import margin_acceptance as m

OPEN_SIG='openPosition((address,address,address,uint256,uint16,uint256,uint256,uint8,bytes))'
CLOSE_SIG='closePosition((uint256,uint16,uint256,uint256,uint256,bytes,bytes))'


def item(target, signature, *args):
    return {'to':target.lower(),'input':m.cast('calldata',signature,*map(str,args)).lower(),'value':0}


def open_plan(side='long', leverage=200, minimum=1, fee=0):
    pair=m.MANIFEST['pairs'][side]
    return [item(m.PUSD,'approve(address,uint256)',m.A['marginVault'],m.SHARES),
            item(m.A['marginVault'],'deposit(address,uint256)',m.PUSD,m.SHARES),
            item(m.PUSD,'approve(address,uint256)',m.A['marginVault'],0),
            item(m.A['executor'],OPEN_SIG,'('+','.join(map(str,[m.PUSD,pair['positionPToken'],pair['debtPToken'],m.SHARES,leverage,fee,minimum,pair['side'],'0x']))+')')]


class MarginAcceptanceTest(unittest.TestCase):
    def test_valid_long_and_short_plans(self):
        for side in ('long','short'):
            m.validate_plan('open',open_plan(side),side)

    def test_no_silent_leverage_increase(self):
        with self.assertRaisesRegex(RuntimeError,'Dynamic'):
            m.validate_plan('open',open_plan(leverage=500),'long')

    def test_no_zero_open_minimum(self):
        with self.assertRaisesRegex(RuntimeError,'Dynamic'):
            m.validate_plan('open',open_plan(minimum=0),'long')

    def test_no_unreviewed_fee(self):
        with self.assertRaisesRegex(RuntimeError,'Dynamic'):
            m.validate_plan('open',open_plan(fee=1),'long')

    def test_wrong_direction_rejected(self):
        with self.assertRaisesRegex(RuntimeError,'Dynamic'):
            m.validate_plan('open',open_plan('long'),'short')

    def test_unlimited_approval_rejected(self):
        plan=open_plan();plan[0]=item(m.PUSD,'approve(address,uint256)',m.A['marginVault'],2**256-1)
        with self.assertRaisesRegex(RuntimeError,'Unexpected action'):
            m.validate_plan('open',plan,'long')

    def test_close_id_and_full_close_enforced(self):
        plan=[item(m.A['executor'],CLOSE_SIG,'(7,10000,0,200000,180000,0x,0x)')]
        m.validate_plan('close',plan,'long',7)
        with self.assertRaisesRegex(RuntimeError,'Dynamic'):
            m.validate_plan('close',plan,'long',8)

    def test_short_uses_dynamic_protocol_floor(self):
        plan=[item(m.A['executor'],CLOSE_SIG,'(7,10000,0,200000,0,0x,0x)')]
        m.validate_plan('close',plan,'short',7)
        with self.assertRaisesRegex(RuntimeError,'Dynamic'):
            m.validate_plan('close',plan,'long',7)

    def test_withdraw_exact_free_balance(self):
        plan=[item(m.A['marginVault'],'withdraw(address,uint256)',m.PUSD,1234)]
        m.validate_plan('withdraw',plan,'long',7,1234)
        with self.assertRaisesRegex(RuntimeError,'Dynamic'):
            m.validate_plan('withdraw',plan,'long',7,1233)

    def test_duplicate_open_events_rejected(self):
        log={'address':m.A['executor'],'topics':[m.cast('keccak',m.OPEN_EVENT)]}
        with self.assertRaisesRegex(RuntimeError,'exactly one'):
            m.match_event({'logs':[log,log]},m.A['executor'],m.OPEN_EVENT)

    def test_other_emitter_ignored(self):
        log={'address':m.A['marginVault'],'topics':[m.cast('keccak',m.OPEN_EVENT)]}
        with self.assertRaisesRegex(RuntimeError,'exactly one'):
            m.match_event({'logs':[log]},m.A['executor'],m.OPEN_EVENT)

    def closed(self):
        return {'position':{'id':7,'owner':m.GOVERNOR.lower(),'account':'0x'+'11'*20,
                            'margin':m.PUSD.lower(),'asset':m.PSTOCK.lower(),'debtMarket':m.PUSD.lower(),
                            'side':0,'status':5,'requestedLeverage':200,'locked':0,
                            'dollarDebt':0,'stockDebt':0,'dollarShares':0,'stockShares':0},
                'allowance':0,'locked':0,'free':950000000,'walletShares':12000000000,'exchangeRate':200000000000000}

    def test_closed_debt_and_share_residue_rejected(self):
        after=self.closed();before={'walletShares':after['walletShares']}
        m.validate_post('long','close',before,after,7,after['position']['account'])
        for field in ('stockDebt','dollarDebt','stockShares','dollarShares','locked'):
            bad=copy.deepcopy(after);bad['position'][field]=1
            with self.assertRaisesRegex(RuntimeError,'left debt'):
                m.validate_post('long','close',before,bad,7,after['position']['account'])

    def test_minimum_return_and_withdraw_delta(self):
        after=self.closed();before={'walletShares':after['walletShares']}
        after['free']=1
        with self.assertRaisesRegex(RuntimeError,'stated floor'):
            m.validate_post('long','close',before,after,7,after['position']['account'])
        after=self.closed();before=copy.deepcopy(after)
        after['walletShares']+=after['free'];after['free']=0
        m.validate_post('long','withdraw',before,after,7,after['position']['account'])
        after['walletShares']-=1
        with self.assertRaisesRegex(RuntimeError,'Withdrawal'):
            m.validate_post('long','withdraw',before,after,7,after['position']['account'])

    def test_verify_mode_never_runs_forge(self):
        intent={'example':'existing intent'}
        with patch.object(m,'rpc',return_value=hex(4663)), patch.object(m.Path,'read_text',return_value=json.dumps(intent)), patch.object(m,'verify') as verify, patch.object(m.subprocess,'run',side_effect=AssertionError('must not run')), patch.object(m.sys,'argv',['margin_acceptance.py','--side','long','--stage','open','--verify']):
            m.main()
        verify.assert_called_once_with('long','open',intent)


if __name__=='__main__':
    unittest.main()
