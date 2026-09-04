# Product-specific targets for sequencer (https://github.com/olegabu/sequencer),
# a raft-based ordering/journaling library — not a bespoke atomic-counter
# demo like braft/openraft/aeron here, but a real application framework;
# APP selects which of its examples/ this measures (default: its one
# example so far, "counter" — see examples/APP/README.md in sequencer's
# own repo for what each one submits/returns). Provision the EC2 fleet
# first with the root Makefile (`make deploy` from the repo root); this
# Makefile only builds/pushes/runs against whatever that created.
-include ../.env
include ../common.mk

APP ?= counter
# Path to a sibling checkout of https://github.com/olegabu/sequencer.
# Override if yours isn't a sibling of this repo.
SEQUENCER_DIR ?= ../../sequencer

PORT              ?= 8300
INPUT_GATEWAY_PORT ?= 8400
GROUP             ?= sequencer
ELECTION_TIMEOUT_MS ?= 1000

# braft's own tuning, at the values ../braft/README.md's optimization
# trials found — not braft's defaults. These are braft's own gflags
# (registered by libbraft itself at static-init time, so counter_node
# gets every one of them for free just by linking it, no code of
# sequencer's own involved — see specification.md's own account of
# checking this directly) — variable names and defaults deliberately
# identical to ../braft/Makefile's, so `make start PIPELINE=4` means
# the same thing in both directories and a side-by-side comparison
# is actually apples to apples, not "tuned vs untuned" by accident.
# See ../braft/README.md's "What fixed braft's tail" (AE_CACHE) and
# "Deciding PIPELINE" for what each one is and why this value.
SYNC               ?= false
EVENT_DISPATCHERS  ?= 1
SERVER_CONCURRENCY ?= 18
PIPELINE           ?= 8
AE_CACHE           ?= true
AE_CACHE_SIZE      ?= 8
LEADER_BATCH       ?= 256
APPLY_BATCH        ?= 32
FSM_COMMIT_BATCH   ?= 512
SEGMENT_SIZE       ?= 8388608
TRACE_LAT          ?= false
HIGH_LAT_US        ?= 1000000

# One relay is enough for this benchmark (RelayObserver only ever
# subscribes to one), so it runs colocated with NODE1 only, not one
# per node the way a real deployment would (specification.md §3.3) —
# NODE1 is the node multi_az.tfvars already puts in the client's own
# AZ and placement group, so this is also the lowest-latency choice.
# Ports match gateway/relay/README.md's own worked example.
RELAY_LISTEN_PORT ?= 8500
RELAY_GRPC_PORT   ?= 8501
# Empty (the default) leaves the relay binary's own gflag default
# (1024, gateway/relay/src/relay_grpc_service_impl.hpp) in place —
# override to sweep batch-size sensitivity, e.g.
# RELAY_MAX_BATCH_RECORDS=256.
RELAY_MAX_BATCH_RECORDS ?=
RELAY_MAX_BATCH_FLAG = $(if $(RELAY_MAX_BATCH_RECORDS),--relay_max_batch_records=$(RELAY_MAX_BATCH_RECORDS),)
# Same idea, for the pump thread's busy-spin idle wait (default 2000,
# see relay_grpc_service_impl.hpp) — override to sweep CPU-vs-latency
# sensitivity, e.g. RELAY_IDLE_SPIN_ITERATIONS=0 for "never busy-spin."
RELAY_IDLE_SPIN_ITERATIONS ?=
RELAY_IDLE_SPIN_FLAG = $(if $(RELAY_IDLE_SPIN_ITERATIONS),--relay_idle_spin_iterations=$(RELAY_IDLE_SPIN_ITERATIONS),)

# The output-gateway counterpart to the relay above: the fourth round
# trip (bench/load_generator/README.md), colocated with NODE1 for the
# same reason. Empty (the default) starts none of the three flavors —
# set to brpc/grpc/websocket to bring one up; only one runs at a time
# on the fleet (all three share OUTPUT_GATEWAY_PORT and one pidfile),
# matching how only one relay runs — nothing in the code forces this
# (examples/counter/load_generator_main.cpp's --output_observer and
# --relay_grpc_addr are independent and combinable), it's just what
# this fleet's single-relay precedent already establishes as enough
# for one benchmark run.
# One binary now serves all three protocols at once, each on its own
# port (sequencer's examples/counter/output_gateway_main.cpp), so
# OUTPUT_GATEWAY_FLAVOR no longer selects a binary — it selects only
# which flavor the load generator OBSERVES. All three are always up,
# which is what lets a single sweep compare them against identical
# traffic instead of three separate cluster lifetimes.
OUTPUT_GATEWAY_FLAVOR ?=
OUTPUT_BRPC_PORT      ?= 8600
OUTPUT_GRPC_PORT      ?= 8601
OUTPUT_WEBSOCKET_PORT ?= 8602
OUTPUT_GATEWAY_BIN    := $(APP)_output_gateway

# The FIX session gateway (sequencer specification.md 8.12). Unlike the
# input gateway it runs on NODE1, not on a client box: it is a SESSION
# gateway, so its output half tails the journal and must be colocated
# with a replica, exactly as the output gateway is. Clients reach it
# over the network.
#
# FIX_SESSIONS is how many concurrent FIX sessions each client opens.
# Throughput scales with sessions because FixInputTransport runs a
# reader thread per connection -- measured locally, one session carried
# ~20k/s and three carried ~45k/s through one gateway.
FIX_GATEWAY_BIN  := $(APP)_fix_gateway

## The QuickFIX-backed gateway (specification.md 8.13). Same codecs,
## same journal, same clients -- QuickFIX's session layer instead of
## ours, so a like-for-like sweep is the only way to price it.
QUICKFIX_GATEWAY_BIN := $(APP)_quickfix_gateway
## QuickFIX 1.15.1 has no dynamic acceptor sessions, so every client's
## CompID must be declared before it connects -- the one configuration
## the hffix gateway does not need, since it adopts identity from the
## Logon.
##
## DERIVED, not typed out: the load generator names each session
## "<fix_sender_comp_id>-<index>", one per address in
## --fix_gateway_addr, so the real CompIDs are FIX0-0, FIX0-1, ... A
## hand-written list of FIX0..FIX4 looked right and matched nothing;
## every client failed to log on and the sweep reported an achieved
## rate of zero, with the gateway healthy and listening the whole time.
QUICKFIX_CLIENT_COMP_IDS = $(subst $(SPACE),$(COMMA),$(foreach c,$(shell seq 0 $$(( $(words $(CLIENTS)) - 1 ))),$(foreach i,$(shell seq 0 $$(( $(FIX_ENDPOINT_COUNT) - 1 ))),FIX$(c)-$(i))))

## Environment forwarded into the remote gateway process. `make` does not
## carry its own environment across ssh, so FIX_STAGE_TIMERS=1 on the
## command line reached the local make and nothing else -- a whole run was
## measured with the timers silently off. Pass it explicitly:
##   make start-fix FIX_STAGE_TIMERS=1
FIX_ENV := $(if $(FIX_STAGE_TIMERS),FIX_STAGE_TIMERS=$(FIX_STAGE_TIMERS),)

