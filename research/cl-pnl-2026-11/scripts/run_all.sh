#!/bin/sh
# Rebuild every result in out/ and every chart in charts/ from the committed tables in data/. No network.
# Usage: PY=/path/to/python PROCS=8 sh run_all.sh [q1|q2|q3|q4|q5|q6|gate|charts ...]   (default: everything; about an hour on 8 cores)
# Needs Python 3.9+ with numpy and matplotlib (requirements.txt).
set -u
cd "$(dirname "$0")"
PY=${PY:-python3}; PROCS=${PROCS:-8}; L=../out/logs; mkdir -p $L
STEPS=${*:-gate q1 q2 q3 q6 q4 q5 q7 q8 charts}
# Position simulations on the v4 NVDA pool start on 16 July 2026: in its first two weeks the pool held so little liquidity
# (about $2,000 moved the price 1 percent) that a $1,000 position would have been 1 percent of it and a hedge trade would have moved the price.
V4N="--from-ts 1784160000"
V3N="--from-ts 1785801600"   # 2026-08-04 00:00 UTC
V3CELLS="600:300,1200:300,1200:600,2400:300,2400:600,2400:1200,4800:300,4800:600,4800:1200"
from_of() { [ "$1" = v4_nvda ] && echo "$V4N"; }
for step in $STEPS; do case $step in
gate) $PY gate1_reproduce_live.py > $L/gate1.txt 2>&1 ;;
q1) for v in v4_nvda v4_spy ramses_nvda ramses_spy univ3_nvda univ3_spy univ3_nvda_005 univ3_spy_005; do
      [ -f "../data/robinhood/${v}_swaps.csv.gz" ] || { echo "skip q1 $v: table not present (see data/.gitignore)"; continue; }
      s=${v#*_}; s=${s%%_*}; $PY q1_fee_vs_lvr.py $v --ref external/${s}_1m.csv.gz > $L/q1_$v.txt 2>&1; done
    $PY q1_fee_vs_lvr.py pharaoh_wavax --ref external/avaxusdt_1m.csv.gz > $L/q1_pharaoh_wavax.txt 2>&1
    $PY q1b_rewards_vs_loss.py > $L/q1b_rewards_vs_loss.txt 2>&1 ;;
q2) for v in v4_nvda ramses_nvda ramses_spy v4_spy pharaoh_wavax; do $PY q2_geometry.py $v --procs $PROCS $(from_of $v) > $L/q2_$v.txt 2>&1; done
    for v in v4_nvda ramses_nvda; do $PY q2_geometry.py $v --capital 10000 --procs $PROCS $(from_of $v) > $L/q2_${v}_10000.txt 2>&1; done
    # Uniswap V3 0.05% NVDA: nine cells, two workers (the table is 3.6 million swaps), from the Ramses NVDA start date
    [ -f ../data/robinhood/univ3_nvda_005_swaps.csv.gz ] && $PY q2_geometry.py univ3_nvda_005 --procs 2 $V3N --cells $V3CELLS > $L/q2_univ3_nvda_005.txt 2>&1 ;;
q3) for v in ramses_nvda ramses_spy pharaoh_wavax v4_nvda; do
      $PY q3_bands.py $v $(from_of $v) > $L/q3_$v.txt 2>&1
      $PY q3_bands.py $v --notional 10000 $(from_of $v) > $L/q3_${v}_10000.txt 2>&1
      $PY q3_bands.py $v --width-ticks 100 --tag _w100 $(from_of $v) > $L/q3_${v}_w100.txt 2>&1
      $PY q3_bands.py $v --horizon-days 2 $(from_of $v) > $L/q3_${v}_2d.txt 2>&1
    done
    # presets scaled to SPY's volatility (about a third of NVDA's): 30 / 60 / 100 ticks away, 50 ticks wide
    $PY q3_bands.py ramses_spy --dist-ticks 30,60,100 --width-ticks 50 --tag _fit > $L/q3_ramses_spy_fit.txt 2>&1 ;;
q6) for v in v4_nvda ramses_nvda ramses_spy pharaoh_wavax; do $PY q6_session.py $v --procs $PROCS $(from_of $v) > $L/q6_$v.txt 2>&1; done ;;
q4) for v in v4_nvda ramses_nvda pharaoh_wavax; do $PY q4_hedged.py $v $(from_of $v) > $L/q4_$v.txt 2>&1; done
    if [ -f ../data/robinhood/univ3_nvda_005_swaps.csv.gz ]; then
      $PY q4_hedged.py univ3_nvda_005 $V3N > $L/q4_univ3_nvda_005.txt 2>&1
      $PY q4_hedged.py v4_nvda $V3N --hedge-venue univ3_nvda_005 --tag _via_v3 > $L/q4_v4_nvda_via_v3.txt 2>&1
    fi
    $PY q4_hedged.py v4_nvda $V3N --tag _from_aug4 --no-windows > $L/q4_v4_nvda_from_aug4.txt 2>&1 ;;
