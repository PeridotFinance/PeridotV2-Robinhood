#!/usr/bin/env bash
# NVDA opt-in vault (pair NVDA/USDG/OPTIN on the live Uniswap vault 0x2808, markets upNVDA / upUSDG on controller
# 0x6148): the exact Ledger calls, phase by phase, each followed by a state check.
#
#   remediation/nvda-optin/ceremony.sh <journal.json> <phase>
#   phases: fund | preflight | t0 | gate | t1 | verify | rollback | rollback-t1
#
# Order: SPY margin ceremony -> pSPY collateral ceremony -> this. Preflight refuses otherwise (controller oracle must be
# adapter3 0x74f1 and pSPY listed). Run in one weekday US session with a fresh NVDA answer; t1 >= 1 h after t0.
# Signing: SIGN defaults to "--ledger" (Ledger 0x064eFE76...B2dD). The anvil rehearsal sets SIGN=--unlocked and
# RPC=http://127.0.0.1:<port>. Every call is a single `cast send` from the Ledger. Nothing here reads a key.
# [LIVE-NVDA] marks the calls that touch live NVDA lending state: t0 step 3 (controller oracle) and t1 O3/O4
# (plain pNVDA / pUSDG stop using the vault and pull their dust home).
#
# Parameters (env, defaults = accepted scope): SEED_NVDA_WEI=20000000000000000 (0.02) SEED_USDG_RAW=4000000 (4)
# RESERVE_NVDA_WEI=30000000000000000 (0.03) RESERVE_USDG_RAW=5000000 (5) DEPLOYER_FUND_WEI=500000000000000 (0.0005 ETH)
set -euo pipefail
J=${1:?journal}; PHASE=${2:?phase}
RPC=${RPC:-https://rpc.mainnet.chain.robinhood.com}; SIGN=${SIGN:---ledger}
HERE=$(cd "$(dirname "$0")" && pwd); ROOT=$(cd "$HERE/../.." && pwd)
L=0x064eFE76812F5f18Bd1aA9708739Fb33C810B2dD; OLD=0x94696d767e65a75581145646960FA0eC886cE5d2
CONTROLLER=0x6148183676E304dbe63a85C350c208DA3cEAc39C; ADAPTER3=0x74f1ea31ED6615833F096D1Ac695036538969A59
VAULT=0x280825b2d856706Ff7E0d6351CcB2e935E1a9A2f; ADAPTER=0xadA73211711e4790bc83B5d6B39f47fE04D276f3
GUARD=0xaE4D4DdB8dD646951d54fE9B13BE23DcB61C6741; RESERVE=0x806b182B050f7EcF908758dD6bBF91DB8B2212aF
TIMELOCK=0x6797FB8Ce049B42C5BC2b42Bf76c6d15C7B12498; KEEPER=0x82ABD8eE038c10F5bE00E5200C94265400999f7e
PNVDA=0xa155ccCB986774AE818b3F10F07d01D1b7A47b26; PUSDG=0x55aEd0569c8f0D166D71facE57B57C2f2624a563
PSPY=0xa6660499a9cB9B6691247fE1FAbc248f502E5fB3; NVDA=0xd0601CE157Db5bdC3162BbaC2a2C8aF5320D9EEC
USDG=0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168; FEED=0x321A36e441DfBadA01Aa6f9884F14a344E133D1F
EXECUTOR=0x6A45Ae86bD992d250580d08D340A06A04D478977; RISK=0xC8b178C3c74570472FF1eeE0DD559e61AF9f9678
NVDA_LENDER=0x12c1e2C33F63F897E0e4ac1969C29493136f2481; DEAD=0x000000000000000000000000000000000000dEaD
ZERO=0x0000000000000000000000000000000000000000
POOLID=0x3bb34a44f1b2b5f32c034c38a53065a521a47b199700fa9bd19d60985ff24bf1
PROD=$(cast keccak "NVDA/USDG"); OPTIN=$(cast keccak "NVDA/USDG/OPTIN"); SALT=$(cast keccak "peridot-nvda-optin-v1")
D1=$(jq -r .upNvda "$J"); D2=$(jq -r .upUsdg "$J"); AN=$(jq -r .adapterN "$J"); DEPLOYER=$(jq -r .deployer "$J" 2>/dev/null || true)
SEED_N=${SEED_NVDA_WEI:-20000000000000000}; SEED_U=${SEED_USDG_RAW:-4000000}
RES_N=${RESERVE_NVDA_WEI:-30000000000000000}; RES_U=${RESERVE_USDG_RAW:-5000000}

FEEDSIG='(address,address,address,address,address,bytes32,uint64,uint64,uint16,uint8,uint8,uint8,uint8,bool,bool,uint16)'
FEEDCFG="($NVDA,$USDG,$FEED,$ZERO,$ZERO,$POOLID,90000,3600,300,18,6,8,0,true,true,600)"
RESSIG='(address,address,uint128,uint128,uint16,bool,bool)'
RESCFG="($NVDA,$USDG,10000000000000000000,25000000000000000000,5000,false,false)"
PAIRSIG='(address,address,address,address,uint128,uint128,uint64,uint32,uint32,uint16,uint16,uint16,uint8,uint8,bool,bool,bool,bool)'
PAIRCFG="($NVDA,$USDG,$D1,$D2,250000000000000000000,25000000000000000000,3600,0,300,2000,100,200,18,6,false,false,false,false)"
REGSIG='(address,address,(address,address,uint24,int24,address),bytes32,uint16)'
REG="($NVDA,$USDG,($USDG,$NVDA,3000,60,$ZERO),$POOLID,400)"
POLSIG='(bool,uint16,uint16,uint32,uint8,uint16,uint128)'
POLICY="(true,2400,600,3600,4,10,50000000000000000000)"

exec 2> >(grep -v "Found unknown" >&2)
c(){ cast call "$@" --rpc-url "$RPC"; }
first(){ cut -d' ' -f1 | head -1; }
ge(){ python3 -c "import sys;sys.exit(0 if int('$1')>=int('$2') else 1)"; }
s(){ local label=$1; shift; echo "-- $label"; cast send "$@" --from $L $SIGN --rpc-url "$RPC" --rpc-timeout 600 --json \
       | python3 -c "import json,sys;r=json.load(sys.stdin);print('   status',r['status'],'gas',int(r['gasUsed'],16),'tx',r['transactionHash']);sys.exit(0 if r['status']=='0x1' else 1)"; }
need(){ if [ "$1" != "$2" ]; then echo "CHECK FAILED: $3 (got '$1', want '$2')"; exit 1; fi; echo "   ok: $3"; }
listed(){ c $CONTROLLER 'markets(address)(bool,uint256,bool)' "$1" | head -2 | tr '\n' ' ' | cut -d' ' -f1,2; }
plain(){ echo "$1" | sed -E 's/ \[[^]]*\]//g'; }

PAYLOADS=""; TARGETS="[$GUARD,$RESERVE,$VAULT,$VAULT]"; VALUES="[0,0,0,0]"
batch(){
  local p1 p2 p3 p4
  p1=$(cast calldata "configurePair(bytes32,$FEEDSIG)" $OPTIN "$FEEDCFG")
  p2=$(cast calldata "configurePair(bytes32,$RESSIG)" $OPTIN "$RESCFG")
  p3=$(cast calldata "registerPair(bytes32,$PAIRSIG,$REGSIG)" $OPTIN "$PAIRCFG" "$REG")
  p4=$(cast calldata "setRangePolicy(bytes32,$POLSIG)" $OPTIN "$POLICY")
  PAYLOADS="[$p1,$p2,$p3,$p4]"
}
opid(){ c $TIMELOCK 'hashOperationBatch(address[],uint256[],bytes[],bytes32,bytes32)(bytes32)' "$TARGETS" "$VALUES" "$PAYLOADS" 0x$(printf '0%.0s' {1..64}) $SALT; }
qid(){ cast keccak $(cast abi-encode "f$1" "${@:2}"); }  # delegate queued-action id = keccak256(abi.encode(...))

# Values the ceremony must leave bit-identical: NVDA/USDG/SPY lending prices, the live NVDA borrower, every active
# margin position's metrics, the production pair's ledger loss and liquidity.
untouched(){
  local o n i p; o=$(c $CONTROLLER 'oracle()(address)')
  echo "$(c $o 'getUnderlyingPrice(address)(uint256)' $PNVDA | first) $(c $o 'getUnderlyingPrice(address)(uint256)' $PUSDG | first) $(c $o 'getUnderlyingPrice(address)(uint256)' $PSPY | first)" \
       "$(c $CONTROLLER 'getAccountLiquidity(address)(uint256,uint256,uint256)' $NVDA_LENDER | cut -d' ' -f1 | tr '\n' ,)" \
       "$(c $VAULT 'ledger(bytes32)(uint256,uint256,uint256,uint256,uint256,uint64)' $PROD | sed -n 5p | first)" \
       "$(c $ADAPTER 'positionState(bytes32)((uint256,uint128,uint256,uint256))' $PROD | tr -d '()' | cut -d, -f1,2)"
  n=$(c $EXECUTOR 'nextPositionId()(uint256)' | first)
  for i in $(seq 1 $((n - 1))); do
    p=$(c $EXECUTOR 'positions(uint256)((uint256,address,address,address,address,address,uint256,uint256,uint256,uint16,uint8,uint8))' $i | tr -d '()')
    if [ "$(echo "$p" | awk -F', ' '{print $12}')" = 2 ]; then
      echo "pos$i $(cast call $RISK 'getMetrics(address)((uint256,uint256,int256,uint256,uint256,uint256,uint256))' $(echo "$p" | awk -F', ' '{print $3}') --rpc-url "$RPC" 2>/dev/null || echo revert)"
    fi
  done
}
feed_fresh(){
  local upd now; upd=$(c $FEED 'latestRoundData()(uint80,int256,uint256,uint256,uint80)' | sed -n 4p | first)
  now=$(cast block latest --field timestamp --rpc-url "$RPC"); echo "   NVDA 24/7 answer age $((now - upd)) s"
  need "$([ $((now - upd)) -le 3600 ] && echo fresh)" fresh "NVDA answer at most 1 h old (run in session)"
  c $GUARD 'validatePoolPrice(bytes32,(address,address,uint24,int24,address))(uint256,uint256,uint160)' $PROD "($USDG,$NVDA,3000,60,$ZERO)" >/dev/null \
    && echo "   ok: pool within the 300 bp allocation bound of the oracle"
}
forge_verify(){
  ( cd "$ROOT" && FOUNDRY_PROFILE=nvda_optin NVDA_OPTIN_ACTION=verify NVDA_OPTIN_JOURNAL=$(basename "$J" .json) \
    forge script remediation/script/DeployNvdaOptIn.s.sol:DeployNvdaOptIn --rpc-url "$RPC" 2>&1 | grep -q NVDA_OPTIN_VERIFY_OK ) \
    && echo "   ok: deployment verify (markets, admin, implementation, adapterN markets and prices)" \
    || { echo "CHECK FAILED: deployment verify"; exit 1; }
}

case $PHASE in
fund)
  need "$([ -n "$DEPLOYER" ] && [ "$DEPLOYER" != null ] && [ "$DEPLOYER" != $L ] && [ "$DEPLOYER" != $OLD ] && echo ok)" ok "throwaway deployer address set in the journal stub"
  s "1 fund the throwaway deployer" $DEPLOYER --value ${DEPLOYER_FUND_WEI:-500000000000000}
  ;;