## QUICKFIX_SOCKET_NODELAY=1 turns TCP_NODELAY on for the QuickFIX
## gateway. Off by default and deliberately so -- QuickFIX writes one
## message per socket write, so un-Nagling multiplies its packet rate
## instead of removing a stall; see the long comment in
## gateway/quickfix/input/src/quickfix_input_transport.cpp. The flag
## exists so that trade can be RE-MEASURED on current code rather than
## trusted from a comment.
## Raise the open-file soft limit before exec'ing anything that reads
## or writes a log.
##
## braft keeps ONE OPEN FD PER LOG SEGMENT, and with its snapshot
## interval left at the 3600s default (deliberately -- it matches
## braft's own harness, see sequencer/README.md) the log never
## truncates during a sweep. A long ladder therefore accumulates
## segments until the process runs out of descriptors:
##
##   E log.cpp:1203] Fail to close old open_segment or create new
##     open_segment path: /data/counter/data/log: Too many open files [24]
##   E raft error: {type=LogError, error_code=5, error_text=`Fail to
##     append entries'}
##   W node ... is not in active state current_term 2 state ERROR
##
## braft LATCHES the node into ERROR at that point -- it does not
## recover -- so every rate measured after it silently returns zero
## completions. That is what truncated the combined output sweep: the
## node died 1008 log segments in, during brpc's post-knee rates, and
## all seventeen WebSocket rates afterwards recorded nothing. It is
## also the most likely explanation for the older seam in
## seq-output-multi.csv, where brpc and gRPC came from one cluster
## lifetime and WebSocket from another.
##
## The soft limit was the default 1024 against a hard limit of
## 1048576, so this needs no privilege -- just asking.
ULIMIT_FDS := ulimit -n 65536;

QUICKFIX_SOCKET_NODELAY ?=
QUICKFIX_ENV := $(if $(QUICKFIX_SOCKET_NODELAY),QUICKFIX_SOCKET_NODELAY=$(QUICKFIX_SOCKET_NODELAY),)

## Answer from the propose receipt instead of the journal (see the
## gateway's --inline_designated_outputs). Default false = §8.11.
FIX_INLINE ?= false

## The product name rows are written under. The two delivery paths are
## charted as separate curves, so they must not share a name: with
## FIX_INLINE=true the same gateway answers from the propose receipt
## instead of the journal, which is a different round trip.
FIX_PRODUCT ?= $(if $(filter true,$(FIX_INLINE)),sequencer-fix-inline,sequencer-fix)
FIX_PORT         ?= 8700
FIX_SESSIONS     ?= 3

# How many FIX gateway processes to run on NODE1, on consecutive ports
# from FIX_PORT. Clients are spread across them round-robin.
#
# More than one exists because a session gateway has a SINGLE ring
# reader thread delivering to every session it serves -- that is
# deliberate, since specification.md 8.11 requires a session's outputs
# in journal order and independent readers could interleave them. It
# also makes that thread a serialization point: measured ~11us per
# entry in deliver(), so one reader saturates a core somewhere around
# 100k messages/sec. Splitting sessions across processes gives each its
# own reader without weakening the ordering guarantee, because ordering
# is per session and a session lives on exactly one gateway.
#
# The cost is that each gateway independently tails the journal and
# runs the codec, so NODE1 does that work once per gateway.
FIX_GATEWAYS     ?= 1

# Which nodes run FIX gateways. NODE1 only by default.
#
# A gateway's OUTPUT half may attach to any replica (specification.md
# 3.3), so followers can host one and tail their own journal. Its INPUT
# half cannot: proposals go to the LEADER, so a gateway on NODE2 or
# NODE3 pays a cross-AZ hop per proposal -- measured ~500us RTT on this
# fleet -- and reads a journal that lags the leader's by replication
# time. Spreading buys ring-reader capacity and costs latency, which is
# a real trade rather than a free win.
FIX_NODES        ?= $(NODE1_PRIV)
ifeq ($(OUTPUT_GATEWAY_FLAVOR),brpc)
OUTPUT_GATEWAY_PORT := $(OUTPUT_BRPC_PORT)
else ifeq ($(OUTPUT_GATEWAY_FLAVOR),grpc)
OUTPUT_GATEWAY_PORT := $(OUTPUT_GRPC_PORT)
else ifeq ($(OUTPUT_GATEWAY_FLAVOR),websocket)
OUTPUT_GATEWAY_PORT := $(OUTPUT_WEBSOCKET_PORT)
else ifneq ($(OUTPUT_GATEWAY_FLAVOR),)
$(error OUTPUT_GATEWAY_FLAVOR must be empty, brpc, grpc, or websocket, got "$(OUTPUT_GATEWAY_FLAVOR)")
endif
# Ring sizing and idle behaviour for the output gateway's per-subscriber
# readers (sequencer's gateway/output/README.md, "Delivery: one ring,
# one reader per subscriber"). Empty leaves each binary's own gflag
# default in place. --batch_window_us used to live here; it was an
# artificial flush delay that the per-subscriber reader design made
# obsolete, and the flag no longer exists.
OUTPUT_RING_SLOTS ?=
OUTPUT_RING_SLOTS_FLAG = $(if $(OUTPUT_RING_SLOTS),--ring_slots=$(OUTPUT_RING_SLOTS),)
OUTPUT_IDLE_SPIN ?=
OUTPUT_IDLE_SPIN_FLAG = $(if $(OUTPUT_IDLE_SPIN),--idle_spin_iterations=$(OUTPUT_IDLE_SPIN),)
OUTPUT_TUNING_FLAGS = $(OUTPUT_RING_SLOTS_FLAG) $(OUTPUT_IDLE_SPIN_FLAG)

# Phase 1 (see sequencer's specification.md §3): measures exactly the
# same round trip raft-tests/braft/client.cpp does — submit to
# synchronous receipt — just one hop longer, through sequencer's own
# input gateway rather than straight to the raft group, because that
# hop is part of what's being measured here (§3.3: "clients should
# submit through an input gateway, never directly to a node's
# Propose"). See sequencer's own bench/load_generator/README.md for
# the other two round trips this repo's own README asks every product
# to report, and why they need a different rig than this one.
# brpc's own gflags on the input gateway process. The node gets
# SERVER_CONCURRENCY/EVENT_DISPATCHERS above; the gateway historically
# got nothing, i.e. brpc's defaults. Exposed because the gateway's
# Submit handler blocks a bthread for a full node round trip per
# request (gateway/input/src/submit_service_impl.hpp), which makes its
# concurrency ceiling a plausible throughput limit worth sweeping.
INPUT_GATEWAY_CONCURRENCY ?=
INPUT_GATEWAY_CONCURRENCY_FLAG = $(if $(INPUT_GATEWAY_CONCURRENCY),--bthread_concurrency=$(INPUT_GATEWAY_CONCURRENCY),)
# Proposal batching bounds (sequencer's gateway/input/src/node_proposer.hpp).
# Empty keeps the binary's own defaults. Both matter: unbounded batch
# size or a single in-flight batch each destroy throughput, for
# different reasons documented there.
INPUT_MAX_BATCH ?=
INPUT_MAX_INFLIGHT_BATCHES ?=
INPUT_BATCH_FLAGS = $(if $(INPUT_MAX_BATCH),--max_batch_size=$(INPUT_MAX_BATCH),) \
                    $(if $(INPUT_MAX_INFLIGHT_BATCHES),--max_inflight_batches=$(INPUT_MAX_INFLIGHT_BATCHES),)
# Escape hatch for any other brpc gflag on the gateway process, e.g.
# INPUT_GATEWAY_EXTRA=--event_dispatcher_num=4.
INPUT_GATEWAY_EXTRA ?=

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

PEERS      = $(NODE1_PRIV):$(PORT):0,$(NODE2_PRIV):$(PORT):0,$(NODE3_PRIV):$(PORT):0
NODE_DIR   = /data/$(APP)
CLIENT_DIR = $(APP)

.PHONY: build push push-multi start start-multi start-fix sweep-fix start-quickfix start-quickfix-on push-quickfix stop-quickfix sweep-quickfix stop stop-fix stop-multi clean-data client client-relay client-output client-fix logs-fix transfer-leader leader logs logs-relay logs-output

## Build sequencer's Release preset (this is a benchmarking repo — see
## sequencer's own README's debug-vs-release note); requires a sibling
## checkout at $(SEQUENCER_DIR).
build:
	cd $(SEQUENCER_DIR) && cmake --preset release && cmake --build --preset release

