#!/bin/bash
# Sample the input gateways' and the leader's bvars while a rate runs,
# and print the deltas.
#
#   scrape-vars.sh <label> <leader-ip> <client-ip>...
#
# brpc publishes every bvar on each process's own /vars page, so this
# needs no agent -- it curls the gateway on each client box and the node
# on the leader, before and after a measurement window, and reports what
# changed. Counters are differenced; gauges and percentile summaries are
# read at the end of the window, while load is still on.
#
# Written for the ~380k ceiling question: it is not CPU on any box, so
# the candidates are the gateway's in-flight batch cap (does
# input_gateway_proposals_deferred climb?) and time inside the raft
# group (does node_propose_batch_apply_wait_us climb?). Those two
# numbers distinguish the cases, which end-to-end latency cannot.
set -u
LABEL=$1; LEADER=$2; shift 2
SSH_KEY=${SSH_KEY:-$HOME/.ssh/id_rsa}
SSH_OPTS="-i $SSH_KEY -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10"
GW_PORT=${INPUT_GATEWAY_PORT:-8400}
NODE_PORT=${PORT:-8300}

# One curl per host; `?format=text` would be nicer but plain /vars is
# already "name : value" lines.
grab() { ssh $SSH_OPTS ubuntu@"$1" "curl -s localhost:$2/vars" 2>/dev/null; }
val()  { grep -E "^$2 " <<<"$1" | head -1 | sed 's/.*: *//' | tr -d '\r'; }

echo "=== $LABEL ==="
for H in "$@"; do
  V=$(grab "$H" "$GW_PORT")
  printf 'gw %-16s deferred=%-12s queue_now=%-6s queue_max=%-8s batch_avg=%-6s qdelay_p50=%-6s qdelay_p99=%s\n' \
    "$H" \
    "$(val "$V" input_gateway_proposals_deferred)" \
    "$(val "$V" input_gateway_queue_depth)" \
    "$(val "$V" input_gateway_queue_depth_max)" \
    "$(val "$V" input_gateway_batch_size)" \
    "$(val "$V" input_gateway_batch_queue_delay_us_50)" \
    "$(val "$V" input_gateway_batch_queue_delay_us_99)"
done
V=$(grab "$LEADER" "$NODE_PORT")
printf 'node %-14s in_progress=%-6s batch_inputs_avg=%-8s apply_p50=%-8s apply_p99=%-8s apply_max=%-8s redirects=%s\n' \
  "$LEADER" \
  "$(val "$V" node_propose_batches_in_progress)" \
  "$(val "$V" node_propose_batch_inputs)" \
  "$(val "$V" node_propose_batch_apply_wait_us_50)" \
  "$(val "$V" node_propose_batch_apply_wait_us_99)" \
  "$(val "$V" node_propose_batch_apply_wait_us_max)" \
  "$(val "$V" node_propose_redirects)"
