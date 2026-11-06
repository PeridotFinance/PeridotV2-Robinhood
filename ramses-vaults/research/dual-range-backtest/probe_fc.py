from common import *
r = rpc()
FC = "0x2Bef16A0081565E72100D73CBe19B1Bd2d802380"
code = r.call("eth_getCode", [FC, "latest"])
print("code len", (len(code)-2)//2)
cands = ["treasury()","treasuryFees()","voter()","accessHub()","feeDistributor()","feeDistributorFor(address)","collectProtocolFees(address)","collectProtocolFees(address,address)","setTreasury(address)","setTreasuryFees(uint256)","owner()","gaugeFeeBps()","treasuryFeesBps()","protocolFee()","getTreasury()","TREASURY()","voteModule()","initialize(address,address)","FEE_DENOMINATOR()","pairFees()"]
for c in cands:
    s = T(c)[2:10]
    print(c, s, s in code)
for c in ["treasury()","treasuryFees()","voter()","accessHub()","owner()"]:
    try: print(c, r.eth_call(FC, T(c)[:10]))
    except Exception as e: print(c, "ERR", str(e)[:80])
# NFP selectors
nfp = r.call("eth_getCode", [NFP, "latest"])
for c in ["getReward(uint256,address[])","stake(uint256)","unstake(uint256)","attach(uint256)","deposit(uint256)","getRewardForOwner(uint256,address[])","isAuthorizedClaimer(address)","setGauge(uint256)","notifyReward()","gaugeFor(address)","voter()","getPeriodReward(uint256,uint256,address[])"]:
    print("NFP", c, T(c)[2:10] in nfp)
