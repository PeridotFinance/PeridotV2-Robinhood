#!/bin/bash
# Full reproduction of the Phase 0 backtest numbers (about 35 minutes on 6 cores). Needs dataset_*.pkl from build_dataset.py.
cd "$(dirname "$0")"
python3 -m unittest test_engine || exit 1
for pair in SPY GLD; do
  for scen in full_norebal full_rebal last28_rebal last28_norebal full_rebal_hist; do
    python3 run_grid.py $pair $scen > results/grid_${pair}_${scen}.log 2>&1
  done
  python3 run_finalists.py $pair $(python3 pick_finalists.py $pair) > results/fin_${pair}.log 2>&1
done
python3 run_capacity.py SPY dual_c200_w500_k85,dual_c200_w300_k70,dual_c100_w500_k70,single_pm5 > results/cap_SPY.log 2>&1
python3 run_capacity.py GLD dual_c200_w800_k50,dual_c200_w500_k70,single_pm5,full_range > results/cap_GLD.log 2>&1
echo done > results/all.done
