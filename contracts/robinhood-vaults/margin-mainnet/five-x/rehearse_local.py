import sys,json,gzip,subprocess,os,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'margin-mainnet/tools'));sys.path.insert(0,str(ROOT/'margin-mainnet/keeper-service'))
import importlib.util
spec=importlib.util.spec_from_file_location("legacy_local_fixture",ROOT/"margin-mainnet/tools/rehearse_local.py")
rh=importlib.util.module_from_spec(spec);spec.loader.exec_module(rh)
feed_code=rh.feed_code
from rpc import cast,call,words,address,rpc,RPC
from service import SignedBackend,cycle
URL='http://127.0.0.1:8559';rh.URL=URL
local=rh.local
A=json.loads((ROOT/'deployments/margin-mainnet-live/addresses.json').read_text());ACTOR=A['actor']
n=int(rpc('eth_blockNumber',[]),16)-16
b=rpc('eth_getBlockByNumber',[hex(n),False])
PIN={'chainId':4663,'rpc':RPC,'stateBlock':n,'nativeEvmBlockNumber':int(b['l1BlockNumber'],16),'timestamp':int(b['timestamp'],16),'blockHash':b['hash']}
print('Local rehearsal pin',PIN,flush=True)
OUT=ROOT/'deployments/margin-mainnet-5x-local';OUT.mkdir(exist_ok=True)
RECORD={'surface':'LOCALHOST FORK ONLY; not mainnet transactions','pin':PIN,'transactions':[]}
assert not (OUT/'result.json').exists(), 'Preserve the existing evidence; use a new output directory for a new rehearsal'
def read(k,sig,*args):return words(call(A.get(k,k),sig,*args,url=URL))
def send(to,data):
 tx={'from':ACTOR,'data':data,'gas':hex(8_000_000)}
 if to:tx['to']=A.get(to,to)
 h=local('eth_sendTransaction',[tx]);r=None
 for _ in range(1800):
  r=local('eth_getTransactionReceipt',[h])
  if r:break
  time.sleep(0.1)
 assert r and r['status']=='0x1',(h,r)
 RECORD['transactions'].append({'hash':h,'receipt':r,'transaction':tx});return r

def invoke(k,sig,*args):return send(k,cast('calldata',sig,*args))
def phase(name):
 env=dict(os.environ,FOUNDRY_PROFILE='margin_mainnet',FOUNDRY_BROADCAST=str(OUT/('broadcast-'+name)))
 cmd=['forge','script','margin-mainnet/five-x/UpdateMainnetFiveX.s.sol:UpdateMainnetFiveX','--sig',name+'()',
      '--rpc-url',URL,'--sender',ACTOR,'--unlocked','--broadcast','--slow','--skip-simulation','--gas-estimate-multiplier','300']
 with (OUT/(name+'.log')).open('w') as f:subprocess.run(cmd,cwd=ROOT,env=env,stdout=f,stderr=subprocess.STDOUT,check=True)
 f=next((OUT/('broadcast-'+name)).glob('**/*-latest.json'));d=json.loads(f.read_text())
 for tx in d['transactions']:
  r=local('eth_getTransactionReceipt',[tx['hash']]);assert r['status']=='0x1';RECORD['transactions'].append({'hash':tx['hash'],'receipt':r,'phase':name})
 print(name,'mined',flush=True)

