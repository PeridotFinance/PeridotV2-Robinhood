#!/usr/bin/env python3
"""Read-only RPC evidence. No accounts/signing/broadcast methods; cached, paced requests.
Uses cast's HTTP client because the public endpoint rejects Python urllib's user agent.
Outputs raw evidence plus a conditional historical range model, not a yield forecast.
"""
import argparse,json,math,os,subprocess,time,pathlib
P='0xdac1904D823F7d6bcAaF431ceEe40c934fEd321b'
N='0x2eBd7B85a4E08D5B508b04BA147976C94afE6590'
G='0x93c6A733FB3165D94774B3A5Fc4319D29bd7b6ce'
RAM='0x5173D45A1191eE33cBB7D8c7e65f21B04eD54802'
A='0x83341F891f898cb5E0cacC8a70501BBa83d9CecF'
F='0xE0c4ceb92d08CA985bB70fe0a22fEb121A9854A8'
parser=argparse.ArgumentParser();parser.add_argument('--block',type=int,required=True);parser.add_argument('--window-blocks',type=int,default=800000)
args=parser.parse_args();url=os.environ['ROBINHOOD_RPC_URL'];block=hex(args.block)
out=pathlib.Path('ramses-vaults/research');out.mkdir(exist_ok=True)
cachefile=out/('rpc-'+str(args.block)+'.json');cache=json.loads(cachefile.read_text()) if cachefile.exists() else {}
def rpc(method,params):
    assert method in ['eth_call','eth_getLogs','eth_getBlockByNumber','eth_getCode','eth_getStorageAt']
    key=json.dumps([method,params])
    if key in cache:return cache[key]
    for attempt in range(4):
        time.sleep(.15+attempt)
        p=subprocess.run(['cast','rpc','--rpc-url',url,method,'--raw',json.dumps(params)],text=True,capture_output=True)
        if p.returncode==0:
            val=json.loads(p.stdout);cache[key]=val;cachefile.write_text(json.dumps(cache,indent=2));return val
        if 'historical state' in p.stderr:raise RuntimeError(p.stderr)
    raise RuntimeError(p.stderr)
def call(a,sig,*values,at=block):
    data=subprocess.check_output(['cast','calldata',sig,*map(str,values)],text=True).strip()
    return rpc('eth_call',[{'to':a,'data':data},at])
def words(s):return [int(s[i:i+64],16) for i in range(2,len(s),64)]
def val(a,sig,*v,at=block):return words(call(a,sig,*v,at=at))[0]
def address(a,sig):return '0x'+call(a,sig)[-40:]
def signed(n,bits=256):return n-(1<<bits) if n>=1<<(bits-1) else n
def topic(s):return subprocess.check_output(['cast','keccak',s],text=True).strip()
head=rpc('eth_getBlockByNumber',[block,False]);startnum=args.block-args.window_blocks
start=rpc('eth_getBlockByNumber',[hex(startnum),False]);duration=int(head['timestamp'],16)-int(start['timestamp'],16)
slot=words(call(P,'slot0()'));tick=signed(slot[1]);sqrt=slot[0]/2**96;price=1e12/sqrt**2;liq=val(P,'liquidity()')
summary={'block':args.block,'nativeBlock':int(head['l1BlockNumber'],16),'timestamp':int(head['timestamp'],16),'hash':head['hash'],'startBlock':startnum,'seconds':duration,'slot0':slot,'tick':tick,'stockPrice':price,'liquidity':liq,'calls':{}}
for a,sigs in [(P,['token0()','token1()','factory()','tickSpacing()','fee()','protocolFees()','feeGrowthGlobal0X128()','feeGrowthGlobal1X128()','grossFeeGrowthGlobal0X128()','grossFeeGrowthGlobal1X128()']),
 (N,['factory()','deployer()','voter()','WETH9()']), (G,['nfpManager()','pool()','voter()','feeCollector()','getRewardTokens()','rewardValidator()','getAuthorizedClaimers()','firstPeriod()']),
 (F,['accessHub()','feeCollector()','voter()']), (A,['timelock()','multisig()'])]:
    for sig in sigs:
        try:summary['calls'][a+' '+sig]=call(a,sig)
        except RuntimeError as e:summary['calls'][a+' '+sig]={'error':str(e)[:250]}
t0=address(P,'token0()');t1=address(P,'token1()')
summary['rewardRates']={t:val(G,'rewardRate(address)',t) for t in [t0,t1,RAM]}
# RAM mark via CL WETH/RAM and CL WETH/USDG spot prices; no RAM/USDG CL depth assumed.
apiFile=out/('api-pools-'+str(args.block)+'.json')
if not apiFile.exists():
    apiFile.write_text(subprocess.check_output(['curl','--fail','--silent','--show-error','--max-time','30','https://gateway.kingdom.dev/robinhood/api/all-pools'],text=True))
api=json.loads(apiFile.read_text())['pools']
WETH='0x0bd7d308f8e1639fab988df18a8011f41eacad73'
def spot_pair(a,b):
    candidates=[p for p in api if p.get('isCl') and {p['token0']['address'].lower(),p['token1']['address'].lower()}=={a.lower(),b.lower()}]
    p=max(candidates,key=lambda p:p['tvlUsd']);w=words(call(p['id'],'slot0()'))
    ratio=(w[0]/2**96)**2*10**(p['token0']['decimals']-p['token1']['decimals'])
    return (ratio if p['token0']['address'].lower()==a.lower() else 1/ratio),p['id'],w
