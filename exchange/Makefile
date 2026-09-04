# Product-specific targets for the `exchange` application: a
# deterministic CLOB on sequencer, reached over FIX 4.4
# (github.com/olegabu/exchange). Provision the EC2 fleet first with the
# root Makefile (`make deploy` from the repo root); this Makefile only
# builds, pushes and runs against whatever that created.
#
# A sibling of braft/, openraft/, aeron/ and sequencer/ -- one directory
# per product under test. It is NOT `sequencer/Makefile APP=exchange`:
# that variable selects among sequencer's own examples/, and exchange is
# a separate repository with its own build tree, its own binaries and an
# instrument to create before any order can be sent.
#
# Deliberately much smaller than sequencer/Makefile, because exchange has
# ONE client-facing transport. sequencer's carries five arms (ack via the
# input gateway, direct propose, relay, three output-gateway flavours,
# hffix FIX and QuickFIX FIX); there is no equivalent choice here, so
# there are no flags for one.
#
#   make build push start start-fix add-instrument   # bring a cluster up
#   make sweep-fix                                   # the knee ladder
#   make chart                                       # render it
#   make stop-fix stop                               # bring it down
#
# Then stop the fleet itself from the repo root: `make stop-instances`,
# and confirm every instance reads `stopped`. It costs ~$79/day running.
-include ../.env
include ../common.mk

EXCHANGE_DIR ?= ../../exchange

PORT     ?= 8300
GROUP    ?= exchange
ELECTION_TIMEOUT_MS ?= 1000
SYNC     ?= false

NODE_DIR   = /data/exchange
CLIENT_DIR = exchange

NODE_BIN        := exchange_node
FIX_GATEWAY_BIN := exchange_fix_gateway
ADMIN_BIN       := exchange_admin
LOADGEN_BIN     := exchange_load_generator
BIN_DIR          = $(EXCHANGE_DIR)/build/release/src
BENCH_BIN_DIR    = $(EXCHANGE_DIR)/build/release/bench

PEERS = $(NODE1_PRIV):$(PORT):0,$(NODE2_PRIV):$(PORT):0,$(NODE3_PRIV):$(PORT):0

# braft holds one fd per raft log segment and truncates only on
# snapshot, so a long ladder accumulates them until it hits the 1024
# soft limit -- at which point braft latches the node into ERROR and
# NEVER RECOVERS, while still running and listening. Every proposal then
# fails and the sweep happily writes rows of zeros. This produced a whole
# silent sweep once (see sequencer/README.md's fd-trap section). Raised
# at every launch site, not just this one.
ULIMIT_FDS := ulimit -n 65536;

# braft tuning, carried over from the sequencer sweeps that established
# it -- the node harness underneath is the same code, so the same
# settings apply and changing them here would make the two products
# incomparable. The reasoning for each is in braft/Makefile.
PIPELINE ?= 8
AE_CACHE ?= true
AE_CACHE_SIZE ?= 8
EVENT_DISPATCHERS ?= 1
SERVER_CONCURRENCY ?= 18
LEADER_BATCH ?= 256
APPLY_BATCH ?= 32
FSM_COMMIT_BATCH ?= 512
SEGMENT_SIZE ?= 8388608

# The instrument every client trades. Created once per cluster, as a
# SEQUENCED input (exchange docs/spec.md §4): instrument static data
# enters the state machine through Propose like any order, so it is
# journaled and replicated rather than configured per node -- config
# that differs between replicas is a divergence waiting to happen.
SYMBOL   ?= ABC
TICK     ?= 0.01
LOT      ?= 1
MAX_QTY  ?= 1000

# FIX gateways. Colocated with the nodes, one or more per host on
# consecutive ports; clients round-robin across all of them.
FIX_PORT     ?= 8700
FIX_GATEWAYS ?= 1
FIX_NODES        ?= $(NODE1_PRIV) $(NODE2_PRIV) $(NODE3_PRIV)
FIX_NODES_PUBLIC ?= $(NODE1) $(NODE2) $(NODE3)
FIX_ENV ?=

COMMA := ,
EMPTY :=
SPACE := $(EMPTY) $(EMPTY)
FIX_ENDPOINTS = $(foreach h,$(FIX_NODES),$(foreach i,$(shell seq 0 $$(( $(FIX_GATEWAYS) - 1 ))),$(h):$(shell echo $$(( $(FIX_PORT) + $(i) )))))