## Copy this example's binaries and this Makefile's own run/stop
## helpers to all instances — the input gateway and load generator
## both go on the client box; sequencer's own input gateways are pure
## network relays (specification.md §3.3), never colocated with a node
## in a real deployment, and there is no separate "gateway" box in this
## repo's fixed 3-node+1-client topology. sequencer_relay (a stock
## binary — see gateway/relay/README.md, nothing example-specific
## about it) goes to NODE1 only, per RELAY_LISTEN_PORT's own comment.
push:
	for h in $(NODES); do \
		ssh $(SSH_OPTS) $(SSH_USER)@$$h "mkdir -p $(NODE_DIR)" && \
		scp $(SSH_OPTS) $(SEQUENCER_DIR)/build/release/examples/$(APP)/$(APP)_node $(SSH_USER)@$$h:$(NODE_DIR)/ ; \
	done
	scp $(SSH_OPTS) $(SEQUENCER_DIR)/build/release/gateway/relay/sequencer_relay $(SSH_USER)@$(NODE1):$(NODE_DIR)/
	# One binary now, serving whichever transports its --*_port flags
	# enable. The three single-transport binaries it replaced were
	# deleted upstream; this used to copy all four, three of which no
	# sweep here ever ran.
	-scp $(SSH_OPTS) $(SEQUENCER_DIR)/build/release/examples/$(APP)/$(APP)_output_gateway \
		$(SSH_USER)@$(NODE1):$(NODE_DIR)/
	# The FIX gateway goes to EVERY node, not just NODE1: its output half
	# may attach to any replica (specification.md 3.3), so a deployment
	# can spread session gateways across the group. Only NODE1 got it at
	# first, and starting one elsewhere failed with a missing binary.
	for h in $(NODE1) $(NODE2) $(NODE3); do \
		scp $(SSH_OPTS) $(SEQUENCER_DIR)/build/release/examples/$(APP)/$(APP)_fix_gateway \
			$(SSH_USER)@$$h:$(NODE_DIR)/ ; \
	done
	# The QuickFIX gateway travels with the hffix one, to every node,
	# for the same reason. Only start-quickfix used to push it, so a
	# plain `make push` left whatever binary the last sweep happened to
	# leave behind -- on NODE1 a stale one, on NODE2/NODE3 none at all.
	# A stale remote binary is the single most expensive mistake
	# available here: it produces real-looking numbers for code that is
	# not the code under test.
	for h in $(NODE1) $(NODE2) $(NODE3); do \
		scp $(SSH_OPTS) $(SEQUENCER_DIR)/build/release/examples/$(APP)/$(APP)_quickfix_gateway \
			$(SSH_USER)@$$h:$(NODE_DIR)/ ; \
	done
	ssh $(SSH_OPTS) $(SSH_USER)@$(CLIENT) "mkdir -p $(CLIENT_DIR)"
	scp $(SSH_OPTS) $(SEQUENCER_DIR)/build/release/examples/$(APP)/$(APP)_input_gateway \
		$(SEQUENCER_DIR)/build/release/examples/$(APP)/$(APP)_load_generator \
		$(SSH_USER)@$(CLIENT):$(CLIENT_DIR)/

## Start one node per NODE[123], sequencer_relay colocated with NODE1
## (reading that same node's own data_dir), and the input gateway on
## the client box.
##
## PID files, not `pkill -f <name>`: over ssh, the *remote shell
## process executing this very command* has that name as a literal
## substring of its own command line (the whole quoted script we sent
## it) -- `pkill -f` matches and kills that shell mid-script, not just
## a stale prior instance, aborting the whole ssh session (exit 255,
## no output — reproduced directly against a live host). `pkill -x`
## would dodge that but re-breaks on counter_input_gateway specifically:
## 22 characters, longer than the kernel's 15-char process-name field,
## so it never matches the truncated name either. PID files sidestep
## both.
# Takes (public IP, private IP) and starts that one node. A canned
# recipe with Make-level parameters, not a shell for-loop indexing
# into NODE1_PRIV/NODE2_PRIV/NODE3_PRIV by number: those only exist as
# Make variables (from -include ../.env), never exported to the
# recipe's own shell, so a shell-side `eval echo \$NODE${i}_PRIV`
# silently reads nothing — reproduced directly: priv='' — and
# `--peer=:8300:0` is exactly what crashed braft's own config parser
# on a live host. $(1)/$(2) below are resolved by Make itself, so
# there is no runtime lookup left to get wrong.
define start_node
	ssh $(SSH_OPTS) $(SSH_USER)@$(1) \
		"cd $(NODE_DIR) && { [ -f $(APP)_node.pid ] && kill -TERM \$$(cat $(APP)_node.pid) 2>/dev/null; :; }; \
		 rm -f $(APP)_node.pid; sleep 1; \
		 $(ULIMIT_FDS) nohup env $(NODE_ENV) ./$(APP)_node --group=$(GROUP) --peer=$(2):$(PORT):0 --peers='$(PEERS)' \
			--election_timeout_ms=$(ELECTION_TIMEOUT_MS) --data_dir=$(NODE_DIR)/data \
			--raft_sync=$(SYNC) --event_dispatcher_num=$(EVENT_DISPATCHERS) \
			--bthread_concurrency=$(SERVER_CONCURRENCY) \
			--raft_max_parallel_append_entries_rpc_num=$(PIPELINE) \
			--raft_enable_append_entries_cache=$(AE_CACHE) \
			--raft_max_append_entries_cache_size=$(AE_CACHE_SIZE) \
			--raft_leader_batch=$(LEADER_BATCH) --raft_apply_batch=$(APPLY_BATCH) \
			--raft_fsm_caller_commit_batch=$(FSM_COMMIT_BATCH) \
			--raft_max_segment_size=$(SEGMENT_SIZE) \
			--raft_trace_append_entry_latency=$(TRACE_LAT) \
			--raft_append_entry_high_lat_us=$(HIGH_LAT_US) \
			$(JOURNAL_GEOMETRY_FLAGS) \
			--logtostderr --logbufsecs=0 \
			> $(NODE_DIR)/std.log 2>&1 < /dev/null & echo \$$! > $(APP)_node.pid"
endef

start:
	$(call start_node,$(NODE1),$(NODE1_PRIV))
	$(call start_node,$(NODE2),$(NODE2_PRIV))
	$(call start_node,$(NODE3),$(NODE3_PRIV))
	sleep 3
	ssh $(SSH_OPTS) $(SSH_USER)@$(NODE1) \
		"cd $(NODE_DIR) && { [ -f sequencer_relay.pid ] && kill -TERM \$$(cat sequencer_relay.pid) 2>/dev/null; :; }; \
		 rm -f sequencer_relay.pid; sleep 1; \
		 $(ULIMIT_FDS) nohup ./sequencer_relay --data_dir=$(NODE_DIR)/data --listen_port=$(RELAY_LISTEN_PORT) \
			--grpc_listen_port=$(RELAY_GRPC_PORT) $(RELAY_MAX_BATCH_FLAG) $(RELAY_IDLE_SPIN_FLAG) --logtostderr --logbufsecs=0 \
			> $(NODE_DIR)/relay.log 2>&1 < /dev/null & echo \$$! > sequencer_relay.pid"
	ssh $(SSH_OPTS) $(SSH_USER)@$(CLIENT) \
		"cd $(CLIENT_DIR) && { [ -f $(APP)_input_gateway.pid ] && kill -TERM \$$(cat $(APP)_input_gateway.pid) 2>/dev/null; :; }; \
		 rm -f $(APP)_input_gateway.pid; sleep 1; \
		 $(ULIMIT_FDS) nohup ./$(APP)_input_gateway --node_peers='$(NODE1_PRIV):$(PORT),$(NODE2_PRIV):$(PORT),$(NODE3_PRIV):$(PORT)' \
			--listen_port=$(INPUT_GATEWAY_PORT) $(INPUT_GATEWAY_CONCURRENCY_FLAG) $(INPUT_BATCH_FLAGS) $(INPUT_GATEWAY_EXTRA) \
			--logtostderr --logbufsecs=0 \
			> std.log 2>&1 < /dev/null & echo \$$! > $(APP)_input_gateway.pid"
