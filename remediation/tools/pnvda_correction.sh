#!/usr/bin/env bash
# pNVDA backing correction after the 2026-11-09 recovery (operator option (a), 2026-11-11). Every signature from the Ledger.
#
# Why: the recovery's settlement swap left the NVDA side 0.00055 NVDA short in kind, backed by 0.096 USDG idle in the vault
# (remediation/evidence/pnvda-gap-2026-11-09.md). A pNVDA sync converts that USDG back to NVDA, but only while settlement
# swaps are open. NEVER run pNVDA.syncVault at flags 110: it spends the NVDA loss reserve and leaves the gap partly open.
#
#   bash pnvda_correction.sh preflight   read-only: roles, flags 110, feed age, pNVDA gap, operation id
#   bash pnvda_correction.sh schedule    Ledger 1: timelock.schedule(vault.setPairPause(pair,true,false,false)) (own salt)
#   bash pnvda_correction.sh execute     Ledger 2, >= 3,600 s later, US session: swaps open (flags 100, allocation paused)
#   bash pnvda_correction.sh gonogo      read-only eth_call of the pNVDA sync from the Ledger; GO or NO-GO
#   bash pnvda_correction.sh sync        Ledger 3: pNVDA.syncVault(max), gas 3,000,000; refuses on NO-GO
#   bash pnvda_correction.sh close       Ledger 4: vault.setPairPause(pair,true,true,false) as guardian (flags 110)
#   bash pnvda_correction.sh checkpoint  Ledger 5: vault.checkpoint(pair, deadline) as keeper: assigns the leftover dust
#   bash pnvda_correction.sh state       read-only: flags, principals, idle, pNVDA claims vs backing, reserve
#   bash pnvda_correction.sh cancel      before execute: timelock.cancel(id)
#
# Run execute, gonogo, sync and close back to back (between execute and close settlement swaps are open).
# Optional: LEDGER_HD, RH_RPC, REHEARSE=1 (local fork only: Ledger unlocked, session/feed-age gates skipped).
set -uo pipefail
# 0.0001 NVDA (~$0.018): the most realised loss the go/no-go accepts. Expected: 0.00055037 - 0.096123/P NVDA, i.e. 0 at
# P <= $174.65 and ~1.8e13 raw at $180; this bound is only crossed if NVDA is above ~$213 at the sync.
MAX_GO_LOSS=100000000000000