MODE     ?= open
RATE     ?= 100000
BURST    ?= 1
PACE     ?= spin
WARMUP   ?= 10
MEASURE  ?= 30
DRAIN_TIMEOUT ?= 10

.PHONY: build push push-multi start start-fix start-fix-on add-instrument \
        stop stop-fix clean-data client sweep-fix chart logs logs-fix leader

## Build exchange's release preset. It add_subdirectory's sequencer, so
## SEQUENCER_DIR must resolve from the exchange checkout (its default,
## ../sequencer, is correct for the standard total-order/ layout).
build:
	cd $(EXCHANGE_DIR) && cmake --preset release && cmake --build --preset release

## Node-side binaries to all three nodes, client-side to $(CLIENT).
## exchange_admin goes to the nodes because it is run once against the
## group, not from a load client.
push:
	for h in $(NODES); do \
		ssh $(SSH_OPTS) $(SSH_USER)@$$h "mkdir -p $(NODE_DIR)" && \
		scp $(SSH_OPTS) $(BIN_DIR)/$(NODE_BIN) $(BIN_DIR)/$(FIX_GATEWAY_BIN) \
			$(BIN_DIR)/$(ADMIN_BIN) $(SSH_USER)@$$h:$(NODE_DIR)/ ; \
	done
	ssh $(SSH_OPTS) $(SSH_USER)@$(CLIENT) "mkdir -p $(CLIENT_DIR)" && \
	scp $(SSH_OPTS) $(BENCH_BIN_DIR)/$(LOADGEN_BIN) $(SSH_USER)@$(CLIENT):$(CLIENT_DIR)/

## The load generator to EVERY client box. Deliberately not dependent on
## `push`: that copies node-side binaries, which fail with "Text file
## busy" whenever the cluster is running, and a failed prerequisite
## meant the client binaries were silently never copied.
push-multi:
	for h in $(CLIENTS); do \
		ssh $(SSH_OPTS) $(SSH_USER)@$$h "mkdir -p $(CLIENT_DIR)" && \
		scp $(SSH_OPTS) $(BENCH_BIN_DIR)/$(LOADGEN_BIN) $(SSH_USER)@$$h:$(CLIENT_DIR)/ ; \
	done

## Verify what is actually on each host before trusting a number. A
## stale remote binary produces real-looking results for code that is
## not under test, which is the most expensive failure mode available in
## this kind of work (exchange docs/spec.md §10.4).
checksums:
	@md5sum $(BIN_DIR)/$(NODE_BIN) $(BIN_DIR)/$(FIX_GATEWAY_BIN) $(BENCH_BIN_DIR)/$(LOADGEN_BIN) | sed 's|  .*/|  |'
	@for h in $(NODES); do \
		echo "-- $$h"; ssh $(SSH_OPTS) $(SSH_USER)@$$h \
			"cd $(NODE_DIR) && md5sum $(NODE_BIN) $(FIX_GATEWAY_BIN) 2>/dev/null"; \
	done
	@for h in $(CLIENTS); do \
		echo "-- $$h"; ssh $(SSH_OPTS) $(SSH_USER)@$$h \
			"cd $(CLIENT_DIR) && md5sum $(LOADGEN_BIN) 2>/dev/null"; \
	done

# PID files, not `pkill -f`: over ssh the remote shell executing this
# very command has the pattern in its own command line, so `pkill -f`
# kills the shell mid-script (exit 255, no output). The parameters are
# resolved by Make, not by the remote shell, because ../.env's
# NODE<n>_PRIV are Make variables and never reach the recipe's shell --
# an `eval echo \$NODE$${i}_PRIV` there reads empty and produces
# `--peer=:8300:0`, which is exactly what crashes braft's config parser.
define start_node
	ssh $(SSH_OPTS) $(SSH_USER)@$(1) \
		"cd $(NODE_DIR) && { [ -f $(NODE_BIN).pid ] && kill -TERM \$$(cat $(NODE_BIN).pid) 2>/dev/null; :; }; \
		 rm -f $(NODE_BIN).pid; sleep 1; \
		 $(ULIMIT_FDS) nohup ./$(NODE_BIN) --group=$(GROUP) --peer=$(2):$(PORT):0 --peers='$(PEERS)' \
			--election_timeout_ms=$(ELECTION_TIMEOUT_MS) --data_dir=$(NODE_DIR)/data \
			--raft_sync=$(SYNC) --event_dispatcher_num=$(EVENT_DISPATCHERS) \
			--bthread_concurrency=$(SERVER_CONCURRENCY) \
			--raft_max_parallel_append_entries_rpc_num=$(PIPELINE) \
			--raft_enable_append_entries_cache=$(AE_CACHE) \
			--raft_max_append_entries_cache_size=$(AE_CACHE_SIZE) \
			--raft_leader_batch=$(LEADER_BATCH) --raft_apply_batch=$(APPLY_BATCH) \
			--raft_fsm_caller_commit_batch=$(FSM_COMMIT_BATCH) \
			--raft_max_segment_size=$(SEGMENT_SIZE) \
			--logtostderr --logbufsecs=0 \
			> $(NODE_DIR)/std.log 2>&1 < /dev/null & echo \$$! > $(NODE_BIN).pid"
