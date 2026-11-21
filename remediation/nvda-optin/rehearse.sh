#!/usr/bin/env bash
# Anvil rehearsal of the NVDA opt-in rollout on a fork of the live chain, on top of the two ceremonies that run before
# it, each applied with ITS OWN script:
#   1. SPY margin   (tools/spy/ceremony.sh, deployed contracts from deployments/spy-margin/spy-margin-mainnet.json)
#   2. pSPY collateral (throwaway DeploySpyCollateral --broadcast, tools/spy-collateral/ceremony.sh preflight / t0)
#   3. fork tests remediation/test/fork/NvdaOptIn.t.sol against that state (they replay this ceremony themselves)
#   4. this rollout: stub journal -> ceremony fund -> throwaway DeployNvdaOptIn --broadcast -> preflight / t0 /
#      (1 h) gate / t1 / verify -> suppliers on both sides -> range keeper --pair NVDA/USDG/OPTIN --execute ->
#      verify -> rollback / (1 h) rollback-t1
# Only the signer differs (SIGN=--unlocked, Ledger impersonated). The SPY scripts run from a COPY of their worktree in
# .scratch (their worktree is read, never written). Logs: .scratch/nvda-optin-rehearsal/ (git-ignored).
#
# Rehearsal-only adjustments, all marked below: (1) pNVDA / pUSDG accrual aligned to anvil's block number (anvil
# reports the L2 height, the chain accrues on the native L1 number); (2) RHSPY and the NVDA 24/7 feed are etched with
# fixed-answer, always-fresh mocks at their real last answers, so the 1 h warps keep them fresh. The real ceremony
# runs in session with the live feeds.
#
#   bash remediation/nvda-optin/rehearse.sh          # from the repository root
set -euo pipefail
cd "$(dirname "$0")/../.."; ROOT=$(pwd); exec 2> >(grep -v "Found unknown" >&2)
SPY_SRC=${SPY_REPO:-/Users/joshua/Peridot/.wt-spy-collateral/contracts}
LOG=$ROOT/.scratch/nvda-optin-rehearsal; SPY=$LOG/spy-contracts; mkdir -p $LOG
P=${ANVIL_PORT:-8683}; F=http://127.0.0.1:$P
L=0x064eFE76812F5f18Bd1aA9708739Fb33C810B2dD; D=0x00000000000000000000000000000000DEC0DE5e
SPYD=0x00000000000000000000000000000000DEC0DE5c; K=0x82ABD8eE038c10F5bE00E5200C94265400999f7e
WHALE=0x8366a39CC670B4001A1121B8F6A443A643e40951
NVDA=0xd0601CE157Db5bdC3162BbaC2a2C8aF5320D9EEC; USDG=0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168
PNVDA=0xa155ccCB986774AE818b3F10F07d01D1b7A47b26; PUSDG=0x55aEd0569c8f0D166D71facE57B57C2f2624a563
VAULT=0x280825b2d856706Ff7E0d6351CcB2e935E1a9A2f; ADAPTER=0xadA73211711e4790bc83B5d6B39f47fE04D276f3
SPYFEED=0x319724394D3A0e3669269846abE664Cd621f9f6A; NVFEED=0x321A36e441DfBadA01Aa6f9884F14a344E133D1F
OPTIN=$(cast keccak "NVDA/USDG/OPTIN"); J=$ROOT/remediation/nvda-optin/journals/nvda-optin-rehearsal.json

echo "== copy the SPY ceremony tooling ($SPY_SRC @ $(git -C $SPY_SRC rev-parse --short HEAD)$(git -C $SPY_SRC diff --quiet && echo '' || echo ' +uncommitted'))"
rsync -a --delete --exclude 'out*' --exclude 'cache*' --exclude broadcast --exclude .scratch "$SPY_SRC/" "$SPY/"
# The copied foundry.toml has relative lib paths; keep the original's lib/ and node_modules (symlinks or dirs).
for d in lib node_modules; do [ -e "$SPY/$d" ] || ln -s "$SPY_SRC/$d" "$SPY/$d"; done

anvil --fork-url https://rpc.mainnet.chain.robinhood.com ${FORK_BLOCK:+--fork-block-number $FORK_BLOCK} --port $P --silent \
  --auto-impersonate --timeout 300000 --retries 20 --fork-retry-backoff 2000 --compute-units-per-second 40 >/dev/null 2>&1 &
AP=$!; trap 'kill $AP 2>/dev/null' EXIT
for i in $(seq 1 120); do cast block-number --rpc-url $F >/dev/null 2>&1 && break; sleep 0.5; done
FB=$(cast rpc anvil_nodeInfo --rpc-url $F | python3 -c 'import json,sys;print(json.load(sys.stdin)["forkConfig"]["forkBlockNumber"])')
echo "fork block $FB, time $(date -u -r $(cast block latest --field timestamp --rpc-url $F))"
for a in $D $SPYD $L $K; do cast rpc anvil_setBalance $a 0x16345785D8A0000 --rpc-url $F >/dev/null; done

