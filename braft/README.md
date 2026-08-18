## Install 

Build tools. Tested with the version installed by default on Ubuntu 22.

```sh
sudo apt install cmake g++ ninja-build flex bison pkg-config
```

Package manager vcpkg.

```sh
git clone https://github.com/microsoft/vcpkg
```

Add location to where you cloned vcpkg to your profile.

```sh
export VCPKG_ROOT='~/workspace/vcpkg'
export PATH="$VCPKG_ROOT:$PATH"
```

## Build

You may need to change paths to where your tools are installed in CMakePresets.json.

Configure. `default` (and its alias `release`) build with optimizations —
this is a benchmarking repo, so that's the default. Use `debug` instead only
when you need unoptimized builds with assertions for troubleshooting a crash;
debug binaries will badly distort any latency/throughput numbers.

```sh
cmake --preset default .
```

Build.

```sh
cmake --build build
```

## Run locally

Start a local cluster of 3 nodes and tail the log of the first one.

```sh
./run_server.sh && \
tail -f runtime/0/std.log
```

Run the client in another terminal.

```sh
./run_client.sh
```

## Benchmarking on EC2

Measures the latency and throughput floor of a real 3-node cluster: 3 raft
nodes plus one command-and-control instance running the client. The AWS
harness (terraform, topology, instance types, `.env` setup) is shared across
raft implementations and documented in the [root README](../README.md) — this
section only covers what's specific to this atomic-counter example.