endef

## One node per NODE[123].
start:
	$(call start_node,$(NODE1),$(NODE1_PRIV))
	$(call start_node,$(NODE2),$(NODE2_PRIV))
	$(call start_node,$(NODE3),$(NODE3_PRIV))
	@sleep 3

## $(FIX_GATEWAYS) gateways per host in $(FIX_NODES_PUBLIC), on
## consecutive ports from $(FIX_PORT). Each tails the journal of the
## node it sits on, so it must be colocated.
##
## --inline_designated_outputs is not offered by exchange_fix_gateway at
## all, so there is no flag for it here: that mode is sound only where
## every output a session receives originates in its own inputs, and a
## PASSIVE FILL is exactly not that. sequencer's counter can measure
## both arms; an exchange cannot.
start-fix:
	@for h in $(FIX_NODES_PUBLIC); do \
		$(MAKE) --no-print-directory start-fix-on FIX_HOST=$$h; \
	done

start-fix-on:
	ssh $(SSH_OPTS) $(SSH_USER)@$(FIX_HOST) \
		"cd $(NODE_DIR) && for i in \$$(seq 0 \$$(( $(FIX_GATEWAYS) - 1 ))); do \
		   p=\$$(( $(FIX_PORT) + \$$i )); \
		   rm -rf fix_resume_\$$i fix_seq_\$$i; \
		   $(ULIMIT_FDS) nohup env $(FIX_ENV) ./$(FIX_GATEWAY_BIN) --node_peers=$(NODE1_PRIV):$(PORT) \
			--listen_port=\$$p --data_dir=$(NODE_DIR)/data \
			--resume_file=$(NODE_DIR)/fix_resume_\$$i \
			--sequence_store_dir=$(NODE_DIR)/fix_seq_\$$i \
			--logtostderr --logbufsecs=0 \
			> $(NODE_DIR)/fix_gateway_\$$i.log 2>&1 < /dev/null & \
		   echo \$$! > $(FIX_GATEWAY_BIN)_\$$i.pid; \
		 done"
	@sleep 3

## Create the instrument. Required before any order is admissible -- an
## order for an unknown symbol is rejected, and a sweep against a
## cluster with no instrument measures nothing but reject encoding.
## Idempotent in effect: a second call is rejected (InstrumentExists)
## and leaves the first one standing, so re-running is harmless.
add-instrument:
	ssh $(SSH_OPTS) $(SSH_USER)@$(NODE1) \
		"cd $(NODE_DIR) && ./$(ADMIN_BIN) add-instrument --node_peers=$(NODE1_PRIV):$(PORT) \
			--symbol=$(SYMBOL) --tick=$(TICK) --lot=$(LOT) --max_qty=$(MAX_QTY) 2>/dev/null"

## Kills by matching the running command line as well as by pid file: a
## stale or missing pid file left a gateway holding its binary and the
## next push failed with "Text file busy".
stop-fix:
	@for h in $(FIX_NODES_PUBLIC); do \
		ssh $(SSH_OPTS) $(SSH_USER)@$$h \
		 "ps -eo pid,args | grep '$(FIX_GATEWAY_BIN) --node_peers' | grep -v grep \
		  | awk '{print \$$1}' | while read p; do kill \$$p 2>/dev/null; done; \
		  cd $(NODE_DIR) && rm -f $(FIX_GATEWAY_BIN)_*.pid; :" 2>/dev/null; \
	done
	@sleep 1