# (1) rehearsal-only: accrual alignment (slot 9 = accrualBlockNumber)
BN=$(cast block-number --rpc-url $F)
for m in $PUSDG $PNVDA; do
  cast rpc anvil_setStorageAt $m 0x0000000000000000000000000000000000000000000000000000000000000009 $(cast to-uint256 $BN) --rpc-url $F >/dev/null; done
# (2) rehearsal-only: fresh fixed-answer feeds at their real last answers
( cd $SPY && FOUNDRY_PROFILE=robinhood_spy forge build >/dev/null 2>&1 )
( FOUNDRY_PROFILE=nvda_optin forge build >/dev/null 2>&1 )
hold(){ local feed=$1 code=$2 ans; ans=$(cast call $feed 'latestRoundData()(uint80,int256,uint256,uint256,uint80)' --rpc-url $F | sed -n 2p | cut -d' ' -f1)
  cast rpc anvil_setCode $feed $code --rpc-url $F >/dev/null
  cast rpc anvil_setStorageAt $feed 0x0000000000000000000000000000000000000000000000000000000000000000 $(cast to-uint256 $ans) --rpc-url $F >/dev/null
  echo "   $feed held at $ans"; }
hold $SPYFEED $(python3 -c "import json;print(json.load(open('$SPY/out-spy/SpyFreshFeedMock.sol/SpyFreshFeedMock.json'))['deployedBytecode']['object'])")
hold $NVFEED $(python3 -c "import json;print(json.load(open('remediation/out-nvda-optin/NvdaOptIn.t.sol/FreshFeedMock.json'))['deployedBytecode']['object'])")

export RPC=$F SIGN=--unlocked
MJ=$SPY/deployments/spy-margin/spy-margin-mainnet.json; CJ=$SPY/deployments/spy-collateral/spy-collateral-nvda-optin.json
warp(){ cast rpc evm_increaseTime $1 --rpc-url $F >/dev/null; cast rpc evm_mine --rpc-url $F >/dev/null; echo "== (warp $1 s)"; }
mrun(){ echo "== [1] SPY margin ceremony $1"; ( cd $SPY && tools/spy/ceremony.sh $MJ "$1" ) > $LOG/spy-margin-$1.log 2>&1 || { cat $LOG/spy-margin-$1.log; exit 1; }
  echo "   Ledger txs: $(grep -c 'status 0x1' $LOG/spy-margin-$1.log || true)"; }
mrun preflight; mrun t0; warp 3601; mrun gate; mrun t1

echo "== [2] pSPY collateral: deploy (throwaway) + ceremony"
rm -f $CJ
( cd $SPY && FOUNDRY_PROFILE=robinhood_spy SPY_COLLATERAL_JOURNAL=spy-collateral-nvda-optin forge script script/spy/DeploySpyCollateral.s.sol:DeploySpyCollateral \
  --rpc-url $F --sender $SPYD --unlocked --broadcast --slow ) > $LOG/spy-collateral-deploy.log 2>&1 || { tail -30 $LOG/spy-collateral-deploy.log; exit 1; }
crun(){ ( cd $SPY && tools/spy-collateral/ceremony.sh $MJ $CJ "$1" ) > $LOG/spy-collateral-$1.log 2>&1 || { cat $LOG/spy-collateral-$1.log; exit 1; }
  echo "   $1: Ledger txs $(grep -c 'status 0x1' $LOG/spy-collateral-$1.log || true)"; }
crun preflight; crun t0

echo "== [3] fork tests on the post-SPY state"
NVDA_OPTIN_FORK_RPC=$F FOUNDRY_PROFILE=nvda_optin forge test --match-path remediation/test/fork/NvdaOptIn.t.sol -vv > $LOG/fork-tests.log 2>&1 \
  || { grep -E "PASS|FAIL|Error|revert" $LOG/fork-tests.log | head -40; exit 1; }
grep -E "^Suite result|^\[PASS\]|^\[FAIL" $LOG/fork-tests.log | sed 's/^/   /'

echo "== [4] NVDA opt-in: stub journal, fund the throwaway deployer, deploy"
printf '{"deployer":"%s"}\n' $D > $J
run(){ echo "== ceremony $1"; remediation/nvda-optin/ceremony.sh $J "$1" 2>&1 | tee $LOG/$1.log; }
run fund
B0=$(cast balance $D --rpc-url $F)
FOUNDRY_PROFILE=nvda_optin NVDA_OPTIN_JOURNAL=nvda-optin-rehearsal forge script remediation/script/DeployNvdaOptIn.s.sol:DeployNvdaOptIn \
  --rpc-url $F --sender $D --unlocked --broadcast --slow > $LOG/deploy.log 2>&1 || { tail -30 $LOG/deploy.log; exit 1; }