preflight)
  need "$(cast chain-id --rpc-url "$RPC")" 4663 "chain 4663"
  need "$(c $CONTROLLER 'admin()(address)')" $L "controller admin = Ledger"
  need "$(c $CONTROLLER 'pauseGuardian()(address)')" $L "pause guardian = Ledger"
  need "$(c $CONTROLLER 'oracle()(address)')" $ADAPTER3 "controller oracle = adapter3 (SPY margin ceremony done)"
  need "$(listed $PSPY | cut -d' ' -f1)" true "pSPY listed (SPY margin ceremony done)"
  need "$(c $TIMELOCK 'hasRole(bytes32,address)(bool)' $(cast keccak PROPOSER_ROLE) $L)/$(c $TIMELOCK 'hasRole(bytes32,address)(bool)' $(cast keccak EXECUTOR_ROLE) $L)" true/true "Ledger is timelock proposer and executor"
  need "$(c $VAULT 'hasRole(bytes32,address)(bool)' $(cast keccak CONFIG_ROLE) $TIMELOCK)/$(c $GUARD 'hasRole(bytes32,address)(bool)' $(cast keccak CONFIG_ROLE) $TIMELOCK)/$(c $RESERVE 'hasRole(bytes32,address)(bool)' $(cast keccak CONFIG_ROLE) $TIMELOCK)" true/true/true "timelock holds CONFIG_ROLE on vault, guard and reserve"
  need "$(c $VAULT 'hasRole(bytes32,address)(bool)' $(cast keccak GUARDIAN_ROLE) $L)" true "Ledger is vault guardian (rollback)"
  need "$(c $VAULT 'hasRole(bytes32,address)(bool)' $(cast keccak KEEPER_ROLE) $KEEPER)" true "range keeper holds KEEPER_ROLE"
  need "$(c $RESERVE 'hasRole(bytes32,address)(bool)' $(cast keccak VAULT_ROLE) $VAULT)" true "reserve grants VAULT_ROLE to the vault"
  need "$(plain "$(c $GUARD "feedConfig(bytes32)($FEEDSIG)" $PROD)")" "$(echo "$FEEDCFG" | sed 's/,/, /g')" "production guard config equals the one copied to OPTIN"
  need "$(c $VAULT 'pairConfig(bytes32)((address,address,address,address,uint128,uint128,uint64,uint32,uint32,uint16,uint16,uint16,uint8,uint8,bool,bool,bool,bool))' $OPTIN | tr -d '()' | awk -F', ' '{print $18}')" false "OPTIN not registered yet"
  need "$(plain "$(c $VAULT 'rangePolicy(bytes32)(bool,uint16,uint16,uint32,uint8,uint16,uint128)' $PROD | tr '\n' ',' | sed 's/,$//')")" "true,2400,600,3600,4,10,50000000000000000000" "production range policy equals the one copied to OPTIN"
  for m in $D1 $D2; do
    need "$(c $m 'admin()(address)')" $L "$m admin = Ledger"
    need "$(listed $m | cut -d' ' -f1)" false "$m not listed yet"
    need "$(c $m 'totalSupply()(uint256)' | first)" 0 "$m empty"
  done
  need "$(c $PNVDA 'admin()(address)')/$(c $PUSDG 'admin()(address)')" $L/$L "Ledger administers pNVDA and pUSDG (O1-O4)"
  need "$(c $PNVDA 'robinhoodPairId()(bytes32)')/$(c $PUSDG 'robinhoodPairId()(bytes32)')" $PROD/$PROD "plain markets still point at the production pair"
  need "$(plain "$(c $VAULT 'pairConfig(bytes32)((address,address,address,address,uint128,uint128,uint64,uint32,uint32,uint16,uint16,uint16,uint8,uint8,bool,bool,bool,bool))' $PROD | tr -d '()' | awk -F', ' '{print $15,$16,$17}')")" "true true false" "production pair flags 110"
  need "$(c $ADAPTER 'positionState(bytes32)((uint256,uint128,uint256,uint256))' $PROD | tr -d '()' | awk -F', ' '{print $2}')" 0 "production pair liquidity 0 (O3/O4 use the oracle-free idle path)"
  need "$(ge $(c $NVDA 'balanceOf(address)(uint256)' $L | first) $((SEED_N + RES_N)) && echo yes)" yes "Ledger holds NVDA seed + reserve"
  need "$(ge $(c $USDG 'balanceOf(address)(uint256)' $L | first) $((SEED_U + RES_U)) && echo yes)" yes "Ledger holds USDG seed + reserve"
  feed_fresh
  forge_verify
  untouched > "${J%.json}.untouched-before.txt"; echo "   untouched-state snapshot saved ($(wc -l < "${J%.json}.untouched-before.txt") lines)"
  ;;
