"""Independently verify the completed vault upgrade and retained containment; read-only."""
import json
from state import ROOT, GOVERNOR, CONTROLLER, MARKETS, call, cast, save, read_state
from record_vault_queue import VAULT, ADMIN, TIMELOCK, LIBRARY, SLOT, ZERO, rpc
from vault_state import PAIRS


def main():
    if int(rpc('eth_chainId', []), 16) != 4663:
        raise RuntimeError('Wrong chain')
    queue = json.loads((ROOT / 'remediation/evidence/vault-upgrade-queue.json').read_text())
    candidate = queue['candidate']
    payload = cast('calldata', 'upgradeAndCall(address,address,bytes)', VAULT, candidate, '0x')
    operation = cast('keccak', cast('abi-encode', 'f(address,uint256,bytes,bytes32,bytes32)',
                                  ADMIN, '0', payload, ZERO, queue['salt']))
    if payload != queue['payload'] or operation != queue['operationId']:
        raise RuntimeError('Queued payload mismatch')
    broadcast = json.loads((ROOT / 'broadcast/UpgradeNativeBacking.s.sol/4663/run-latest.json').read_text())
    entries = broadcast['transactions']
    if len(entries) != 1:
        raise RuntimeError('Expected one execution transaction')
    tx_hash = entries[0]['hash']
    tx = rpc('eth_getTransactionByHash', [tx_hash])
    receipt = rpc('eth_getTransactionReceipt', [tx_hash])
    expected = cast('calldata', 'execute(address,uint256,bytes,bytes32,bytes32)',
                    ADMIN, '0', payload, ZERO, queue['salt'])
    if (not tx or not receipt or int(receipt['status'], 16) != 1
            or tx['from'].lower() != GOVERNOR.lower() or tx['to'].lower() != TIMELOCK.lower()
            or int(tx['chainId'], 16) != 4663 or int(tx['value'], 16) != 0
            or tx['input'].lower() != expected.lower()):
        raise RuntimeError('Unexpected execution transaction/receipt/calldata')
    execution_block = rpc('eth_getBlockByNumber', [receipt['blockNumber'], False])
    if (execution_block['hash'] != receipt['blockHash']
            or int(execution_block['timestamp'], 16) < queue['readyAtTimestamp']):
        raise RuntimeError('Noncanonical or premature execution')

    state = read_state()
    tag = hex(state['block'])
    if (not state['seizePaused'] or any(not m['mintPaused'] or not m['borrowPaused']
            or int(m['totalBorrowsRaw']) for m in state['markets'].values())):
        raise RuntimeError('Containment or zero-debt expectation changed')
    implementation = '0x' + rpc('eth_getStorageAt', [VAULT, SLOT, tag])[-40:]
    if implementation.lower() != candidate.lower():
        raise RuntimeError('Proxy implementation mismatch')
    artifact = json.loads((ROOT / 'remediation/artifacts/RobinhoodBoostedVaultV2.json').read_text())
    code = rpc('eth_getCode', [implementation, tag])
    if (code.lower() != artifact['deployedBytecode']['object'].lower()
            or cast('keccak', code) != queue['runtimeHash']):
        raise RuntimeError('Implementation runtime mismatch')
    if cast('keccak', rpc('eth_getCode', [LIBRARY, tag])) != '0x2db5ef48328c828e38fe4e523a7191c4321f0eb501641c65380313097826724d':
        raise RuntimeError('Settlement library changed')
    if not int(call(TIMELOCK, 'isOperationDone(bytes32)', operation, block=tag), 16):
        raise RuntimeError('Timelock operation not completed')
    if ('0x' + call(ADMIN, 'owner()', block=tag)[-40:]).lower() != TIMELOCK.lower():
        raise RuntimeError('ProxyAdmin owner changed')
    ledgers = {name: call(VAULT, 'ledger(bytes32)', pair, block=tag) for name, pair in PAIRS.items()}
    if ledgers != queue['preExecutionLedgersRaw']:
        raise RuntimeError('Pair ledgers differ from independently recorded pre-execution state')
    config = call(VAULT, 'pairConfig(bytes32)', PAIRS['production'], block=tag)[2:]
    words = [int(config[i:i+64], 16) for i in range(0, len(config), 64)]
    if words[-4:] != [1, 1, 0, 1]:
        raise RuntimeError('Unexpected pair containment flags')
    integration = {}
    prior = json.loads((ROOT / 'remediation/evidence/installed-adapter.json').read_text())['state']
    for name, market in MARKETS.items():
        m = state['markets'][name]
        if m['exchangeRateStored'] != prior['markets'][name]['exchangeRateStored']:
            raise RuntimeError('Stored exchange rate changed: ' + name)
        cash = int(call(market, 'getCash()', block=tag), 16)
        vault_cash = int(call(VAULT, 'withdrawableAssets(bytes32,address)',
                             PAIRS['production'], m['underlying'], block=tag), 16)
        if cash != int(m['localCashRaw']) + vault_cash:
            raise RuntimeError('pToken cash does not match reachable vault cash: ' + name)
        integration[name] = {'storedExchangeRateUnchanged': True, 'marketCashRaw': str(cash),
                             'vaultWithdrawableCashRaw': str(vault_cash), 'localCashRaw': m['localCashRaw']}
    if rpc('eth_getBlockByNumber', [tag, False])['hash'] != state['blockHash']:
        raise RuntimeError('Evidence block changed')
    report = {'status': 'UPGRADED_AND_VERIFIED_PAUSED', 'chainId': 4663, 'block': state['block'],
              'blockHash': state['blockHash'], 'timestamp': state['timestamp'], 'vault': VAULT,
              'implementation': implementation, 'runtimeHash': cast('keccak', code),
              'operationId': operation, 'operationDone': True, 'bothPairLedgersUnchanged': True,
              'pairLedgersRaw': ledgers, 'marketIntegration': integration,
              'allocationPaused': True, 'settlementSwapsPaused': True, 'emergencyMode': False,
              'state': state, 'transaction': {'hash': tx_hash, 'from': tx['from'], 'to': tx['to'],
              'nonce': int(tx['nonce'], 16), 'valueWei': '0', 'inputKeccak': cast('keccak', tx['input']),
              'executionTimestamp': int(execution_block['timestamp'], 16), 'receipt': receipt}}
    save(ROOT / 'remediation/evidence/vault-upgrade-execution.json', report)
    print(json.dumps({k: v for k, v in report.items() if k not in ('state', 'pairLedgersRaw', 'transaction')}, indent=2))
    print('Canonical execution transaction:', tx_hash)


if __name__ == '__main__':
    main()
