#!/usr/bin/env bash
# Raise the SPY/USDG in-kind exit limit from 0 to LIMIT_USD per UTC day (DEPLOY_SPY_USDG.md section 6). Operator decision
# 2026-11-11: 250 USD/day. One timelock operation, 2 Ledger signatures. setInKindLimit reads no oracle.
#
#   bash inkind_limit.sh cleanday   read-only: the "clean day" evidence (checkpoint age, loss since launch, reward shortfall,
#                                   coverage, flags, keeper wallet); prints CLEAN or what is not
#   bash inkind_limit.sh schedule   Ledger 1: timelock.schedule(vault, 0, setInKindLimit(pair, LIMIT), 0, salt, 3600)
#   bash inkind_limit.sh execute    Ledger 2, >= 3,600 s later
#   bash inkind_limit.sh verify     read-only: inKindStats(pair).limitUsdPerDay == LIMIT, nothing else changed
#   bash inkind_limit.sh cancel     before execute
#
# Optional: LEDGER_HD, RH_RPC, REHEARSE=1 (local fork only: Ledger unlocked).
set -uo pipefail
cd "$(dirname "$0")/.."
LIMIT_USD=${LIMIT_USD:-250}
LIMIT=$(python3 -c "print(int('$LIMIT_USD')*10**18)")
R=${RH_RPC:-https://rpc.mainnet.chain.robinhood.com}
L=0x064eFE76812F5f18Bd1aA9708739Fb33C810B2dD
T=0x6797FB8Ce049B42C5BC2b42Bf76c6d15C7B12498
J=deployments/spy-usdg-mainnet.json
V=$(jq -r .vault $J); PAIR=$(jq -r .pair $J); AD=$(jq -r .adapter $J); CG=$(jq -r .coverageGuard $J); K=$(jq -r .keeper $J)
RAM=0x5173D45A1191eE33cBB7D8c7e65f21B04eD54802
SALT=$(cast keccak "peridot-spy-inkind-limit-$LIMIT_USD-v1")
ZERO32=0x0000000000000000000000000000000000000000000000000000000000000000
STATS_T='(uint256,uint64,uint192,uint256,uint256,uint256,uint256,uint256,uint256)'

die() { echo "STOPPED: $*" >&2; exit 1; }
call() { cast call "$@" --rpc-url "$R" 2>/dev/null; }
ledger_send() {
  local auth=(--ledger)
  [[ -n "${LEDGER_HD:-}" ]] && auth+=(--mnemonic-derivation-path "$LEDGER_HD")
  [[ "${REHEARSE:-}" == 1 ]] && auth=(--unlocked)
  cast send "$@" --from "$L" "${auth[@]}" --rpc-url "$R" --json | python3 -c '
import json,sys; r=json.loads(sys.stdin.read()); print("   tx", r["transactionHash"], "status", r["status"], "gas", int(r["gasUsed"],16))
sys.exit(0 if r["status"]=="0x1" else 1)' || die "transaction failed or reverted"
}
data() { cast calldata 'setInKindLimit(bytes32,uint256)' "$PAIR" "$LIMIT"; }
op_id() { call $T 'hashOperation(address,uint256,bytes,bytes32,bytes32)(bytes32)' "$V" 0 "$(data)" "$ZERO32" "$SALT"; }
limit_now() { call $V "inKindStats(bytes32)($STATS_T)" "$PAIR" | tr -d '()' | cut -d, -f1 | cut -d' ' -f1; }

[[ "$(cast chain-id --rpc-url "$R")" == 4663 ]] || die "not Robinhood Chain"
(( LIMIT_USD > 0 && LIMIT_USD <= 1000 )) || die "LIMIT_USD must be 1..1000 (the pair cap is 1,000)"
case "${1:-}" in
  cleanday)
    python3 - "$R" "$V" "$PAIR" "$AD" "$CG" "$K" "$RAM" <<'E'