t0)
  batch
  s "2 timelock: schedule the OPTIN batch (guard, reserve, registerPair, range policy; 1 h)" $TIMELOCK \
    'scheduleBatch(address[],uint256[],bytes[],bytes32,bytes32,uint256)' "$TARGETS" "$VALUES" "$PAYLOADS" 0x$(printf '0%.0s' {1..64}) $SALT 3600
  need "$(c $TIMELOCK 'isOperationPending(bytes32)(bool)' $(opid))" true "batch pending"
  BEFORE=$(untouched)
  s "3 [LIVE-NVDA] controller price oracle -> 5-market adapter" $CONTROLLER '_setPriceOracle(address)' $AN
  need "$(c $CONTROLLER 'oracle()(address)')" $AN "controller oracle switched (Compound error codes do not revert)"
  need "$(untouched)" "$BEFORE" "[LIVE-NVDA] pNVDA/pUSDG/pSPY prices, NVDA borrower, margin positions, production pair unchanged"
  s "4 list upNVDA" $CONTROLLER '_supportMarket(address)' $D1
  s "5 list upUSDG" $CONTROLLER '_supportMarket(address)' $D2
  need "$(listed $D1)/$(listed $D2)" "true 0/true 0" "both listed at collateral factor 0"
  s "6 pause upNVDA borrowing" $CONTROLLER '_setBorrowPaused(address,bool)' $D1 true
  s "7 pause upUSDG borrowing" $CONTROLLER '_setBorrowPaused(address,bool)' $D2 true
  need "$(c $CONTROLLER 'borrowGuardianPaused(address)(bool)' $D1)/$(c $CONTROLLER 'borrowGuardianPaused(address)(bool)' $D2)" true/true "borrowing paused"
  s "8 pause upNVDA mint (until seeded)" $CONTROLLER '_setMintPaused(address,bool)' $D1 true
  s "9 pause upUSDG mint (until seeded)" $CONTROLLER '_setMintPaused(address,bool)' $D2 true
  need "$(c $CONTROLLER 'mintGuardianPaused(address)(bool)' $D1)/$(c $CONTROLLER 'mintGuardianPaused(address)(bool)' $D2)" true/true "mint paused"
  need "$(c $D1 'totalSupply()(uint256)' | first)/$(c $D2 'totalSupply()(uint256)' | first)/$(c $D1 'totalBorrows()(uint256)' | first)/$(c $D2 'totalBorrows()(uint256)' | first)" 0/0/0/0 "nothing minted or borrowed in the listing window"
  s "10 upNVDA: queue vault config (OPTIN, buffer 0.5, operator Ledger)" $D1 'queueSetVaultConfig(address,bytes32,uint256,address)' $VAULT $OPTIN 500000000000000000 $L
  s "11 upUSDG: queue vault config" $D2 'queueSetVaultConfig(address,bytes32,uint256,address)' $VAULT $OPTIN 500000000000000000 $L
  s "12 upNVDA: queue vault unpause" $D1 'queueSetVaultPaused(bool)' false
  s "13 upUSDG: queue vault unpause" $D2 'queueSetVaultPaused(bool)' false
  s "O1 pNVDA: queue vault pause (plain by configuration)" $PNVDA 'queueSetVaultPaused(bool)' true
  s "O2 pUSDG: queue vault pause (plain by configuration)" $PUSDG 'queueSetVaultPaused(bool)' true
  ;;
