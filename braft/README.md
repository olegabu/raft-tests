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

Configure as default or release.

```sh
cmake --preset default .
```

Build.

```sh
cmake --build build
```

## Run

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
make client        # run the load generator on the C&C instance
make logs          # tail the first node's std.log
make stop          # kill the servers
```

Config comes from the shared `../.env` (see root `.env.example`); the flags below can be overridden on the make command line.

| Flag | Target | Default | Effect |
|---|---|---|---|
| `PORT` | `start`, `client` | `8300` | Raft RPC/stats port on every node |
| `SYNC` | `start` | `false` | `-raft_sync`; `true` fsyncs the log on every commit |
| `PIPELINE` | `start` | `4` | `-raft_max_parallel_append_entries_rpc_num`; in-flight AppendEntries RPCs per follower. braft's own default is 1, which forces a full round trip before the next entry can go out and caps throughput under concurrency; raise it (e.g. `PIPELINE=8`) to let more batches overlap |
| `THREADS` | `client` | `1` | `-thread_num`; concurrent sending threads on the load generator |
| `CLIENT_CONCURRENCY` | `client` | `$(THREADS)` | `-bthread_concurrency` on the client; kept equal to `THREADS` by default so sending threads never queue for fewer real workers than they need — which would inflate measured latency with client-side scheduling delay rather than the cluster's own latency. Set explicitly to reintroduce that mismatch on purpose |

Examples:

```sh
make start PIPELINE=8              # more AppendEntries pipelining per follower
make start SYNC=true PIPELINE=8    # fsync + more pipelining
make client THREADS=64             # CLIENT_CONCURRENCY follows automatically
```

### Multi-AZ: designating a starting leader

After `make deploy TOPOLOGY=multi_az` from the repo root (see root README),
`make push start` here, then:

```sh
make transfer-leader            # designate NODE1 as the starting leader
make client THREADS=32
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