q5) for v in ramses_nvda ramses_spy pharaoh_wavax; do $PY q5_reward_timing.py $v > $L/q5_$v.txt 2>&1; done ;;
q7) $PY q1_fee_vs_lvr.py pharaoh_wavax --ref external/avaxusdt_1m.csv.gz > $L/q1_pharaoh_wavax.txt 2>&1
    $PY q1_fee_vs_lvr.py blackhole_wavax --ref external/avaxusdt_1m.csv.gz > $L/q1_blackhole_wavax.txt 2>&1
    $PY q7_check_inrange.py blackhole > $L/q7_check_inrange_blackhole.txt 2>&1
    $PY q7_check_inrange.py pharaoh > $L/q7_check_inrange_pharaoh.txt 2>&1
    $PY q7_blackhole.py > $L/q7_blackhole.txt 2>&1
    $PY q2_geometry.py pharaoh_wavax --procs $PROCS > $L/q2_pharaoh_wavax.txt 2>&1
    $PY q2_geometry.py blackhole_wavax --procs 2 --cells 1200:600,2400:600,2400:1200,4800:600,4800:1200 > $L/q2_blackhole_wavax.txt 2>&1 ;;
q8) $PY q8_wide.py v4_nvda $V4N --procs 2 > $L/q8_v4_nvda.txt 2>&1
    $PY q8_wide.py v4_nvda $V3N --procs 2 --tag _from_aug4 > $L/q8_v4_nvda_from_aug4.txt 2>&1
    # the reserve funded by a larger cut of fees (live cut 20%, contract cap 50%)
    for c in 3000 3500 4000 4500 5000; do $PY q8_wide.py v4_nvda $V3N --procs 2 --reserve-bps $c --tag _from_aug4_cut$c > $L/q8_v4_nvda_from_aug4_cut$c.txt 2>&1; done
    $PY q8_wide.py v4_spy --procs 2 > $L/q8_v4_spy.txt 2>&1
    $PY q8_wide.py univ3_spy_005 --from-ts 1787097600 --procs 2 > $L/q8_univ3_spy_005.txt 2>&1
    $PY q8_wide.py ramses_nvda --protocol-share 0.5 --procs 2 > $L/q8_ramses_nvda_share50.txt 2>&1
    $PY q8_wide.py ramses_spy --protocol-share 0.5 --procs 2 > $L/q8_ramses_spy_share50.txt 2>&1
    $PY q1_fee_vs_lvr.py v4_nvda --from 2026-07-16 --tag _q8 > $L/q1_v4_nvda_q8.txt 2>&1
    $PY q1_fee_vs_lvr.py v4_spy --tag _q8 > $L/q1_v4_spy_q8.txt 2>&1
    $PY q1_fee_vs_lvr.py univ3_spy_005 --from 2026-08-19 --tag _q8 > $L/q1_univ3_spy_005_q8.txt 2>&1
    $PY q1_fee_vs_lvr.py ramses_nvda --protocol-share 0.5 --tag _q8 > $L/q1_ramses_nvda_q8.txt 2>&1
    $PY q1_fee_vs_lvr.py ramses_spy --protocol-share 0.5 --tag _q8 > $L/q1_ramses_spy_q8.txt 2>&1
    if [ -f ../data/robinhood/univ3_nvda_005_swaps.csv.gz ]; then
      $PY q8_wide.py univ3_nvda_005 $V3N --procs 2 > $L/q8_univ3_nvda_005.txt 2>&1
      for c in 4000 5000; do $PY q8_wide.py univ3_nvda_005 $V3N --procs 2 --reserve-bps $c --tag _cut$c > $L/q8_univ3_nvda_005_cut$c.txt 2>&1; done
      $PY q1_fee_vs_lvr.py univ3_nvda_005 --from 2026-08-04 --tag _q8 > $L/q1_univ3_nvda_005_q8.txt 2>&1
    fi ;;
charts) $PY charts_q1.py > $L/charts.txt 2>&1; $PY charts_rest.py >> $L/charts.txt 2>&1
    $PY tables_q1.py > ../out/tables_q1.md; $PY tables_q2.py > ../out/tables_q2.md; $PY tables_q3.py > ../out/tables_q3.md; $PY tables_q456.py > ../out/tables_q456.md; $PY tables_q78.py q7 > ../out/tables_q7.md; $PY tables_q78.py q8 > ../out/tables_q8.md ;;
esac; echo "$step done"; done
