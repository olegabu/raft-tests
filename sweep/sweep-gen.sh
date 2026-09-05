#!/usr/bin/env bash
# A knee sweep with SEVERAL load generators per client box.
#
# Why this exists rather than ../sweep/sweep-multi.sh: that script runs
# ONE generator per box, and a single exchange generator tops out near
# 25,000/s -- so with five boxes it caps the measurement at ~125k and
# reports it as the exchange's ceiling. It was: the first ladders here
# were measuring the rig. Running four per box lifted the same cluster
# from ~123k to 500,000/s with no other change.
#
# It also splits generators across gateways, because each gateway is a
# separate process with its own session ids and its own delivery
# thread; ten sessions on each of two gateways behaved very differently
# from twenty on one (p99 938us against 235ms at 500k).
#
#   CLIENTS="ip ip ..." GATEWAYS="host:port host:port" \
#   sweep-gen.sh <product> <csv> <gens-per-box> <warmup> <measure> <rate>...
set -u
PROD=$1; CSV=$2; PER=$3; WU=$4; ME=$5; shift 5
: "${CLIENTS:?set CLIENTS}"; : "${GATEWAYS:?set GATEWAYS}"
SSH_KEY=${SSH_KEY:-~/.ssh/id_rsa}
SSH_OPTS="-i $SSH_KEY -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10"
CLIENT_DIR=${CLIENT_DIR:-exchange}
# Which load generator to run on the client boxes. The script is
# app-agnostic otherwise: it only needs a binary that takes
# --fix_gateway_addr/--fix_sender_comp_id/--client_id and the usual
# open-loop flags, which both exchange_load_generator and the counter's
# load_generator do.
GEN_BIN=${GEN_BIN:-exchange_load_generator}
FLEET=${FLEET:-unknown}
EXTRA=${EXTRA:-}
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