gate)
  batch
  NOW=$(cast block latest --field timestamp --rpc-url "$RPC")
  need "$(c $TIMELOCK 'isOperationReady(bytes32)(bool)' $(opid))" true "timelock batch ready"
  CFG=$(qid '(string,address,bytes32,uint256,address)' setVaultConfig $VAULT $OPTIN 500000000000000000 $L)
  UNP=$(qid '(string,bool)' setVaultPaused false); PAU=$(qid '(string,bool)' setVaultPaused true)
  for pair in "$D1:$CFG" "$D2:$CFG" "$D1:$UNP" "$D2:$UNP" "$PNVDA:$PAU" "$PUSDG:$PAU"; do
    m=${pair%%:*}; id=${pair##*:}; eta=$(c $m 'queuedActions(bytes32)(uint256)' $id | first)
    need "$([ "$eta" -gt 0 ] && [ $NOW -ge "$eta" ] && echo ready)" ready "queued action ready on $m"
  done
  need "$(c $D1 'totalSupply()(uint256)' | first)/$(c $D2 'totalSupply()(uint256)' | first)" 0/0 "seed front-run gate: both markets still empty"
  need "$(c $D1 'totalBorrows()(uint256)' | first)/$(c $D2 'totalBorrows()(uint256)' | first)" 0/0 "no borrows on the new markets"
  feed_fresh
  ;;
t1)
  batch
  s "14 timelock: execute the OPTIN batch" $TIMELOCK 'executeBatch(address[],uint256[],bytes[],bytes32,bytes32)' \
    "$TARGETS" "$VALUES" "$PAYLOADS" 0x$(printf '0%.0s' {1..64}) $SALT --gas-limit 3000000
  need "$(c $TIMELOCK 'isOperationDone(bytes32)(bool)' $(opid))" true "batch executed"
  need "$(c $VAULT 'pairConfig(bytes32)((address,address,address,address,uint128,uint128,uint64,uint32,uint32,uint16,uint16,uint16,uint8,uint8,bool,bool,bool,bool))' $OPTIN | tr -d '()' | awk -F', ' '{print $3,$4,$15,$16,$17,$18}')" "$D1 $D2 false false false true" "OPTIN registered: accounts, flags 000"
  s "15 upNVDA: apply vault config" $D1 '_setVaultConfig(address,bytes32,uint256,address)' $VAULT $OPTIN 500000000000000000 $L
  s "16 upUSDG: apply vault config" $D2 '_setVaultConfig(address,bytes32,uint256,address)' $VAULT $OPTIN 500000000000000000 $L
  s "17 upNVDA: vault unpause" $D1 '_setVaultPaused(bool)' false --gas-limit 3000000
  s "18 upUSDG: vault unpause" $D2 '_setVaultPaused(bool)' false --gas-limit 3000000
  need "$(c $D1 'robinhoodPairId()(bytes32)')/$(c $D1 'vaultPaused()(bool)')/$(c $D2 'robinhoodPairId()(bytes32)')/$(c $D2 'vaultPaused()(bool)')" "$OPTIN/false/$OPTIN/false" "both markets route to OPTIN"
  s "19 approve NVDA for the reserve" $NVDA 'approve(address,uint256)' $RESERVE $RES_N
  s "20 fund the OPTIN reserve with NVDA" $RESERVE 'deposit(bytes32,address,uint256)' $OPTIN $NVDA $RES_N
  s "21 approve USDG for the reserve" $USDG 'approve(address,uint256)' $RESERVE $RES_U
  s "22 fund the OPTIN reserve with USDG" $RESERVE 'deposit(bytes32,address,uint256)' $OPTIN $USDG $RES_U
  need "$(c $RESERVE 'available(bytes32,address)(uint256)' $OPTIN $NVDA | first)/$(c $RESERVE 'available(bytes32,address)(uint256)' $OPTIN $USDG | first)" "$RES_N/$RES_U" "reserve funded"
  s "23 approve the NVDA seed" $NVDA 'approve(address,uint256)' $D1 $SEED_N
  s "24 approve the USDG seed" $USDG 'approve(address,uint256)' $D2 $SEED_U
  s "25a unpause upNVDA mint" $CONTROLLER '_setMintPaused(address,bool)' $D1 false
  need "$(c $D1 'totalSupply()(uint256)' | first)" 0 "upNVDA still empty right before the seed (else: re-pause and stop)"
  s "25b seed upNVDA" $D1 'mint(uint256)' $SEED_N --gas-limit 3000000
  s "26a unpause upUSDG mint" $CONTROLLER '_setMintPaused(address,bool)' $D2 false
  need "$(c $D2 'totalSupply()(uint256)' | first)" 0 "upUSDG still empty right before the seed (else: re-pause and stop)"
  s "26b seed upUSDG" $D2 'mint(uint256)' $SEED_U --gas-limit 3000000
  LG=$(c $VAULT 'ledger(bytes32)(uint256,uint256,uint256,uint256,uint256,uint64)' $OPTIN)
  need "$(echo "$LG" | sed -n 1p | first) $(echo "$LG" | sed -n 2p | first)" "$((SEED_N / 2)) $((SEED_U / 2))" "half of each seed is in the vault (buffer 0.5; no VaultDepositFailed)"
  s "27 send the upNVDA seed shares to the dead address" $D1 'transfer(address,uint256)' $DEAD $(c $D1 'balanceOf(address)(uint256)' $L | first)
  s "28 send the upUSDG seed shares to the dead address" $D2 'transfer(address,uint256)' $DEAD $(c $D2 'balanceOf(address)(uint256)' $L | first)
  need "$(c $D1 'balanceOf(address)(uint256)' $DEAD | first)/$(c $D2 'balanceOf(address)(uint256)' $DEAD | first)" "$(c $D1 'totalSupply()(uint256)' | first)/$(c $D2 'totalSupply()(uint256)' | first)" "all seed shares at the dead address"
  BEFORE=$(untouched)
  s "O3 [LIVE-NVDA] pNVDA: vault pause, dust home" $PNVDA '_setVaultPaused(bool)' true --gas-limit 3000000
  s "O4 [LIVE-NVDA] pUSDG: vault pause, dust home" $PUSDG '_setVaultPaused(bool)' true --gas-limit 3000000
  need "$(c $PNVDA 'vaultPaused()(bool)')/$(c $PUSDG 'vaultPaused()(bool)')/$(c $PNVDA 'vaultAccountedAssets()(uint256)' | first)/$(c $PUSDG 'vaultAccountedAssets()(uint256)' | first)" true/true/0/0 "plain markets: vault paused, claims 0"
  need "$(untouched)" "$BEFORE" "[LIVE-NVDA] prices, NVDA borrower and margin positions unchanged by O3/O4"
  echo "   next (keeper, not Ledger): checkpoint + rebalance OPTIN once both sides have deposits"
  ;;
