# Aeron Cluster — echo service benchmark

Benchmarks [Aeron Cluster](https://github.com/aeron-io/aeron), Real Logic's
Raft implementation, using the echo service that ships in
`io.aeron:aeron-samples`. A client message is sequenced into the replicated
log, committed, delivered to the clustered service, and echoed straight back,
so a round trip measures a full consensus round trip.

The AWS harness (terraform, topology, instance types, `.env` setup) is shared
across raft implementations and documented in the
[root README](../README.md) — this file only covers what's specific to this
product.

## No server code here

The cluster node is `io.aeron.samples.cluster.EchoServiceNode` from the
published `aeron-samples` artifact, run as-is. Its `main()` launches a
complete single-process node — media driver, archive, and consensus module via
`ClusteredMediaDriver`, plus a `ClusteredServiceContainer` running the echo
service — configured by two system properties (`aeron.cluster.tutorial.nodeId`
and `aeron.cluster.tutorial.hostnames`). Everything Aeron is pulled from Maven
Central by Gradle; only the load generator under `loadgen/` is ours.

Membership is static: a node's index in the hostnames list is its member id,
so the cluster forms on its own with no separate initialization step.

## Ports

`ClusterConfig` assigns each member a 100-port block:
`portBase + (memberId * 100) + offset`, where offsets 1–5 are archive-control,
client-facing (ingress), member-facing (consensus), log, and transfer. With the
sample's `portBase` of 9000 a 3-node cluster uses **UDP** 9001–9005,
9101–9105, and 9201–9205.

These need no security-group changes: the rule that permits all protocols and
ports between members of the group already covers both node↔node and
client↔node traffic. Aeron serves no HTTP status page, so unlike the other
products there is nothing to expose to your own address.

## Two non-obvious requirements

Both are handled by the Makefile; they're documented because hitting either
without knowing produces a confusing failure.

1. **JVM module flags.** agrona's `ShutdownSignalBarrier` reflects into
   `jdk.internal.misc.Signal`, which JDK 17+ does not export to unnamed
   modules. Without
   `--add-opens java.base/java.util.zip=ALL-UNNAMED --add-opens java.base/jdk.internal.misc=ALL-UNNAMED`
   every process here — node and client alike — dies at startup with
   `IllegalAccessException`. This is the same set Aeron's own
   `scripts/run-java` uses.
2. **An explicit ingress channel.** `aeron-samples`' `ClusterConfig` does not
   set one, so the consensus module refuses to start with
   `ClusterException: ERROR - ingressChannel must be specified`. The Makefile
   passes `-Daeron.cluster.ingress.channel=aeron:udp?term-length=64k`
   (override with `INGRESS_CHANNEL`).

## Build

```sh
make build          # or: cd loadgen && ./gradlew build
```

Requires a JDK 17 or newer (Aeron 1.48 will not run on less). Two toolchain
traps worth knowing about:

`build` also collects every runtime jar plus the loadgen into `libs/`, which is
what gets pushed. Nodes and client receive the same directory, so no remote
host needs Maven Central access.

## Run locally

Three nodes on one host work unmodified — Aeron derives per-member directory
names, so they don't collide. From a scratch directory (the cluster and archive
directories are created under the working directory):

```sh
LIBS=/path/to/aeron/libs
OPENS="--add-opens java.base/java.util.zip=ALL-UNNAMED --add-opens java.base/jdk.internal.misc=ALL-UNNAMED"
for i in 0 1 2; do
  java -cp "$LIBS/*" $OPENS -Xms1G -Xmx1G \
    -Daeron.cluster.ingress.channel='aeron:udp?term-length=64k' \
    -Daeron.cluster.tutorial.nodeId=$i \
    -Daeron.cluster.tutorial.hostnames=localhost,localhost,localhost \
    io.aeron.samples.cluster.EchoServiceNode > n$i.log 2>&1 &
done
```

Each log should report `[N] Started Cluster Node on localhost...`. Then:

```sh
java -cp "$LIBS/*" $OPENS \
  -Daeron.cluster.ingress.channel='aeron:udp?term-length=64k' \
  io.raftbench.aeron.Loadgen \
  --hostnames localhost,localhost,localhost --egress_host localhost --thread_num 4
```

## Benchmarking on EC2

Prerequisites: the shared fleet deployed from the repo root (`make deploy &&
make env`), and `make build` run locally.

```sh
make provision      # one-time per fleet: install a JDK (the AMI ships none)
make push           # scp libs/ to all instances
make start          # one EchoServiceNode per node; membership is static
make client         # run the load generator on the C&C instance
make logs           # tail the first node's std.log
make stop           # kill the nodes
```

