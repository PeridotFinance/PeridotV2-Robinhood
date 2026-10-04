"""Read-only verification of the five September 26 vault queue transactions and live state."""
import json,sys
from pathlib import Path
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
from state import ROOT,GOVERNOR,CONTROLLER,MARKETS,call,cast,rpc,save
from vault_state import PAIRS
sys.path.insert(0,str(ROOT/'contracts/robinhood-vaults/margin-mainnet/tools'))
from rpc import rpc
VAULT='0x280825b2d856706Ff7E0d6351CcB2e935E1a9A2f'
ADMIN='0xad2165E6f3b8146D17815968470eDb8B9a0A4ab7'
TIMELOCK='0x6797FB8Ce049B42C5BC2b42Bf76c6d15C7B12498'
LIBRARY='0x813AbFeC0DE50f8674798CbaB72Ed7b5D8CcB9cB'
SLOT='0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc'
OLD='0x21c7e1c2caded480fa373c5c9b3f51492b2d50ac'
ZERO='0x'+'00'*32

def main():
    if int(rpc('eth_chainId',[]),16)!=4663: raise RuntimeError('Wrong chain')
    broadcast=json.loads((ROOT/'broadcast/UpgradeNativeBacking.s.sol/4663/run-latest.json').read_text())
    entries=broadcast['transactions']
    if len(entries)!=5: raise RuntimeError('Expected five queue transactions')
    artifact=json.loads((ROOT/'remediation/artifacts/RobinhoodBoostedVaultV2.json').read_text())
    candidate=entries[3]['contractAddress']
    block=rpc('eth_getBlockByNumber',['latest',False]); tag=block['number']
    code=rpc('eth_getCode',[candidate,tag])
    if code.lower()!=artifact['deployedBytecode']['object'].lower(): raise RuntimeError('Candidate runtime mismatch')
    if cast('keccak',rpc('eth_getCode',[LIBRARY,tag]))!='0x2db5ef48328c828e38fe4e523a7191c4321f0eb501641c65380313097826724d': raise RuntimeError('Library mismatch')
    delay=int(call(TIMELOCK,'getMinDelay()',block=tag),16)
    if delay<3600: raise RuntimeError('Delay too short')
    salt=cast('keccak','PERIDOT_VAULT_NATIVE_BACKING_2026_09_26')
    payload=cast('calldata','upgradeAndCall(address,address,bytes)',VAULT,candidate,'0x')
    operation=cast('keccak',cast('abi-encode','f(address,uint256,bytes,bytes32,bytes32)',ADMIN,'0',payload,ZERO,salt))
    if call(TIMELOCK,'hashOperation(address,uint256,bytes,bytes32,bytes32)',ADMIN,0,payload,ZERO,salt,block=tag).lower()!=operation.lower(): raise RuntimeError('Operation hash mismatch')
    expected=[
     ('pauseUSDGSupply',CONTROLLER,cast('calldata','_setMintPaused(address,bool)',MARKETS['pUSDG'],'true')),
     ('pauseStockSupply',CONTROLLER,cast('calldata','_setMintPaused(address,bool)',MARKETS['pNVDA'],'true')),
     ('pausePairAllocationAndSwaps',VAULT,cast('calldata','setPairPause(bytes32,bool,bool,bool)',PAIRS['production'],'true','true','false')),
     ('deployVaultV2',None,artifact['bytecode']['object']),
     ('scheduleUpgrade',TIMELOCK,cast('calldata','schedule(address,uint256,bytes,bytes32,bytes32,uint256)',ADMIN,'0',payload,ZERO,salt,str(delay)))
    ]
    records=[]
    for entry,(action,target,data) in zip(entries,expected):
     h=entry['hash']; tx=rpc('eth_getTransactionByHash',[h]); receipt=rpc('eth_getTransactionReceipt',[h])
     if not tx or not receipt or int(receipt['status'],16)!=1: raise RuntimeError('Missing successful receipt')
     if tx['from'].lower()!=GOVERNOR.lower() or int(tx['chainId'],16)!=4663 or int(tx['value'],16)!=0: raise RuntimeError('Wrong transaction identity')
     if (tx.get('to') or '').lower()!=(target or '').lower() or tx['input'].lower()!=data.lower(): raise RuntimeError('Wrong exact calldata/creation: '+action)
     canonical=rpc('eth_getBlockByNumber',[receipt['blockNumber'],False])
     if receipt['blockHash']!=canonical['hash']: raise RuntimeError('Noncanonical receipt')
     if action=='deployVaultV2' and receipt['contractAddress'].lower()!=candidate.lower(): raise RuntimeError('Wrong creation address')
     records.append({'action':action,'hash':h,'from':tx['from'],'to':tx.get('to'),'nonce':int(tx['nonce'],16),'valueWei':'0','inputKeccak':cast('keccak',tx['input']),'blockTimestamp':int(canonical['timestamp'],16),'receipt':receipt})
    eta=int(call(TIMELOCK,'getTimestamp(bytes32)',operation,block=tag),16)
    if eta!=records[-1]['blockTimestamp']+delay: raise RuntimeError('Unexpected operation timestamp')
    ready=bool(int(call(TIMELOCK,'isOperationReady(bytes32)',operation,block=tag),16))
    implementation='0x'+rpc('eth_getStorageAt',[VAULT,SLOT,tag])[-40:]
    if implementation.lower()!=OLD: raise RuntimeError('Implementation already changed; separately verify execution')
    if ('0x'+call(ADMIN,'owner()',block=tag)[-40:]).lower()!=TIMELOCK.lower(): raise RuntimeError('Proxy admin owner changed')
    config=call(VAULT,'pairConfig(bytes32)',PAIRS['production'],block=tag)[2:]
    words=[int(config[i:i+64],16) for i in range(0,len(config),64)]
    if words[-4:]!=[1,1,0,1]: raise RuntimeError('Unexpected pair flags: '+str(words[-4:]))
    ledgers={name:call(VAULT,'ledger(bytes32)',pair,block=tag) for name,pair in PAIRS.items()}
    for market in MARKETS.values():
     for sig in ['borrowGuardianPaused(address)','mintGuardianPaused(address)']:
      if not int(call(CONTROLLER,sig,market,block=tag),16): raise RuntimeError('Missing containment')
     if int(call(market,'totalBorrows()',block=tag),16): raise RuntimeError('Outstanding debt requires review')
    if not int(call(CONTROLLER,'seizeGuardianPaused()',block=tag),16): raise RuntimeError('Seizure not paused')
    if rpc('eth_getBlockByNumber',[tag,False])['hash']!=block['hash']: raise RuntimeError('Evidence block changed')
    report={'status':'DEPLOYED_AND_QUEUED_NOT_ACTIVATED','chainId':4663,'block':int(tag,16),'blockHash':block['hash'],'timestamp':int(block['timestamp'],16),'candidate':candidate,'runtimeHash':cast('keccak',code),'runtimeMatchesReviewedArtifact':True,'currentImplementation':implementation,'proxyAdmin':ADMIN,'timelock':TIMELOCK,'operationId':operation,'payload':payload,'salt':salt,'minDelaySeconds':delay,'readyAtTimestamp':eta,'readyAtUTC':datetime.fromtimestamp(eta,timezone.utc).isoformat(),'readyAtNewYork':datetime.fromtimestamp(eta,ZoneInfo('America/New_York')).isoformat(),'isReady':ready,'remainingSeconds':max(0,eta-int(block['timestamp'],16)),'containment':{'bothSupplyPaused':True,'bothBorrowPaused':True,'ordinarySeizePaused':True,'allocationPaused':True,'settlementSwapsPaused':True,'emergencyMode':False,'bothTotalBorrowsZero':True},'preExecutionLedgersRaw':ledgers,'transactions':records}
    save(ROOT/'remediation/evidence/vault-upgrade-queue.json',report)
    print(json.dumps({k:v for k,v in report.items() if k not in ['transactions','preExecutionLedgersRaw','payload']},indent=2))


if __name__ == "__main__":
    main()