ifneq ($(OUTPUT_GATEWAY_FLAVOR),)
	ssh $(SSH_OPTS) $(SSH_USER)@$(NODE1) \
		"cd $(NODE_DIR) && { [ -f $(APP)_output_gateway.pid ] && kill -TERM \$$(cat $(APP)_output_gateway.pid) 2>/dev/null; :; }; \
		 rm -f $(APP)_output_gateway.pid; sleep 1; \
		 $(ULIMIT_FDS) nohup env $(OUTPUT_ENV) ./$(OUTPUT_GATEWAY_BIN) --data_dir=$(NODE_DIR)/data \
			--resume_file=$(NODE_DIR)/output_resume_all \
			--brpc_port=$(OUTPUT_BRPC_PORT) --grpc_port=$(OUTPUT_GRPC_PORT) \
			--websocket_port=$(OUTPUT_WEBSOCKET_PORT) $(OUTPUT_TUNING_FLAGS) \
			--logtostderr --logbufsecs=0 \
			> $(NODE_DIR)/output_gateway.log 2>&1 < /dev/null & echo \$$! > $(APP)_output_gateway.pid"
endif

## Start the FIX session gateway on NODE1, beside the node whose
## journal it tails. Separate from `start` because a FIX sweep is its
## own experiment -- it replaces the input gateway AND the output
## gateway with one binary, so running it alongside them would only
## confuse which path a measurement came from.
## Starts FIX_GATEWAYS gateways on each host in FIX_NODES_PUBLIC.
##
## RUN `clean-data` FIRST, always. This wipes each gateway's resume
## position, so a gateway tails the journal FROM THE BEGINNING -- which
## is right for a fresh experiment and catastrophic against a journal
## that has history. Measured against a 3.9GB journal left by earlier
## sweeps, the tail loop replayed 1.3 MILLION records/sec into the ring,
## every read returned Overrun, the reader's cursor reset continuously,
## and no live traffic was delivered at all. It looked exactly like the
## gateway collapsing under load, and it is what made the first rate of
## several sweeps fail while the rest passed.
start-fix:
	@for h in $(FIX_NODES_PUBLIC); do \
		$(MAKE) --no-print-directory start-fix-on FIX_HOST=$$h; \
	done
	@echo "$(FIX_GATEWAYS) gateway(s) on each of: $(FIX_NODES_PUBLIC)"

start-quickfix: push-quickfix
	@for h in $(FIX_NODES_PUBLIC); do \
		$(MAKE) --no-print-directory start-quickfix-on FIX_HOST=$$h; \
	done
	@echo "$(FIX_GATEWAYS) quickfix gateway(s) on each of: $(FIX_NODES_PUBLIC)"

## RUN `clean-data` FIRST, always: this removes each gateway's resume
## file, so a gateway started against an accumulated journal replays it
## from record 1 at full speed and floods its own ring.
start-quickfix-on:
	ssh $(SSH_OPTS) $(SSH_USER)@$(FIX_HOST) \
		"cd $(NODE_DIR) && for i in \$$(seq 0 \$$(( $(FIX_GATEWAYS) - 1 ))); do \
		   p=\$$(( $(FIX_PORT) + \$$i )); \
		   rm -rf qfix_resume_\$$i qfix_seq_\$$i; \
		   $(ULIMIT_FDS) nohup env $(QUICKFIX_ENV) ./$(QUICKFIX_GATEWAY_BIN) --node_peers=$(NODE1_PRIV):$(PORT) \
			--listen_port=\$$p --data_dir=$(NODE_DIR)/data \
			--resume_file=$(NODE_DIR)/qfix_resume_\$$i \
			--sequence_store_dir=$(NODE_DIR)/qfix_seq_\$$i \
			--client_comp_ids=$(QUICKFIX_CLIENT_COMP_IDS) \
			--logtostderr --logbufsecs=0 \
			> $(NODE_DIR)/quickfix_gateway_\$$i.log 2>&1 < /dev/null & \
		   echo \$$! > $(APP)_quickfix_gateway_\$$i.pid; \
		 done"
	@sleep 3

push-quickfix:
	@for h in $(FIX_NODES_PUBLIC); do \
		scp $(SSH_OPTS) $(SEQUENCER_DIR)/build/release/examples/$(APP)/$(APP)_quickfix_gateway \
			$(SSH_USER)@$$h:$(NODE_DIR)/ ; \
	done

stop-quickfix:
	@for h in $(FIX_NODES_PUBLIC); do \
		ssh $(SSH_OPTS) $(SSH_USER)@$$h \
		"cd $(NODE_DIR) && for f in $(APP)_quickfix_gateway_*.pid; do \
		   [ -f \$$f ] && kill -TERM \$$(cat \$$f) 2>/dev/null; done; \
		 rm -f $(APP)_quickfix_gateway_*.pid; sleep 1; :" ; \
	done

## The quickfix arm's sweep. Same script, same client command, same
## rates as sweep-fix -- only the product name differs, so the two land
## as separate curves on one chart.
SWEEP_QUICKFIX_CSV ?= quickfix.csv
sweep-quickfix:
	echo 'product,rate,achieved,p50,p90,p99,p999,max,dropped,lag,fleet' > $(SWEEP_QUICKFIX_CSV)
	CLIENTS="$(CLIENTS)" SSH_KEY=$(SSH_KEY) CLIENT_DIR=$(CLIENT_DIR) FLEET="$(FLEET)" \
		CLIENT_CMD="$(FIX_CLIENT_CMD)" \
		../sweep/sweep-multi.sh sequencer-quickfix . "$(abspath $(SWEEP_QUICKFIX_CSV))" \
		$(SWEEP_WARMUP) $(SWEEP_MEASURE) "" $(SWEEP_FIX_RATES)

start-fix-on:
	ssh $(SSH_OPTS) $(SSH_USER)@$(FIX_HOST) \
		"cd $(NODE_DIR) && for i in \$$(seq 0 \$$(( $(FIX_GATEWAYS) - 1 ))); do \
		   p=\$$(( $(FIX_PORT) + \$$i )); \
		   rm -rf fix_resume_\$$i fix_seq_\$$i; \
		   $(ULIMIT_FDS) nohup env $(FIX_ENV) ./$(FIX_GATEWAY_BIN) --node_peers=$(NODE1_PRIV):$(PORT) \
			--inline_designated_outputs=$(FIX_INLINE) \
			--listen_port=\$$p --data_dir=$(NODE_DIR)/data \
			--resume_file=$(NODE_DIR)/fix_resume_\$$i \
			--sequence_store_dir=$(NODE_DIR)/fix_seq_\$$i \
			--logtostderr --logbufsecs=0 \
			> $(NODE_DIR)/fix_gateway_\$$i.log 2>&1 < /dev/null & \
		   echo \$$! > $(APP)_fix_gateway_\$$i.pid; \
		 done"
	@sleep 3

## Kills by matching the running command line, not only by pid file: a
## stale or missing pid file left the gateway holding its binary and the
## next `push` failed with "Text file busy", which is easy to miss when
## push output is suppressed.
stop-fix:
	@for h in $(FIX_NODES_PUBLIC); do \
		ssh $(SSH_OPTS) $(SSH_USER)@$$h \
		 "ps -eo pid,args | grep '$(FIX_GATEWAY_BIN) --node_peers' | grep -v grep \
		  | awk '{print \$$1}' | while read p; do kill \$$p 2>/dev/null; done" 2>/dev/null; \
	done
	@sleep 1