read -r -a HOSTS <<< "$CLIENTS"
read -r -a GWS <<< "$GATEWAYS"
TOTAL=$(( ${#HOSTS[@]} * PER ))
echo "sweep-gen: ${#HOSTS[@]} box(es) x $PER generator(s) = $TOTAL sessions across ${#GWS[@]} gateway(s)"

# Every gateway must be accepting connections BEFORE any client starts.
# A client that starts first either fails to connect or spends its
# warm-up retrying, and then the rates are offered by a changing number
# of sessions -- which is not a measurement of anything. Checked from a
# client box, because the gateways listen on private addresses.
PROBE_HOST=${HOSTS[0]}
for gw in "${GWS[@]}"; do
  gwh=${gw%%:*}; gwp=${gw##*:}
  ready=""
  for _ in $(seq 1 30); do
    if ssh $SSH_OPTS "ubuntu@$PROBE_HOST" \
        "timeout 2 bash -c '</dev/tcp/$gwh/$gwp' 2>/dev/null"; then ready=yes; break; fi
    sleep 2
  done
  if [ -z "$ready" ]; then
    echo "sweep-gen: gateway $gw never accepted a connection; aborting rather than measuring a partial fleet"
    exit 1
  fi
  echo "sweep-gen: gateway $gw ready"
done

echo 'product,rate,achieved,p50,p90,p99,p999,max,dropped,lag,fleet' > "$CSV"

for R in "$@"; do
  PER_GEN=$(( R / TOTAL ))
  # Clear stale raw histograms: a run that fails otherwise reports the
  # PREVIOUS run's percentiles, which happened here once already.
  for h in "${HOSTS[@]}"; do
    ssh $SSH_OPTS "ubuntu@$h" "rm -f /tmp/gen_*.csv /tmp/gen_*.log" 2>/dev/null &
  done; wait

  id=0; PIDS=()
  for hi in "${!HOSTS[@]}"; do
    for g in $(seq 0 $((PER - 1))); do
      h=${HOSTS[$hi]}
      gw=${GWS[$(( id % ${#GWS[@]} ))]}          # round-robin across gateways
      cid=$(( id + 1 ))
      ssh $SSH_OPTS "ubuntu@$h" "cd $CLIENT_DIR && ./$GEN_BIN \
        --fix_gateway_addr=$gw --fix_sender_comp_id=S${hi}_${g} --client_id=$cid \
        --rate $PER_GEN --mode open --pace spin --burst 1 \
        --warmup $WU --measure $ME --drain_timeout 10 \
        --hdr_raw_out /tmp/gen_${g}.csv $EXTRA --logtostderr" \
        > "/tmp/gen_${PROD}_${R}_${hi}_${g}.log" 2>&1 &
      PIDS+=($!); id=$(( id + 1 ))
    done
  done
  # Every generator must finish before any result is read: one still
  # running is still loading the cluster the others just measured.
  for p in "${PIDS[@]}"; do wait "$p"; done

  # Concurrency is the whole point, and it is checkable: every
  # generator's log must have been created within a couple of seconds
  # of the others. Launching them sequentially instead -- 20 ssh calls
  # at a few seconds each, against a 55s run -- means the first
  # finishes before the last starts, and summing their individual
  # "achieved rate" lines yields a total that was never offered at
  # once. That produced a fictitious 500,000/s here, when the same
  # config run concurrently manages ~108,000.
  SPREAD=$(for hi in "${!HOSTS[@]}"; do for g in $(seq 0 $((PER - 1))); do
      stat -c %Y "/tmp/gen_${PROD}_${R}_${hi}_${g}.log" 2>/dev/null; done; done \
    | sort -n | awk 'NR==1{a=$1} END{print $1-a}')
  if [ "${SPREAD:-0}" -gt 5 ]; then
    echo "sweep-gen: WARNING generators started ${SPREAD}s apart at rate $R; they did not overlap and this row is not a concurrent measurement"
  fi

  ACH=0; DROP=0; LAG=0; RAWS=()
  for hi in "${!HOSTS[@]}"; do
    for g in $(seq 0 $((PER - 1))); do
      L="/tmp/gen_${PROD}_${R}_${hi}_${g}.log"
      a=$(grep 'achieved rate' "$L" | awk '{print $3}' | tr -d '\r'); ACH=$(( ACH + ${a:-0} ))
      d=$(grep 'dropped-by-rig' "$L" | awk '{print $2}' | tr -d '\r'); DROP=$(( DROP + ${d:-0} ))
      l=$(grep 'schedule lag' "$L" | awk '{print $6}' | tr -d '\r'); [ "${l:-0}" -gt "$LAG" ] 2>/dev/null && LAG=$l
      scp $SSH_OPTS "ubuntu@${HOSTS[$hi]}:/tmp/gen_${g}.csv" "/tmp/raw_${PROD}_${R}_${hi}_${g}.csv" >/dev/null 2>&1 \
        && RAWS+=("/tmp/raw_${PROD}_${R}_${hi}_${g}.csv")
    done
  done

  if [ ${#RAWS[@]} -eq 0 ]; then
    echo "sweep-gen: WARNING no histograms at rate $R; row skipped"
    continue
  fi
  # Percentiles cannot be averaged: merge the raw buckets.
  M=$(python3 "$HERE/merge-hdr.py" "${RAWS[@]}" | grep -E '^merged_')
  p50=$(echo "$M" | grep merged_p50_us | cut -d= -f2)
  p90=$(echo "$M" | grep merged_p90_us | cut -d= -f2)
  p99=$(echo "$M" | grep merged_p99_us | cut -d= -f2)
  p999=$(echo "$M" | grep merged_p99_9_us | cut -d= -f2)
  mx=$(echo "$M" | grep merged_max_us | cut -d= -f2)
  echo "$PROD,$R,$ACH,$p50,$p90,$p99,$p999,$mx,$DROP,$LAG,$FLEET" | tee -a "$CSV"
done
