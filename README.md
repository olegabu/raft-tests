# Performance tests for implementations of Raft consensus

Monorepo for performance tests of RAFT protocol implementations. Each
subdirectory (e.g. `braft/`) is one implementation under test, with its own
build/run/benchmark instructions in its own README. The AWS harness below is
shared across all of them.

## AWS harness

Provisions a small EC2 fleet — 3 raft nodes + 1 command-and-control instance
running the load generator — in a single AWS account/region (us-east-1),
reusable across whichever product's `Makefile` you drive it with.

Prerequisites: terraform, an AWS profile with us-east-1 access, an ssh key
pair.

```sh
make deploy        # terraform apply -var-file=deploy/single_az.tfvars (TOPOLOGY=single_az by default)
make env           # write EC2 IPs from terraform state into .env (gitignored)
make node-rtt       # measure real inter-node RTT
make destroy        # tear everything down
```

`.env` (see `.env.example`) holds the shared IPs/ssh config and is read by
both this root `Makefile` and each product's own `Makefile` (via `-include
../.env`). Product-specific run knobs (ports, batch sizes, thread counts,
...) live in each product's own README/Makefile, not here.

### Topology: single-AZ vs. multi-AZ

`TOPOLOGY` selects a `deploy/*.tfvars` file and controls where the 3 raft
nodes and the client land:

- `single_az` (default, `deploy/single_az.tfvars`): all 4 instances in one
  cluster placement group in one AZ — the low-latency floor.
- `multi_az` (`deploy/multi_az.tfvars`): the 3 raft nodes spread across
  `node_azs` (default `us-east-1a`/`1b`/`1c`); the client stays colocated with
  `node[0]`/`NODE1` in its own 2-instance cluster placement group. AWS cluster
  placement groups are AZ-scoped, so there's no way to keep all 4 instances in
  one PG once the nodes are spread out — `node[1]`/`node[2]` are plain
  instances in their own AZs.

```sh
make deploy TOPOLOGY=multi_az   # reapplies in place: only the 2 nodes whose
                                 # AZ changed get replaced, node[0] + client don't
make env                        # refresh .env with the new IPs
```

`make deploy TOPOLOGY=single_az` switches back the same way. Always pass the
same `TOPOLOGY` to `deploy`/`destroy`/`plan` you last deployed with — it
selects which `.tfvars` file describes the current state.

AWS doesn't publish which AZs are physically closest to each other (and AZ
*names* map to different physical zone-ids per account, so general advice
doesn't transfer), so `node_azs` defaults to `1a`/`1b`/`1c` rather than a
guess. `make node-rtt` pings between the deployed nodes (works in either
topology) so you can see the real numbers and swap in `1d`/`1e`/`1f` if one
leg is a clear outlier.

### What `make node-rtt` actually measures

Node-to-node only — not your dev machine, and not the client/C&C instance:

1. Your dev machine `ssh`es into each of `NODE1`/`NODE2`/`NODE3` (public IPs)
   in turn. That ssh hop is purely a remote-execution mechanism, not
   something being timed.
2. Once connected, it runs `ping` **on that remote node**, targeting the
   other two nodes' **private IPs**.
3. The reported RTTs are strictly the pairwise latency between the 3 raft
   nodes themselves over their private VPC network — exactly the
   leader→follower replication path.

Your dev machine's own latency to AWS never factors into the numbers, and
the client/C&C instance is never a ping source or target — its latency to
the leader isn't measured by this tool.

### Instance types

`deploy/main.tf` defaults both `node_instance_type` and `client_instance_type`
to `c6i.2xlarge` (8 vCPU, no local NVMe, not network-optimized). That's the
right default for any raft implementation being benchmarked without fsync on
small messages — network bandwidth and packet rate aren't the bottleneck at
that scale, so `c6in` buys nothing, and no fsync means no need for local
NVMe. If your test needs synchronous disk writes, switch to `c6id.2xlarge` —
its instance-store NVMe is auto-mounted at `/data` by the node user-data
script (tens of microseconds per fsync vs. 0.5–1 ms on EBS). Override either
var with `-var` on `terraform apply`, or edit the defaults in `deploy/main.tf`.

Instances run Ubuntu 22.04, chosen to match the glibc of binaries built on a
typical Ubuntu 22 dev machine — build locally, `scp` the binary as-is.

### Node stats / product ports

Each product gets its own CIDR-scoped ingress rule in `deploy/main.tf`,
opened to `ssh_ingress_cidr` only (same CIDR as ssh) so you can reach it
directly from your own machine — separate from the self-referencing rule
that already covers client↔node traffic on any port for the benchmark
itself to run:

- `raft_port` (default `8300`) — braft's brpc server; both RPC traffic and
  its builtin HTTP stats pages multiplexed on the same port.
- `openraft_api_port` (default `21001`) — `raft-key-value`'s client-facing
  HTTP API (`/metrics`, `/write`, `/read`, ...).

If a product's server binds a different port than its variable's default,
override it with `-var <name>=<port>` on `terraform apply` to match. Adding
a new product with its own port follows the same pattern — a new variable
plus a matching ingress rule.

## Adding a new raft product

1. Create a new subdirectory with your build files and a `Makefile` that
   does `-include ../.env` and `include ../common.mk` (for `SSH_USER`,
   `SSH_KEY`, `SSH_OPTS`, `NODES`).
2. Add product-specific targets there: `push` (scp binaries), `start`/`stop`
   (run the server), `client` (run your load generator), `logs`. See
   `braft/Makefile` (one port, static peer config) or `openraft/Makefile`
   (two ports per node, runtime-configured membership) for working examples
   of two fairly different shapes.
3. Deploy/tear down the shared fleet from the repo root as above; run your
   product's own targets from its subdirectory.

No terraform changes are needed just to add a product with different or
additional ports — the security group's self-referencing "all traffic within
the cluster" rule already covers client↔node traffic on any port. The
`raft_port` variable only matters if you want to open a port to *your own
laptop* (e.g. for stats pages), which is optional.
