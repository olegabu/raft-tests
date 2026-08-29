#!/bin/bash
# Walk a set of offered rates and, for each, report what the input
# gateways and the raft leader were actually doing -- not just the
# end-to-end latency the sweep already records.
#
#   probe-ceiling.sh <csv> <warmup> <measure> <rate>...
#
# Needs CLIENTS and NODE1 in the environment (`make env` writes both).
#
# The question it exists for: sweeps put a throughput ceiling near 380k
# req/s that is not CPU on any box -- the leader measured 38.7% busy at
# 350k on 16 cores. The two remaining candidates are the gateway's
# in-flight batch cap and time spent inside the raft group, and they
# call for opposite fixes, so guessing between them is not good enough.
#
#   deferred_per_s climbing with the rate  -> the gateway's cap binds
#   apply_wait rising while deferred stays -> the raft group is the wall
#     flat                                    and the gateway is idle
#     waiting on it
#
# Counters are differenced across the measurement window; percentile
# summaries are read at the end of it, while load is still on. bvar's
# LatencyRecorder publishes an average as `<name>_latency` and
# percentiles as `<name>_latency_99` / `<name>_max_latency` -- there is
# no p50, so the average stands in for the middle of the distribution.
set -u
CSV=$1; WU=$2; ME=$3; shift 3
: "${CLIENTS:?set CLIENTS}"; : "${NODE1:?set NODE1}"
SSH_KEY=${SSH_KEY:-$HOME/.ssh/id_rsa}
SSH_OPTS="-i $SSH_KEY -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10"
GW_PORT=${INPUT_GATEWAY_PORT:-8400}
NODE_PORT=${PORT:-8300}
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
read -r -a HOSTS <<< "$CLIENTS"

grab() { ssh $SSH_OPTS ubuntu@"$1" "curl -s localhost:$2/vars" 2>/dev/null; }
val() { local v; v=$(grep -E "^$2 " <<<"$1" | head -1 | sed 's/.*: *//' | tr -d '\r'); echo "${v:-0}"; }

echo 'rate,achieved,e2e_p50,deferred_per_s,gw_queue_max,gw_batch_avg,gw_qdelay_avg,gw_qdelay_p99,node_batch_inputs,node_apply_avg,node_apply_p99,node_apply_max' > "$CSV"

for R in "$@"; do
  # Snapshot the counters before the run so they can be differenced.
  BEFORE_DEF=0
  for H in "${HOSTS[@]}"; do
    BEFORE_DEF=$((BEFORE_DEF + $(val "$(grab "$H" "$GW_PORT")" input_gateway_proposals_deferred)))
  done

  CLIENTS="$CLIENTS" "$HERE/sweep-multi.sh" probe . /tmp/probe_row.csv "$WU" "$ME" "" "$R" \
    > /tmp/probe_$R.log 2>&1 &
  SWEEP=$!
  # Land the scrape in the MIDDLE of the measurement window. bvar's
  # windowed statistics decay to zero when a bvar goes idle, so a scrape
  # that slips past the end of the window reports 0 for everything --
  # which is indistinguishable from "nothing was happening" and was
  # exactly the first result this script produced. The leader is scraped
  # first for the same reason: six sequential ssh round trips take
  # several seconds, and whatever is read last is closest to the edge.
  sleep $((WU + ME / 2))
  V=$(grab "$NODE1" "$NODE_PORT")
  NB=$(val "$V" node_propose_batch_inputs)
  NA=$(val "$V" node_propose_batch_apply_wait_us_latency)
  NP=$(val "$V" node_propose_batch_apply_wait_us_latency_99)
  NM=$(val "$V" node_propose_batch_apply_wait_us_max_latency)
  GW_DEF=0; GW_QMAX=0; GW_BSUM=0; GW_DSUM=0; GW_DP99=0; N=0
  for H in "${HOSTS[@]}"; do
    V=$(grab "$H" "$GW_PORT")
    GW_DEF=$((GW_DEF + $(val "$V" input_gateway_proposals_deferred)))
    Q=$(val "$V" input_gateway_queue_depth_max); [ "$Q" -gt "$GW_QMAX" ] 2>/dev/null && GW_QMAX=$Q
    GW_BSUM=$(awk -v a="$GW_BSUM" -v b="$(val "$V" input_gateway_batch_size)" 'BEGIN{print a+b}')
    GW_DSUM=$(awk -v a="$GW_DSUM" -v b="$(val "$V" input_gateway_batch_queue_delay_us_latency)" 'BEGIN{print a+b}')
    P=$(val "$V" input_gateway_batch_queue_delay_us_latency_99); [ "$P" -gt "$GW_DP99" ] 2>/dev/null && GW_DP99=$P
    N=$((N + 1))
  done
  wait $SWEEP

  ROW=$(tail -1 /tmp/probe_row.csv)
  ACH=$(cut -d, -f3 <<<"$ROW"); P50=$(cut -d, -f4 <<<"$ROW")
  DEF_RATE=$(awk -v d=$((GW_DEF - BEFORE_DEF)) -v m="$ME" 'BEGIN{printf "%d", d/m}')
  printf '%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n' \
    "$R" "$ACH" "$P50" "$DEF_RATE" "$GW_QMAX" \
    "$(awk -v s="$GW_BSUM" -v n="$N" 'BEGIN{printf "%.1f", s/n}')" \
    "$(awk -v s="$GW_DSUM" -v n="$N" 'BEGIN{printf "%.1f", s/n}')" \
    "$GW_DP99" "$NB" "$NA" "$NP" "$NM" >> "$CSV"
  tail -1 "$CSV"
done
