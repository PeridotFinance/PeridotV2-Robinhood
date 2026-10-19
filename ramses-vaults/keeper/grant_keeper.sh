#!/usr/bin/env bash
# Operator only. No default signer, plaintext key or remote automatic broadcast.
set -euo pipefail
: "${RAMSES_PUBLIC_SENDER:?Set the public governance sender address}"
: "${FOUNDRY_ACCOUNT:?Set the encrypted Foundry keystore alias}"
: "${ROBINHOOD_RPC_URL:?Set the reviewed RPC endpoint}"
broadcast=()
if [[ $# -gt 0 ]]; then
  [[ $# -eq 1 && $1 == --broadcast ]] || { echo 'Usage: grant_keeper.sh [--broadcast]' >&2; exit 2; }
  broadcast=(--broadcast)
fi
FOUNDRY_PROFILE=ramses forge script \
  ramses-vaults/script/GrantKeeperRole.s.sol:GrantRamsesKeeperRole \
  --rpc-url "$ROBINHOOD_RPC_URL" --sender "$RAMSES_PUBLIC_SENDER" \
  --account "$FOUNDRY_ACCOUNT" --threads 1 "${broadcast[@]}"
