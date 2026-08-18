## Install

Build tools and the vcpkg package manager. Tested with the versions installed by
default on Ubuntu 22.

```sh
sudo apt install cmake g++ ninja-build flex bison pkg-config
git clone https://github.com/microsoft/vcpkg
export VCPKG_ROOT='~/workspace/vcpkg'
export PATH="$VCPKG_ROOT:$PATH"
```

## Build

```sh
make build
```

Builds three binaries as Release — this is a benchmarking repo, so that's the
default preset, and a debug build would badly distort every latency/throughput
number below. See [Development](#development) for the raw `cmake` steps this
wraps, and for how to build a debug binary instead when troubleshooting a
crash.

| Binary | What it is |
|---|---|
| `atomic_server` | The raft node — braft's stock atomic-counter example (int64 compare-exchange over a brpc service), run three times to form the cluster |
| `atomic_client` | The load generator this repo adds — open- and closed-loop modes, HdrHistogram percentiles; everything under [Benchmark on EC2](#benchmark-on-ec2) below drives this |
| `test` | A small one-shot CLI (`--atomic_op=get\|set\|cas`) for poking a running cluster by hand — not used by the benchmark itself, useful for sanity-checking a deploy |

## Benchmark on EC2

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

Config comes from the shared `../.env` (see root `.env.example`); every flag
shown here can be overridden on the `make` command line. The full list, including
the ones still at their default, is one reference table in
[Development](#make-flag-reference); the four that differ from upstream (`SYNC`,
`PIPELINE`, `AE_CACHE`, `SERVER_CONCURRENCY`) have their own table and the story
behind each right below, since knowing *that* they changed matters more than
knowing they exist.

Examples:

```sh
make start SYNC=true               # fsync on top of this repo's other defaults
make start PIPELINE=4 AE_CACHE=false  # reproduce upstream braft's behaviour
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
| `raft_max_parallel_append_entries_rpc_num` | `1` | **`8`** | `PIPELINE` | Upstream forces a full round trip before the next AppendEntries can go out, capping throughput under concurrency. Pushed the comfort zone from 150k to 160k with no cost at typical rates; 16 is worse. See [Deciding PIPELINE](#deciding-pipeline) |
| `raft_sync` | `true` | **`false`** | `SYNC` | No fsync on the commit path. Durability then rests on quorum replication rather than local disk, which is the configuration being benchmarked — see the root README |
| `bthread_concurrency` (brpc) | `9` | **`18`** | `SERVER_CONCURRENCY` | Kept at the stock value of this harness. Matching it to the box's 8 vCPUs measures *worse* (p50 1908/1366 µs against 1058/1020), so the oversubscription is not hurting |

Everything else this harness exposes as a `make` variable is still at its
upstream or brpc default, because it was measured and made no difference (or was
worse) — see [Development](#development) for the full list and the two
diagnostic flags (`TRACE_LAT`, `HIGH_LAT_US`).

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
| `AE_CACHE=true`, `PIPELINE=4` | 770 µs | 1032 µs | **1.3x** | **150k** |

Every 100k run with the cache on carried the full load: 3,000,000 requests
completed, 10 dropped-by-rig — the rig's startup burst and nothing else — 0
unanswered, schedule lag p99 under 70 µs. Raw runs in
[../sweep/braft-ae-cache.csv](../sweep/braft-ae-cache.csv) and
[../sweep/braft-knee-runs.csv](../sweep/braft-knee-runs.csv).

That row is the cache's effect in isolation, `PIPELINE` held at 4 in both arms so
the comparison attributes cleanly. A later round raised `PIPELINE` to 8 on top of
the cache and pushed the comfort zone again, 150k to 160k — see
[Deciding PIPELINE](#deciding-pipeline) below; both charts
in this section already reflect `PIPELINE=8`, this repo's current default.

It also changed the *shape* of the curve, not just its level. Before, braft had two
widely separated knees — the tail turned at 75k while p50 held to about 105k. After
both the cache and the later `PIPELINE=8` step, the two percentiles break together,
now at 160–165k, because what made the tail lead the median was retry traffic
rather than queueing — once that's gone, there's nothing left to separate them.

| Before: upstream `braft`, `PIPELINE=4` | After: this repo's current default |
|---|---|
| ![braft before the append-entries cache: two separate knees, the tail turning at 65k while p50 holds to roughly 90k](../knee-braft-before-ae-cache.svg) | ![braft after the append-entries cache and PIPELINE=8: one knee, both percentiles flat together to 160k then breaking as one](../knee-braft.svg) |

Same axes, same rig, same fleet class — only the server flags differ. The left
chart is [an earlier revision of this repo's own chart](https://github.com/olegabu/raft-tests/blob/59e4e51/knee-braft.svg),
restored rather than redrawn, so it is exactly what shipped before this fix
existed. The chart on the right is the one referenced throughout this README as
"the knee".

This also explains a result that had looked anomalous: `PIPELINE=16` measured
worse than 4 with the cache off, because deeper pipelining creates more reordering
and therefore more rejected RPCs. With the cache on, `PIPELINE=16` is fine again
(p99 1188/1199 µs) — just no longer better than the now-default 8.

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

### Deciding PIPELINE

**Decision: `PIPELINE=8`, `AE_CACHE_SIZE` left at 8. Adopted as this repo's
default.** The brief was to optimize for a tight p99/p50 ratio, not the lowest
p50, so that's the criterion applied below.

The reasoning going in was that `PIPELINE` and `AE_CACHE_SIZE` are coupled —
`AppendEntriesCache::store` in braft's `node.cpp` keys cached RPCs by
`prev_log_index`, one entry per out-of-order arrival, so with `PIPELINE=N` in
flight per follower a worst-case reorder can leave up to `N-1` of them in the
cache at once. At `PIPELINE=4`/`AE_CACHE_SIZE=8` that's 8 against a worst case
of 3, about 2.7x headroom; raising `PIPELINE` to 8 without raising the cache to
match would only leave 7-against-8, under 1.2x — on paper, a bad idea to do
alone. Measured, on a fresh fleet
([../sweep/braft-pipeline-cache.csv](../sweep/braft-pipeline-cache.csv)):

| config | 55k / 100k p99 (no regression check) | 150k p99 (3 reps) | 160k p99, dropped |
|---|---|---|---|
| `PIPELINE=4` (old default) | 970\*/1022, 1039 | 8895, 9287, 4163 | 13703 (0.84%), 13655 (0.39%) |
| `PIPELINE=8`, cache **8** (adopted) | 776, 795 / 983, 986 | 3029\*\* | **7643 (0.00%), 6087 (0.00%)** |
| `PIPELINE=8`, cache **16** (paired, rejected) | 983, 1009 (100k only) | 11927, 3977, 2879 | 11823 (0.10%), 13391 (0.23%) |

\*from the published sweep, first fleet, for reference only. \*\*mean of two reps,
1282/2709 and 1244/3349, µs p50/p99, both drop 10.

**Why p99 favors `PIPELINE=8` cleanly.** At 160k, two reps gave 10 dropped-by-rig
each — the fixed startup cost, nothing else — against `PIPELINE=4`'s 18,514 and
40,526 at the identical rate: no overlap between the two groups. p99 there is
about 2x tighter as a direct consequence (real overload dropped, not just
averaged down).
And it costs nothing at rates you'd actually run: p99 at 55k and 100k is equal or
slightly *better* than `PIPELINE=4`'s, not worse — the fear that a deeper pipeline
trades typical-case latency for headroom didn't materialize.

**Why not the paired `AE_CACHE_SIZE=16`, against the a priori expectation.**
Pairing it did not reproduce `PIPELINE=8` alone's clean 160k result: both reps
show real drops (4,970 and 11,213) where the unpaired version showed none. The
theory said undersizing the cache should be the worse choice; the data says the
opposite, at least at these batch sizes and on this fleet, and there's no
confirmed explanation — the leading guess is that reordering deep enough to
matter is rare enough at `PIPELINE=8` that the default cache of 8 already covers
it, and the larger cache (a bigger `std::map` to search and evict from) is pure
overhead rather than useful headroom. Not isolated further. This fleet's own
noise floor is wide enough (`PIPELINE=4`'s three 150k p99 reps span 4163–9287;
the paired config's span 2879–11927) that a factor-of-2 comparison from two or
three reps deserves a discount — what survives that discount is the 160k
drop-count gap, 10 and 10 against four other reps all in the thousands to
hundreds of thousands, with no overlap.

With `PIPELINE=8` adopted, braft's whole curve was re-swept
([../sweep/braft-pipeline8-knee.csv](../sweep/braft-pipeline8-knee.csv), 26 runs)
and both `knee-braft.svg` and the root README's numbers now reflect it: comfort
zone **150k → 160k**, knee **160k → 165k**, p99/p50 ratio at the comfort-zone
edge **2.80x** (was already past 3x at the old edge under `PIPELINE=4`). See the
before/after charts under [What fixed braft's tail](#what-fixed-brafts-tail-raft_enable_append_entries_cache)
above.

**Two more brpc knobs, tested at the same time — neither adopted.**
`EVENT_DISPATCHERS=2` had no effect, matching the earlier `=4` result (100k gave
771/1019 and 775/1045 µs against `PIPELINE=4`'s 764/1039 and 763/1022 —
indistinguishable); expected, since the leader has never been CPU-bound (see
[How it was found](#how-it-was-found-and-what-it-cost-to-find)).
`SERVER_CONCURRENCY=6` showed p50 consistently lower at every rep (100k: 715,
718 vs 764, 763; 150k: 883, 855 vs 2131, 1995, 1634) but p99 mixed — one 150k
outlier at 19,919 µs that a second rep didn't reproduce (1,189 µs). Promising,
not conclusive at two reps; a proper multi-rate sweep would settle it, and
would need to be re-run against `PIPELINE=8` now that it's the default, not the
`PIPELINE=4` baseline it was tested against. Not adopted pending that.

## Development

Manual steps and implementation-detail settings that a benchmark run doesn't
need — useful for extending or troubleshooting this harness, not for using it.

### Building manually

`make build` (above) wraps this:

```sh
cmake --preset default .    # 'default' and its alias 'release' build with
                             # optimizations -- see debug note below
cmake --build build
```

You may need to change tool paths in `CMakePresets.json` first. Use
`cmake --preset debug .` instead of `default` only when you need an unoptimized
build with assertions to troubleshoot a crash; debug binaries badly distort
every latency/throughput number in this README.

### Running locally

Skips AWS entirely — a 3-node cluster and client on one machine, useful for a
quick sanity check before touching the fleet:

```sh
./run_server.sh && tail -f runtime/0/std.log   # start 3 nodes, tail node 0
./run_client.sh                                # in another terminal
```

### `make` flag reference

Every flag this harness's Makefile exposes, default or not — the four that
changed from upstream (`SYNC`, `PIPELINE`, `AE_CACHE`, `SERVER_CONCURRENCY`) are
included here too for completeness, but their story lives in
[Non-default settings](#non-default-settings) and the two sections after it;
this table exists to answer "what can I pass to `make`", not "what did you find".

| Flag | Target | Default | Effect |
|---|---|---|---|
| `PORT` | `start`, `client` | `8300` | Raft RPC/stats port on every node |
| `SYNC` | `start` | `false` | `-raft_sync`; `true` fsyncs the log on every commit. Non-default — upstream is `true` |
| `PIPELINE` | `start` | `8` | `-raft_max_parallel_append_entries_rpc_num`; in-flight AppendEntries RPCs per follower. Non-default — upstream is `1` |
| `AE_CACHE` | `start` | `true` | `-raft_enable_append_entries_cache`; followers hold an out-of-order AppendEntries instead of rejecting it. Non-default — upstream is `false` |
| `AE_CACHE_SIZE` | `start` | `8` | `-raft_max_append_entries_cache_size`; out-of-order RPCs a follower may hold. At upstream default |
| `SERVER_CONCURRENCY` | `start` | `18` | `-bthread_concurrency` on the nodes. Non-default — brpc's own default is `9` |
| `LEADER_BATCH` | `start` | `256` | `-raft_leader_batch`; appends coalesced into one disk-queue flush. At upstream default |
| `APPLY_BATCH` | `start` | `32` | `-raft_apply_batch`; committed entries per FSM batch. At upstream default |
| `FSM_COMMIT_BATCH` | `start` | `512` | `-raft_fsm_caller_commit_batch`. At upstream default |
| `SEGMENT_SIZE` | `start` | `8388608` | `-raft_max_segment_size`; log segment bytes before rollover. At upstream default (8 MB) |
| `EVENT_DISPATCHERS` | `start`, `client` | `1` | `-event_dispatcher_num` (brpc) on nodes and client alike. At brpc's own default |
| `TRACE_LAT` | `start` | `false` | `-raft_trace_append_entry_latency`; log slow leader appends with a phase breakdown |
| `HIGH_LAT_US` | `start` | `1000000` | `-raft_append_entry_high_lat_us`; threshold for the above, microseconds |
| `CONNECTION_TYPE` | `client` | unset | `--connection_type` on the client's channel. Empty leaves brpc's default (`single`) |
| `CHANNELS` | `client` | `1` | Distinct connections the open-loop client round-robins over, via brpc `connection_group` |
| `THREADS` | `client` | `100` | `-thread_num`; concurrent sending threads. All three products default to 100 so their numbers are comparable |
| `CLIENT_CONCURRENCY` | `client` | `$(THREADS)` | `-bthread_concurrency` on the client; kept equal to `THREADS` so sending threads never queue for fewer real workers than they need |
| `MODE` | `client` | `open` | `open` emits at `RATE` on a fixed schedule; requires `RATE`. `closed` keeps `THREADS` outstanding instead. See [root README](../README.md#load-modes) |
| `RATE` | `client` | `100000` | Requests/sec offered in open mode |
| `BURST` | `client` | `1` | Requests per scheduled instant; same mean rate, clustered arrivals |
| `MAX_INFLIGHT` | `client` | derived | Cap on unanswered requests; hitting it counts as dropped-by-rig |
| `WARMUP` | `client` | `10` | Seconds discarded before measuring |
| `MEASURE` | `client` | `30` | Seconds recorded |
| `DRAIN_TIMEOUT` | `client` | `10` | Seconds to wait for in-flight replies after the window closes |
| `PACE` | `client` | `spin` | Open-mode wait strategy: `spin` with a spare core, `park` on a shared box |
| `HDR_OUT` | `client` | unset | Write a percentile report to this path |

The rig-mechanics rows (`THREADS` through `HDR_OUT`) are shared verbatim across
all three products and explained in more depth once, in the
[root README](../README.md#load-modes), rather than repeated per product here.

Not exposed as a `make` variable at all, so these sit at whatever
`run_server.sh`'s own `shflags` defaults are — which mirror upstream, and were
never worth wiring through because no finding ever pointed at them:
`raft_max_entries_size` (`1024`), `raft_max_body_size` (`524288`, 512 KB),
`raft_max_append_buffer_size` (`262144`, 256 KB).

Every default-value row above was measured against changing it and found no
difference or worse — see
[How it was found](#how-it-was-found-and-what-it-cost-to-find) for
`AE_CACHE_SIZE`, `EVENT_DISPATCHERS`, `LEADER_BATCH`, `APPLY_BATCH` and
`FSM_COMMIT_BATCH`, and [Deciding PIPELINE](#deciding-pipeline)
for `CONNECTION_TYPE` and `CHANNELS` — `pooled` fails outright at 100k on
ephemeral port exhaustion, and extra `CHANNELS` had no effect.
