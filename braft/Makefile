# Product-specific targets for the braft atomic-counter example. Provision
# the EC2 fleet first with the root Makefile (`make deploy` from the repo
# root); this Makefile only builds/pushes/runs against whatever that created.
-include ../.env
include ../common.mk

PORT     ?= 8300
SYNC     ?= false
THREADS  ?= 100
# One bthread worker per sending thread, else threads queue for workers and
# inflate measured latency instead of the raft cluster's own latency.
CLIENT_CONCURRENCY ?= $(THREADS)
# In-flight AppendEntries RPCs per follower; 1 (braft's own default) forces a
# full round trip before the next entry can go out, capping throughput under
# concurrency. Raise it so several batches can be in flight at once.
PIPELINE ?= 4

PEERS      = $(NODE1_PRIV):$(PORT):0,$(NODE2_PRIV):$(PORT):0,$(NODE3_PRIV):$(PORT):0
NODE_DIR   = /data/atomic
CLIENT_DIR = atomic

.PHONY: push start stop client logs transfer-leader

# Built separately from the braft repo (BUILD_TOOLS is off by default there):
#   cmake --preset default -DBUILD_TOOLS=ON ~/workspace/braft
#   cmake --build ~/workspace/braft/bld --target braft_cli
# Only needed for `make transfer-leader`; push skips it silently if absent.
BRAFT_CLI ?= ~/workspace/braft/bld/tools/output/bin/braft_cli

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
			"cd $(NODE_DIR) && ./stop.sh 2>/dev/null; ./run_server.sh --clean --server_num 1 --port $(PORT) --peers '$(PEERS)' --sync $(SYNC) --max_parallel_append_entries_rpc_num $(PIPELINE)" ; \
	done

stop:
	for h in $(NODES); do \
		ssh $(SSH_OPTS) $(SSH_USER)@$$h "cd $(NODE_DIR) && ./stop.sh" ; \
	done

## Run the load generator from the client box (override THREADS=n)
client:
	ssh -t $(SSH_OPTS) $(SSH_USER)@$(CLIENT) \
		"cd $(CLIENT_DIR) && ./run_client.sh --peers '$(PEERS)' --thread_num $(THREADS) --bthread_concurrency $(CLIENT_CONCURRENCY)"

logs:
	ssh $(SSH_OPTS) $(SSH_USER)@$(NODE1) "tail -n 100 -f $(NODE_DIR)/runtime/0/std.log"

## Designate node[0]/NODE1 (colocated with the client in multi_az) as the
## starting leader. braft may re-elect a different leader later; the client
## will then be talking to a remote leader, which is expected.
transfer-leader:
	ssh $(SSH_OPTS) $(SSH_USER)@$(CLIENT) \
		"cd $(CLIENT_DIR) && ./braft_cli transfer_leader --group=Atomic --peer=$(NODE1_PRIV):$(PORT):0 --conf='$(PEERS)'"
