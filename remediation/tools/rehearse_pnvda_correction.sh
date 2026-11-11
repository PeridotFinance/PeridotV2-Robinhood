#!/usr/bin/env bash
set -uo pipefail
P=8711; F=http://127.0.0.1:$P; L=0x064eFE76812F5f18Bd1aA9708739Fb33C810B2dD
anvil --fork-url https://rpc.mainnet.chain.robinhood.com --port $P --silent --auto-impersonate --timeout 60000 --retries 8 >/dev/null 2>&1 &
AP=$!
for i in $(seq 1 80); do cast block-number --rpc-url $F >/dev/null 2>&1 && break; sleep 0.5; done
echo "fork block $(cast rpc anvil_nodeInfo --rpc-url $F | python3 -c 'import json,sys;print(json.load(sys.stdin)["forkConfig"]["forkBlockNumber"])')"
cast rpc anvil_setBalance $L 0x16345785D8A0000 --rpc-url $F >/dev/null
acc(){ BN=$(cast block-number --rpc-url $F); for m in 0xa155ccCB986774AE818b3F10F07d01D1b7A47b26 0x55aEd0569c8f0D166D71facE57B57C2f2624a563; do cast rpc anvil_setStorageAt $m 0x0000000000000000000000000000000000000000000000000000000000000009 $(cast to-uint256 $BN) --rpc-url $F >/dev/null; done; }
PU=0x55aEd0569c8f0D166D71facE57B57C2f2624a563
pusd(){ python3 - <<P
import subprocess
def n(*a): return int(subprocess.run(['cast','call',*a,'--rpc-url','$F'],capture_output=True,text=True).stdout.split()[0])
U='0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168'
c=n('$PU','totalSupply()(uint256)')*n('$PU','exchangeRateStored()(uint256)')//10**18
b=n(U,'balanceOf(address)(uint256)','$PU')+n('$PU','vaultWithdrawableAssets()(uint256)')+n('$PU','totalBorrows()(uint256)')-n('$PU','totalReserves()(uint256)')
print('  pUSDG claims',c,'backing',b,'exchangeRate',n('$PU','exchangeRateStored()(uint256)'),' pNVDA exchangeRate',n('0xa155ccCB986774AE818b3F10F07d01D1b7A47b26','exchangeRateStored()(uint256)'))
P
}
S(){ echo "== $1"; REHEARSE=1 RH_RPC=$F bash pnvda_correction.sh $1 2>&1 | tail -${2:-4} || { echo STOPPED; kill $AP; exit 1; }; }
S preflight 8; pusd
S schedule 2
echo "  early execute: $(REHEARSE=1 RH_RPC=$F bash pnvda_correction.sh execute 2>&1 | tail -1)"
cast rpc evm_increaseTime 3601 --rpc-url $F >/dev/null; cast rpc evm_mine --rpc-url $F >/dev/null
S execute 2
acc; S gonogo 3
S sync 3
S close 2
acc; S checkpoint 6
S state 4; pusd
kill $AP
