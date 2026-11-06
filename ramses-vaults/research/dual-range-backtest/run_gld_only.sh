#!/bin/bash
cd "$(dirname "$0")"
for scen in full_norebal full_rebal last28_rebal last28_norebal full_rebal_hist; do
  python3 run_grid.py GLD $scen > results/grid_GLD_${scen}.log 2>&1
done
echo done > results/grid_gld.done
