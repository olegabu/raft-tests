#!/bin/bash
# Phase 3's sweep — the relay-observed counterpart to
# ../sweep/sweep.sh, which this deliberately does not modify (shared,
# used by every product). sweep.sh's own CSV columns (achieved, p50,
# p90, p99, p999, max, dropped, lag) come from grepping bare "p50" /
# "p90" / etc. labels out of the run's log — exactly the labels
# sequencer's own load generator deliberately does NOT use for its
# relay-observed summary (relay_p50_us, not p50 — see
# bench/load_generator/README.md's own "thread synchronization,
# precisely" section for why that distinction has to be airtight), so
# sweep.sh has nothing to grab for phase 3. This script is that
# missing extraction, driving the exact same `make client
# RELAY_GRPC_ADDR=...` sweep.sh itself calls for phase 1, just reading
# a different set of labels out of the same log.
#
#   sweep-relay.sh <csv> <warmup> <measure> "<extra make flags>" <rate>...
#
# <extra make flags> MUST include RELAY_GRPC_ADDR=<node1_priv>:<port>
# (see `make -n client-relay` in this directory for the default) —
# required explicitly, the same way sweep.sh's own callers spell out
# BURST/MAX_INFLIGHT rather than this script assuming a default.
#
#   sweep-relay.sh out.csv 10 30 "RELAY_GRPC_ADDR=172.31.3.149:8501" \
#     10000 25000 40000 55000 70000 85000 100000
#
# Writes rows with product=sequencer-relay, in the exact same 10-column
# shape sweep.sh's own CSV uses (see ../sweep/README.md), so
# sweep/mkcharts.py reads this file exactly the way it reads any
# other sweep's — no code changes to mkcharts.py needed, only a new
# "sequencer-relay" entry in its CFG dict once real data exists to size
# the axes from (see this directory's own README).
set -u
CSV=$1; WU=$2; ME=$3; EXTRA=$4; shift 4

case "$EXTRA" in
  *RELAY_GRPC_ADDR=*) ;;
  *) echo "sweep-relay.sh: \$EXTRA must include RELAY_GRPC_ADDR=<addr> — see this script's own header" >&2; exit 1 ;;
esac

# relay_p50_us=1234 style (RelayObserver::printSummary()), not
# sweep.sh's own "  p50      1234" — anchor on label=digits, take the
# last match's value, strip the CRLF `ssh -t` leaves in the log.
pct() { grep -E "^$2=[0-9]+\r?$" "$1" | tail -1 | cut -d= -f2 | tr -d '\r'; }

for R in "$@"; do
  L=/tmp/sweep_sequencer-relay_$R.log
  timeout 90 make client MODE=open RATE="$R" WARMUP="$WU" MEASURE="$ME" $EXTRA > "$L" 2>&1
  printf '%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n' \
    "sequencer-relay" "$R" \
    "$(grep 'achieved rate' "$L" | awk '{print $3}' | tr -d '\r')" \
    "$(pct "$L" relay_p50_us)" "$(pct "$L" relay_p90_us)" "$(pct "$L" relay_p99_us)" \
    "$(pct "$L" relay_p99_9_us)" "$(pct "$L" relay_max_us)" \
    "$(grep 'dropped-by-rig' "$L" | awk '{print $2}' | tr -d '\r')" \
    "$(grep 'schedule lag' "$L" | grep -oE 'p50 [0-9]+' | awk '{print $2}')" \
    >> "$CSV"
  tail -1 "$CSV"
done
