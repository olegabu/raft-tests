# Product-specific targets for Aeron Cluster, benchmarked via the echo service
# that ships in io.aeron:aeron-samples. Provision the EC2 fleet first with the
# root Makefile (`make deploy` from the repo root); this Makefile only builds/
# pushes/runs against whatever that created.
-include ../.env
include ../common.mk

PORT_BASE ?= 9000

# Load generator flags -- see loadgen/src/main/java/.../Loadgen.java and README.md.
THREADS    ?= 100
VALUE_SIZE ?= 64
# --log_each_request is a bare switch in loadgen (no true/false value), so
# translate LOG_EACH_REQUEST=true into the flag and anything else into nothing.
LOG_EACH_REQUEST ?= false
LOG_EACH_REQUEST_FLAG = $(if $(filter true,$(LOG_EACH_REQUEST)),--log_each_request,)

# Required, not optional: agrona's ShutdownSignalBarrier reflects into
# jdk.internal.misc.Signal, which JDK 17+ does not export to unnamed modules.
# Without these every JVM here dies at startup with IllegalAccessException.
# Same set Aeron's own scripts/run-java uses.
AERON_OPENS = --add-opens java.base/java.util.zip=ALL-UNNAMED --add-opens java.base/jdk.internal.misc=ALL-UNNAMED

# Also required: aeron-samples' ClusterConfig does not set an ingress channel,
# so the consensus module refuses to start ("ingressChannel must be specified").
INGRESS_CHANNEL ?= aeron:udp?term-length=64k

# Aeron's own benchmark rig uses -Xms4G -Xmx4G; 1G is ample for the echo
# service and friendlier when running several nodes on one box locally.
JVM_OPTS ?= -Xms1G -Xmx1G -XX:+AlwaysPreTouch

# Idle strategies. Aeron ships every agent on
# BackoffIdleStrategy(maxPark=1ms); on a cluster that is not saturated the
# agents park between messages and each handoff on the request path pays a
# wakeup, which measured 2100 us against 437 us for the settings below at a
# single outstanding request on one 3-node multi-AZ fleet. The cost is CPU:
# ~310% of each node's 800% sits in spin loops, which is the right trade on
# dedicated benchmark boxes.
SPIN_IDLE    ?= org.agrona.concurrent.BusySpinIdleStrategy
ARCHIVE_IDLE ?= org.agrona.concurrent.YieldingIdleStrategy

# Appoint the leader rather than electing one. The multi_az topology deliberately
# puts node 0 in the client's AZ, and a leader elsewhere adds a cross-AZ hop to
# every request -- measured ~514 us versus ~2000 us at 100 outstanding requests,
# purely from which member won the election. Pinning it makes runs reproducible
# and comparable across products. Aeron labels this a testing feature, which is
# precisely this use. Set empty to fall back to normal election.
APPOINTED_LEADER ?= 0
LEADER_FLAG = $(if $(APPOINTED_LEADER),-Daeron.cluster.appointed.leader.id=$(APPOINTED_LEADER),)
IDLE_OPTS = -Daeron.cluster.idle.strategy=$(SPIN_IDLE) \
            -Daeron.sender.idle.strategy=$(SPIN_IDLE) \
            -Daeron.receiver.idle.strategy=$(SPIN_IDLE) \
            -Daeron.conductor.idle.strategy=$(SPIN_IDLE) \
            -Daeron.archive.idle.strategy=$(ARCHIVE_IDLE) \
            -Daeron.archive.recorder.idle.strategy=$(ARCHIVE_IDLE) \
            -Daeron.archive.replayer.idle.strategy=$(ARCHIVE_IDLE)

NODE_DIR    = /data/aeron
CLIENT_DIR  = aeron
HOSTNAMES   = $(NODE1_PRIV),$(NODE2_PRIV),$(NODE3_PRIV)
NODE_MAIN   = io.aeron.samples.cluster.EchoServiceNode
CLIENT_MAIN = io.raftbench.aeron.Loadgen

# The bracket keeps the pattern from matching pkill's own command line. It must
# also be issued in an ssh call that does NOT contain $(NODE_MAIN) anywhere,
# because that literal would match too and the shell would kill itself before
# launching anything -- hence `start` uses a separate ssh for the kill.
KILL_NODE = pkill -9 -f '[E]choServiceNode' 2>/dev/null; true

# Killing with -9 leaves the media driver's CnC file behind with a fresh
# timestamp, so a driver starting seconds later aborts with "active driver
# detected" until the liveness timeout expires. Deleting the driver and cluster
# directories avoids the wait and makes every run start from a clean cluster,
# which is what we want for a benchmark anyway.
CLEAN_NODE = $(KILL_NODE); sleep 2; rm -rf /dev/shm/aeron-* $(NODE_DIR)/aeron-cluster-*; true

# A load generator that is interrupted -- Ctrl-C, a dropped ssh, a timeout --
# can outlive the ssh session and keep driving traffic. Several of those
# accumulating unnoticed will saturate the client box and silently corrupt every
# later measurement, so `client` clears them first. Same bracket trick, and it
# likewise has to run in an ssh call that does not mention $(CLIENT_MAIN).
KILL_CLIENT = pkill -9 -f '[L]oadgen' 2>/dev/null; true