`provision` installs the JDK over ssh rather than through `deploy/main.tf`'s
`user_data`, because changing `user_data` forces instance replacement and would
destroy a running fleet. It needs re-running whenever the fleet is recreated.

## Load generator

`loadgen/` is a small Java client using `AeronCluster` with an `EgressListener`
and an embedded media driver. Each message carries an 8-byte correlation id and
an 8-byte send timestamp; the echo service returns the payload verbatim, so the
client recovers both to compute latency and match each reply to its request.
Out-of-order or missing replies are counted and reported rather than quietly
averaged in.

| Flag | Default | Effect |
|---|---|---|
| `--hostnames` | (required) | comma-separated node addresses; index = member id |
| `--port_base` | `9000` | must match the cluster's port base |
| `--egress_host` | `localhost` | address the cluster sends replies to — must be reachable *from the nodes*, so on EC2 it has to be the client's own private address, never loopback |
| `--thread_num` | `1` | outstanding messages in flight (see below) |
| `--value_size` | `64` | payload bytes; minimum 16 for the id and timestamp |
| `--log_each_request` | off | print every send and receive |

Reports once a second:
`Sending Request to AeronCluster (<endpoints>) at qps=<X> latency=<Y>`, where
latency is the mean round trip in microseconds over that second (a rolling
window, not a cumulative average).

**What `--thread_num` means here.** An `AeronCluster` session is
single-threaded by design, so the load generator runs one thread that keeps N
messages outstanding and polls egress in the same loop, rather than N threads
each holding one request open. The quantity that is comparable with the other
load generators in this repo is the number of outstanding requests, which is
what `--thread_num` sets everywhere; only the mechanism for achieving it
differs.

## Makefile flags

| Flag | Target | Default | Effect |
|---|---|---|---|
| `PORT_BASE` | `client` | `9000` | Cluster port base |
| `THREADS` | `client` | `100` | loadgen `--thread_num`; outstanding requests. All three products in this repo default to 100 so their numbers are directly comparable |
| `VALUE_SIZE` | `client` | `64` | loadgen `--value_size` |
| `LOG_EACH_REQUEST` | `client` | `false` | set to `true` to pass `--log_each_request` |
| `MODE` | `client` | `closed` | `closed` keeps `THREADS` outstanding; `open` emits at `RATE` on a fixed schedule and measures from each request's scheduled send time. See [root README](../README.md#load-modes) |
| `RATE` | `client` | — | requests/sec, required when `MODE=open` |
| `BURST` | `client` | `1` | requests per scheduled instant; same mean rate, clustered arrivals |
| `MAX_INFLIGHT` | `client` | derived | cap on unanswered requests; hitting it counts as dropped-by-rig |
| `WARMUP` | `client` | `10` | seconds discarded before measuring |
| `MEASURE` | `client` | `30` | seconds recorded |
| `DRAIN_TIMEOUT` | `client` | `10` | seconds to wait for in-flight replies after the window closes |
| `PACE` | `client` | `spin` | open-mode wait strategy: `spin` when the client has a spare core, `park` on a shared box |
| `HDR_OUT` | `client` | unset | write a percentile report to this path |
| `JVM_OPTS` | `start` | `-Xms1G -Xmx1G -XX:+AlwaysPreTouch` | node heap; Aeron's own rig uses 4G |
| `INGRESS_CHANNEL` | `start`, `client` | `aeron:udp?term-length=64k` | required, see above |
| `SPIN_IDLE` | `start`, `client` | `org.agrona.concurrent.BusySpinIdleStrategy` | idle strategy for the cluster and driver agents |
| `ARCHIVE_IDLE` | `start` | `org.agrona.concurrent.YieldingIdleStrategy` | idle strategy for the archive agents |
| `APPOINTED_LEADER` | `start` | `0` | member id pinned as leader; empty to elect normally |

```sh
make client                # 100 outstanding requests, the default
make client THREADS=32     # override
```

`client` kills any stale load generator on the client box before starting. That
matters: an interrupted run (Ctrl-C, dropped ssh, a `timeout`) can leave a JVM
alive still driving traffic, and a few of those accumulating will saturate the
client and quietly degrade every later measurement — which is exactly what
happened while writing this, producing a slow drift that looked like the cluster
was at fault. If numbers get worse over a session, check
`pgrep -cf '[L]oadgen'` on the client first.

## Tuning notes