import subprocess, sys, time
R, V, P, AD, CG, K, RAM = sys.argv[1:]
def c(*a): return subprocess.run(['cast', *a, '--rpc-url', R], capture_output=True, text=True).stdout.strip()
def n(x): return int(x.split()[0])
now = int(c('block', 'latest', '--field', 'timestamp'))
led = [n(x) for x in c('call', V, 'ledger(bytes32)(uint256,uint256,uint256,uint256,uint256,uint64)', P).splitlines()]
cfg = c('call', V, '0xf1a8151c' + P[2:])
w = [int(cfg[2:][i:i+64], 16) for i in range(0, len(cfg) - 2, 64)]
flags = '%d%d%d' % (w[14], w[15], w[16])
short = n(c('call', AD, 'rewardShortfall(bytes32,address)(uint256)', P, RAM))
cov = n(c('call', CG, 'status()(uint8)'))
head = n(c('call', CG, 'headroomUsd()(uint256)'))
kbal = int(c('balance', K)); knonce = int(c('nonce', K))
age = now - led[5]
ok = []
live = now - 1794382751  # keeper's first transaction (recenter 0x602f862c, block 111537349, 2026-11-11 07:39:11 UTC)
ok.append(('keeper live for %.1f h (a clean day needs >= 24 h)' % (live / 3600), live >= 86400))
ok.append(('last checkpoint age %d s (harvest checkpoints every 6 h; expect <= 25,200)' % age, age <= 25200))
ok.append(('cumulative loss since launch %.6f USD (expect < $0.01)' % (led[4] / 1e18), led[4] < 10**16))
ok.append(('reward shortfall booked %d RAM raw (expect 0)' % short, short == 0))
ok.append(('coverage status %d, headroom $%.2f (0 = covered)' % (cov, head / 1e18), cov == 0))
ok.append(('pause flags %s (allocation open, swaps per config, no emergency: expect 0x0 or 000)' % flags, w[16] == 0 and w[14] == 0))
ok.append(('keeper %s: %d txs, %.5f ETH (>= 0.0005)' % (K, knonce, kbal / 1e18), kbal >= 5 * 10**14))
for label, good in ok:
    print(('  ok   ' if good else '  FAIL ') + label)
print('CLEAN' if all(g for _, g in ok) else 'NOT CLEAN: do not schedule; send me this output')
E
    echo "  in-kind limit now: $(limit_now) (expect 0 before the pair)"
    echo "  operation $(op_id) pending=$(call $T 'isOperationPending(bytes32)(bool)' "$(op_id)") done=$(call $T 'isOperationDone(bytes32)(bool)' "$(op_id)")"
    ;;
  schedule)
    [[ "$(limit_now)" == 0 ]] || die "limit is not 0 (it is $(limit_now))"
    ledger_send $T 'schedule(address,uint256,bytes,bytes32,bytes32,uint256)' "$V" 0 "$(data)" "$ZERO32" "$SALT" 3600
    echo "operation $(op_id) pending=$(call $T 'isOperationPending(bytes32)(bool)' "$(op_id)"); executable from $(date -u -r $(( $(date +%s) + 3600 )) +%H:%M) UTC"
    ;;
  execute)
    [[ "$(call $T 'isOperationReady(bytes32)(bool)' "$(op_id)")" == true ]] || die "operation is not ready yet"
    ledger_send $T 'execute(address,uint256,bytes,bytes32,bytes32)' "$V" 0 "$(data)" "$ZERO32" "$SALT"
    bash "$0" verify
    ;;
  verify)
    got=$(limit_now)
    [[ "$got" == "$LIMIT" ]] || die "in-kind limit is $got, expected $LIMIT"
    echo "verified: in-kind limit $LIMIT_USD USD per UTC day ($got); used today $(call $V "inKindStats(bytes32)($STATS_T)" "$PAIR" | tr -d '()' | cut -d, -f3 | cut -d' ' -f2)"
    ;;
  cancel)
    [[ "$(call $T 'isOperationPending(bytes32)(bool)' "$(op_id)")" == true ]] || die "nothing pending"
    ledger_send $T 'cancel(bytes32)' "$(op_id)"
    ;;
  *) sed -n 2,13p "$0"; exit 2 ;;
esac