stop-fix-node1:
	ssh $(SSH_OPTS) $(SSH_USER)@$(NODE1) \
		"ps -eo pid,args | grep '$(FIX_GATEWAY_BIN) --node_peers' | grep -v grep \
		 | awk '{print \$$1}' | while read p; do kill \$$p 2>/dev/null; done; \
		 cd $(NODE_DIR) && rm -f $(APP)_fix_gateway_*.pid; sleep 1; :"

logs-fix:
	ssh $(SSH_OPTS) $(SSH_USER)@$(NODE1) "tail -n 100 -f $(NODE_DIR)/fix_gateway.log"

## One client box driving the FIX gateway. Each client opens
## FIX_SESSIONS sessions with DISTINCT CompIDs -- distinct because a FIX
## session identity is the CompID pair, and two live connections
## claiming one identity are correctly refused by the gateway.
client-fix:
	ssh -t $(SSH_OPTS) $(SSH_USER)@$(CLIENT) \
		"cd $(CLIENT_DIR) && ./$(APP)_load_generator \
			--fix_gateway_addr=$(NODE1_PRIV):$(FIX_PORT) \
			--fix_sender_comp_id=FIXCLI \
			--mode open --pace $(PACE) --rate $(RATE) --burst $(BURST) \
			--warmup $(WARMUP) --measure $(MEASURE) --drain_timeout $(DRAIN_TIMEOUT) \
			--logtostderr"

## The FIX sweep, multi-client. One gateway on NODE1; every client box
## drives it with its own load generator, the offered rate split N ways,
## and latencies merged from every client's raw histogram rather than
## averaged (sweep/merge-hdr.py explains why averaging percentiles is
## wrong).
##
## Every client opens a session on EVERY gateway and spreads its
## requests across them, so each gateway carries an identical share.
## One session per client would split five clients over two gateways
## 3/2, and the merged latency would then blend a gateway at 60% of the
## load with one at 40% -- neither gateway's real number.
##
## Each client gets a distinct CompID and a distinct nonce base, and
## both matter. Distinct CompIDs because a FIX session identity is the
## CompID pair and the gateway refuses two live connections claiming
## one. Distinct nonce bases because every client subscribes to the SAME
## broadcast topic and therefore sees every other client's replies --
## without a per-client payload value they complete each other's
## requests, which reads as impossibly low latency rather than as an
## error. That produced a measured p50 of 75us against a fleet whose
## leader-to-follower RTT alone is 500us.
# Every gateway endpoint, host:port, in a stable order. A client picks
# one by its own index, so clients spread round-robin across gateways
# AND across nodes.
FIX_NODES_PUBLIC ?= $(NODE1)
COMMA := ,
EMPTY :=
SPACE := $(EMPTY) $(EMPTY)
FIX_ENDPOINTS = $(foreach h,$(FIX_NODES),$(foreach i,$(shell seq 0 $$(( $(FIX_GATEWAYS) - 1 ))),$(h):$(shell echo $$(( $(FIX_PORT) + $(i) )))))
FIX_ENDPOINT_COUNT = $(words $(FIX_ENDPOINTS))

SWEEP_FIX_CSV   ?= fix.csv
SWEEP_FIX_RATES ?= 10000 25000 50000 75000 100000 150000
FIX_CLIENT_CMD = ./$(APP)_load_generator \
	--fix_gateway_addr=$(subst $(SPACE),$(COMMA),$(FIX_ENDPOINTS)) \
	--fix_sender_comp_id=FIX{CLIENT} \
	--client_id={CLIENT} \
	--mode open --pace $(PACE) --rate {RATE} --burst $(BURST) \
	--warmup $(SWEEP_WARMUP) --measure $(SWEEP_MEASURE) \
	--drain_timeout $(DRAIN_TIMEOUT) --hdr_raw_out {RAW} --logtostderr

## The output-gateway sweep, driven from all five client boxes.
##
## The old single-box sweep-output.sh (deleted) drove from ONE box, and
## that turned out to be the
## thing the earlier charts were measuring: all three flavors "kneed" at
## exactly 145k with the load generator's own schedule lag jumping from
## ~100us to over half a second, which is a client falling over, not a
## gateway. FIX reached 250k on this rig with lag=1us because five boxes
## shared the offered rate.
##
## {RAW} goes to --output_hdr_raw_out, NOT --hdr_raw_out: the merge has
## to combine the OUTPUT path's histograms. --hdr_raw_out would merge
## the ack path and quietly report the wrong round trip.
##
## Each client submits through the input gateway in its own process
## space (start-multi) and observes ONE output gateway on NODE1, so the
## fan-out being measured is five subscribers against one gateway --
## the same shape as one FIX gateway serving five sessions.
OUTPUT_CLIENT_CMD = ./$(APP)_load_generator \
	--input_gateway_addr=127.0.0.1:$(INPUT_GATEWAY_PORT) \
	--client_id={CLIENT} \
	--output_observer=$(OUTPUT_GATEWAY_FLAVOR) \
	--output_gateway_addr=$(NODE1_PRIV):$(OUTPUT_SWEEP_PORT) \
	--mode open --pace $(PACE) --thread_num {THREADS} --rate {RATE} --burst $(BURST) \
	--warmup $(SWEEP_WARMUP) --measure $(SWEEP_MEASURE) \
	--drain_timeout $(DRAIN_TIMEOUT) --output_hdr_raw_out {RAW} --logtostderr

OUTPUT_SWEEP_PORT = $(if $(filter grpc,$(OUTPUT_GATEWAY_FLAVOR)),$(OUTPUT_GRPC_PORT),$(if $(filter websocket,$(OUTPUT_GATEWAY_FLAVOR)),$(OUTPUT_WEBSOCKET_PORT),$(OUTPUT_BRPC_PORT)))

sweep-output-multi:
	@test -n "$(OUTPUT_GATEWAY_FLAVOR)" || \
		{ echo "sweep-output-multi: set OUTPUT_GATEWAY_FLAVOR=grpc|brpc|websocket" >&2; exit 1; }
	CLIENTS="$(CLIENTS)" SSH_KEY=$(SSH_KEY) CLIENT_DIR=$(CLIENT_DIR) FLEET="$(FLEET)" \
		CLIENT_CMD="$(OUTPUT_CLIENT_CMD)" \
		../sweep/sweep-multi.sh sequencer-output-$(OUTPUT_GATEWAY_FLAVOR) . \
		"$(abspath $(SWEEP_OUTPUT_CSV))" \
		$(SWEEP_WARMUP) $(SWEEP_MEASURE) "" $(SWEEP_RATES)

## All three flavors from five clients, one cluster lifetime. The
## gateway process serves all three protocols at once, so what differs
## between the curves is the subscribe protocol and nothing else.
sweep-output-multi-all:
	echo 'product,rate,achieved,p50,p90,p99,p999,max,dropped,lag,fleet' > $(SWEEP_OUTPUT_CSV)
	$(MAKE) stop-multi >/dev/null 2>&1 || true
	$(MAKE) clean-data >/dev/null 2>&1
	$(MAKE) start-multi OUTPUT_GATEWAY_FLAVOR=brpc >/dev/null 2>&1
	sleep 5
	for f in grpc brpc websocket; do \
		$(MAKE) --no-print-directory sweep-output-multi OUTPUT_GATEWAY_FLAVOR=$$f ; \
	done

sweep-fix:
	echo 'product,rate,achieved,p50,p90,p99,p999,max,dropped,lag,fleet' > $(SWEEP_FIX_CSV)
	CLIENTS="$(CLIENTS)" SSH_KEY=$(SSH_KEY) CLIENT_DIR=$(CLIENT_DIR) FLEET="$(FLEET)" \
		CLIENT_CMD="$(FIX_CLIENT_CMD)" \
		../sweep/sweep-multi.sh $(FIX_PRODUCT) . "$(abspath $(SWEEP_FIX_CSV))" \
		$(SWEEP_WARMUP) $(SWEEP_MEASURE) "" $(SWEEP_FIX_RATES)

