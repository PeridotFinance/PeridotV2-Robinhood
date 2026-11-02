#!/bin/bash
# Runs the whole grid matrix for both pairs sequentially (each grid uses 5 processes).
cd "$(dirname "$0")"
for pair in SPY GLD; do
  for scen in full_norebal full_rebal last28_rebal last28_norebal full_rebal_hist; do
    python3 run_grid.py $pair $scen > results/grid_${pair}_${scen}.log 2>&1
  done
done
echo done > results/grid_all.done
