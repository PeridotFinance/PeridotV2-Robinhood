"""Safety regressions for accepting actual receipts before advancing a deployment."""
import copy
import unittest
from deploy_live import ACTOR, check_receipts


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.plan = {'transactions':[{'hash':'0x123','transaction':{'to':'0xAbCd','input':'0x1234','value':'0x0'}}]}
        self.receipt = {'transactionHash':'0x123','status':'0x1','blockNumber':'0x10','blockHash':'0xblock'}
        self.sent = {'from':ACTOR,'nonce':'0x98','chainId':hex(4663),'to':'0xabcd','input':'0x1234','value':'0x0'}
        self.block = {'hash':'0xblock'}

    def fetch(self, method, params):
        return {'eth_getTransactionReceipt':self.receipt,'eth_getTransactionByHash':self.sent,
                'eth_getBlockByNumber':self.block}[method]

    def check(self):
        return check_receipts(self.plan,self.fetch,1,152)

    def test_accepts_canonical_matching_transaction_with_checksum_difference(self):
        self.assertEqual(self.check(),[self.receipt])

    def test_missing_transaction_hash_cannot_be_replayed(self):
        self.plan['transactions'][0]['hash'] = None
        with self.assertRaisesRegex(RuntimeError,'ambiguous'): self.check()

    def test_reverted_receipt_cannot_advance(self):
        self.receipt['status'] = '0x0'
        with self.assertRaisesRegex(RuntimeError,'reverted'): self.check()

    def test_pending_receipt_cannot_advance(self):
        self.receipt = None
        with self.assertRaisesRegex(RuntimeError,'pending'): self.check()

    def test_wrong_nonce_or_chain_cannot_advance(self):
        for field,value in [('nonce','0x99'),('chainId',hex(46630))]:
            old = self.sent[field];self.sent[field] = value
            with self.assertRaisesRegex(RuntimeError,'differs'): self.check()
            self.sent[field] = old

    def test_changed_sender_target_call_or_value_cannot_advance(self):
        for field,value in [('from','0xwrong'),('to','0xother'),('input','0x9999'),('value','0x1')]:
            old = self.sent[field];self.sent[field] = value
            with self.assertRaisesRegex(RuntimeError,'differs'): self.check()
            self.sent[field] = old

    def test_partial_broadcast_cannot_advance(self):
        with self.assertRaisesRegex(RuntimeError,'count'):
            check_receipts(self.plan,self.fetch,2,152)

    def test_reorged_receipt_cannot_advance(self):
        self.block['hash'] = '0xother'
        with self.assertRaisesRegex(RuntimeError,'canonical'): self.check()

    def test_creation_receipt_must_match_predicted_address(self):
        self.plan['transactions'][0].update(contractAddress='0x1234')
        self.plan['transactions'][0]['transaction']['to'] = None
        self.sent['to'] = None
        self.receipt['contractAddress'] = '0x9999'
        with self.assertRaisesRegex(RuntimeError,'CREATE'): self.check()
        self.receipt['contractAddress'] = '0x1234'
        self.assertEqual(self.check(),[self.receipt])


if __name__ == '__main__':
    unittest.main()