ramWeth,rp,rs=spot_pair(RAM,WETH);ethUSD,ep,es=spot_pair(WETH,t0);ramPrice=ramWeth*ethUSD
summary['ram']={'pool':rp,'slot0':rs,'wethUsdgPool':ep,'wethUsdgSlot0':es,'spotUSD':ramPrice,'ethUSD':ethUSD}
# Snapshot initialized ticks around ±10%, reconstruct active L by applying net changes.
ticks={}
for word in range((tick-1100)//2560,(tick+1100)//2560+1):
    bitmap=val(P,'tickBitmap(int16)',word)
    for bit in range(256):
        t=(word*256+bit)*10
        if bitmap>>bit&1 and abs(t-tick)<=1100:
            w=words(call(P,'ticks(int24)',t));ticks[t]={'gross':w[0],'net':signed(w[1]),'outside0':w[2],'outside1':w[3]}
summary['initializedTicks']=ticks
summary['distribution']=[]
for t in sorted(ticks):
    l=liq+sum(v['net'] for k,v in ticks.items() if tick<k<=t) if t>=tick else liq-sum(v['net'] for k,v in ticks.items() if t<k<=tick)
    summary['distribution'].append({'tick':t,'activeLiquidityAbove':l})
logs=[]
for lo in range(startnum,args.block+1,100000):
    logs+=rpc('eth_getLogs',[{'address':P,'fromBlock':hex(lo),'toBlock':hex(min(lo+99999,args.block))}])
swapTopic=topic('Swap(address,address,int256,int256,uint160,uint128,int24)')
summary['events']={'total':len(logs),'swaps':sum(x['topics'][0]==swapTopic for x in logs)}
swaps=[]
for x in logs:
    if x['topics'][0]!=swapTopic:continue
    w=words(x['data']);a0=signed(w[0]);a1=signed(w[1]);st=signed(w[4]);sl=w[3]
    swaps.append({'block':int(x['blockNumber'],16),'a0':a0,'a1':a1,'sqrt':w[2],'L':sl,'tick':st})
summary['events']['swapsDecoded']=swaps
volume=sum(abs(x['a0'])/1e6 for x in swaps);summary['volumeUSDG']=volume
# Historical approximation: swaps assigned at post-swap tick/L, seconds interpolated by block.
# Crossing swaps, liquidity updates between swaps, fee changes and timestamp interpolation are explicit error sources.
fee=val(P,'fee()')/1e6;protocol=slot[5]/1e6
summary['feeFraction']=fee;summary['protocolFraction']=protocol
summary['grossFeeUSDapprox']=sum((x['a0']/1e6 if x['a0']>0 else x['a1']/1e18*price)*fee for x in swaps)
summary['lpFeeUSDapprox']=summary['grossFeeUSDapprox']*(1-protocol)
summary['bands']=[]
last24=next(p for p in api if p['id'].lower()==P.lower());summary['api']=last24
for half in [10,50,100,500,1000]:
    lower=tick//10*10-half;upper=tick//10*10+half
    low=1.0001**(lower/2);high=1.0001**(upper/2)
    unit0=(1/sqrt-1/high)/1e6;unit1=(sqrt-low)/1e18
    capital=unit0+unit1*price
    feeUnit=0.;rewardUnit=0.;inSecs=0.
    for x in swaps:
        if lower<=x['tick']<upper and x['L']:
            gross=(x['a0']/1e6 if x['a0']>0 else x['a1']/1e18*price)*fee
            feeUnit+=gross*(1-protocol)/x['L']
    points=[{'block':startnum,**({'tick':swaps[0]['tick'],'L':swaps[0]['L']} if swaps else {'tick':tick,'L':liq})}]+swaps+[{'block':args.block,'tick':tick,'L':liq}]
    for a,b in zip(points,points[1:]):
        dt=(b['block']-a['block'])/args.window_blocks*duration
        if lower<=a['tick']<upper and a['L']:
            inSecs+=dt;rewardUnit+=dt/a['L']*summary['rewardRates'][RAM]/1e18*ramPrice
    summary['bands'].append({'halfTicks':half,'lower':lower,'upper':upper,'capitalForCurrentActiveL':capital*liq,'inRangeTimeApprox':inSecs/duration,
       'feeAprPctApprox':feeUnit/capital*31536000/duration*100,'rewardAprPctApprox':rewardUnit/capital*31536000/duration*100,
       'staticRewardAprPct':summary['rewardRates'][RAM]/1e18*ramPrice/(capital*liq)*31536000*100})
# Recent direct fee-growth delta when archive reads are available.
try:
    recent=args.block-1000
    bh=rpc('eth_getBlockByNumber',[hex(recent),False]);dt=int(head['timestamp'],16)-int(bh['timestamp'],16)
    summary['recentGrowth']={'start':recent,'seconds':dt,'net0':val(P,'feeGrowthGlobal0X128()')-val(P,'feeGrowthGlobal0X128()',at=hex(recent)),
        'net1':val(P,'feeGrowthGlobal1X128()')-val(P,'feeGrowthGlobal1X128()',at=hex(recent))}
except RuntimeError as e:summary['recentGrowth']={'unavailable':str(e)[:250]}
(out/'evidence.json').write_text(json.dumps(summary,indent=2));print(json.dumps({k:v for k,v in summary.items() if k not in ['calls','events','api','initializedTicks']},indent=2))
