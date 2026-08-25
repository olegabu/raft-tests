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

# One relay is enough for this benchmark (RelayObserver only ever
# subscribes to one), so it runs colocated with NODE1 only, not one
# per node the way a real deployment would (specification.md §3.3) —
# NODE1 is the node multi_az.tfvars already puts in the client's own
# AZ and placement group, so this is also the lowest-latency choice.
# Ports match gateway/relay/README.md's own worked example.
RELAY_LISTEN_PORT ?= 8500
RELAY_GRPC_PORT   ?= 8501

# Phase 1 (see sequencer's specification.md §3): measures exactly the
# same round trip raft-tests/braft/client.cpp does — submit to
# synchronous receipt — just one hop longer, through sequencer's own
# input gateway rather than straight to the raft group, because that
# hop is part of what's being measured here (§3.3: "clients should
# submit through an input gateway, never directly to a node's
# Propose"). See sequencer's own bench/load_generator/README.md for
# the other two round trips this repo's own README asks every product
# to report, and why they need a different rig than this one.
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

.PHONY: build push start stop client client-relay logs logs-relay

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
	ssh $(SSH_OPTS) $(SSH_USER)@$(CLIENT) "mkdir -p $(CLIENT_DIR)"
	scp $(SSH_OPTS) $(SEQUENCER_DIR)/build/release/examples/$(APP)/$(APP)_input_gateway \
		$(SEQUENCER_DIR)/build/release/examples/$(APP)/$(APP)_load_generator \
		$(SSH_USER)@$(CLIENT):$(CLIENT_DIR)/

## Start one node per NODE[123], sequencer_relay colocated with NODE1
## (reading that same node's own data_dir), and the input gateway on
## the client box.
start:
	for h in $(NODES); do \
		i=1; for n in $(NODES); do [ "$$n" = "$$h" ] && break; i=$$((i+1)); done ; \
		priv=$$(eval echo \$$NODE$${i}_PRIV) ; \
		ssh $(SSH_OPTS) $(SSH_USER)@$$h \
			"cd $(NODE_DIR) && pkill -f ./$(APP)_node 2>/dev/null; sleep 1; \
			 nohup ./$(APP)_node --group=$(GROUP) --peer=$$priv:$(PORT):0 --peers='$(PEERS)' \
				--election_timeout_ms=$(ELECTION_TIMEOUT_MS) --data_dir=$(NODE_DIR)/data \
				> $(NODE_DIR)/std.log 2>&1 < /dev/null &" ; \
	done
	sleep 3
	ssh $(SSH_OPTS) $(SSH_USER)@$(NODE1) \
		"cd $(NODE_DIR) && pkill -f ./sequencer_relay 2>/dev/null; sleep 1; \
		 nohup ./sequencer_relay --data_dir=$(NODE_DIR)/data --listen_port=$(RELAY_LISTEN_PORT) \
			--grpc_listen_port=$(RELAY_GRPC_PORT) > $(NODE_DIR)/relay.log 2>&1 < /dev/null &"
	ssh $(SSH_OPTS) $(SSH_USER)@$(CLIENT) \
		"cd $(CLIENT_DIR) && pkill -f ./$(APP)_input_gateway 2>/dev/null; sleep 1; \
		 nohup ./$(APP)_input_gateway --node_peers='$(NODE1_PRIV):$(PORT),$(NODE2_PRIV):$(PORT),$(NODE3_PRIV):$(PORT)' \
			--listen_port=$(INPUT_GATEWAY_PORT) > $(CLIENT_DIR)/std.log 2>&1 < /dev/null &"

stop:
	for h in $(NODES); do \
		ssh $(SSH_OPTS) $(SSH_USER)@$$h "pkill -TERM -f ./$(APP)_node" ; \
	done
	ssh $(SSH_OPTS) $(SSH_USER)@$(NODE1) "pkill -TERM -f ./sequencer_relay"
	ssh $(SSH_OPTS) $(SSH_USER)@$(CLIENT) "pkill -TERM -f ./$(APP)_input_gateway"

## Run the load generator from the client box (override THREADS=n).
## Flag names deliberately match braft/Makefile's `client:` target
## (--mode/--rate/--burst/--max_inflight/--warmup/--measure/
## --drain_timeout/--pace/--hdr_out), so sweep.sh needs no per-product
## awareness of the difference — see sequencer's own
## bench/load_generator/README.md for why.
client:
	ssh -t $(SSH_OPTS) $(SSH_USER)@$(CLIENT) \
		"cd $(CLIENT_DIR) && ./$(APP)_load_generator --input_gateway_addr=127.0.0.1:$(INPUT_GATEWAY_PORT) \
			--mode $(MODE) --pace $(PACE) \
			--thread_num $(THREADS) $(OPEN_FLAGS) \
			--warmup $(WARMUP) --measure $(MEASURE) \
			--drain_timeout $(DRAIN_TIMEOUT) $(HDR_FLAG)"

## Phase 3 (bench/load_generator/README.md): the same run as `client`,
## plus a second, separately-labeled summary (relay_p50_us, etc.) for
## submission-to-relay-delivery latency — RelayObserver, running
## inside the load generator process itself, subscribing to
## sequencer_relay over real gRPC. Needs `start` to have brought
## sequencer_relay up on NODE1 first.
client-relay:
	ssh -t $(SSH_OPTS) $(SSH_USER)@$(CLIENT) \
		"cd $(CLIENT_DIR) && ./$(APP)_load_generator --input_gateway_addr=127.0.0.1:$(INPUT_GATEWAY_PORT) \
			--mode $(MODE) --pace $(PACE) \
			--thread_num $(THREADS) $(OPEN_FLAGS) \
			--warmup $(WARMUP) --measure $(MEASURE) \
			--drain_timeout $(DRAIN_TIMEOUT) $(HDR_FLAG) \
			--relay_grpc_addr=$(NODE1_PRIV):$(RELAY_GRPC_PORT)"

logs:
	ssh $(SSH_OPTS) $(SSH_USER)@$(NODE1) "tail -n 100 -f $(NODE_DIR)/std.log"

logs-relay:
	ssh $(SSH_OPTS) $(SSH_USER)@$(NODE1) "tail -n 100 -f $(NODE_DIR)/relay.log"
