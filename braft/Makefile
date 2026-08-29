# Product-specific targets for the braft atomic-counter example. Provision
# the EC2 fleet first with the root Makefile (`make deploy` from the repo
# root); this Makefile only builds/pushes/runs against whatever that created.
-include ../.env
include ../common.mk

PORT     ?= 8300
SYNC     ?= false
# MODE=open (the default) emits at RATE on a fixed schedule and measures each
# request from its *scheduled* send time, so time spent waiting to send is charged
# to the system -- the rule that makes it immune to coordinated omission, and the
# only mode that can show the saturation knee. MODE=closed holds THREADS requests
# outstanding instead; it measures the service latency one well-behaved client
# sees and by construction cannot see the knee. RATE defaults to 100k, which is
# the same offered rate for all three products so their runs are directly
# comparable -- but it sits differently against each one's comfort zone (braft
# ~150k, openraft ~85k, aeron ~400k), so always report the rate a result came
# from rather than leaving it implied.
MODE     ?= open
THREADS  ?= 100
RATE     ?= 100000
BURST    ?= 1
MAX_INFLIGHT ?=
WARMUP   ?= 10
MEASURE  ?= 30
DRAIN_TIMEOUT ?= 10
PACE     ?= spin
HDR_OUT  ?=

OPEN_FLAGS = $(if $(RATE),--rate $(RATE),) --burst $(BURST) \
             $(if $(MAX_INFLIGHT),--max_inflight $(MAX_INFLIGHT),)
HDR_FLAG   = $(if $(HDR_OUT),--hdr_out $(HDR_OUT),)
# One bthread worker per sending thread, else threads queue for workers and
# inflate measured latency instead of the raft cluster's own latency.
CLIENT_CONCURRENCY ?= $(THREADS)
# In-flight AppendEntries RPCs per follower; 1 (braft's own default) forces a
# full round trip before the next entry can go out, capping throughput under
# concurrency. 8 held drops near-zero at 160k (10 and 10, the fixed startup
# cost) where 4 was already shedding 0.4-0.8% of the offered rate at the same
# rate -- and cost nothing at 55k/100k, where p99 was equal or slightly
# better. AE_CACHE_SIZE=16 paired with it did not help further and looked
# worse on this data; left at 8. See "Deciding PIPELINE" in the README for
# the comparison.
PIPELINE ?= 8
# brpc socket read/parse threads, on nodes and client alike. brpc's default is 1,
# so one pthread parses every connection -- a serialization point that has
# nothing to do with consensus and shows up long before it. Raising this alone
# does nothing if there is only one connection to spread: one socket is always
# handled by one dispatcher, so pair it with CONNECTION_TYPE=pooled.
EVENT_DISPATCHERS ?= 1
# brpc connection type for the client's channel to the leader. Empty = brpc's
# default ("single": one multiplexed connection). "pooled" gives each in-flight
# RPC its own connection.
CONNECTION_TYPE ?=
# Distinct connections the open-loop client round-robins over. One TCP stream
# head-of-line blocks its whole queue on a lost segment, which shows up in p99
# and not p50.
CHANNELS ?= 1
# Server-side bthread workers. The stock 18 is over-provisioned for the
# 8-vCPU c6i.2xlarge these run on.
SERVER_CONCURRENCY ?= 18
# See run_server.sh for why these three matter: the disk ExecutionQueue is serial,
# so batching is the lever; segment rollover is a periodic stall (8 MB rolls every
# ~1.4 s at 100k); and TRACE_LAT turns on braft's own phase breakdown.
LEADER_BATCH ?= 256
# On by default: braft ships this off, and leaving it off costs a wasted round trip
# every time a pipelined AppendEntries reaches a follower ahead of a gap in its log.
# Turning it on moved p99 at 100k from 6171 us to 1015 us and the comfort zone from
# 70k to 150k, with no downside found. Set AE_CACHE=false to reproduce the old
# numbers.
AE_CACHE ?= true
AE_CACHE_SIZE ?= 8
# The apply path is a second single-consumer ExecutionQueue, and the client's
# response is sent from on_apply -- so apply-queue delay is directly in the
# measured latency. Its default batch of 32 is 8x smaller than the log path's.
APPLY_BATCH ?= 32
FSM_COMMIT_BATCH ?= 512
SEGMENT_SIZE ?= 8388608
TRACE_LAT ?= false
HIGH_LAT_US ?= 1000000

PEERS      = $(NODE1_PRIV):$(PORT):0,$(NODE2_PRIV):$(PORT):0,$(NODE3_PRIV):$(PORT):0
NODE_DIR   = /data/atomic
CLIENT_DIR = atomic

.PHONY: build push push-multi start stop client sweep-multi sweep-multi-baseline logs transfer-leader

