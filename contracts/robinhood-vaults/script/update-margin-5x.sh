#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
exec python3 -B margin-mainnet/five-x/update.py "$@"