local('anvil_reset',[{'forking':{'jsonRpcUrl':PIN['rpc'],'blockNumber':PIN['stateBlock']}}])
# Restore native L1-derived EVM clock on this disposable fork, preserving all balances.
d=json.loads(gzip.decompress(bytes.fromhex(local('anvil_dumpState',[])[2:])));d['block']['number']=hex(PIN['nativeEvmBlockNumber']);d['best_block_number']=PIN['nativeEvmBlockNumber'];local('anvil_loadState',['0x'+gzip.compress(json.dumps(d).encode()).hex()]);local('evm_mine',[])
local('anvil_impersonateAccount',[ACTOR]);phase('queue');now=int(local('eth_getBlockByNumber',['latest',False])['timestamp'],16);local('evm_setNextBlockTimestamp',[now+3601]);local('evm_mine',[]);phase('applyRisk')
# Mint/deposit/open a real 20-cent long using original mainnet balances and reserves.
before=read('pUsd','balanceOf(address)',ACTOR)[0]
invoke('usd','approve(address,uint256)',A['pUsd'],200000);invoke('pUsd','mint(uint256)',200000)
shares=read('pUsd','balanceOf(address)',ACTOR)[0]-before
invoke('pUsd','approve(address,uint256)',A['marginVault'],shares);invoke('marginVault','deposit(address,uint256)',A['pUsd'],shares)
amount=shares*read('pUsd','exchangeRateStored()')[0]//10**18
flash,minimum=read('quoter','quoteOpen(address,address,address,uint256,uint16)',A['pUsd'],A['pStock'],A['pUsd'],amount,500)
position=read('executor','nextPositionId()')[0]
invoke('executor','openPosition((address,address,address,uint256,uint16,uint256,uint256,uint8,bytes))',f"({A['pUsd']},{A['pStock']},{A['pUsd']},{shares},500,0,{minimum},0,0x)")
print('opened position',position,flush=True)
state=OUT/'keeper';state.mkdir(exist_ok=True);backend=SignedBackend(URL,A,state,local_unlocked=True)
healthy=cycle(backend,state,True);assert healthy['positions'][0]['status']=='not_executable_stop',healthy;RECORD['healthyKeeper']=healthy
# Independent severe price scenario: injected funds only for pool shock, never for baseline open.
artifact=json.loads((ROOT/'out-margin-mainnet/RobinhoodMainnetMarginFork.t.sol/MainnetPoolShock.json').read_text())
constructor=cast('abi-encode','f(address)','0x8366a39CC670B4001A1121B8F6A443A643e40951')[2:]
mover=send(None,artifact['bytecode']['object']+constructor)['contractAddress']
RECORD['poolShockFundingFixtures']=[rh.wallet_balance(A['usd'],1_000_000*10**6),rh.wallet_balance(A['stock'],10_000*10**18)]
invoke('usd','transfer(address,uint256)',mover,1_000_000*10**6);invoke('stock','transfer(address,uint256)',mover,10_000*10**18)
# Read sqrtPrice from v4 state storage using extsload(poolId slot).
pair=cast('keccak','NVDA/USDG');key=read('0xadA73211711e4790bc83B5d6B39f47fE04D276f3','poolKey(bytes32)',pair)
keystr=f'({address(key[0])},{address(key[1])},{key[2]},{key[3]},{address(key[4])})'
poolid=cast('keccak',cast('abi-encode','f((address,address,uint24,int24,address))',keystr));slot=cast('keccak',cast('abi-encode','f(bytes32,uint256)',poolid,6))
packed=read('0x8366a39CC670B4001A1121B8F6A443A643e40951','extsload(bytes32)',slot)[0];sqrt=packed&((1<<160)-1)
import math
target=sqrt*10**11//math.isqrt(8500*10**18)
invoke(mover,'move((address,address,uint24,int24,address),uint160,bool,uint256)',keystr,target,'false',10_000*10**18)
# Oracle scenario tracks actual pool sqrt-price (USD currency0, stock currency1).
price8=(10**18*2**192//(target*target))*100
now=int(local('eth_getBlockByNumber',['latest',False])['timestamp'],16)
local('anvil_setCode',['0x379EC4f7C378F34a1B47E4F3cbeBCbAC3E8E9F15',feed_code(price8,now)])
invoke('0x280825b2d856706Ff7E0d6351CcB2e935E1a9A2f','checkpoint(bytes32,uint256)',pair,now+120)
print('pool/feed shock applied',price8,flush=True)
result=cycle(backend,state,True);RECORD['liquidationKeeper']=result
for _ in range(40):
 if result['status']!='waiting_receipt':break
 time.sleep(2);result=cycle(backend,state,True)
assert result['positions'][0]['status']=='resolved',result
nonce=local('eth_getTransactionCount',[ACTOR,'latest']);repeat=cycle(backend,state,True);assert local('eth_getTransactionCount',[ACTOR,'latest'])==nonce
RECORD['repeatNoAdditionalTransactions']=True;RECORD['status']='passed';(OUT/'result.json').write_text(json.dumps(RECORD,indent=2)+'\n');print('Keeper liquidation and no-resend verified',flush=True)
