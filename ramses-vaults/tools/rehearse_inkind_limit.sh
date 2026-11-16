#!/usr/bin/env bash
# Anvil fork rehearsal of inkind_limit.sh (schedule, early execute refused, 3,601 s, execute + verify). Ledger unlocked on the fork only.
set -uo pipefail
cd "$(dirname "$0")/.."
P=8721; F=http://127.0.0.1:$P; L=0x064eFE76812F5f18Bd1aA9708739Fb33C810B2dD
anvil --fork-url https://rpc.mainnet.chain.robinhood.com --port $P --silent --auto-impersonate --timeout 60000 --retries 8 >/dev/null 2>&1 &
AP=$!
for i in $(seq 1 80); do cast block-number --rpc-url $F >/dev/null 2>&1 && break; sleep 0.5; done
echo "fork block $(cast rpc anvil_nodeInfo --rpc-url $F | python3 -c 'import json,sys;print(json.load(sys.stdin)["forkConfig"]["forkBlockNumber"])')"
cast rpc anvil_setBalance $L 0x16345785D8A0000 --rpc-url $F >/dev/null
S(){ echo "== $1"; REHEARSE=1 RH_RPC=$F bash tools/inkind_limit.sh $1 2>&1 | tail -${2:-3}; }
S cleanday 10
S schedule 2
echo "  early execute: $(REHEARSE=1 RH_RPC=$F bash tools/inkind_limit.sh execute 2>&1 | tail -1)"
J=deployments/spy-usdg-mainnet.json; V=$(jq -r .vault $J); PAIR=$(jq -r .pair $J)
echo "  direct setInKindLimit from the Ledger (not CONFIG): $(cast call $V 'setInKindLimit(bytes32,uint256)' $PAIR 250000000000000000000 --from $L --rpc-url $F 2>&1 | grep -o 'revert.*' | head -1)"
cast rpc evm_increaseTime 3601 --rpc-url $F >/dev/null; cast rpc evm_mine --rpc-url $F >/dev/null
S execute 3
# DeployRamsesInKind verify asserts limit == 0 (IN_KIND_LIMIT_NOT_ZERO), so after this pair use `inkind_limit.sh verify`.
kill $AP