verify)
  need "$(c $CONTROLLER 'oracle()(address)')" $AN "controller oracle = 5-market adapter"
  need "$(listed $D1)/$(listed $D2)" "true 0/true 0" "upNVDA / upUSDG listed at CF 0"
  need "$(c $CONTROLLER 'borrowGuardianPaused(address)(bool)' $D1)/$(c $CONTROLLER 'borrowGuardianPaused(address)(bool)' $D2)" true/true "borrowing paused"
  need "$(c $VAULT 'pairConfig(bytes32)((address,address,address,address,uint128,uint128,uint64,uint32,uint32,uint16,uint16,uint16,uint8,uint8,bool,bool,bool,bool))' $OPTIN | tr -d '()' | awk -F', ' '{print $3,$4,$18}')" "$D1 $D2 true" "OPTIN registered to upNVDA / upUSDG"
  need "$(c $ADAPTER 'removalTolerance(bytes32)(uint16)' $OPTIN)" 400 "adapter removal tolerance 400"
  need "$(c $PNVDA 'vaultPaused()(bool)')/$(c $PUSDG 'vaultPaused()(bool)')" true/true "pNVDA / pUSDG plain by configuration"
  forge_verify
  echo "   OPTIN ledger (stockPrincipal usdgPrincipal stockIdle usdgIdle lossUSD18 lastCheckpoint): $(c $VAULT 'ledger(bytes32)(uint256,uint256,uint256,uint256,uint256,uint64)' $OPTIN | cut -d' ' -f1 | tr '\n' ' ')"
  echo "   OPTIN position: $(c $ADAPTER 'positionState(bytes32)((uint256,uint128,uint256,uint256))' $OPTIN)"
  echo "   reserve OPTIN: $(c $RESERVE 'available(bytes32,address)(uint256)' $OPTIN $NVDA | first) NVDA wei, $(c $RESERVE 'available(bytes32,address)(uint256)' $OPTIN $USDG | first) USDG raw"
  ;;
rollback)
  # Instant, Ledger only. Stops new exposure; exits keep working. Unpausing anything again needs the timelock (1 h).
  s "R1 vault guardian: pause OPTIN allocation and swaps" $VAULT 'setPairPause(bytes32,bool,bool,bool)' $OPTIN true true false
  s "R2 pause upNVDA mint" $CONTROLLER '_setMintPaused(address,bool)' $D1 true
  s "R3 pause upUSDG mint" $CONTROLLER '_setMintPaused(address,bool)' $D2 true
  s "R4 upNVDA: queue vault pause (pulls the claim home after 1 h)" $D1 'queueSetVaultPaused(bool)' true
  s "R5 upUSDG: queue vault pause" $D2 'queueSetVaultPaused(bool)' true
  echo "   controller oracle stays the 5-market adapter (a superset; never switch back while an account has entered upNVDA/upUSDG)"
  ;;
rollback-t1)
  s "R6 upNVDA: vault pause" $D1 '_setVaultPaused(bool)' true --gas-limit 3000000
  s "R7 upUSDG: vault pause" $D2 '_setVaultPaused(bool)' true --gas-limit 3000000
  ;;
*) echo "unknown phase $PHASE"; exit 2;;
esac