## Push the client-side binaries to EVERY client box, not just
## CLIENT. `push` targets the single-client topology this repo was
## built around; the multi-gateway experiment needs the same two
## binaries on all of $(CLIENTS).
## Deliberately NOT dependent on `push`: that copies NODE-side binaries,
## which fail with "Text file busy" whenever the cluster is running, and
## a failed prerequisite meant the client binaries were silently never
## copied. Client and node pushes are independent; keeping them so means
## updating a load generator does not require stopping the cluster.
push-multi:
	for h in $(CLIENTS); do \
		ssh $(SSH_OPTS) $(SSH_USER)@$$h "mkdir -p $(CLIENT_DIR)" && \
		scp $(SSH_OPTS) $(SEQUENCER_DIR)/build/release/examples/$(APP)/$(APP)_input_gateway \
			$(SEQUENCER_DIR)/build/release/examples/$(APP)/$(APP)_load_generator \
			$(SSH_USER)@$$h:$(CLIENT_DIR)/ ; \
	done

## `start`, plus one input gateway on every OTHER client box. Each
## client talks only to the gateway in its own process space
## (127.0.0.1), so N clients means N independent gateways all
## proposing to the one leader — which is the deployment shape this
## measures, and the reason the leader must be NODE1 (see
## transfer-leader) rather than wherever braft happened to elect.
start-multi: start
	for h in $(CLIENTS); do \
		ssh $(SSH_OPTS) $(SSH_USER)@$$h \
			"cd $(CLIENT_DIR) && { [ -f $(APP)_input_gateway.pid ] && kill -TERM \$$(cat $(APP)_input_gateway.pid) 2>/dev/null; :; }; \
			 rm -f $(APP)_input_gateway.pid; sleep 1; \
			 $(ULIMIT_FDS) nohup ./$(APP)_input_gateway --node_peers='$(NODE1_PRIV):$(PORT),$(NODE2_PRIV):$(PORT),$(NODE3_PRIV):$(PORT)' \
				--listen_port=$(INPUT_GATEWAY_PORT) $(INPUT_GATEWAY_CONCURRENCY_FLAG) $(INPUT_BATCH_FLAGS) $(INPUT_GATEWAY_EXTRA) \
				--logtostderr --logbufsecs=0 \
				> std.log 2>&1 < /dev/null & echo \$$! > $(APP)_input_gateway.pid" ; \
	done
	@$(MAKE) --no-print-directory transfer-leader

stop-multi: stop
	for h in $(CLIENTS); do \
		ssh $(SSH_OPTS) $(SSH_USER)@$$h "cd $(CLIENT_DIR) && { [ -f $(APP)_input_gateway.pid ] && kill -TERM \$$(cat $(APP)_input_gateway.pid) 2>/dev/null; :; }" ; \
	done

stop:
	for h in $(NODES); do \
		ssh $(SSH_OPTS) $(SSH_USER)@$$h "cd $(NODE_DIR) && { [ -f $(APP)_node.pid ] && kill -TERM \$$(cat $(APP)_node.pid) 2>/dev/null; :; }" ; \
	done
	ssh $(SSH_OPTS) $(SSH_USER)@$(NODE1) "cd $(NODE_DIR) && { [ -f sequencer_relay.pid ] && kill -TERM \$$(cat sequencer_relay.pid) 2>/dev/null; :; }"
	ssh $(SSH_OPTS) $(SSH_USER)@$(NODE1) "cd $(NODE_DIR) && { [ -f $(APP)_output_gateway.pid ] && kill -TERM \$$(cat $(APP)_output_gateway.pid) 2>/dev/null; :; }"
	ssh $(SSH_OPTS) $(SSH_USER)@$(CLIENT) "cd $(CLIENT_DIR) && { [ -f $(APP)_input_gateway.pid ] && kill -TERM \$$(cat $(APP)_input_gateway.pid) 2>/dev/null; :; }"

## Wipe every node's journal (run with the fleet stopped). Before a
## sweep meant to be reproducible from a known-empty starting point —
## a fresh journal makes --relay_from_sequence_number's default (0,
## "from the beginning") correct by construction instead of relying on
## RelayObserver's own backlog-skipping (bench/load_generator/README.md)
## to paper over a long-lived journal, and keeps every sweep point's
## own "achieved rate" describing that point's traffic alone. Also
## wipes NODE1's output-gateway resume files (output_resume_<flavor>,
## living outside $(NODE_DIR)/data on purpose — they're meant to
## survive a normal restart, per specification.md §8.3's own
## "restartable from any sequence number with identical output") —
## reproduced live: a resume file surviving this target while the
## journal it refers to didn't left an output gateway waiting forever
## for a sequence number the fresh journal will never reach, an entire
## sweep point silently reporting zero completions with no error
## anywhere. Harmless if none exist yet (rm -f, not rm).
clean-data:
	for h in $(NODES); do \
		ssh $(SSH_OPTS) $(SSH_USER)@$$h "rm -rf $(NODE_DIR)/data" ; \
	done
	ssh $(SSH_OPTS) $(SSH_USER)@$(NODE1) "rm -f $(NODE_DIR)/output_resume_*"

# Empty (default): client is phase 1 only, byte-for-byte the same
# invocation as before this variable existed. Set to enable phase 3's
# second, separately-labeled summary (relay_p50_us, etc.) in the same
# run — RelayObserver, running inside the load generator process
# itself, subscribing to sequencer_relay over real gRPC (needs `start`
# to have brought it up on NODE1 first). A Make variable rather than a
# second target's own hardcoded flag specifically so sweep.sh (shared,
# unmodified, in the sibling sweep/ directory) can drive a phase-3
# sweep through this exact same target, the same way it already drives
# phase 1 — see sweep-relay.sh, which sets this via sweep.sh's own
# generic $EXTRA argument.
RELAY_GRPC_ADDR ?=
RELAY_FLAG = $(if $(RELAY_GRPC_ADDR),--relay_grpc_addr=$(RELAY_GRPC_ADDR),)

# Same idea, for the fourth round trip (bench/load_generator/README.md):
# empty disables output-gateway observation. OUTPUT_OBSERVER selects the
# flavor (grpc/brpc/websocket) and must match whichever
# OUTPUT_GATEWAY_FLAVOR `start` brought up — see client-output below for
# the thin wrapper that sets both consistently.
OUTPUT_OBSERVER ?=
OUTPUT_GATEWAY_ADDR ?=
OUTPUT_FLAG = $(if $(OUTPUT_OBSERVER),--output_observer=$(OUTPUT_OBSERVER) --output_gateway_addr=$(OUTPUT_GATEWAY_ADDR),)

## Run the load generator from the client box (override THREADS=n).
## Flag names deliberately match braft/Makefile's `client:` target
## (--mode/--rate/--burst/--max_inflight/--warmup/--measure/
## --drain_timeout/--pace/--hdr_out), so sweep.sh needs no per-product
## awareness of the difference — see sequencer's own
## bench/load_generator/README.md for why.
# Empty (the default): submit through the input gateway, the real
# deployment path (specification.md §3.3). Set to bypass it and call a
# node's own ProposeService directly — the control arm for "what does
# that hop cost?". See sequencer's examples/counter/load_generator_main.cpp
# (ProposeRequester) for the three things the direct arm skips, only
# one of which a deployment could actually skip too.
NODE_ADDR ?=
SUBMIT_TARGET_FLAG = $(if $(NODE_ADDR),--node_addr=$(NODE_ADDR),--input_gateway_addr=127.0.0.1:$(INPUT_GATEWAY_PORT))

