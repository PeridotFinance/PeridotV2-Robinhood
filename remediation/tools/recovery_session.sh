#!/usr/bin/env bash
# Sequential USDG-shortage recovery, every signature from the Ledger. Read-only unless a step name is given.
#
#   bash recovery_session.sh preflight   read-only: roles, flags, feed age, US session, current state
#   bash recovery_session.sh schedule    Ledger signature 1: timelock.schedule(vault.setPairPause(pair,true,false,false))
#   bash recovery_session.sh execute     Ledger signature 2, at least 3,600 s later: timelock.execute(...) opens the window
#   bash recovery_session.sh gonogo      read-only eth_call of step 2 from the Ledger; prints GO or NO-GO
#   bash recovery_session.sh sync        Ledger signature 3: pUSDG.syncVault(max), gas limit 3,000,000; refuses on NO-GO
#   bash recovery_session.sh close       Ledger signature 4: vault.setPairPause(pair,true,true,false) as guardian
#   bash recovery_session.sh state       read-only post-state: the three flags, usdgPrincipal, liquidity, realised loss
#   bash recovery_session.sh cancel      abort before execute: timelock.cancel(id)
#
# Run execute, gonogo, sync and close back to back: between execute and close the settlement swap is public.
# Optional: LEDGER_HD=<derivation path>. RH_RPC overrides the RPC.
set -uo pipefail