echo "   txs $(python3 -c "import json;print(len(json.load(open('broadcast/DeployNvdaOptIn.s.sol/4663/run-latest.json'))['receipts']))"), deployer gas $(cast from-wei $((B0 - $(cast balance $D --rpc-url $F)))) ETH"
jq -c '{upNvda,upUsdg,adapterN}' $J
run preflight
run t0
warp 3601
run gate
run t1
run verify

echo "== suppliers on both sides, then the range keeper on OPTIN"
D1=$(jq -r .upNvda $J); D2=$(jq -r .upUsdg $J)
for t in "$NVDA:$D1:300000000000000000:0x000000000000000000000000000000000000A11C" "$USDG:$D2:44000000:0x000000000000000000000000000000000000B0B0"; do
  IFS=: read -r tok mkt amt who <<< "$t"
  cast rpc anvil_setBalance $who 0x16345785D8A0000 --rpc-url $F >/dev/null
  cast send $tok 'transfer(address,uint256)' $who $amt --from $WHALE --unlocked --rpc-url $F >/dev/null
  cast send $tok 'approve(address,uint256)' $mkt $amt --from $who --unlocked --rpc-url $F >/dev/null
  cast send $mkt 'mint(uint256)' $amt --from $who --unlocked --rpc-url $F --gas-limit 3000000 --json | jq -r '"   supply \(.status) gas \(.gasUsed)"'
done
echo "   OPTIN ledger before keeper: $(cast call $VAULT 'ledger(bytes32)(uint256,uint256,uint256,uint256,uint256,uint64)' $OPTIN --rpc-url $F | cut -d' ' -f1 | tr '\n' ' ')"
KJ=$LOG/range-keeper-optin.json; rm -f $KJ
for pass in 1 2; do
  python3 remediation/tools/range_keeper.py --rpc $F --unlocked --pair NVDA/USDG/OPTIN --sender $K --journal $KJ \
    --max-gas-price-wei 1000000000000 --execute > $LOG/keeper-$pass.log 2>&1 || true
  sed -E 's/"output": "[^"]*"/"output": "..."/;s/"time": [0-9]+, //' $LOG/keeper-$pass.log | tail -2 | sed 's/^/   keeper: /'
done
echo "   OPTIN position: $(cast call $ADAPTER 'positionState(bytes32)((uint256,uint128,uint256,uint256))' $OPTIN --rpc-url $F)"
echo "   production position (must stay liquidity 0): $(cast call $ADAPTER 'positionState(bytes32)((uint256,uint128,uint256,uint256))' $(cast keccak NVDA/USDG) --rpc-url $F)"

echo "== redeem both suppliers (native-token exits through the vault)"
for t in "$D1:0x000000000000000000000000000000000000A11C:$NVDA" "$D2:0x000000000000000000000000000000000000B0B0:$USDG"; do
  IFS=: read -r mkt who tok <<< "$t"; b0=$(cast call $tok 'balanceOf(address)(uint256)' $who --rpc-url $F | cut -d' ' -f1)
  cast send $mkt 'redeem(uint256)' $(cast call $mkt 'balanceOf(address)(uint256)' $who --rpc-url $F | cut -d' ' -f1) --from $who --unlocked --rpc-url $F --gas-limit 3000000 --json | jq -r '"   redeem \(.status)"'
  echo "   $who got $(( $(cast call $tok 'balanceOf(address)(uint256)' $who --rpc-url $F | cut -d' ' -f1) - b0 )) raw"
done
run verify
run rollback
warp 3601
run rollback-t1
echo "   after rollback: OPTIN flags $(cast call $VAULT 'pairConfig(bytes32)((address,address,address,address,uint128,uint128,uint64,uint32,uint32,uint16,uint16,uint16,uint8,uint8,bool,bool,bool,bool))' $OPTIN --rpc-url $F | tr -d '()' | awk -F', ' '{print $15,$16,$17}'), upNVDA/upUSDG vault claims $(cast call $D1 'vaultAccountedAssets()(uint256)' --rpc-url $F | cut -d' ' -f1)/$(cast call $D2 'vaultAccountedAssets()(uint256)' --rpc-url $F | cut -d' ' -f1)"
echo "== Ledger transactions sent in the NVDA opt-in ceremony (fund..t1): $(cat $LOG/fund.log $LOG/t0.log $LOG/t1.log | grep -c '^   status 0x1'), rollback: $(cat $LOG/rollback.log $LOG/rollback-t1.log | grep -c '^   status 0x1')"
echo "REHEARSAL COMPLETE"