# Built separately from the braft repo (BUILD_TOOLS is off by default there):
#   cmake --preset default -DBUILD_TOOLS=ON ~/workspace/braft
#   cmake --build ~/workspace/braft/bld --target braft_cli
# Only needed for `make transfer-leader`; push skips it silently if absent.
BRAFT_CLI ?= ~/workspace/braft/bld/tools/output/bin/braft_cli

## Build the server, client and test binaries. The `default` CMake preset
## builds Release (this is a benchmarking repo, so that's the default -- see
## README's debug-vs-release note); use `cmake --preset debug .` first for an
## unoptimized build with assertions when troubleshooting a crash.
build:
	cmake --preset default .
	cmake --build build

## Copy binaries and run scripts to all instances
push:
	for h in $(NODES); do \
		ssh $(SSH_OPTS) $(SSH_USER)@$$h "mkdir -p $(NODE_DIR)/build" && \
		scp $(SSH_OPTS) build/atomic_server $(SSH_USER)@$$h:$(NODE_DIR)/build/ && \
		scp $(SSH_OPTS) run_server.sh stop.sh shflags $(SSH_USER)@$$h:$(NODE_DIR)/ ; \
	done
	ssh $(SSH_OPTS) $(SSH_USER)@$(CLIENT) "mkdir -p $(CLIENT_DIR)/build"
	scp $(SSH_OPTS) build/atomic_client $(SSH_USER)@$(CLIENT):$(CLIENT_DIR)/build/
	scp $(SSH_OPTS) run_client.sh shflags $(SSH_USER)@$(CLIENT):$(CLIENT_DIR)/
	if [ -x $(BRAFT_CLI) ]; then \
		scp $(SSH_OPTS) $(BRAFT_CLI) $(SSH_USER)@$(CLIENT):$(CLIENT_DIR)/ ; \
	else \
		echo "braft_cli not found at $(BRAFT_CLI); skipping (needed for 'make transfer-leader')" ; \
	fi

## Start one atomic_server per node, peered over private IPs
start:
	for h in $(NODES); do \
		ssh $(SSH_OPTS) $(SSH_USER)@$$h \
			"cd $(NODE_DIR) && ./stop.sh 2>/dev/null; ./run_server.sh --clean --server_num 1 --port $(PORT) --peers '$(PEERS)' --sync $(SYNC) --max_parallel_append_entries_rpc_num $(PIPELINE) --event_dispatcher_num $(EVENT_DISPATCHERS) --bthread_concurrency $(SERVER_CONCURRENCY) --leader_batch $(LEADER_BATCH) --enable_ae_cache $(AE_CACHE) --ae_cache_size $(AE_CACHE_SIZE) --apply_batch $(APPLY_BATCH) --fsm_caller_commit_batch $(FSM_COMMIT_BATCH) --max_segment_size $(SEGMENT_SIZE) --trace_append_latency $(TRACE_LAT) --append_entry_high_lat_us $(HIGH_LAT_US)" ; \
	done

stop:
	for h in $(NODES); do \
		ssh $(SSH_OPTS) $(SSH_USER)@$$h "cd $(NODE_DIR) && ./stop.sh" ; \
	done

## Run the load generator from the client box (override THREADS=n)
client:
	ssh -t $(SSH_OPTS) $(SSH_USER)@$(CLIENT) \
		"cd $(CLIENT_DIR) && ./run_client.sh --peers '$(PEERS)' \
			--mode $(MODE) --pace $(PACE) \
			--thread_num $(THREADS) $(OPEN_FLAGS) \
			--bthread_concurrency $(CLIENT_CONCURRENCY) \
			--warmup $(WARMUP) --measure $(MEASURE) \
			--drain_timeout $(DRAIN_TIMEOUT) $(HDR_FLAG) \
			--event_dispatcher_num $(EVENT_DISPATCHERS) \
			--connection_type '$(CONNECTION_TYPE)' --channels $(CHANNELS)"

## Push the client harness to EVERY client box, not just CLIENT. The
## split-load sweep (../sweep/sweep-multi.sh) runs one load generator
## per box against the same leader; `push` only ever provisioned the
## one client this repo's original topology had.
push-multi: push
	for h in $(CLIENTS); do \
		ssh $(SSH_OPTS) $(SSH_USER)@$$h "mkdir -p $(CLIENT_DIR)/build" && \
		scp $(SSH_OPTS) build/atomic_client $(SSH_USER)@$$h:$(CLIENT_DIR)/build/ && \
		scp $(SSH_OPTS) run_client.sh shflags $(SSH_USER)@$$h:$(CLIENT_DIR)/ ; \
	done

