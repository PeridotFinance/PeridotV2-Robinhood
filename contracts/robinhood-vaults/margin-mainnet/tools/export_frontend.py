"""Export tested ABIs and an explicitly undeployed frontend integration manifest.

New margin addresses stay null until a separate receipt/runtime-verified mainnet
deployment record exists. Never promote localhost simulation addresses here.
"""
import hashlib
import json
from pathlib import Path
from rpc import ROOT
from preflight import ADDRESSES

CONTRACTS = {
    'executor': 'IsolatedMarginExecutorUpgradeable',
    'marginVault': 'IsolatedMarginVaultUpgradeable',
    'config': 'IsolatedMarginConfigUpgradeable',
    'riskEngine': 'IsolatedMarginRiskEngineUpgradeable',
    'quoter': 'IsolatedMarginQuoter',
    'liquidator': 'IsolatedMarginLiquidatorUpgradeable',
    'oracle': 'RobinhoodMarginPriceOracle',
    'guardedSource': 'GuardedMarginPriceSource',
    'flashVault': 'SimpleFlashLoanVault',
    'router': 'RobinhoodV4RouterAdapter',
    'swapModule': 'IsolatedMarginSwapModule',
    'accountFactory': 'IsolatedMarginAccountFactory',
    'insuranceFund': 'MarginInsuranceFundUpgradeable',
    'feeDistributor': 'MarginFeeDistributorUpgradeable',
    'pToken': 'RobinhoodBoostedDelegate',
    'erc20': 'IERC20',
}


def main():
    output = ROOT/'frontend/margin-mainnet'
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output/'manifest.json'
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text())
        assert previous['status'] == 'AWAITING_MAINNET_DEPLOYMENT', 'Never overwrite a live manifest'
        assert not any(previous['marginAddresses'].values()), 'Existing addresses require reconciliation'
    artifacts = {}
    for key, name in CONTRACTS.items():
        source = ROOT/'out-margin-mainnet'/(name+'.sol')/(name+'.json')
        artifact = json.loads(source.read_text())
        abi = artifact['abi']
        assert isinstance(abi, list) and abi
        target = output/(name+'.abi.json')
        target.write_text(json.dumps(abi, indent=2)+'\n')
        artifacts[key] = {'contract': name, 'abiFile': target.name,
                          'abiSha256': hashlib.sha256(target.read_bytes()).hexdigest(),
                          'buildArtifactSha256': hashlib.sha256(source.read_bytes()).hexdigest()}
    result = {
        'status': 'AWAITING_MAINNET_DEPLOYMENT', 'tradingReady': False,
        'chainId': 4663, 'rpcURL': 'https://rpc.mainnet.chain.robinhood.com',
        'explorerURL': 'https://robinhoodchain.blockscout.com', 'gasSymbol': 'ETH',
        'existingAddresses': {key: ADDRESSES[key] for key in ['usd','stock','pUsd','pStock','controller','feed','guard']},
        'marginAddresses': {key: None for key in CONTRACTS if key not in ['pToken','erc20']},
        'requestedFinalState': {'opensPaused': False, 'flashPaused': False, 'maxLeverageX100': 200,
                               'maxPositionValueUsd18': str(2*10**18), 'maxDebtValueUsd18': str(10**18),
                               'perPositionCapsOnly': True, 'testerAllowlist': False},
        'pairs': {
            'long': {'marginPToken': ADDRESSES['pUsd'], 'positionPToken': ADDRESSES['pStock'],
                     'debtPToken': ADDRESSES['pUsd'], 'side': 0},
            'short': {'marginPToken': ADDRESSES['pUsd'], 'positionPToken': ADDRESSES['pUsd'],
                      'debtPToken': ADDRESSES['pStock'], 'side': 1},
        },
        'decimals': {'usd': 6, 'stock': 18, 'pToken': 8, 'usdRiskValues': 18},
        'positionStatus': {'NONE':0,'OPENING':1,'ACTIVE':2,'CLOSING':3,'LIQUIDATING':4,'CLOSED':5,'LIQUIDATED':6},
        'artifacts': artifacts,
        'note': 'ABIs are ready for integration. No new margin address is deployed by this exporter. Keep transactions disabled until live addresses and state are verified.'
    }
    manifest_path.write_text(json.dumps(result, indent=2)+'\n')
    print('Exported', len(artifacts), 'ABIs. Margin addresses remain null; tradingReady=false.')


if __name__ == '__main__':
    main()
