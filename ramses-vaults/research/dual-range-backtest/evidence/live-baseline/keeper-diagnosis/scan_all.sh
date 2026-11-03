#!/bin/bash
# Read-only native-transfer scans of the keeper wallet, narrowest windows first.
cd "$(dirname "$0")"
python3 -B scan_funding.py 20261021 $(date -u -j -f '%FT%TZ' 2026-10-21T16:56:10Z +%s) $(date -u -j -f '%FT%TZ' 2026-10-21T17:01:40Z +%s)
