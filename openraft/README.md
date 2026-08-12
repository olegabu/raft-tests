# openraft — raft-kv-memstore benchmark

Benchmarks [openraft](https://github.com/databendlabs/openraft) using its
networked `raft-kv-memstore` example (an in-memory key-value store speaking
HTTP/JSON).

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

Requires a current stable Rust toolchain (`rustup update stable`) —
`raft-kv-memstore`'s `edition = "2024"` needs rustc ≥ 1.85.

```sh
make build
```

Builds release of the vendored `raft-kv-memstore` and our benchmarking client `loadgen`.

## Run locally

Cluster membership is entirely runtime-configured over HTTP, not a static
peer/conf list. Start 3 nodes, each with its own
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
make push           # scp the server binary, init-cluster.sh, loadgen to all instances
make start          # start one raft-key-value process per node
make init-cluster   # one-time: form the 3-node cluster (skip on subsequent restarts)
make client         # run the load generator on the C&C instance
make logs           # tail the first node's std.log
make stop           # kill the servers
```

## Load generator

`loadgen/` is a small standalone Rust client (no `openraft` dependency —
just HTTP/JSON against `/write`/`/read`) that drives concurrent writes
against the cluster and reports live throughput/latency.

| Flag | Default | Effect |
|---|---|---|
| `--peers` | (required) | comma-separated `api_addr` list, e.g. `host1:21001,host2:21001,host3:21001` |
| `--thread_num` | `1` | concurrent sending tasks |
| `--worker_threads` | `8` | Tokio runtime worker thread count (real OS threads) — keep `>=` `thread_num`, else tasks queue for fewer real workers than they need and measured latency includes that queueing delay rather than just the cluster's own cost |
| `--log_each_request` | off | print each request/response (a bare flag; the Makefile's `LOG_EACH_REQUEST=true` sets it) |
| `--cas_percentage` | `0` | percentage of ops that do a full read+CompareAndSet increment instead of an unconditional `Set` — see below |
| `--value_size` | `64` | size in bytes of the value written on each op |
| `--key` | `bench` | the single key all sending tasks contend on, rather than spreading writes across a key space |

**Why `--cas_percentage` exists**: an unconditional `Set` is one RPC. A
`CompareAndSet`-based increment needs the client to already know the current
`expected_version`, which means a full read first — two round trips, not
one. The loadgen defaults to the cheaper `Set` path and lets
`--cas_percentage` opt a fraction of requests into the two-RPC read+CAS
sequence for anyone who wants to exercise that path specifically. Latency
and qps naturally look different once `--cas_percentage > 0`, since you're
now measuring a different (heavier) operation.

**Leader tracking**: starts with `peers[0]` as its initial guess for who the
leader is, then handles two distinct failure modes, both confirmed against a
real cluster:
- A live node responds "I'm not the leader" (`ForwardToLeader` in the
  response body) → jump straight to the leader address it names.
- The tracked leader is simply *unreachable* (killed, mid-election) — there's
  no redirect to react to, so the loadgen cycles to the next known peer
  until one responds. Verified by killing the live leader mid-benchmark: qps
  drops to 0 for the duration of openraft's own election (a few seconds, not
  a loadgen issue), then recovers automatically with no manual intervention.

**Reporting**: every 1s, prints `Sending Request to OpenRaft (<peers>) at
qps=<X> latency=<Y>` (µs) — a rolling window count/average over that second,
not a cumulative average.

## Makefile flags

| Flag | Target | Default | Effect |
|---|---|---|---|
| `API_PORT` | `start`, `client`, `init-cluster` | `21001` | Client-facing HTTP API port |
| `RAFT_PORT` | `start`, `init-cluster` | `22001` | Inter-node raft RPC port |
| `THREADS` | `client` | `1` | loadgen `--thread_num`; concurrent sending tasks |
| `WORKER_THREADS` | `client` | `$(THREADS)` | loadgen `--worker_threads`; kept equal to `THREADS` by default so sending tasks never queue for fewer real workers than they need. Set explicitly to reintroduce that mismatch on purpose |
| `CAS_PERCENTAGE` | `client` | `0` | loadgen `--cas_percentage` |
| `VALUE_SIZE` | `client` | `64` | loadgen `--value_size` |
| `KEY` | `client` | `bench` | loadgen `--key` |
| `LOG_EACH_REQUEST` | `client` | `false` | set to `true` to pass `--log_each_request` |

```sh
make client THREADS=32              # WORKER_THREADS follows automatically
make client CAS_PERCENTAGE=100 KEY=counter   # exercise the read+CAS increment path
```

`raft-kv-memstore` is a fixed in-memory example — it doesn't expose any
append-entries-pipelining or fsync tuning flags on the server side.

The security group opens `API_PORT`'s default (`21001`) to `ssh_ingress_cidr`
for direct access (e.g. `curl http://$NODE1:21001/metrics`) via
`deploy/main.tf`'s `openraft_api_port` variable. If you override `API_PORT`
here, also set `-var openraft_api_port=<port>` on `terraform apply` so the
security group rule matches.
