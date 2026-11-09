#!/usr/bin/env bash
# Switch the NVDA price source of the margin oracle and the pNVDA lending oracle from the 24/5 RHNVDA feed to Chainlink
# "Robinhood NVDA (24/7)". PREPARED, NOT EXECUTED. 3 Ledger signatures: a prerequisite, then a timelock pair.
#
#   bash switch_nvda_feed.sh state       read-only: both feeds, the guard config, the lending feed, every price path
#   bash switch_nvda_feed.sh preflight   read-only: every condition; prints GO or the failing one
#   bash switch_nvda_feed.sh prereq      Ledger 1 (direct): lending oracle setAdmin(timelock), so the batch is atomic
#   bash switch_nvda_feed.sh schedule    Ledger 2: timelock.scheduleBatch(guard.configurePair(24/7), lending.registerChainlinkFeed(24/7))
#   bash switch_nvda_feed.sh execute     Ledger 3: at least 3,600 s later
#   bash switch_nvda_feed.sh verify      read-only: the read-backs
#   bash switch_nvda_feed.sh cancel      before execute
#
# MAX_STALENESS (seconds) is the guard's bound after the switch. Operator decision 2026-11-09: 90000, the 24/7 feed's
# 86,400 s heartbeat plus one hour, with a separate alarm on the feed's age (keeper/alarm.py FEED_247_LATE). 43200 (the
# old 12 h policy) is still accepted for a deliberate rollback. Scheduled in the 15 to 17 Nov ceremony.
# Optional: LEDGER_HD, RH_RPC, REHEARSE=1 (local fork, accounts unlocked).
set -uo pipefail

