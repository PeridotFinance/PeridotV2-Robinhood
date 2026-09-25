"""Explicitly refresh the rehearsal pin after compilation, before collecting its evidence."""
import hashlib
import json
from rpc import ROOT, RPC, rpc

if __name__ == '__main__':
    assert int(rpc('eth_chainId', []), 16) == 4663
    number = int(rpc('eth_blockNumber', []), 16) - 128
    block = rpc('eth_getBlockByNumber', [hex(number), False])
    result = {'chainId': 4663, 'rpc': RPC, 'stateBlock': number,
              'nativeEvmBlockNumber': int(block['l1BlockNumber'], 16),
              'timestamp': int(block['timestamp'], 16), 'blockHash': block['hash']}
    path = ROOT/'deployments/robinhood-mainnet.margin-pin.json'
    path.write_text(json.dumps(result, indent=2)+'\n')
    path.with_suffix('.sha256').write_text(hashlib.sha256(path.read_bytes()).hexdigest()+'  '+path.name+'\n')
    print(json.dumps(result))