.PHONY: provision build push start stop client logs

## Install a JDK on every instance. The AMI ships none, and this is done over
## ssh rather than in deploy/main.tf's user_data because changing user_data
## forces instance replacement, destroying a running fleet.
provision:
	for h in $(NODES) $(CLIENT); do \
		ssh $(SSH_OPTS) $(SSH_USER)@$$h \
			"sudo apt-get update -q && sudo apt-get install -y -q openjdk-21-jdk-headless && java -version" ; \
	done

## Build the loadgen and collect all runtime jars into libs/.
## Uses the Gradle wrapper deliberately: a system-wide `gradle` is often far
## too old (4.x cannot even start on JDK 21), and the build pins a Java 21
## toolchain so an older `javac` on PATH is ignored.
build:
	cd loadgen && ./gradlew build

## Copy the jars to all instances. One libs/ dir serves both roles: nodes run
## EchoServiceNode from aeron-samples, the client runs the loadgen.
push:
	for h in $(NODES); do \
		ssh $(SSH_OPTS) $(SSH_USER)@$$h "mkdir -p $(NODE_DIR)/libs" && \
		scp $(SSH_OPTS) libs/*.jar $(SSH_USER)@$$h:$(NODE_DIR)/libs/ ; \
	done
	ssh $(SSH_OPTS) $(SSH_USER)@$(CLIENT) "mkdir -p $(CLIENT_DIR)/libs"
	scp $(SSH_OPTS) libs/*.jar $(SSH_USER)@$(CLIENT):$(CLIENT_DIR)/libs/

## Start one node per instance. Membership is static (index in HOSTNAMES ==
## nodeId), so the cluster forms on its own -- no init step. Runs from
## $(NODE_DIR) because Aeron's ClusterConfig puts the cluster and archive dirs
## under the process working directory.
##
## The braces around the backgrounded java command are load bearing. `&` binds
## looser than `&&`, so `cd X && nohup java ... &` backgrounds the whole chain
## as a single job; the redirections then apply only to java while the
## backgrounded subshell still holds the ssh session's stdout/stderr, and ssh
## hangs waiting for them to close. That left the cluster half started with no
## error. Braces scope `&` to java alone, whose fds are all redirected.
## `< /dev/null` and `ssh -n` close off stdin for the same reason.
start:
	for h in $(NODE1):0 $(NODE2):1 $(NODE3):2; do \
		host=$${h%%:*}; id=$${h##*:}; \
		ssh -n $(SSH_OPTS) $(SSH_USER)@$$host "$(CLEAN_NODE)" ; \
		ssh -n $(SSH_OPTS) $(SSH_USER)@$$host \
			"mkdir -p $(NODE_DIR) && cd $(NODE_DIR) && \
			 { nohup java -cp 'libs/*' $(AERON_OPENS) $(JVM_OPTS) $(IDLE_OPTS) $(LEADER_FLAG) \
			   -Daeron.cluster.ingress.channel='$(INGRESS_CHANNEL)' \
			   -Daeron.cluster.tutorial.nodeId=$$id \
			   -Daeron.cluster.tutorial.hostnames=$(HOSTNAMES) \
			   $(NODE_MAIN) > std.log 2>&1 < /dev/null & }" ; \
	done

## Stops the nodes and any load generator left on the client, so a session
## cannot end with something still driving traffic.
stop:
	for h in $(NODES); do \
		ssh -n $(SSH_OPTS) $(SSH_USER)@$$h "$(KILL_NODE)" ; \
	done
	ssh -n $(SSH_OPTS) $(SSH_USER)@$(CLIENT) "$(KILL_CLIENT)"

## Run the load generator from the client box (override THREADS=n).
## --egress_host must be an address the cluster can route replies back to, so
## it is resolved on the client itself rather than taken from .env (which
## carries the client's public IP, not the private one nodes can reach).
client:
	ssh -n $(SSH_OPTS) $(SSH_USER)@$(CLIENT) "$(KILL_CLIENT)"
	ssh -t $(SSH_OPTS) $(SSH_USER)@$(CLIENT) \
		"cd $(CLIENT_DIR) && java -cp 'libs/*' $(AERON_OPENS) \
		   -Daeron.cluster.ingress.channel='$(INGRESS_CHANNEL)' \
		   -Daeron.shared.idle.strategy=$(SPIN_IDLE) \
		   $(CLIENT_MAIN) \
		   --hostnames $(HOSTNAMES) \
		   --egress_host \$$(hostname -I | awk '{print \$$1}') \
		   --port_base $(PORT_BASE) \
		   --thread_num $(THREADS) \
		   --value_size $(VALUE_SIZE) $(LOG_EACH_REQUEST_FLAG)"

logs:
	ssh $(SSH_OPTS) $(SSH_USER)@$(NODE1) "tail -n 100 -f $(NODE_DIR)/std.log"
