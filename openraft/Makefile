# Product-specific targets for the openraft raft-kv-memstore example.
# Provision the EC2 fleet first with the root Makefile (`make deploy` from
# the repo root); this Makefile only builds/pushes/runs against whatever
# that created. Vendored server code lives in vendor/ (see vendor/NOTICE.md).
-include ../.env
include ../common.mk

API_PORT  ?= 21001
RAFT_PORT ?= 22001

# Load generator flags -- see loadgen/src/main.rs and README.md.
THREADS ?= 100
# Real tokio worker threads; kept equal to THREADS by default so sending
# tasks never queue for fewer real workers than they need -- which would
# inflate measured latency with client-side scheduling delay rather than
# the cluster's own latency. Set explicitly to reintroduce that mismatch.
WORKER_THREADS  ?= $(THREADS)
CAS_PERCENTAGE  ?= 0
VALUE_SIZE      ?= 64
KEY             ?= bench
# --log_each_request is a bare switch in loadgen (no true/false value), so
# translate LOG_EACH_REQUEST=true into the flag and anything else into nothing.
LOG_EACH_REQUEST ?= false
LOG_EACH_REQUEST_FLAG = $(if $(filter true,$(LOG_EACH_REQUEST)),--log_each_request,)

NODE_DIR   = /data/openraft
CLIENT_DIR = openraft
SERVER_BIN = vendor/raft-kv-memstore/target/release/raft-key-value
LOADGEN_BIN = loadgen/target/release/loadgen

API_PEERS = $(NODE1_PRIV):$(API_PORT),$(NODE2_PRIV):$(API_PORT),$(NODE3_PRIV):$(API_PORT)

.PHONY: build push start init-cluster stop client logs

## Build the vendored server and the load generator (release, matters here
## too -- see README's debug-vs-release note)
build:
	cd vendor/raft-kv-memstore && cargo build --release
	cd loadgen && cargo build --release

## Copy binaries and init-cluster.sh to all instances
push:
	for h in $(NODES); do \
		ssh $(SSH_OPTS) $(SSH_USER)@$$h "mkdir -p $(NODE_DIR)" && \
		scp $(SSH_OPTS) $(SERVER_BIN) $(SSH_USER)@$$h:$(NODE_DIR)/ ; \
	done
	ssh $(SSH_OPTS) $(SSH_USER)@$(CLIENT) "mkdir -p $(CLIENT_DIR)"
	scp $(SSH_OPTS) $(LOADGEN_BIN) init-cluster.sh $(SSH_USER)@$(CLIENT):$(CLIENT_DIR)/

## Start one raft-key-value process per node, peered over private IPs.
## Unlike braft there's no static peer config -- see `init-cluster`.
start:
	for h in $(NODE1):$(NODE1_PRIV) $(NODE2):$(NODE2_PRIV) $(NODE3):$(NODE3_PRIV); do \
		pub=$${h%%:*}; priv=$${h##*:}; \
		id=1; [ "$$pub" = "$(NODE2)" ] && id=2; [ "$$pub" = "$(NODE3)" ] && id=3; \
		ssh $(SSH_OPTS) $(SSH_USER)@$$pub \
			"cd $(NODE_DIR) && killall raft-key-value 2>/dev/null; sleep 1; \
			 nohup ./raft-key-value --id $$id --api-addr $$priv:$(API_PORT) --raft-addr $$priv:$(RAFT_PORT) > std.log 2>&1 & disown" ; \
	done

## One-time cluster formation (init + add-learner x2 + change-membership).
## Re-run only after a fresh `make start` on a torn-down/recreated cluster.
init-cluster:
	ssh $(SSH_OPTS) $(SSH_USER)@$(CLIENT) \
		"cd $(CLIENT_DIR) && ./init-cluster.sh $(NODE1_PRIV):$(API_PORT) \
			2:$(NODE2_PRIV):$(API_PORT):$(NODE2_PRIV):$(RAFT_PORT) \
			3:$(NODE3_PRIV):$(API_PORT):$(NODE3_PRIV):$(RAFT_PORT)"

stop:
	for h in $(NODES); do \
		ssh $(SSH_OPTS) $(SSH_USER)@$$h "killall raft-key-value" ; \
	done

## Run the load generator from the client box (override THREADS=n)
client:
	ssh -t $(SSH_OPTS) $(SSH_USER)@$(CLIENT) \
		"cd $(CLIENT_DIR) && ./loadgen --peers '$(API_PEERS)' --thread_num $(THREADS) \
			--worker_threads $(WORKER_THREADS) --cas_percentage $(CAS_PERCENTAGE) \
			--value_size $(VALUE_SIZE) --key $(KEY) $(LOG_EACH_REQUEST_FLAG)"

logs:
	ssh $(SSH_OPTS) $(SSH_USER)@$(NODE1) "tail -n 100 -f $(NODE_DIR)/std.log"
