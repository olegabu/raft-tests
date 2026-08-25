#!/bin/bash
# Phase 4's sweep — the output-gateway-observed counterpart to
# ../sweep/sweep.sh (phase 1) and sweep-relay.sh (phase 3), which this
# deliberately does not modify (shared, or already product-specific for
# a different phase). Same extraction shape as sweep-relay.sh, just
# reading a namespaced set of labels that varies by transport flavor
# (output_grpc_p50_us / output_brpc_p50_us / output_websocket_p50_us —
# see sequencer's own bench/load_generator/README.md's "four round
# trips" section) instead of a single fixed relay_p50_us.
#
#   sweep-output.sh <flavor> <csv> <warmup> <measure> "<extra make flags>" <rate>...
#
# <flavor>: grpc, brpc, or websocket — must match whichever flavor
# `make start OUTPUT_GATEWAY_FLAVOR=<flavor>` actually brought up.
# <extra make flags> MUST include OUTPUT_OBSERVER=<flavor> and
# OUTPUT_GATEWAY_ADDR=<node1_priv>:<port> (see `make -n client-output
# OUTPUT_GATEWAY_FLAVOR=<flavor>` in this directory for the values) —
# required explicitly, the same way sweep-relay.sh spells out
# RELAY_GRPC_ADDR rather than assuming a default.
#
#   sweep-output.sh grpc out.csv 10 30 \
#     "OUTPUT_OBSERVER=grpc OUTPUT_GATEWAY_ADDR=172.31.3.149:8600" \
#     10000 25000 40000 55000 70000 85000 100000
#
# Writes rows with product=sequencer-output-<flavor>, in the exact same
# 10-column shape sweep.sh's own CSV uses (see ../sweep/README.md), so
# sweep/mkcharts.py reads this file exactly the way it reads any
# other sweep's — no code changes to mkcharts.py needed beyond a new
# CFG entry per flavor once real data exists to size the axes from.
set -u
FLAVOR=$1; CSV=$2; WU=$3; ME=$4; EXTRA=$5; shift 5

case "$FLAVOR" in
  grpc|brpc|websocket) ;;
  *) echo "sweep-output.sh: <flavor> must be grpc, brpc, or websocket, got \"$FLAVOR\"" >&2; exit 1 ;;
esac
case "$EXTRA" in
  *OUTPUT_OBSERVER=*) ;;
  *) echo "sweep-output.sh: \$EXTRA must include OUTPUT_OBSERVER=<flavor> — see this script's own header" >&2; exit 1 ;;
esac

PRODUCT="sequencer-output-$FLAVOR"
PREFIX="output_${FLAVOR}"

# output_<flavor>_p50_us=1234 style (SequenceCorrelator::printSummary()),
# not sweep.sh's own "  p50      1234" — anchor on label=digits, take
# the last match's value, strip the CRLF `ssh -t` leaves in the log.
pct() { grep -E "^$2=[0-9]+\r?$" "$1" | tail -1 | cut -d= -f2 | tr -d '\r'; }

for R in "$@"; do
  L=/tmp/sweep_${PRODUCT}_$R.log
  timeout 90 make client MODE=open RATE="$R" WARMUP="$WU" MEASURE="$ME" $EXTRA > "$L" 2>&1
  printf '%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n' \
    "$PRODUCT" "$R" \
    "$(grep 'achieved rate' "$L" | awk '{print $3}' | tr -d '\r')" \
    "$(pct "$L" ${PREFIX}_p50_us)" "$(pct "$L" ${PREFIX}_p90_us)" "$(pct "$L" ${PREFIX}_p99_us)" \
    "$(pct "$L" ${PREFIX}_p99_9_us)" "$(pct "$L" ${PREFIX}_max_us)" \
    "$(grep 'dropped-by-rig' "$L" | awk '{print $2}' | tr -d '\r')" \
    "$(grep 'schedule lag' "$L" | grep -oE 'p50 [0-9]+' | awk '{print $2}')" \
    >> "$CSV"
  tail -1 "$CSV"
done