R=${RH_RPC:-https://rpc.mainnet.chain.robinhood.com}
L=0x064eFE76812F5f18Bd1aA9708739Fb33C810B2dD
T=0x6797FB8Ce049B42C5BC2b42Bf76c6d15C7B12498
GUARD=0xaE4D4DdB8dD646951d54fE9B13BE23DcB61C6741
LENDING=0x266F014d1325774F1190f963Df4369E07dDA1d33
LENDING_ADAPTER=0xe4e03C2FdaeF915ACe705D106b2660B1E342A2E4
MARGIN_ORACLE=0x63150Eb3DDf71420dA0b09b66838aab398f7dEdD
GUARDED_SOURCE=0x25E02b142E0785a59D85a6CA820c400e601a9E8E
PNVDA=0xa155ccCB986774AE818b3F10F07d01D1b7A47b26
NVDA=0xd0601CE157Db5bdC3162BbaC2a2C8aF5320D9EEC
F5=0x379EC4f7C378F34a1B47E4F3cbeBCbAC3E8E9F15
F7=0x321A36e441DfBadA01Aa6f9884F14a344E133D1F
PAIR=0xe2050352f4346597cc69d2776d99ae60c9440dfc28f9406ce66be0bbe3fb6b06
MAX_STALENESS=${MAX_STALENESS:-90000}
SALT=$(cast keccak "peridot-nvda-247-feed-switch-v1-$MAX_STALENESS")
ZERO32=0x0000000000000000000000000000000000000000000000000000000000000000
CFG_T='(address,address,address,address,address,bytes32,uint64,uint64,uint16,uint8,uint8,uint8,uint8,bool,bool,uint16)'

die() { echo "STOPPED: $*" >&2; exit 1; }
lc() { echo "$1" | tr '[:upper:]' '[:lower:]'; }  # bash 3.2 (macOS) has no ${var,,}
call() { cast call "$@" --rpc-url "$R" 2>/dev/null; }
send() {
  local auth=(--ledger)
  [[ -n "${LEDGER_HD:-}" ]] && auth+=(--mnemonic-derivation-path "$LEDGER_HD")
  [[ "${REHEARSE:-}" == 1 ]] && auth=(--unlocked)
  cast send "$@" --from "$L" "${auth[@]}" --rpc-url "$R" --json | python3 -c '
import json,sys; r=json.loads(sys.stdin.read()); print("   tx", r["transactionHash"], "status", r["status"])
sys.exit(0 if r["status"]=="0x1" else 1)' || die "transaction failed or reverted; run state"
}
# The new guard config: today's, with only the stock feed (and, if chosen, the staleness bound) changed.
new_cfg() {
  call $GUARD "feedConfig(bytes32)($CFG_T)" $PAIR | python3 -c '
import re,sys
f=[x.strip() for x in re.sub(r"\s*\[[^]]*\]","",sys.stdin.read()).strip("()\n").split(",")]
f[2]=sys.argv[1]; f[6]=sys.argv[2]
print("("+",".join(f)+")")' $F7 $MAX_STALENESS
}
payloads() {
  P1=$(cast calldata "configurePair(bytes32,$CFG_T)" $PAIR "$(new_cfg)")
  P2=$(cast calldata 'registerChainlinkFeed(address,address)' $NVDA $F7)
}
op_id() { payloads; call $T 'hashOperationBatch(address[],uint256[],bytes[],bytes32,bytes32)(bytes32)' "[$GUARD,$LENDING]" "[0,0]" "[$P1,$P2]" $ZERO32 $SALT; }
age() { echo $(( $(cast block latest --field timestamp --rpc-url "$R") - $(call $1 'latestRoundData()(uint80,int256,uint256,uint256,uint80)' | sed -n 4p | cut -d' ' -f1) )); }
answer() { call $1 'latestRoundData()(uint80,int256,uint256,uint256,uint80)' | sed -n 2p | cut -d' ' -f1; }

state() {
  echo "24/5 $F5: answer $(answer $F5) age $(age $F5) s"
  echo "24/7 $F7: answer $(answer $F7) age $(age $F7) s, description $(call $F7 'description()(string)')"
  echo "guard NVDA feed $(call $GUARD "feedConfig(bytes32)($CFG_T)" $PAIR | python3 -c 'import sys;f=sys.stdin.read().strip("()\n").split(",");print(f[2].strip(),"maxStaleness",f[6].split()[0])')"
  echo "lending NVDA feed $(call $LENDING 'assetToAggregator(address)(address)' $NVDA); timelock lending admin $(call $LENDING 'admin(address)(bool)' $T)"
  echo "margin oracle NVDA $(call $MARGIN_ORACLE 'getPrice(address)(uint256)' $NVDA | cut -d' ' -f1); guarded source $(call $GUARDED_SOURCE 'assetPrices(address)(uint256)' $NVDA | cut -d' ' -f1)"
  echo "lending assetPrices(NVDA) $(call $LENDING 'assetPrices(address)(uint256)' $NVDA | cut -d' ' -f1); pNVDA controller price $(call $LENDING_ADAPTER 'getUnderlyingPrice(address)(uint256)' $PNVDA | cut -d' ' -f1)"
  echo "token paused $(call $NVDA 'paused()(bool)') oraclePaused $(call $NVDA 'oraclePaused()(bool)')"
}

conditions() {
  [[ "$(call $F7 'description()(string)')" == '"Robinhood NVDA (24/7)"' ]] || { echo "NO-GO: 24/7 feed description"; return 1; }
  [[ "$(call $F7 'decimals()(uint8)')" == 8 ]] || { echo "NO-GO: 24/7 decimals"; return 1; }
  (( $(answer $F7) > 0 )) || { echo "NO-GO: 24/7 answer"; return 1; }
  (( $(age $F7) <= 90000 )) || { echo "NO-GO: 24/7 answer older than its heartbeat plus 1 h"; return 1; }
  [[ "$(call $NVDA 'paused()(bool)')" == false && "$(call $NVDA 'oraclePaused()(bool)')" == false ]] || { echo "NO-GO: token paused"; return 1; }
  local cur; cur=$(call $GUARD "feedConfig(bytes32)($CFG_T)" $PAIR | python3 -c 'import sys;print(sys.stdin.read().strip("()\n").split(",")[2].strip())')
  [[ "$(lc $cur)" == "$(lc $F5)" ]] || { echo "NO-GO: guard does not read the 24/5 feed today ($cur)"; return 1; }
  [[ "$(call $LENDING 'assetToAggregator(address)(address)' $NVDA)" == "$F5" ]] || { echo "NO-GO: lending does not read the 24/5 feed today"; return 1; }
  [[ "$(call $GUARD 'hasRole(bytes32,address)(bool)' $(cast keccak CONFIG_ROLE) $T)" == true ]] || { echo "NO-GO: timelock lacks CONFIG_ROLE on the guard"; return 1; }
  [[ "$MAX_STALENESS" == 43200 || "$MAX_STALENESS" == 90000 ]] || { echo "NO-GO: MAX_STALENESS must be 43200 or 90000"; return 1; }
  return 0
}

[[ "$(cast chain-id --rpc-url "$R")" == 4663 ]] || die "not Robinhood Chain"
case "${1:-}" in
  state) state ;;
  preflight)
    state; conditions || exit 1
    echo "timelock lending admin: $(call $LENDING 'admin(address)(bool)' $T) (prereq needed if false)"
    echo "operation $(op_id) (MAX_STALENESS $MAX_STALENESS) done=$(call $T 'isOperationDone(bytes32)(bool)' "$(op_id)")"
    echo "GO" ;;
  prereq)
    [[ "$(call $LENDING 'admin(address)(bool)' $T)" == true ]] && { echo "already done"; exit 0; }
    send $LENDING 'setAdmin(address)' $T
    [[ "$(call $LENDING 'admin(address)(bool)' $T)" == true ]] || die "timelock is not lending admin after prereq" ;;
  schedule)
    conditions || die "conditions not met"
    [[ "$(call $LENDING 'admin(address)(bool)' $T)" == true ]] || die "run prereq first (otherwise the batch reverts at execute)"
    payloads; send $T 'scheduleBatch(address[],uint256[],bytes[],bytes32,bytes32,uint256)' "[$GUARD,$LENDING]" "[0,0]" "[$P1,$P2]" $ZERO32 $SALT 3600
    echo "operation $(op_id) pending=$(call $T 'isOperationPending(bytes32)(bool)' "$(op_id)")" ;;
  execute)
    conditions || die "conditions not met"
    [[ "$(call $T 'isOperationReady(bytes32)(bool)' "$(op_id)")" == true ]] || die "operation not ready"
    payloads; send $T 'executeBatch(address[],uint256[],bytes[],bytes32,bytes32)' "[$GUARD,$LENDING]" "[0,0]" "[$P1,$P2]" $ZERO32 $SALT
    bash "$0" verify ;;
  verify)
    cfg=$(call $GUARD "feedConfig(bytes32)($CFG_T)" $PAIR | python3 -c 'import sys;f=sys.stdin.read().strip("()\n").split(",");print(f[2].strip(), f[6].split()[0], f[8].split()[0], f[15].split()[0])')
    read -r feed stale dev rem <<< "$cfg"
    [[ "$(lc $feed)" == "$(lc $F7)" && "$stale" == "$MAX_STALENESS" && "$dev" == 300 && "$rem" == 600 ]] || die "guard config: $cfg"
    [[ "$(call $LENDING 'assetToAggregator(address)(address)' $NVDA)" == "$F7" ]] || die "lending feed"
    g=$(age $F7)
    m=$(call $MARGIN_ORACLE 'getPrice(address)(uint256)' $NVDA | cut -d' ' -f1)
    # 1e18-scaled prices exceed bash's 64-bit integers: compute in python
    want=$(python3 -c "import sys;a,g,b=map(int,sys.argv[1:]);print(a*10**10 if g<=b else 0)" $(answer $F7) $g $MAX_STALENESS)
    [[ "$m" == "$want" ]] || die "margin oracle $m, expected $want (24/7 age $g s, bound $MAX_STALENESS)"
    [[ "$(call $GUARDED_SOURCE 'assetPrices(address)(uint256)' $NVDA | cut -d' ' -f1)" == "$m" ]] || die "guarded source"
    lp=$(call $LENDING_ADAPTER 'getUnderlyingPrice(address)(uint256)' $PNVDA | cut -d' ' -f1)
    python3 -c "import sys;sys.exit(0 if int(sys.argv[1] or 0)>0 else 1)" "$lp" || die "pNVDA lending price 0"  # 1e20-scale: not bash arithmetic
    echo "verified: guard and lending read the 24/7 feed; margin oracle $m (24/7 age $g s, bound $MAX_STALENESS); pNVDA priced" ;;
  cancel) send $T 'cancel(bytes32)' "$(op_id)" ;;
  *) sed -n 2,16p "$0"; exit 2 ;;
esac
