#!/bin/bash
# Sweep sequencer with the offered load SPLIT ACROSS N CLIENT BOXES, each
# running its own input gateway, and report percentiles over the union of
# every client's samples.
#
#   sweep-multi.sh <product> <dir> <csv> <warmup> <measure> "<extra make flags>" <rate>...
#
# Same CSV shape and argument order as sweep.sh, so mkcharts.py reads the
# output with no special case. Two things it does that sweep.sh cannot:
#
#  1. It launches every client's load generator CONCURRENTLY. Run them one
#     after another and each measures an otherwise-idle cluster, which is
#     the opposite of the question -- the whole point is what a leader does
#     when N gateways push at it at once.
#
#  2. It merges raw histograms instead of averaging percentiles. Averaging
#     p50s is only correct when every client saw the same distribution,
#     which is exactly what this test is trying to find out; one straggler
#     client disappears into a mean. Per-client rows are printed next to
#     the merged line so a straggler is visible rather than smoothed away.
#
# Needs CLIENTS (space-separated public IPs) in the environment -- `make
# env` writes it from the terraform outputs.
#
# CLIENT_CMD is the per-client command, as a template with {RATE},
# {THREADS} and {RAW} substituted per run. It defaults to sequencer's
# load generator; braft/openraft/aeron pass their own, which is what
# lets one script drive a split-load sweep for any of them. The product
# only has to write raw histogram buckets to {RAW} -- see merge-hdr.py
# for why merging those beats averaging reported percentiles.
set -u
PROD=$1; DIR=$2; CSV=$3; WU=$4; ME=$5; EXTRA=$6; shift 6

: "${CLIENTS:?set CLIENTS (space-separated client IPs); run \`make env\` first}"
SSH_KEY=${SSH_KEY:-$HOME/.ssh/id_rsa}
SSH_USER=${SSH_USER:-ubuntu}
SSH_OPTS="-i $SSH_KEY -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10"
APP=${APP:-counter}
CLIENT_DIR=${CLIENT_DIR:-$APP}
INPUT_GATEWAY_PORT=${INPUT_GATEWAY_PORT:-8400}
DRAIN_TIMEOUT=${DRAIN_TIMEOUT:-10}
PACE=${PACE:-spin}
BURST=${BURST:-1}
# Total sender threads across the whole fleet, held constant as N varies so
# the rig offers load the same way at N=1 and N=5 and only the gateway count
# differs. Per-client share is this over N.
THREADS_TOTAL=${THREADS_TOTAL:-100}
# WU and ME are already set from the positional arguments above, so the
# default template interpolates them here, once.
CLIENT_CMD=${CLIENT_CMD:-"./${APP}_load_generator --input_gateway_addr=127.0.0.1:${INPUT_GATEWAY_PORT} --mode open --pace ${PACE} --thread_num {THREADS} --rate {RATE} --burst ${BURST} --warmup ${WU} --measure ${ME} --drain_timeout ${DRAIN_TIMEOUT} --hdr_raw_out {RAW} --logtostderr"}

read -r -a HOSTS <<< "$CLIENTS"
N=${#HOSTS[@]}
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

cd "$DIR" || exit 1
echo "sweep-multi: $N client(s): ${HOSTS[*]}"

for R in "$@"; do
  # Split the offered rate N ways, giving the remainder to the first clients
  # so the fleet total is exactly R and not R rounded down N times.
  BASE=$((R / N)); REM=$((R % N))
  THREADS=$((THREADS_TOTAL / N)); [ "$THREADS" -lt 1 ] && THREADS=1

  PIDS=(); LOGS=(); RAWS=()
  for i in "${!HOSTS[@]}"; do
    H=${HOSTS[$i]}
    MYRATE=$BASE; [ "$i" -lt "$REM" ] && MYRATE=$((BASE + 1))
    L=/tmp/multi_${PROD}_${R}_$i.log; LOGS+=("$L"); RAWS+=("/tmp/multi_${PROD}_${R}_$i.csv")
    # No -t: a tty per background ssh interleaves the five outputs into one
    # unparseable stream, and CRLF-mangles every field sweep.sh has to strip.
    CMD=${CLIENT_CMD//\{RATE\}/$MYRATE}
    CMD=${CMD//\{THREADS\}/$THREADS}
    CMD=${CMD//\{RAW\}//tmp/raw.csv}
    timeout $((WU + ME + DRAIN_TIMEOUT + 60)) \
      ssh $SSH_OPTS "$SSH_USER@$H" "cd $CLIENT_DIR && $CMD" > "$L" 2>&1 &
    PIDS+=($!)
  done
  # Wait for every client before touching any result: a client still running
  # is still loading the cluster the others just measured.
  for p in "${PIDS[@]}"; do wait "$p"; done

  for i in "${!HOSTS[@]}"; do
    scp $SSH_OPTS "$SSH_USER@${HOSTS[$i]}:/tmp/raw.csv" "${RAWS[$i]}" >/dev/null 2>&1 \
      || echo "sweep-multi: WARNING no raw histogram from ${HOSTS[$i]} at rate $R"
  done

  ACH=0; DROP=0; LAG=0
  for L in "${LOGS[@]}"; do
    a=$(grep 'achieved rate' "$L" | awk '{print $3}' | tr -d '\r'); ACH=$((ACH + ${a:-0}))
    d=$(grep 'dropped-by-rig' "$L" | awk '{print $2}' | tr -d '\r'); DROP=$((DROP + ${d:-0}))
    # Lag is a rig-health signal, not a quantity to add up: the worst client
    # is what says whether the rig kept up, so keep the max.
    l=$(grep 'schedule lag' "$L" | grep -oE 'p50 [0-9]+' | awk '{print $2}')
    [ "${l:-0}" -gt "$LAG" ] 2>/dev/null && LAG=$l
  done

  M=$(python3 "$HERE/merge-hdr.py" "${RAWS[@]}" 2>/dev/null)
  echo "$M" | sed '/^merged_/d'
  g() { echo "$M" | grep -oE "^$1=[0-9]+" | cut -d= -f2; }
  printf '%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n' \
    "$PROD" "$R" "$ACH" \
    "$(g merged_p50_us)" "$(g merged_p90_us)" "$(g merged_p99_us)" \
    "$(g merged_p99_9_us)" "$(g merged_max_us)" "$DROP" "$LAG" >> "$CSV"
  tail -1 "$CSV"
done
