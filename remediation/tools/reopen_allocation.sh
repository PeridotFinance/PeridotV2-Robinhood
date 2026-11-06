#!/usr/bin/env bash
# Reopen allocation on the NVDA/USDG pair after the sequential recovery (RECOVERY_SESSION.md). 2 Ledger signatures.
#
#   bash reopen_allocation.sh preflight   read-only: every reopen condition, prints GO or the failing one
#   bash reopen_allocation.sh schedule    Ledger signature 1: timelock.schedule(vault.setPairPause(pair, false, true, false))
#   bash reopen_allocation.sh execute     Ledger signature 2, at least 3,600 s later, in the US session with a fresh answer
#   bash reopen_allocation.sh state       read-only
#   bash reopen_allocation.sh cancel      before execute
#
# Opens allocation only; settlement swaps stay paused. Optional: LEDGER_HD, RH_RPC, REHEARSE=1 (local fork).
set -uo pipefail

R=${RH_RPC:-https://rpc.mainnet.chain.robinhood.com}
L=0x064eFE76812F5f18Bd1aA9708739Fb33C810B2dD
V=0x280825b2d856706Ff7E0d6351CcB2e935E1a9A2f
A=0xadA73211711e4790bc83B5d6B39f47fE04D276f3
T=0x6797FB8Ce049B42C5BC2b42Bf76c6d15C7B12498
FEED=0x379EC4f7C378F34a1B47E4F3cbeBCbAC3E8E9F15
KEEPER=0x82ABD8eE038c10F5bE00E5200C94265400999f7e
PAIR=0xe2050352f4346597cc69d2776d99ae60c9440dfc28f9406ce66be0bbe3fb6b06
SALT=$(cast keccak "peridot-reopen-allocation-2026-11-1")
ZERO32=0x0000000000000000000000000000000000000000000000000000000000000000
RECOVERY_LOSS=${RECOVERY_LOSS:-1606780967761417357} # cumulativeLossUSDG at the recovery's last read; override with the session's value

die() { echo "STOPPED: $*" >&2; exit 1; }
call() { cast call "$@" --rpc-url "$R" 2>/dev/null; }
data() { cast calldata 'setPairPause(bytes32,bool,bool,bool)' "$PAIR" false true false; }
op_id() { call "$T" 'hashOperation(address,uint256,bytes,bytes32,bytes32)(bytes32)' "$V" 0 "$(data)" "$ZERO32" "$SALT"; }
ledger_send() {
  local auth=(--ledger)
  [[ -n "${LEDGER_HD:-}" ]] && auth+=(--mnemonic-derivation-path "$LEDGER_HD")
  [[ "${REHEARSE:-}" == 1 ]] && auth=(--unlocked)
  cast send "$@" --from "$L" "${auth[@]}" --rpc-url "$R" --json | python3 -c '
import json,sys; r=json.loads(sys.stdin.read()); print("   tx", r["transactionHash"], "status", r["status"])
sys.exit(0 if r["status"]=="0x1" else 1)' || die "transaction failed or reverted; run state"
}
snapshot() { # flags usdgPrincipal liquidity cumulativeLoss feedAge
  python3 - "$R" "$V" "$A" "$PAIR" "$FEED" <<'E'
import subprocess, sys
R, V, A, P, F = sys.argv[1:]
c = lambda *a: subprocess.run(['cast', 'call', *a, '--rpc-url', R], capture_output=True, text=True, check=True).stdout.strip()
w = lambda h: [int(h[2:][i:i+64], 16) for i in range(0, len(h) - 2, 64)]
cfg = w(c(V, '0xf1a8151c' + P[2:])); led = w(c(V, '0x15977d45' + P[2:])); feed = w(c(F, '0xfeaf968c'))
liq = c(A, 'positionState(bytes32)((uint256,uint128,uint256,uint256))', P).strip('()').split(',')[1].split()[0]
now = int(subprocess.run(['cast', 'block', 'latest', '--field', 'timestamp', '--rpc-url', R], capture_output=True, text=True, check=True).stdout)
print('%d%d%d' % (cfg[14], cfg[15], cfg[16]), led[1], liq, led[4], now - feed[3])
E
}
us_session() { python3 -c 'import time,sys; t=time.gmtime(); m=t.tm_hour*60+t.tm_min; sys.exit(0 if t.tm_wday<5 and 870<=m<1260 else 1)'; }

conditions() { # every reopen condition; returns non-zero on the first failure
  read -r flags up liq loss age < <(snapshot) || die "state read failed"
  echo "flags=$flags usdgPrincipal=$up liquidity=$liq cumulativeLossUSDG=$loss feedAge=${age}s"
  [[ "$flags" == 110 ]] || { echo "NO-GO: flags $flags, expected 110 (recovery closed the window)"; return 1; }
  [[ "$liq" == 0 ]] || { echo "NO-GO: liquidity $liq, expected 0 (recovery unwound the position)"; return 1; }
  (( up < 10000 )) || { echo "NO-GO: usdgPrincipal $up above 0.01 USDG (recovery did not settle)"; return 1; }
  [[ "$loss" == "$RECOVERY_LOSS" ]] || { echo "NO-GO: cumulativeLossUSDG $loss differs from the recovery's $RECOVERY_LOSS"; return 1; }
  [[ "$(call $V 'hasRole(bytes32,address)(bool)' "$(cast keccak KEEPER_ROLE)" $KEEPER)" == true ]] || { echo "NO-GO: keeper lacks KEEPER_ROLE"; return 1; }
  (( $(cast balance $KEEPER --rpc-url "$R") >= 1000000000000000 )) || { echo "NO-GO: keeper gas under 0.001 ETH"; return 1; }
  return 0
}

[[ "$(cast chain-id --rpc-url "$R")" == 4663 ]] || die "not Robinhood Chain"
case "${1:-}" in
  preflight)
    conditions || exit 1
    echo "operation $(op_id) pending=$(call $T 'isOperationPending(bytes32)(bool)' "$(op_id)")"
    echo "GO for schedule. Execute additionally needs the US session and a feed answer under 1 h old."
    ;;
  schedule)
    conditions || die "conditions not met"
    ledger_send "$T" 'schedule(address,uint256,bytes,bytes32,bytes32,uint256)' "$V" 0 "$(data)" "$ZERO32" "$SALT" 3600
    echo "operation $(op_id) pending=$(call $T 'isOperationPending(bytes32)(bool)' "$(op_id)")"
    ;;
  execute)
    conditions || die "conditions not met"
    if [[ "${REHEARSE:-}" != 1 ]]; then
      us_session || die "outside Mon-Fri 14:30-21:00 UTC"
      read -r _ _ _ _ age < <(snapshot); (( age <= 3600 )) || die "feed answer ${age}s old; wait for a fresh one"
    fi
    [[ "$(call $T 'isOperationReady(bytes32)(bool)' "$(op_id)")" == true ]] || die "operation not ready"
    ledger_send "$T" 'execute(address,uint256,bytes,bytes32,bytes32)' "$V" 0 "$(data)" "$ZERO32" "$SALT"
    read -r flags _ _ _ _ < <(snapshot); [[ "$flags" == 010 ]] && echo "PASS: allocation open, swaps paused (010)" || die "flags $flags after execute"
    ;;
  cancel) ledger_send "$T" 'cancel(bytes32)' "$(op_id)" ;;
  state) read -r f u l c a < <(snapshot); echo "flags=$f usdgPrincipal=$u liquidity=$l cumulativeLossUSDG=$c feedAge=${a}s" ;;
  *) sed -n 2,10p "$0"; exit 2 ;;
esac