The benchmark runs with `-raft_sync=false` (the `run_server.sh` default), so
log writes go to the page cache and the floor is bound by network round trips,
not disk — consistent with the root README's default instance-type rationale
(no fsync, no need for local NVMe; 32-byte entries are well under 1 Gbps even
at high qps). To test `-sync=true`, switch nodes to `c6id.2xlarge` (see root
README's "Instance types").

Prerequisites: the shared fleet already deployed from the repo root (`make
deploy && make env`), and the binaries built locally (see Build above).

```sh
make push          # scp binaries and run scripts to all instances
make start         # start one atomic_server per node, peered over private IPs
make client        # open loop at the default 100k req/s
make logs          # tail the first node's std.log
make stop          # kill the servers
```

Config comes from the shared `../.env` (see root `.env.example`); the flags below can be overridden on the make command line.

| Flag | Target | Default | Effect |
|---|---|---|---|
| `PORT` | `start`, `client` | `8300` | Raft RPC/stats port on every node |
| `SYNC` | `start` | `false` | `-raft_sync`; `true` fsyncs the log on every commit |
| `PIPELINE` | `start` | `4` | `-raft_max_parallel_append_entries_rpc_num`; in-flight AppendEntries RPCs per follower. braft's default of 1 forces a full round trip before the next entry can go out. 4 and 8 measure alike; 16 is worse |
| `AE_CACHE` | `start` | `true` | `-raft_enable_append_entries_cache`; followers hold an AppendEntries that arrives ahead of a gap instead of rejecting it. braft's default is `false`, which costs a wasted round trip per rejection. This is the setting that fixed braft's tail — see below |
| `AE_CACHE_SIZE` | `start` | `8` | `-raft_max_append_entries_cache_size`; how many out-of-order RPCs a follower may hold |
| `LEADER_BATCH` | `start` | `256` | `-raft_leader_batch`; appends coalesced into one disk-queue flush. Raising it to 4096 made no measurable difference |
| `APPLY_BATCH` | `start` | `32` | `-raft_apply_batch`; committed entries handed to the state machine per batch. 1024 made no difference |
| `FSM_COMMIT_BATCH` | `start` | `512` | `-raft_fsm_caller_commit_batch`; entries committed to the FSM per batch |
| `SEGMENT_SIZE` | `start` | `8388608` | `-raft_max_segment_size`; log segment bytes before rollover. Rollover measured 0–4 µs, so this was never worth changing |
| `TRACE_LAT` | `start` | `false` | `-raft_trace_append_entry_latency`; log leader appends slower than `HIGH_LAT_US` with a phase breakdown (queue wait / segment open / write / sync) |
| `HIGH_LAT_US` | `start` | `1000000` | `-raft_append_entry_high_lat_us`; the threshold for the above |
| `EVENT_DISPATCHERS` | `start`, `client` | `1` | `-event_dispatcher_num` on nodes and client alike; brpc pthreads doing epoll plus socket read/parse. Raising it to 4 measured no effect — the leader was never CPU-bound |
| `CONNECTION_TYPE` | `client` | unset | `--connection_type` on the client's channel. Empty leaves brpc's default (`single`: one multiplexed connection). `pooled` gives each in-flight RPC its own connection and **fails at 100k** — ephemeral port exhaustion |
| `CHANNELS` | `client` | `1` | distinct connections the open-loop client round-robins over, via brpc `connection_group`. 4/8/16 measured no effect |
| `SERVER_CONCURRENCY` | `start` | `18` | `-bthread_concurrency` on the nodes, against brpc's default of 9. Nominally over-provisioned for 8 vCPUs, but matching it to the core count measures worse |
| `THREADS` | `client` | `100` | `-thread_num`; concurrent sending threads on the load generator. All three products in this repo default to 100 outstanding requests so their numbers are directly comparable |
| `CLIENT_CONCURRENCY` | `client` | `$(THREADS)` | `-bthread_concurrency` on the client; kept equal to `THREADS` by default so sending threads never queue for fewer real workers than they need — which would inflate measured latency with client-side scheduling delay rather than the cluster's own latency. Set explicitly to reintroduce that mismatch on purpose |
| `MODE` | `client` | `open` | `open` (the default) emits at `RATE` on a fixed schedule and measures from each request's scheduled send time; it requires `RATE`. `closed` keeps `THREADS` outstanding instead and cannot show the knee. See [root README](../README.md#load-modes) |
| `RATE` | `client` | `100000` | requests/sec offered in open mode. 100k is inside braft's ~150k comfort zone |
| `BURST` | `client` | `1` | requests per scheduled instant; same mean rate, clustered arrivals |
| `MAX_INFLIGHT` | `client` | derived | cap on unanswered requests; hitting it counts as dropped-by-rig |
| `WARMUP` | `client` | `10` | seconds discarded before measuring |
| `MEASURE` | `client` | `30` | seconds recorded |
| `DRAIN_TIMEOUT` | `client` | `10` | seconds to wait for in-flight replies after the window closes |
| `PACE` | `client` | `spin` | open-mode wait strategy: `spin` when the client has a spare core, `park` on a shared box |
| `HDR_OUT` | `client` | unset | write a percentile report to this path |

Examples:

```sh
make start PIPELINE=8              # more AppendEntries pipelining per follower
make start SYNC=true PIPELINE=8    # fsync + more pipelining
make client                        # open loop at the default 100k req/s
make client MODE=closed            # 100 outstanding requests instead
make client MODE=closed THREADS=32 # override; CLIENT_CONCURRENCY follows automatically
```

### Multi-AZ: designating a starting leader

After `make deploy TOPOLOGY=multi_az` from the repo root (see root README),
`make push start` here, then:

```sh
make transfer-leader            # designate NODE1 as the starting leader
make client
```

`make transfer-leader` forces `NODE1` to be the starting leader (needs
`braft_cli`, built separately — see the comment above `BRAFT_CLI` in the
Makefile). braft can still re-elect a different leader afterward (timeout,
failure, or another `make transfer-leader`), at which point the client is
talking to a remote leader and multi-AZ RTT applies to every write, not just
the replication leg — that's expected, not a bug.

### Node stats pages

Each node's brpc server exposes braft's builtin stats/status pages over HTTP
on the same port it serves raft RPCs on (brpc multiplexes protocols per
connection, so there's no separate stats port). With a node's public IP from
`../.env` and the default port:

```
http://$NODE1:8300/          # index of builtin services
http://$NODE1:8300/raft_stat # per-group raft state: term, role, log indexes
http://$NODE1:8300/status
http://$NODE1:8300/vars
```

The security group opens this port to `ssh_ingress_cidr` only (same CIDR as
ssh). If you override `PORT` on `make start`, also set `-var raft_port=<port>`
on `terraform apply` (or edit the default in `deploy/main.tf`) so the security
group rule matches.


## Non-default settings

Everything this harness runs with that differs from upstream, and the `make`
variable that controls it. Overriding any of these on `make start` reproduces the
upstream behaviour.

| Setting | Upstream | Here | `make` variable | Why |
|---|---|---|---|---|
| `raft_enable_append_entries_cache` | `false` | **`true`** | `AE_CACHE` | The single biggest win in this repo: p99 at 100k from 6171 µs to ~1030 µs and the comfort zone from 70k to 150k. See below |
| `raft_max_parallel_append_entries_rpc_num` | `1` | **`4`** | `PIPELINE` | Upstream forces a full round trip before the next AppendEntries can go out, capping throughput under concurrency. 4 and 8 measure alike; 16 is worse |
| `raft_sync` | `true` | **`false`** | `SYNC` | No fsync on the commit path. Durability then rests on quorum replication rather than local disk, which is the configuration being benchmarked — see the root README |
| `bthread_concurrency` (brpc) | `9` | **`18`** | `SERVER_CONCURRENCY` | Kept at the stock value of this harness. Matching it to the box's 8 vCPUs measures *worse* (p50 1908/1366 µs against 1058/1020), so the oversubscription is not hurting |

Exposed as knobs but left at upstream values, because each was measured and made no
difference or was worse: `raft_max_entries_size` (1024), `raft_max_body_size`
(512 KB), `raft_apply_batch` (32, `APPLY_BATCH`), `raft_fsm_caller_commit_batch`
(512, `FSM_COMMIT_BATCH`), `raft_leader_batch` (256, `LEADER_BATCH`),
`raft_max_append_buffer_size` (256 KB), `raft_max_segment_size` (8 MB,
`SEGMENT_SIZE`), `raft_max_append_entries_cache_size` (8, `AE_CACHE_SIZE`),
`event_dispatcher_num` (1, `EVENT_DISPATCHERS`).

Two diagnostics rather than settings: `TRACE_LAT=true` turns on
`raft_trace_append_entry_latency`, and `HIGH_LAT_US` sets the threshold above which
a slow leader append is logged with a phase breakdown. `CONNECTION_TYPE` and
`CHANNELS` are client-side and default to brpc's own behaviour.

## What fixed braft's tail: `raft_enable_append_entries_cache`

Off by default in braft, and worth more than every other knob combined.

With `PIPELINE=4` the leader keeps up to four AppendEntries in flight per follower.
When one reaches a follower ahead of a gap in its log, the default behaviour is to
**reject** it, so the leader retries from an earlier index and the work of that round
trip is discarded. The flag makes the follower hold the out-of-order RPC until the
gap fills instead. `handle_out_of_order_append_entries` in braft's `node.cpp` is the
path; it returns early unless the flag is set.

At 100k, on a 3-node multi-AZ cluster:

| | p50 | p99 | p99/p50 | comfort zone |
|---|---|---|---|---|
| `AE_CACHE=false` (upstream) | 970 µs | 6171 µs | 8.3x | 70k |
| `AE_CACHE=true` (default here) | 770 µs | 1032 µs | **1.3x** | **150k** |

Every 100k run with the cache on carried the full load: 3,000,000 requests
completed, 10 dropped-by-rig — the rig's startup burst and nothing else — 0
unanswered, schedule lag p99 under 70 µs. Raw runs in
[../sweep/braft-ae-cache.csv](../sweep/braft-ae-cache.csv) and
[../sweep/braft-knee-runs.csv](../sweep/braft-knee-runs.csv).

It also changed the *shape* of the curve, not just its level. Before, braft had two
widely separated knees — the tail turned at 75k while p50 held to about 105k. After,
the two break together at 150–160k, because what made the tail lead was retry
traffic rather than queueing. And it explains a result that had looked anomalous:
`PIPELINE=16` measured worse than 4 with the cache off, because deeper pipelining
creates more reordering and therefore more rejected RPCs. With the cache on,
`PIPELINE=16` is fine again (p99 1188/1199 µs) — just no longer better than 4.

### How it was found, and what it cost to find

Worth recording because the search was almost entirely wrong before it was right.
Two rounds of tuning at a fixed 100k, 21 runs in the first
([../sweep/braft-tuning.csv](../sweep/braft-tuning.csv)), found nothing:

| Tried | Result |
|---|---|
| `EVENT_DISPATCHERS=4` (brpc socket-read threads) | no effect beyond noise |
| `CHANNELS=4/8/16` (separate connections via `connection_group`) | no effect |
| `CONNECTION_TYPE=pooled` | fails outright — ephemeral port exhaustion at 100k |
| `PIPELINE=16` | worse |
| `SERVER_CONCURRENCY=8` (match vCPUs) | worse |
| `BURST=1` | worse above ~40k |
| `LEADER_BATCH=4096`, `APPLY_BATCH=1024` | no effect |
| Lowering `MAX_INFLIGHT` | "improves" p99 by discarding load — not a fix, see below |

What redirected the search was measurement, not more guessing:

1. **Per-thread CPU on the leader** (`top -H -p $(pgrep atomic_server)`) showed 18
   brpc workers at 21–27% each — about 54% of 8 vCPUs, evenly spread, nothing
   pegged. That rules out the entire family of "some thread is saturated"
   explanations, and it is why every parallelism flag above failed.
2. **braft's own tracer** (`TRACE_LAT=true HIGH_LAT_US=500`) logs slow leader
   appends split into `bthread_queue_time_us` / `open_segment_time_us` /
   `append_entry_time_us` / `sync_segment_time_us`. At a 3 ms threshold it logged
   **zero** appends across 3M requests. At 500 µs it caught 0.17% of them, dominated
   by queue wait — so the log path is genuinely serialized, but only past p99.8.
3. **`/vars`** gave the rest: `raft_storage_append_entries_latency` is 24 µs at p99
   on the leader and 112–114 µs on the followers, and
   `raft_storage_append_entries_concurrency` reads 1.

That produced a budget in which almost nothing was service time:

| component | p99 |
|---|---|
| leader log append | 24 µs |
| follower log append | 112–114 µs |
| quorum network RTT (`ping`) | 390 µs |
| **sum** | **~530 µs** |
| **observed client p99** | **6171 µs** |
| unaccounted | ~5640 µs (91%) |

I first read that 91% as queueing, which was wrong — it was retry round trips, and
the cache removed it. The useful lesson is the order of operations: the phase
breakdown and `/vars` cost two runs and pointed at the answer, while the flag ladder
cost 21 runs and found nothing.

**On `MAX_INFLIGHT` as a "fix".** Lowering it does improve the tail, and it is not a
fix. It bounds how deep the queue may get, so it also bounds how bad latency can
look — by discarding load. At `MAX_INFLIGHT=250` the old configuration reported p99
3121 µs while never placing 1.5% of the offered requests, so the cluster was really
asked for 98.5k, and the tail looked good because the hardest requests were the ones
dropped. Acceptable as a client design, fatal as a benchmark number; see the root
README's dropped-by-rig section.

### What is left, if 150k is not enough

91% of the *remaining* tail is still round-trip-bound rather than CPU-bound, and
entries must be appended, replicated, quorum-acked, committed and applied in order.
So the lever is the pipeline's cycle time, and the quorum hop — 390 µs here — is its
clock.

- **Shorten the quorum hop within multi-AZ.** Commit needs only the *nearer* of two
  followers, so the minimum RTT matters, not the average. This fleet measures
  0.387 ms and 0.451 ms; AZ-to-AZ distance is not uniform and AZ ids are
  per-account, so sweeping `node_azs` with `make node-rtt` and keeping the closest
  pair is free latency.
- **Single-AZ** would cut the clock several-fold and move the knee furthest, but it
  is excluded by the availability requirement that makes multi-AZ the default.
- **More raft groups** raises aggregate throughput without moving the knee of one
  group, which is the figure measured here.
