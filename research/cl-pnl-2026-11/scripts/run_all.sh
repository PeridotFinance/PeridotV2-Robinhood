#!/bin/sh
# Rebuild every result in out/ and every chart in charts/ from the committed tables in data/. No network.
# Usage: PY=/path/to/python PROCS=8 sh run_all.sh [q1|q2|q3|q4|q5|q6|gate|charts ...]   (default: everything; about an hour on 8 cores)
# Needs Python 3.9+ with numpy and matplotlib (requirements.txt).
set -u
cd "$(dirname "$0")"
PY=${PY:-python3}; PROCS=${PROCS:-8}; L=../out/logs; mkdir -p $L
STEPS=${*:-gate q1 q2 q3 q6 q4 q5 charts}
# Position simulations on the v4 NVDA pool start on 16 July 2026: in its first two weeks the pool held so little liquidity
# (about $2,000 moved the price 1 percent) that a $1,000 position would have been 1 percent of it and a hedge trade would have moved the price.
V4N="--from-ts 1784160000"
from_of() { [ "$1" = v4_nvda ] && echo "$V4N"; }
for step in $STEPS; do case $step in
gate) $PY gate1_reproduce_live.py > $L/gate1.txt 2>&1 ;;
q1) for v in v4_nvda v4_spy ramses_nvda ramses_spy univ3_nvda univ3_spy univ3_nvda_005 univ3_spy_005; do
      s=${v#*_}; s=${s%%_*}; $PY q1_fee_vs_lvr.py $v --ref external/${s}_1m.csv.gz > $L/q1_$v.txt 2>&1; done
    $PY q1b_rewards_vs_loss.py > $L/q1b_rewards_vs_loss.txt 2>&1
    $PY q1_fee_vs_lvr.py pharaoh_wavax --ref external/avaxusdt_1m.csv.gz > $L/q1_pharaoh_wavax.txt 2>&1 ;;
q2) for v in v4_nvda ramses_nvda ramses_spy v4_spy pharaoh_wavax; do $PY q2_geometry.py $v --procs $PROCS $(from_of $v) > $L/q2_$v.txt 2>&1; done
    for v in v4_nvda ramses_nvda; do $PY q2_geometry.py $v --capital 10000 --procs $PROCS $(from_of $v) > $L/q2_${v}_10000.txt 2>&1; done ;;
q3) for v in ramses_nvda ramses_spy pharaoh_wavax v4_nvda; do
      $PY q3_bands.py $v $(from_of $v) > $L/q3_$v.txt 2>&1
      $PY q3_bands.py $v --notional 10000 $(from_of $v) > $L/q3_${v}_10000.txt 2>&1
      $PY q3_bands.py $v --width-ticks 100 --tag _w100 $(from_of $v) > $L/q3_${v}_w100.txt 2>&1
      $PY q3_bands.py $v --horizon-days 2 $(from_of $v) > $L/q3_${v}_2d.txt 2>&1
    done
    # presets scaled to SPY's volatility (about a third of NVDA's): 30 / 60 / 100 ticks away, 50 ticks wide
    $PY q3_bands.py ramses_spy --dist-ticks 30,60,100 --width-ticks 50 --tag _fit > $L/q3_ramses_spy_fit.txt 2>&1 ;;
q6) for v in v4_nvda ramses_nvda ramses_spy pharaoh_wavax; do $PY q6_session.py $v --procs $PROCS $(from_of $v) > $L/q6_$v.txt 2>&1; done ;;
q4) for v in v4_nvda ramses_nvda pharaoh_wavax; do $PY q4_hedged.py $v $(from_of $v) > $L/q4_$v.txt 2>&1; done ;;
q5) for v in ramses_nvda ramses_spy pharaoh_wavax; do $PY q5_reward_timing.py $v > $L/q5_$v.txt 2>&1; done ;;
charts) $PY charts_q1.py > $L/charts.txt 2>&1; $PY charts_rest.py >> $L/charts.txt 2>&1
    $PY tables_q1.py > ../out/tables_q1.md; $PY tables_q2.py > ../out/tables_q2.md; $PY tables_q3.py > ../out/tables_q3.md; $PY tables_q456.py > ../out/tables_q456.md ;;
esac; echo "$step done"; done