stop:
	@for h in $(NODES); do \
		ssh $(SSH_OPTS) $(SSH_USER)@$$h \
		 "cd $(NODE_DIR) && { [ -f $(NODE_BIN).pid ] && kill -TERM \$$(cat $(NODE_BIN).pid) 2>/dev/null; :; }; \
		  rm -f $(NODE_BIN).pid; :" 2>/dev/null; \
	done

## Wipe the journal, raft log and snapshots on every node. The
## instrument lives in that state, so `add-instrument` must be re-run
## after this.
clean-data:
	@for h in $(NODES); do \
		ssh $(SSH_OPTS) $(SSH_USER)@$$h "rm -rf $(NODE_DIR)/data $(NODE_DIR)/fix_resume_* $(NODE_DIR)/fix_seq_*"; \
	done

## One client box, one rate. What it measures is a NewOrderSingle to its
## first ExecutionReport, from the journal (exchange docs/spec.md §7).
client:
	ssh $(SSH_OPTS) $(SSH_USER)@$(CLIENT) \
		"cd $(CLIENT_DIR) && ./$(LOADGEN_BIN) \
			--fix_gateway_addr=$(subst $(SPACE),$(COMMA),$(FIX_ENDPOINTS)) \
			--symbol=$(SYMBOL) --client_id=1 \
			--mode $(MODE) --pace $(PACE) --rate $(RATE) --burst $(BURST) \
			--warmup $(WARMUP) --measure $(MEASURE) --drain_timeout $(DRAIN_TIMEOUT) --logtostderr"

SWEEP_WARMUP    ?= 10
SWEEP_MEASURE   ?= 30
SWEEP_FIX_CSV   ?= exchange-fix.csv
SWEEP_FIX_RATES ?= 10000 25000 50000 75000 100000 150000 200000

## Every client box needs a DISTINCT --client_id: it goes in the high
## bits of every ClOrdID, and the exchange rejects a duplicate live
## ClOrdID from one CompID. Sharing one would turn most of a sweep into
## rejects, which still complete and so still look like a measurement.
FIX_CLIENT_CMD = ./$(LOADGEN_BIN) \
	--fix_gateway_addr=$(subst $(SPACE),$(COMMA),$(FIX_ENDPOINTS)) \
	--fix_sender_comp_id=FIX{CLIENT} \
	--client_id={CLIENT} \
	--symbol=$(SYMBOL) \
	--mode open --pace $(PACE) --rate {RATE} --burst $(BURST) \
	--warmup $(SWEEP_WARMUP) --measure $(SWEEP_MEASURE) \
	--drain_timeout $(DRAIN_TIMEOUT) --hdr_raw_out {RAW} --logtostderr

## The knee ladder, driven from all client boxes at once. Multi-client
## is not optional: one sender tops out well below the rates of
## interest, so a single box measures the rig rather than the exchange.
## Percentiles are merged across clients by sweep/merge-hdr.py, because
## percentiles cannot be averaged and a straggler client is invisible in
## an average.
sweep-fix:
	echo 'product,rate,achieved,p50,p90,p99,p999,max,dropped,lag,fleet' > $(SWEEP_FIX_CSV)
	CLIENTS="$(CLIENTS)" SSH_KEY=$(SSH_KEY) CLIENT_DIR=$(CLIENT_DIR) FLEET="$(FLEET)" \
		CLIENT_CMD="$(FIX_CLIENT_CMD)" \
		../sweep/sweep-multi.sh exchange-fix . "$(abspath $(SWEEP_FIX_CSV))" \
		$(SWEEP_WARMUP) $(SWEEP_MEASURE) "" $(SWEEP_FIX_RATES)

CHART_DIR ?= ..
chart:
	python3 ../sweep/mkcharts.py $(SWEEP_FIX_CSV) $(CHART_DIR)

logs:
	ssh $(SSH_OPTS) $(SSH_USER)@$(NODE1) "tail -n 100 -f $(NODE_DIR)/std.log"

logs-fix:
	ssh $(SSH_OPTS) $(SSH_USER)@$(NODE1) "tail -n 100 -f $(NODE_DIR)/fix_gateway_0.log"

## Which node believes it is leader. The FIX gateways all propose to
## NODE1, so a leader elsewhere adds a redirect hop to every order.
leader:
	@for h in $(NODES); do \
		echo "-- $$h"; ssh $(SSH_OPTS) $(SSH_USER)@$$h "curl -s 127.0.0.1:$(PORT)/raft_stat | head -12"; \
	done