client:
	ssh -t $(SSH_OPTS) $(SSH_USER)@$(CLIENT) \
		"cd $(CLIENT_DIR) && ./$(APP)_load_generator $(SUBMIT_TARGET_FLAG) \
			--mode $(MODE) --pace $(PACE) \
			--thread_num $(THREADS) $(OPEN_FLAGS) \
			--warmup $(WARMUP) --measure $(MEASURE) \
			--drain_timeout $(DRAIN_TIMEOUT) $(HDR_FLAG) $(RELAY_FLAG) $(OUTPUT_FLAG) --logtostderr"

## The direct-to-node arm: same rig, same rates, but submitting to
## NODE1's ProposeService instead of through the input gateway.
client-direct:
	$(MAKE) client NODE_ADDR=$(NODE1_PRIV):$(PORT)

## Convenience alias for `make client` with RELAY_GRPC_ADDR already
## pointed at the relay `start` brought up on NODE1 — see RELAY_FLAG's
## own comment above for why this is a thin wrapper around client
## rather than its own independent recipe.
client-relay:
	$(MAKE) client RELAY_GRPC_ADDR=$(NODE1_PRIV):$(RELAY_GRPC_PORT)

## Same idea for the output gateway: OUTPUT_GATEWAY_FLAVOR must name
## whichever flavor `start` actually brought up (brpc/grpc/websocket) —
## this just points OUTPUT_OBSERVER/OUTPUT_GATEWAY_ADDR at it
## consistently rather than requiring both be typed out by hand every
## time. `make client-output OUTPUT_GATEWAY_FLAVOR=grpc` after `make
## start OUTPUT_GATEWAY_FLAVOR=grpc`.
client-output:
	$(MAKE) client OUTPUT_OBSERVER=$(OUTPUT_GATEWAY_FLAVOR) OUTPUT_GATEWAY_ADDR=$(NODE1_PRIV):$(OUTPUT_GATEWAY_PORT)

## Move leadership to NODE1, which is the node every client box shares
## an AZ and cluster placement group with (deploy/multi_az.tfvars).
## braft elects whoever it likes on startup, and a leader on NODE2/NODE3
## puts a full cross-AZ hop between every input gateway and the leader:
## measured 1466us p50 at 100k with a remote leader against ~1060us with
## a local one, on the same fleet. Any comparison across runs has to pin
## this, so `make start` is not enough on its own.
##
## Reuses braft/'s own braft_cli, already pushed to the client box by
## `make -C ../braft push` — it speaks to any braft group, and
## counter_node is one.
transfer-leader:
	ssh $(SSH_OPTS) $(SSH_USER)@$(CLIENT) \
		"cd atomic && ./braft_cli transfer_leader --group=$(GROUP) \
			--peer=$(NODE1_PRIV):$(PORT):0 --conf='$(PEERS)'"
	@sleep 2
	@$(MAKE) --no-print-directory leader

## Print which node currently holds leadership. Reads braft's own
## /raft_stat page over the node's brpc built-in services rather than
## the log: the log records elections, so after a transfer it still
## shows the stale winner, while /raft_stat is current state.
leader:
	@for h in $(NODE1) $(NODE2) $(NODE3); do \
		printf "%-16s " $$h; \
		ssh $(SSH_OPTS) $(SSH_USER)@$$h \
			"curl -s localhost:$(PORT)/raft_stat | grep -o 'state: [A-Z]*' | head -1" \
			2>/dev/null || echo "unreachable"; \
		echo ""; \
	done

logs:
	ssh $(SSH_OPTS) $(SSH_USER)@$(NODE1) "tail -n 100 -f $(NODE_DIR)/std.log"

logs-relay:
	ssh $(SSH_OPTS) $(SSH_USER)@$(NODE1) "tail -n 100 -f $(NODE_DIR)/relay.log"

logs-output:
	ssh $(SSH_OPTS) $(SSH_USER)@$(NODE1) "tail -n 100 -f $(NODE_DIR)/output_gateway.log"

# A starting point, not a known comfort zone — sequencer adds a real
# hop on top of bare braft, so its own knee is somewhere at or below
# braft's ~160k, not necessarily the same place. Narrow or widen once
# a first pass shows roughly where it turns (see README.md and
# ../sweep/README.md's own "Rates are spaced tightest inside each
# product's comfort zone and through its knee").
SWEEP_RATES   ?= 10000 25000 40000 55000 70000 85000 100000 115000 130000 145000 160000 175000
SWEEP_WARMUP  ?= 10
SWEEP_MEASURE ?= 30
SWEEP_CSV       ?= seq.csv
SWEEP_DIRECT_CSV ?= seq-direct.csv
SWEEP_RELAY_CSV ?= seq-relay.csv
# Phase 4: all three output-gateway flavors land in ONE csv (rows are
# tagged product=sequencer-output-<flavor>), because the interesting
# comparison is across flavors — see `sweep-output-multi-all` below.
## The output-gateway sweep's results. Named for the multi-client rig
## because that is the only one now: the single-client seq-output.csv
## was deleted along with sweep-output.sh, its "knee" having been the
## one client box running out of send capacity.
## Journal segment geometry, for the tail-latency experiment.
##
## Every gateway flavor shows a 50-240us... no: 50-240 MILLISECOND p999
## at every rate from 75k up, while bare braft on the same fleet stays
## under 2ms. The journal is the thing sequencer has that braft does
## not, and it rolls to a new segment every ~1.05M records -- every
## ~8.4s at 125k -- creating a fresh 16MiB index file each time.
##
## Rolls x stall-duration x rate is what decides whether those stalls
## land in p999 or cross into p99, which is exactly where the measured
## crossover sits. Raising the record count makes rolls proportionally
## rarer; if the tail follows, the segment roll is the cause.
##
## ONLY read when a journal is created -- reopening takes geometry from
## the manifest -- so clean-data must precede any change here.
## Raise these TOGETHER, keeping their product constant. A segment's
## data file is reserved at records_per_segment * max_record_bytes, and
## the default pair reserves 256 GiB (1,048,576 x 262,144). Raising the
## record count 16x on its own asks for a 4.4 PB sparse file, which is
## past ext4's 16 TB per-file limit and simply fails to start.
## Report any apply-thread step whose phases, or whose gap since the
## previous record, exceed this many microseconds. Off unless set, and
## off costs nothing -- no clock is read. Set it to chase a tail:
##   make start-multi APPLY_STALL_US=5000
## then read [apply-stall] lines from the node's std.log. A large `gap`
## with small phases means the stall was upstream of the apply thread.
APPLY_STALL_US ?=
NODE_ENV = $(if $(APPLY_STALL_US),SEQ_APPLY_STALL_US=$(APPLY_STALL_US),)

## Report any journal segment open slower than this from whichever
## process TAILS the journal -- the output gateway, which opens and maps
## each new segment on the very thread that delivers records.
SEGMENT_OPEN_US ?=
## Report a record that took too long from committed-in-the-journal to
## published-on-the-ring, or too long a gap since the previous one.
TAIL_STALL_US ?=
OUTPUT_ENV = $(if $(SEGMENT_OPEN_US),SEQ_SEGMENT_OPEN_US=$(SEGMENT_OPEN_US),) \
	$(if $(TAIL_STALL_US),SEQ_TAIL_STALL_US=$(TAIL_STALL_US),)

JOURNAL_RECORDS_PER_SEGMENT ?=
JOURNAL_MAX_RECORD_BYTES ?=
JOURNAL_GEOMETRY_FLAGS = \
	$(if $(JOURNAL_RECORDS_PER_SEGMENT),--journal_records_per_segment=$(JOURNAL_RECORDS_PER_SEGMENT),) \
	$(if $(JOURNAL_MAX_RECORD_BYTES),--journal_max_record_bytes=$(JOURNAL_MAX_RECORD_BYTES),)

SWEEP_OUTPUT_CSV ?= seq-output-multi.csv
CHART_DIR       ?= .

