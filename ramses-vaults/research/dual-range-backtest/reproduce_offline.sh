#!/bin/sh
# Offline reproduction of the Ramses Phase 0 research from the release alone. No network is needed or used.
#   sh reproduce_offline.sh            quick: verify inputs, replay the pinned quotes, rerun the current-caps baselines (SPY, NVDA)
#   sh reproduce_offline.sh full       also every other baseline (GLD, research scenarios) and the unit tests
set -e
cd "$(dirname "$0")"
python3 -m unittest test_bundle test_quote_math test_feasibility test_engine test_replay_check
python3 unpack_datasets.py SPY GLD NVDA
for P in SPY GLD NVDA; do python3 bundle.py verify "$P"; done
python3 quote_offline.py --check
python3 verify_replays.py        # every fork proof: snapshot hash, pin, forge-log hash, strict row accounting
for P in SPY NVDA; do python3 run_baseline.py "$P" caps > /dev/null; python3 compare_baseline.py "$P" caps; done
if [ "$1" = "full" ]; then
  python3 run_baseline.py GLD caps > /dev/null; python3 compare_baseline.py GLD caps
  for P in SPY NVDA GLD; do python3 run_baseline.py "$P" research > /dev/null; python3 compare_baseline.py "$P" research; done
fi
echo "REPRODUCED"
