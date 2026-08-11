# openraft — raft-kv-memstore benchmark

Benchmarks [openraft](https://github.com/databendlabs/openraft) using its
networked `raft-kv-memstore` example (an in-memory key-value store speaking
HTTP/JSON), not the repo's own `cluster_benchmark` — that one runs in-memory
with no network, single process, and is explicitly captioned by its authors
as "NOT a real world application benchmark."

The AWS harness (terraform, topology, instance types, `.env` setup) is
shared across raft implementations and documented in the
[root README](../README.md) — this file only covers what's specific to this
product.

## Vendored server code

`vendor/` contains `raft-kv-memstore` and its five support crates
(`app-http`, `log-mem`, `network-v2-http`, `sm-mem`, `types-kv`), copied from
upstream's `examples/` directory rather than built from a separate clone —
see `vendor/NOTICE.md` for exactly what was changed (the `openraft`
dependency swapped from a workspace path to the published crates.io version,
and one dead trait impl removed). openraft itself is a normal Cargo
dependency (`openraft = "0.10.0-alpha.33"`, pinned to match the exact
upstream commit this was vendored from — the crates.io *stable* release is
older, `0.9.25`, and would risk API drift against this vendored example code).

## Build

```sh
cd vendor/raft-kv-memstore && cargo build --release
cd ../../loadgen && cargo build --release
```

(or `make build` from this directory, which does both). **Release matters
here** — same trap as braft's debug-vs-release issue earlier, arguably worse
in Rust (no inlining, overflow checks in debug builds).

Requires a current stable Rust toolchain (`rustup update stable`) —
`raft-kv-memstore`'s `edition = "2024"` needs rustc ≥ 1.85.

## Run locally

Unlike braft, there's no static peer/conf list — cluster membership is
entirely runtime-configured over HTTP. Start 3 nodes, each with its own
`--id`/`--api-addr`/`--raft-addr`:

```sh
BIN=vendor/raft-kv-memstore/target/release/raft-key-value
$BIN --id 1 --api-addr 127.0.0.1:21001 --raft-addr 127.0.0.1:22001 &
$BIN --id 2 --api-addr 127.0.0.1:21002 --raft-addr 127.0.0.1:22002 &
$BIN --id 3 --api-addr 127.0.0.1:21003 --raft-addr 127.0.0.1:22003 &
```

Then form the cluster (init node 1 as the sole member, add the other two as
learners, promote all three to voters):

```sh
./init-cluster.sh 127.0.0.1:21001 \
  2:127.0.0.1:21002:127.0.0.1:22002 \
  3:127.0.0.1:21003:127.0.0.1:22003
```

Run the load generator against it:

```sh
loadgen/target/release/loadgen --peers 127.0.0.1:21001,127.0.0.1:21002,127.0.0.1:21003 --thread_num 4
```

## Benchmarking on EC2

Prerequisites: the shared fleet already deployed from the repo root (`make
deploy && make env`), and both binaries built locally (see Build above).

```sh
make push          # scp the server binary, init-cluster.sh, and loadgen to all instances
make start         # start one raft-key-value process per node
make init-cluster   # one-time: form the 3-node cluster (skip on subsequent restarts)
make client         # run the load generator on the C&C instance
make logs           # tail the first node's std.log
make stop           # kill the servers
```

## Load generator

`loadgen/` deliberately mirrors braft's `client.cpp`/`run_client.sh` in
design and configuration options, adapted where the platform genuinely
differs:

| braft flag | loadgen flag | Notes |
|---|---|---|
| `--peers` | `--peers` | comma-separated `api_addr` list instead of `ip:port:index` — openraft has no static conf format, just addresses |
| `--thread_num` | `--thread_num` | concurrent sending tasks (tokio tasks, not OS threads) |
| `--bthread_concurrency` | `--worker_threads` | renamed to match the platform: bthread is a brpc-specific concept, Tokio's runtime worker-thread count is the direct analog. Same *purpose* (real thread pool size vs. logical concurrency, keep `>=` thread_num so tasks don't queue for fewer real workers than they need), different name |
| `--log_each_request` | `--log_each_request` | same name and behavior, but a bare switch (clap flag) rather than a `true`/`false`-valued string — the Makefile's `LOG_EACH_REQUEST=true` translates this for you |
| `--add_percentage` | `--cas_percentage` | repurposed, see below |
| (n/a) | `--value_size` | new — openraft values are arbitrary strings, not braft's fixed 64-byte register, so payload size needs its own knob |
| (n/a) | `--key` | new — the single key all tasks contend on, matching braft-atomic's single replicated register rather than spreading writes across a key space |

**Why `--add_percentage` became `--cas_percentage`, not a literal port**:
braft's `fetch_add` is *one* RPC — the server does read-modify-write
atomically inside `on_apply`. `raft-kv-memstore`'s `CompareAndSet` requires
the *client* to already know `expected_version`, so a client-side "increment"
is read-then-CAS — **two** round trips, not one. To keep the default workload
cost-comparable to braft's single-RPC default, the loadgen defaults to
`/write` `Set` (one RPC, unconditional) and `--cas_percentage` (default 0)
opts a fraction of requests into the full read+CompareAndSet sequence for
anyone who specifically wants to exercise that path. Latency/qps numbers
with `--cas_percentage > 0` are not RPC-count comparable to braft's.

**Leader tracking**: starts with `peers[0]` as the initial guess (mirrors
braft's initial route-table guess). Two distinct failure modes are handled
differently, both confirmed against a real cluster:
- A live node says "I'm not the leader" (`ForwardToLeader` in the response
  body) → jump straight to the leader address it names. Same shape as
  braft's `braft::rtb::update_leader` + retry.
- The tracked leader is simply *unreachable* (killed, mid-election) → there's
  no redirect to react to, so the loadgen cycles to the next known peer until
  one responds. Verified by killing the live leader mid-benchmark: qps drops
  to 0 for the duration of openraft's own election (a few seconds, not a
  loadgen issue), then recovers automatically with no manual intervention.

**Reporting**: every 1s, a line in the same shape as braft's client —
`Sending Request to OpenRaft (<peers>) at qps=<X> latency=<Y>` (µs) — a
rolling window count/average, not cumulative, so output from both products
reads the same way side by side.

## Makefile flags

| Flag | Target | Default | Effect |
|---|---|---|---|
| `API_PORT` | `start`, `client`, `init-cluster` | `21001` | Client-facing HTTP API port |
| `RAFT_PORT` | `start`, `init-cluster` | `22001` | Inter-node raft RPC port |
| `THREAD_NUM` | `client` | `1` | loadgen `--thread_num` |
| `WORKER_THREADS` | `client` | `8` | loadgen `--worker_threads` |
| `CAS_PERCENTAGE` | `client` | `0` | loadgen `--cas_percentage` |
| `VALUE_SIZE` | `client` | `64` | loadgen `--value_size` |
| `KEY` | `client` | `bench` | loadgen `--key` |
| `LOG_EACH_REQUEST` | `client` | `false` | set to `true` to pass `--log_each_request` |

```sh
make client THREAD_NUM=32 WORKER_THREADS=32
make client CAS_PERCENTAGE=100 KEY=counter   # exercise the read+CAS increment path
```

No `PIPELINE`/`SYNC` equivalent exists here — `raft-kv-memstore` is a fixed
in-memory example with no append-entries-pipelining or fsync flags exposed.