.PHONY: sweep sweep-multi sweep-multi-baseline sweep-direct sweep-relay sweep-output-multi sweep-output-multi-all sweep-all \
        chart chart-relay chart-output charts charts-all charts-gateways

## Phase 1's sweep: reuses ../sweep/sweep.sh unmodified, against
## SWEEP_RATES (override to change the range), writing SWEEP_CSV
## (overwritten each run — rename it first if you want to keep more
## than one). See README.md's "One sweep at a time, from a clean
## journal" before running this against a fleet that already has
## traffic on it.
sweep:
	echo 'product,rate,achieved,p50,p90,p99,p999,max,dropped,lag' > $(SWEEP_CSV)
	../sweep/sweep.sh sequencer . "$(abspath $(SWEEP_CSV))" $(SWEEP_WARMUP) $(SWEEP_MEASURE) "" $(SWEEP_RATES)

## The multi-gateway sweep: the same offered rates, but split across
## every box in $(CLIENTS), each with its own input gateway, all
## proposing to the one leader concurrently. Percentiles come from
## merging every client's raw histogram, not from averaging their
## reported percentiles — see ../sweep/merge-hdr.py for why that
## distinction is not pedantry here.
##
## Run `make sweep-multi-baseline` first: the N=1 arm has to come off
## THIS fleet with THIS script, or the comparison is against a
## differently-placed fleet and measures placement, not gateway count.
SWEEP_MULTI_CSV ?= seq-multi.csv
SWEEP_MULTI_BASELINE_CSV ?= seq-multi-baseline.csv

sweep-multi:
	echo 'product,rate,achieved,p50,p90,p99,p999,max,dropped,lag,fleet' > $(SWEEP_MULTI_CSV)
	CLIENTS="$(CLIENTS)" SSH_KEY=$(SSH_KEY) APP=$(APP) FLEET="$(FLEET)" INPUT_GATEWAY_PORT=$(INPUT_GATEWAY_PORT) \
		../sweep/sweep-multi.sh sequencer-multi . "$(abspath $(SWEEP_MULTI_CSV))" \
		$(SWEEP_WARMUP) $(SWEEP_MEASURE) "" $(SWEEP_RATES)

## The N=1 control arm, driven through the identical script and the
## identical rig settings, differing only in how many clients carry
## the load. Rows are tagged product=sequencer-multi1.
sweep-multi-baseline:
	echo 'product,rate,achieved,p50,p90,p99,p999,max,dropped,lag,fleet' > $(SWEEP_MULTI_BASELINE_CSV)
	CLIENTS="$(CLIENT)" SSH_KEY=$(SSH_KEY) APP=$(APP) FLEET="$(FLEET)" INPUT_GATEWAY_PORT=$(INPUT_GATEWAY_PORT) \
		../sweep/sweep-multi.sh sequencer-multi1 . "$(abspath $(SWEEP_MULTI_BASELINE_CSV))" \
		$(SWEEP_WARMUP) $(SWEEP_MEASURE) "" $(SWEEP_RATES)

## The same sweep, submitting straight to a node's ProposeService
## instead of through the input gateway — the control arm that prices
## that hop. Rows are tagged product=sequencer-direct.
sweep-direct:
	echo 'product,rate,achieved,p50,p90,p99,p999,max,dropped,lag' > $(SWEEP_DIRECT_CSV)
	../sweep/sweep.sh sequencer-direct . "$(abspath $(SWEEP_DIRECT_CSV))" $(SWEEP_WARMUP) $(SWEEP_MEASURE) \
		"NODE_ADDR=$(NODE1_PRIV):$(PORT)" $(SWEEP_RATES)

## Phase 3's sweep: sweep-relay.sh (this directory, not shared — see
## its own header for why sweep.sh itself can't do this), same
## SWEEP_RATES, writing SWEEP_RELAY_CSV.
sweep-relay:
	echo 'product,rate,achieved,p50,p90,p99,p999,max,dropped,lag' > $(SWEEP_RELAY_CSV)
	./sweep-relay.sh "$(abspath $(SWEEP_RELAY_CSV))" $(SWEEP_WARMUP) $(SWEEP_MEASURE) \
		"RELAY_GRPC_ADDR=$(NODE1_PRIV):$(RELAY_GRPC_PORT)" $(SWEEP_RATES)

## Every round trip this repo measures for sequencer, in one go:
## phase 1 (synchronous ack), phase 3 (relay), phase 4 (all three
## output-gateway flavors, from all five client boxes). Each phase
## re-cleans the journal first, so
## this is the single reproducible entry point behind every sequencer
## chart in README.md. Long — five full rate sweeps.
sweep-all:
	$(MAKE) stop >/dev/null 2>&1 || true
	$(MAKE) clean-data
	$(MAKE) start
	sleep 5
	$(MAKE) sweep
	$(MAKE) sweep-relay
	$(MAKE) sweep-output-multi-all

## Regenerates knee-sequencer.svg (and knee-curves.svg, if SWEEP_CSV
## happens to also carry aeron/braft/openraft rows) from SWEEP_CSV
## into CHART_DIR. Needs a "sequencer" entry in ../sweep/mkcharts.py's
## own CFG dict first — see README.md's "Generating the chart" for the
## template; there is nothing to derive real axis ranges from before a
## real sweep exists, so this is not done for you automatically.
chart:
	python3 ../sweep/mkcharts.py $(SWEEP_CSV) $(CHART_DIR)

## Same, for phase 3's sweep — needs a "sequencer-relay" CFG entry.
chart-relay:
	python3 ../sweep/mkcharts.py $(SWEEP_RELAY_CSV) $(CHART_DIR)

## Same, for phase 4's three flavors.
chart-output:
	python3 ../sweep/mkcharts.py $(SWEEP_OUTPUT_CSV) $(CHART_DIR)

## Both charts in one step, once both CFG entries exist.
charts: chart chart-relay

## Every sequencer chart, including the cross-round-trip comparison
## (round-trips.svg) that only exists when all five sweeps are handed
## to mkcharts.py together — hence one invocation with every CSV,
## rather than `charts chart-output`.
## The five gateway round trips on shared axes, into CHART_DIR
## (sequencer/, not the repo root -- the root charts belong to the
## top-level README's cross-product comparison).
##
## One invocation with every gateway CSV, because round-trips.svg and
## round-trips-p50.svg only exist when mkcharts.py sees them together.
SWEEP_FIX_INLINE_CSV ?= fix-inline.csv
## Bare braft on the same fleet, drawn as the floor beneath the gateways.
BRAFT_FLOOR_CSV ?= ../braft/braft-multi-c7a.csv
## The journal-segment tail A/B that diagnosed the rollover stall. Kept
## as evidence, not charted: it is three rates of one flavor, and the
## knee-chart template it borrowed had no knee to draw and a comfort
## band that covered the whole plot. The fix it led to (background
## segment prepare/seal) is visible in the full sweeps instead.
charts-gateways:
	python3 ../sweep/mkcharts.py --combined $(SWEEP_OUTPUT_CSV) $(SWEEP_FIX_CSV) \
		$(SWEEP_FIX_INLINE_CSV) $(SWEEP_QUICKFIX_CSV) $(BRAFT_FLOOR_CSV) $(CHART_DIR)

## Deliberately NOT --combined: charts-gateways owns round-trips.svg
## and round-trips-p50.svg. Both targets would otherwise write that one
## pair of files with DIFFERENT series -- seven gateways here, six
## ack/direct/relay/output round trips there -- so whichever ran last
## would win, which is precisely how the overlay silently lost four of
## its seven series once already.
charts-all:
	python3 ../sweep/mkcharts.py $(SWEEP_CSV) $(SWEEP_DIRECT_CSV) $(SWEEP_RELAY_CSV) \
		$(SWEEP_OUTPUT_CSV) $(CHART_DIR)