R=${RH_RPC:-https://rpc.mainnet.chain.robinhood.com}
L=0x064eFE76812F5f18Bd1aA9708739Fb33C810B2dD
V=0x280825b2d856706Ff7E0d6351CcB2e935E1a9A2f
A=0xadA73211711e4790bc83B5d6B39f47fE04D276f3
T=0x6797FB8Ce049B42C5BC2b42Bf76c6d15C7B12498
PN=0xa155ccCB986774AE818b3F10F07d01D1b7A47b26
PU=0x55aEd0569c8f0D166D71facE57B57C2f2624a563
NVDA=0xd0601CE157Db5bdC3162BbaC2a2C8aF5320D9EEC
GUARD=0xaE4D4DdB8dD646951d54fE9B13BE23DcB61C6741
RES=0x806b182B050f7EcF908758dD6bBF91DB8B2212aF
PAIR=0xe2050352f4346597cc69d2776d99ae60c9440dfc28f9406ce66be0bbe3fb6b06
SALT=$(cast keccak "peridot-pnvda-correction-2026-11-1")
ZERO32=0x0000000000000000000000000000000000000000000000000000000000000000
MAX=115792089237316195423570985008687907853269984665640564039457584007913129639935
SYNC_GAS=3000000

die() { echo "STOPPED: $*" >&2; exit 1; }
call() { cast call "$@" --rpc-url "$R" 2>/dev/null; }
ledger_send() {
  local auth=(--ledger)
  [[ -n "${LEDGER_HD:-}" ]] && auth+=(--mnemonic-derivation-path "$LEDGER_HD")
  [[ "${REHEARSE:-}" == 1 ]] && auth=(--unlocked)
  cast send "$@" --from "$L" "${auth[@]}" --rpc-url "$R" --json | python3 -c '
import json,sys; r=json.loads(sys.stdin.read()); print("   tx", r["transactionHash"], "status", r["status"], "gas", int(r["gasUsed"],16))
sys.exit(0 if r["status"]=="0x1" else 1)' || die "transaction failed or reverted; run state before anything else"
}
pause_data() { cast calldata 'setPairPause(bytes32,bool,bool,bool)' "$PAIR" "$1" "$2" "$3"; }
op_id() { call "$T" 'hashOperation(address,uint256,bytes,bytes32,bytes32)(bytes32)' "$V" 0 "$(pause_data true false false)" "$ZERO32" "$SALT"; }

# Prints: flags stockPrincipal usdgPrincipal stockIdle usdgIdle liquidity cumulativeLoss feedAge gap
snapshot() {
  python3 - "$R" "$V" "$A" "$PAIR" "$GUARD" "$PN" "$NVDA" <<'E'
import subprocess, sys
R, V, A, P, G, PN, NVDA = sys.argv[1:]
def c(*a): return subprocess.run(['cast', 'call', *a, '--rpc-url', R], capture_output=True, text=True, check=True).stdout.strip()
def words(h): h = h[2:]; return [int(h[i:i+64], 16) for i in range(0, len(h), 64)]
def n(*a): return int(c(*a).split()[0])
cfg = words(c(V, '0xf1a8151c' + P[2:]))
led = words(c(V, '0x15977d45' + P[2:]))
liq = c(A, 'positionState(bytes32)((uint256,uint128,uint256,uint256))', P).strip('()').split(',')[1].split()[0]
fc = c(G, 'feedConfig(bytes32)((address,address,address,address,address,bytes32,uint64,uint64,uint16,uint8,uint8,uint8,uint8,bool,bool,uint16))', P)
feed = fc.strip('()').split(',')[2].strip()
upd = words(c(feed, '0xfeaf968c'))[3]
now = int(subprocess.run(['cast', 'block', 'latest', '--field', 'timestamp', '--rpc-url', R], capture_output=True, text=True, check=True).stdout)
claims = n(PN, 'totalSupply()(uint256)') * n(PN, 'exchangeRateStored()(uint256)') // 10**18
backing = n(NVDA, 'balanceOf(address)(uint256)', PN) + n(PN, 'vaultWithdrawableAssets()(uint256)') + n(PN, 'totalBorrows()(uint256)') - n(PN, 'totalReserves()(uint256)')
print('%d%d%d' % (cfg[14], cfg[15], cfg[16]), led[0], led[1], led[2], led[3], liq, led[4], now - upd, claims - backing)
E
}

show() {
  read -r flags sp up si ui liq loss age gap < <(snapshot) || die "state read failed"
  echo "flags(alloc,swap,emerg)=$flags stockPrincipal=$sp usdgPrincipal=$up stockIdle=$si usdgIdle=$ui liquidity=$liq"
  echo "cumulativeLossUSDG=$loss feedAge=${age}s pNVDA claims-backing=$gap raw NVDA"
  echo "reserve available NVDA $(call $RES 'available(bytes32,address)(uint256)' $PAIR $NVDA | cut -d' ' -f1)"
}

us_session() { python3 -c 'import time,sys; t=time.gmtime(); m=t.tm_hour*60+t.tm_min; sys.exit(0 if t.tm_wday<5 and 870<=m<1260 else 1)'; }

[[ "$(cast chain-id --rpc-url "$R")" == 4663 ]] || die "not Robinhood Chain"
case "${1:-}" in
  preflight)
    r() { cast keccak "$1"; }
    for role in PROPOSER_ROLE EXECUTOR_ROLE CANCELLER_ROLE; do
      [[ "$(call $T 'hasRole(bytes32,address)(bool)' "$(r $role)" $L)" == true ]] || die "Ledger lacks timelock $role"
    done
    [[ "$(call $V 'hasRole(bytes32,address)(bool)' "$(r GUARDIAN_ROLE)" $L)" == true ]] || die "Ledger is not guardian"
    [[ "$(call $V 'hasRole(bytes32,address)(bool)' "$(r KEEPER_ROLE)" $L)" == true ]] || die "Ledger is not keeper"
    [[ "$(call $V 'hasRole(bytes32,address)(bool)' "$(r CONFIG_ROLE)" $T)" == true ]] || die "timelock lacks CONFIG_ROLE"
    [[ "$(call $PN 'vaultOperator()(address)')" == "$L" ]] || die "Ledger is not the pNVDA vault operator"
    [[ "$(call $T 'getMinDelay()(uint256)' | cut -d' ' -f1)" == 3600 ]] || die "timelock delay changed"
    show
    read -r flags sp up _ _ liq _ age gap < <(snapshot)
    [[ "$flags" == 110 && "$liq" == 0 && "$up" == 0 ]] || echo "NOTE: expected flags 110, liquidity 0, usdgPrincipal 0"
    (( gap > 0 )) || echo "NOTE: no pNVDA gap left; nothing to correct"
    us_session || echo "NOTE: outside Mon-Fri 14:30-21:00 UTC"
    echo "operation $(op_id) pending=$(call $T 'isOperationPending(bytes32)(bool)' "$(op_id)") ready=$(call $T 'isOperationReady(bytes32)(bool)' "$(op_id)") done=$(call $T 'isOperationDone(bytes32)(bool)' "$(op_id)")"
    ;;
  schedule)
    read -r flags _ up _ _ liq _ _ gap < <(snapshot)
    [[ "$flags" == 110 && "$liq" == 0 && "$up" == 0 ]] || die "unexpected start state (flags $flags, liquidity $liq, usdgPrincipal $up)"
    ledger_send "$T" 'schedule(address,uint256,bytes,bytes32,bytes32,uint256)' "$V" 0 "$(pause_data true false false)" "$ZERO32" "$SALT" 3600
    echo "operation $(op_id) pending=$(call $T 'isOperationPending(bytes32)(bool)' "$(op_id)"); executable from $(date -u -r $(( $(date +%s) + 3600 )) +%H:%M) UTC"
    ;;
  execute)
    if [[ "${REHEARSE:-}" != 1 ]]; then
      us_session || die "outside Mon-Fri 14:30-21:00 UTC; wait (the schedule does not expire)"
      read -r _ _ _ _ _ _ _ age _ < <(snapshot)
      (( age <= 3600 )) || die "NVDA answer is ${age}s old; wait for a fresh update"
    fi
    [[ "$(call $T 'isOperationReady(bytes32)(bool)' "$(op_id)")" == true ]] || die "operation is not ready yet"
    ledger_send "$T" 'execute(address,uint256,bytes,bytes32,bytes32)' "$V" 0 "$(pause_data true false false)" "$ZERO32" "$SALT"
    read -r flags _ _ _ _ _ _ _ _ < <(snapshot)
    [[ "$flags" == 100 ]] && echo "PASS: swaps open, allocation paused (flags 100)" || die "flags are $flags after execute"
    ;;
  gonogo|sync)
    read -r flags sp _ _ _ liq loss _ gap < <(snapshot)
    [[ "$flags" == 100 ]] || die "swaps are not open (flags $flags); never sync pNVDA at 110"
    out=$(cast call "$PN" 'syncVault(uint256)(uint256,uint256)' "$MAX" --from "$L" --gas-limit $SYNC_GAS --rpc-url "$R" 2>&1) || die "NO-GO: eth_call reverted: ${out:0:120}"
    returned=$(sed -n 1p <<<"$out" | cut -d' ' -f1); realized=$(sed -n 2p <<<"$out" | cut -d' ' -f1)
    echo "eth_call pNVDA.syncVault from Ledger: returned=$returned realizedLoss=$realized (stockPrincipal $sp, gap $gap)"
    if python3 -c "import sys;r,l,s,m=map(int,sys.argv[1:]);sys.exit(0 if r>0 and r+l==s and l<=m else 1)" "$returned" "$realized" "$sp" "$MAX_GO_LOSS"; then
      echo "GO: full claim accounted for, realised loss $(python3 -c "import sys;print('%.8f' % (int(sys.argv[1])/1e18))" "$realized") NVDA (limit 0.0001)"
    else
      echo "NO-GO: returned+loss != stockPrincipal, nothing returned, or loss above 0.0001 NVDA. Close now: bash pnvda_correction.sh close"
      exit 1
    fi
    [[ "$1" == gonogo ]] && exit 0
    ledger_send "$PN" 'syncVault(uint256)' "$MAX" --gas-limit $SYNC_GAS
    read -r flags sp2 _ _ _ _ loss2 _ gap2 < <(snapshot)
    echo "after sync: flags=$flags stockPrincipal=$sp2 pNVDA claims-backing=$gap2 cumulativeLossUSDG $loss -> $loss2"
    python3 -c "import sys;sys.exit(0 if int(sys.argv[1])<=0 and int(sys.argv[2])<10**12 else 1)" "$gap2" "$sp2" \
      || die "gap not closed as expected; close the window now and send the output"
    echo "PASS: pNVDA fully backed; now close"
    ;;
  close)
    ledger_send "$V" 'setPairPause(bytes32,bool,bool,bool)' "$PAIR" true true false
    read -r flags _ _ _ _ _ _ _ _ < <(snapshot)
    [[ "$flags" == 110 ]] && echo "PASS: swaps closed, flags 110" || die "flags are $flags after close"
    ;;
  checkpoint)
    dl=$(( $(cast block latest --field timestamp --rpc-url "$R") + 280 ))
    ledger_send "$V" 'checkpoint(bytes32,uint256)' "$PAIR" "$dl" --gas-limit 2000000
    show
    ;;
  cancel)
    [[ "$(call $T 'isOperationPending(bytes32)(bool)' "$(op_id)")" == true ]] || die "nothing pending to cancel"
    ledger_send "$T" 'cancel(bytes32)' "$(op_id)"
    ;;
  state) show ;;
  *) sed -n 2,20p "$0"; exit 2 ;;
esac