R=${RH_RPC:-https://rpc.mainnet.chain.robinhood.com}
L=0x064eFE76812F5f18Bd1aA9708739Fb33C810B2dD
V=0x280825b2d856706Ff7E0d6351CcB2e935E1a9A2f
A=0xadA73211711e4790bc83B5d6B39f47fE04D276f3
T=0x6797FB8Ce049B42C5BC2b42Bf76c6d15C7B12498
PU=0x55aEd0569c8f0D166D71facE57B57C2f2624a563
FEED=0x379EC4f7C378F34a1B47E4F3cbeBCbAC3E8E9F15
PAIR=0xe2050352f4346597cc69d2776d99ae60c9440dfc28f9406ce66be0bbe3fb6b06
SALT=$(cast keccak "peridot-recovery-2026-11-sequential-1")
ZERO32=0x0000000000000000000000000000000000000000000000000000000000000000
MAX=115792089237316195423570985008687907853269984665640564039457584007913129639935
SYNC_GAS=3000000
FEED_MAX_AGE=43200

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

# Prints: flags usdgPrincipal stockPrincipal liquidity cumulativeLoss feedAge
snapshot() {
  python3 - "$R" "$V" "$A" "$PAIR" "$FEED" <<'E'
import subprocess, sys
R, V, A, P, F = sys.argv[1:]
def c(*a): return subprocess.run(['cast', 'call', *a, '--rpc-url', R], capture_output=True, text=True, check=True).stdout.strip()
def words(h): h = h[2:]; return [int(h[i:i+64], 16) for i in range(0, len(h), 64)]
cfg = words(c(V, '0xf1a8151c' + P[2:]))
led = words(c(V, '0x15977d45' + P[2:]))
liq = c(A, 'positionState(bytes32)((uint256,uint128,uint256,uint256))', P).strip('()').split(',')[1].split()[0]
feed = words(c(F, '0xfeaf968c'))
now = int(subprocess.run(['cast', 'block', 'latest', '--field', 'timestamp', '--rpc-url', R], capture_output=True, text=True, check=True).stdout)
print('%d%d%d' % (cfg[14], cfg[15], cfg[16]), led[1], led[0], liq, led[4], now - feed[3])
E
}

show() {
  read -r flags up sp liq loss age < <(snapshot) || die "state read failed"
  echo "flags(alloc,swap,emerg)=$flags usdgPrincipal=$up stockPrincipal=$sp liquidity=$liq cumulativeLossUSDG=$loss feedAge=${age}s"
}

expect() { # expect <label> <flags> <liquidity: open|zero>
  read -r flags up sp liq loss age < <(snapshot) || die "state read failed"
  local ok=1
  [[ "$flags" == "$2" ]] || ok=0
  if [[ "$3" == zero ]]; then [[ "$liq" == 0 ]] || ok=0; else [[ "$liq" != 0 ]] || ok=0; fi
  echo "$1: flags=$flags usdgPrincipal=$up liquidity=$liq cumulativeLossUSDG=$loss -> $([[ $ok == 1 ]] && echo PASS || echo FAIL)"
  [[ $ok == 1 ]]
}

us_session() { # Mon-Fri 14:30-21:00 UTC (US standard time, 9:30-16:00 New York)
  python3 -c 'import time,sys; t=time.gmtime(); m=t.tm_hour*60+t.tm_min; sys.exit(0 if t.tm_wday<5 and 870<=m<1260 else 1)'
}

[[ "$(cast chain-id --rpc-url "$R")" == 4663 ]] || die "not Robinhood Chain"
case "${1:-}" in
  preflight)
    r() { cast keccak "$1"; }
    for role in PROPOSER_ROLE EXECUTOR_ROLE CANCELLER_ROLE; do
      [[ "$(call $T 'hasRole(bytes32,address)(bool)' "$(r $role)" $L)" == true ]] || die "Ledger lacks timelock $role"
    done
    [[ "$(call $V 'hasRole(bytes32,address)(bool)' "$(r GUARDIAN_ROLE)" $L)" == true ]] || die "Ledger is not guardian"
    [[ "$(call $V 'hasRole(bytes32,address)(bool)' "$(r CONFIG_ROLE)" $T)" == true ]] || die "timelock lacks CONFIG_ROLE"
    [[ "$(call $PU 'vaultOperator()(address)')" == "$L" ]] || die "Ledger is not the pUSDG vault operator"
    [[ "$(call $T 'getMinDelay()(uint256)' | cut -d' ' -f1)" == 3600 ]] || die "timelock delay changed"
    show
    read -r flags _ _ liq _ age < <(snapshot)
    [[ "$flags" == 010 ]] || echo "NOTE: flags are $flags, expected 010 before schedule"
    (( age <= FEED_MAX_AGE )) || echo "NOTE: feed is ${age}s old, over 12 h: do not execute until it updates"
    us_session || echo "NOTE: outside Mon-Fri 14:30-21:00 UTC"
    echo "operation $(op_id) pending=$(call $T 'isOperationPending(bytes32)(bool)' "$(op_id)") ready=$(call $T 'isOperationReady(bytes32)(bool)' "$(op_id)")"
    ;;
  schedule)
    expect "before schedule" 010 open || die "unexpected start state"
    ledger_send "$T" 'schedule(address,uint256,bytes,bytes32,bytes32,uint256)' "$V" 0 "$(pause_data true false false)" "$ZERO32" "$SALT" 3600
    echo "operation $(op_id) pending=$(call $T 'isOperationPending(bytes32)(bool)' "$(op_id)"); executable from $(date -u -r $(( $(date +%s) + 3600 )) +%H:%M) UTC"
    expect "after schedule" 010 open
    ;;
  execute)
    if [[ "${REHEARSE:-}" != 1 ]]; then # a local fork warps time, so these two gates are skipped only there
      us_session || die "outside Mon-Fri 14:30-21:00 UTC; wait (the schedule does not expire)"
      read -r _ _ _ _ _ age < <(snapshot)
      (( age <= 3600 )) || die "feed answer is ${age}s old; wait for a fresh update (deviation-triggered, so it moves in session)"
    fi
    [[ "$(call $T 'isOperationReady(bytes32)(bool)' "$(op_id)")" == true ]] || die "operation is not ready yet"
    ledger_send "$T" 'execute(address,uint256,bytes,bytes32,bytes32)' "$V" 0 "$(pause_data true false false)" "$ZERO32" "$SALT"
    expect "after execute (window open)" 100 open
    ;;
  gonogo|sync)
    read -r flags up _ liq loss age < <(snapshot)
    [[ "$flags" == 100 ]] || die "window is not open (flags $flags)"
    (( age <= FEED_MAX_AGE )) || die "NO-GO: feed ${age}s old"
    out=$(cast call "$PU" 'syncVault(uint256)(uint256,uint256)' "$MAX" --from "$L" --gas-limit $SYNC_GAS --rpc-url "$R" 2>&1) || die "NO-GO: eth_call reverted: ${out:0:120}"
    returned=$(sed -n 1p <<<"$out" | cut -d' ' -f1); realized=$(sed -n 2p <<<"$out" | cut -d' ' -f1)
    echo "eth_call syncVault from Ledger: returned=$returned realizedLoss=$realized (usdgPrincipal now $up, liquidity $liq)"
    if [[ "$returned" == "$up" && "$returned" != 0 && "$realized" == 0 ]]; then
      echo "GO: the full USDG claim comes back with no loss. Expected after sync: liquidity 0, usdgPrincipal under 0.01 USDG, cumulativeLossUSDG $loss unchanged, flags 100."
    else
      echo "NO-GO: a silent settlement failure or a loss. Close the window now: bash recovery_session.sh close"
      exit 1
    fi
    [[ "$1" == gonogo ]] && exit 0
    ledger_send "$PU" 'syncVault(uint256)' "$MAX" --gas-limit $SYNC_GAS
    read -r flags up2 _ liq2 loss2 _ < <(snapshot)
    echo "after sync: flags=$flags usdgPrincipal=$up2 liquidity=$liq2 cumulativeLossUSDG=$loss2"
    [[ "$liq2" == 0 && "$loss2" == "$loss" && $up2 -lt 10000 ]] || die "settlement did not complete as expected; close the window now and do not retry blindly"
    echo "PASS: settled; now close"
    ;;
  close)
    ledger_send "$V" 'setPairPause(bytes32,bool,bool,bool)' "$PAIR" true true false
    read -r flags _ _ _ _ _ < <(snapshot)
    [[ "$flags" == 110 ]] && echo "PASS: window closed, flags 110" || die "flags are $flags after close"
    show
    ;;
  cancel)
    [[ "$(call $T 'isOperationPending(bytes32)(bool)' "$(op_id)")" == true ]] || die "nothing pending to cancel"
    ledger_send "$T" 'cancel(bytes32)' "$(op_id)"
    echo "pending=$(call $T 'isOperationPending(bytes32)(bool)' "$(op_id)")"
    ;;
  state) show ;;
  *) sed -n 2,13p "$0"; exit 2 ;;
esac