## The split-load sweep, for the same reason sequencer has one: braft's
## published knee was measured with a SINGLE client box, and sequencer's
## own "knee" turned out to be that client rather than the cluster --
## five clients moved it 2.6x. Until braft is measured the same way, any
## comparison between the two is against a handicapped field.
##
## Percentiles come from merging every client's raw histogram
## (--hdr_raw_out, ../sweep/merge-hdr.py), not from averaging their
## reported p50s, which is only valid if every client saw the same
## distribution -- the thing being measured.
SWEEP_MULTI_CSV ?= braft-multi.csv
SWEEP_MULTI_BASELINE_CSV ?= braft-multi-baseline.csv
SWEEP_RATES   ?= 40000 70000 100000 130000 160000 200000 250000 300000 350000
SWEEP_WARMUP  ?= 10
SWEEP_MEASURE ?= 25
# The braft client's own invocation, as sweep-multi.sh's CLIENT_CMD
# template; {RATE}/{THREADS}/{RAW} are substituted per run.
MULTI_CLIENT_CMD = ./run_client.sh --peers '$(PEERS)' --mode open --pace $(PACE) \
	--thread_num {THREADS} --rate {RATE} --burst $(BURST) \
	--bthread_concurrency $(CLIENT_CONCURRENCY) \
	--warmup $(SWEEP_WARMUP) --measure $(SWEEP_MEASURE) --drain_timeout $(DRAIN_TIMEOUT) \
	--event_dispatcher_num $(EVENT_DISPATCHERS) \
	--connection_type '$(CONNECTION_TYPE)' --channels $(CHANNELS) \
	--hdr_raw_out {RAW}

sweep-multi:
	echo 'product,rate,achieved,p50,p90,p99,p999,max,dropped,lag' > $(SWEEP_MULTI_CSV)
	CLIENTS="$(CLIENTS)" SSH_KEY=$(SSH_KEY) CLIENT_DIR=$(CLIENT_DIR) \
		CLIENT_CMD="$(MULTI_CLIENT_CMD)" \
		../sweep/sweep-multi.sh braft-multi . "$(abspath $(SWEEP_MULTI_CSV))" \
		$(SWEEP_WARMUP) $(SWEEP_MEASURE) "" $(SWEEP_RATES)

## The N=1 control arm, through the identical script and rig settings so
## the only difference is how many boxes carry the load.
sweep-multi-baseline:
	echo 'product,rate,achieved,p50,p90,p99,p999,max,dropped,lag' > $(SWEEP_MULTI_BASELINE_CSV)
	CLIENTS="$(CLIENT)" SSH_KEY=$(SSH_KEY) CLIENT_DIR=$(CLIENT_DIR) \
		CLIENT_CMD="$(MULTI_CLIENT_CMD)" \
		../sweep/sweep-multi.sh braft-multi1 . "$(abspath $(SWEEP_MULTI_BASELINE_CSV))" \
		$(SWEEP_WARMUP) $(SWEEP_MEASURE) "" $(SWEEP_RATES)

logs:
	ssh $(SSH_OPTS) $(SSH_USER)@$(NODE1) "tail -n 100 -f $(NODE_DIR)/runtime/0/std.log"

## Designate node[0]/NODE1 (colocated with the client in multi_az) as the
## starting leader. braft may re-elect a different leader later; the client
## will then be talking to a remote leader, which is expected.
transfer-leader:
	ssh $(SSH_OPTS) $(SSH_USER)@$(CLIENT) \
		"cd $(CLIENT_DIR) && ./braft_cli transfer_leader --group=Atomic --peer=$(NODE1_PRIV):$(PORT):0 --conf='$(PEERS)'"

# ---------------------------------------------------------------------------
# Rate sweep. Previously driven by invoking ../sweep/sweep.sh by hand (its own
# header documents that form); a make target so the chart in ../README.md has a
# single reproducible command behind it, the same way sequencer/ does.
#
# BURST=10 MAX_INFLIGHT=2000 are this product's own comfort-zone flags — see
# ../README.md's comfort-zone section for why these are per-transport rather
# than uniform. Rates are the ones the published curve was measured at.
SWEEP_RATES   ?= 10000 25000 40000 55000 70000 85000 100000 115000 130000 145000 \
                 150000 155000 160000 165000 170000 175000 190000 200000
SWEEP_WARMUP  ?= 10
SWEEP_MEASURE ?= 30
SWEEP_CSV     ?= braft.csv
SWEEP_FLAGS   ?= BURST=10 MAX_INFLIGHT=2000

.PHONY: sweep
sweep:
	echo 'product,rate,achieved,p50,p90,p99,p999,max,dropped,lag' > $(SWEEP_CSV)
	../sweep/sweep.sh braft . "$(abspath $(SWEEP_CSV))" $(SWEEP_WARMUP) $(SWEEP_MEASURE) \
		"$(SWEEP_FLAGS)" $(SWEEP_RATES)