**Idle strategy is the single biggest factor, and the defaults are slow.**
Aeron defaults every agent to
`BackoffIdleStrategy(maxSpins=10, maxYields=20, minPark=1µs, maxPark=1ms)`. On a
cluster that is not saturated the agents go idle between messages, so each
handoff on the request path pays a park wakeup — and a park costs 50-55 µs even
when a core is free, backing off toward 1 ms. Measured on a 3-node multi-AZ EC2
fleet, one outstanding request, changing nothing but the idle strategies:

| Idle strategy | Mean round trip |
|---|---|
| Aeron defaults (backoff/park) | ~2100 µs |
| Spin + yield (what this Makefile now sets) | ~437 µs |

That is 4.8× from configuration alone, and the tuned figure is also far
steadier (436-439 µs across samples versus 1985-2313 µs). It costs CPU: about
310% of each node's 800% sits in spin loops. `SPIN_IDLE` and `ARCHIVE_IDLE`
control this; set both to `org.agrona.concurrent.BackoffIdleStrategy` to measure
Aeron as it ships.

Note the shape of the defaults' penalty: it is *fixed per handoff*, so it hurts
most at low load and washes out as agents stay busy. Latency that barely moves
as concurrency rises is the tell.

It also makes service time depend on the *arrival pattern*, not just the arrival
rate, which is visible as a disagreement between the two load modes: with
parking strategies, open loop at a given rate measured 5.8× the latency closed
loop measured at the same rate, because evenly spaced arrivals leave gaps for
agents to park in while a closed loop never lets them idle. With the
non-parking defaults this Makefile sets, the two modes agree to 13.8% (see the
root README's cross-mode table). If you switch back to parking strategies,
expect that agreement to break and do not read the difference as a rig fault.

**Measured result** on a 3-node multi-AZ fleet of `c6i.2xlarge`, leader
appointed to member 0 (the client's AZ), 100 outstanding requests, 37 second
run:

```
qps=185683 latency=538
qps=184845 latency=540
qps=189614 latency=527
```

Steady at ~185,000 qps and ~540 µs, varying only 526-578 µs across the run.
Little's Law holds exactly (100 ÷ 540 µs = 185,000), so this is latency-bound
rather than any resource ceiling. The leader stayed 52% idle at that rate; the
load generator is the first thing to saturate.

**Leader placement.** `APPOINTED_LEADER` pins the leader (default member 0)
instead of leaving it to election. In the multi_az topology node 0 shares an AZ
with the client, so a leader elsewhere adds a cross-AZ hop to every request; and
because election is nondeterministic, an unpinned cluster gives run-to-run
results that are not comparable. The load generator prints `leader member N` on
connect so every run records which member it measured. (An earlier revision of
this file quantified the co-located/remote gap; those numbers were taken while
stale load generators were competing for the client's CPU, so the multiplier is
withdrawn rather than restated from bad data.)

**Do not benchmark Aeron on a shared or small box.** Each node runs roughly
eight duty-cycle agent threads — media driver conductor/sender/receiver, archive
conductor/recorder/replayer, consensus module, clustered service — so three
nodes want ~24 runnable threads before the load generator's own driver counts.
Aeron's own rig pins each agent to a dedicated core, which says a lot about what
the design assumes. On a 4-core laptop with all three nodes plus the client, one
outstanding request measured ~3300 µs against ~280 µs for a single-node cluster
on the same box, with the machine 95% busy: that 12× is CPU starvation, not
replication cost, since two extra loopback hops are tens of microseconds.
Local 3-node runs are a functional smoke test only.

**Archive durability.** Aeron Archive persists the log, but
`aeron.archive.file.sync.level` defaults to `0` (no fsync), which is the
comparable setting to the no-fsync baselines used elsewhere in this repo.
Raising it to `2` is the durable-write comparison, and only then does moving
the nodes to an instance type with local NVMe matter — see the root README's
"Instance types".

**Leader failure.** Verified by killing the leader mid-run: throughput goes to
zero for the few seconds Aeron takes to elect a new leader, then recovers on
its own with one elevated-latency interval as the queued messages drain. No
client restart or manual step is needed.

## Relationship to Aeron's official benchmarks

[`aeron-io/benchmarks`](https://github.com/aeron-io/benchmarks) has a cluster
benchmark maintained by the Aeron authors. It is deliberately not used here:
it is **open-loop and rate-controlled** (drive a fixed message rate, record an
HdrHistogram), whereas this load generator is **closed-loop** (hold N requests
outstanding, measure the achieved rate), matching the other products in this
repo so the three can be read side by side. It also expects around thirty
environment variables per node, including per-thread CPU pinning, and brings
its own ssh deployment machinery that would duplicate this repo's harness.

Use it, not this, if you want absolute Aeron numbers comparable to Real Logic's
published figures — the two measure different things and will not agree.
