#!/bin/bash
# Sweep one product across a list of offered rates in open loop, appending one
# CSV row per rate. The CSV feeds mkcharts.py and the README's per-product
# tables; keeping the raw rows means both can be regenerated and checked.
#
#   sweep.sh <product> <dir> <csv> <warmup> <measure> "<extra make flags>" <rate>...
#
# e.g. the three sweeps behind the charts in the root README:
#   sweep.sh braft    braft    out.csv 10 30 "BURST=10 MAX_INFLIGHT=2000" 40000 55000 70000 ...
#   sweep.sh openraft openraft out.csv 10 30 "MAX_INFLIGHT=400"           40000 55000 70000 ...
#   sweep.sh aeron    aeron    out.csv 10 30 "BURST=10 MAX_INFLIGHT=1000" 100000 175000 ...
#
# Header (write it yourself before the first run):
#   product,rate,achieved,p50,p90,p99,p999,max,dropped,lag
set -u
PROD=$1; DIR=$2; CSV=$3; WU=$4; ME=$5; EXTRA=$6; shift 6

# Pull one percentile out of the end-of-run HdrHistogram summary. The lines look
# like "  p99      1069", so anchor on the label surrounded by whitespace and
# take the last field of the last match.
# `make client` runs the loadgen over `ssh -t`, so the log carries CRLF; strip it
# or every extracted field ends up with a trailing carriage return.
pct() { grep -E "[[:space:]]$2[[:space:]]+[0-9]+$" "$1" | tail -1 | awk '{print $NF}' | tr -d '\r'; }

cd "$DIR" || exit 1
for R in "$@"; do
  L=/tmp/sweep_${PROD}_$R.log
  # The timeout is a backstop: warmup + measure + drain should finish well
  # inside it, and a run that doesn't is a hang worth noticing rather than
  # waiting out.
  timeout 90 make client MODE=open RATE="$R" WARMUP="$WU" MEASURE="$ME" $EXTRA > "$L" 2>&1
  printf '%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n' \
    "$PROD" "$R" \
    "$(grep 'achieved rate' "$L" | awk '{print $3}' | tr -d '\r')" \
    "$(pct "$L" p50)" "$(pct "$L" p90)" "$(pct "$L" p99)" "$(pct "$L" 'p99\.9')" "$(pct "$L" max)" \
    "$(grep 'dropped-by-rig' "$L" | awk '{print $2}' | tr -d '\r')" \
    "$(grep 'schedule lag' "$L" | grep -oE 'p50 [0-9]+' | awk '{print $2}')" \
    >> "$CSV"
  tail -1 "$CSV"
done
