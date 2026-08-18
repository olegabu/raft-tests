# Performance tests for implementations of Raft consensus

Monorepo for performance tests of RAFT protocol implementations. Each
subdirectory is one implementation under test, with its own build/run/benchmark
instructions in its own README:

- [`braft/`](braft/) — braft (C++, brpc), replicated atomic counter
- [`openraft/`](openraft/) — openraft (Rust), HTTP key-value store
- [`aeron/`](aeron/) — Aeron Cluster (Java, UDP), echo service

The AWS harness below is shared across all of them.

## AWS harness

Provisions a small EC2 fleet — 3 raft nodes + 1 command-and-control instance
running the load generator — in a single AWS account/region (us-east-1),
reusable across whichever product's `Makefile` you drive it with.

Prerequisites: terraform, an AWS profile with us-east-1 access, an ssh key
pair.

```sh
make deploy        # terraform apply -var-file=deploy/multi_az.tfvars (TOPOLOGY=multi_az by default)
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

- `multi_az` (**default**, `deploy/multi_az.tfvars`): the 3 raft nodes spread across
  `node_azs` (default `us-east-1a`/`1b`/`1c`); the client stays colocated with
  `node[0]`/`NODE1` in its own 2-instance cluster placement group. AWS cluster
  placement groups are AZ-scoped, so there's no way to keep all 4 instances in
  one PG once the nodes are spread out — `node[1]`/`node[2]` are plain
  instances in their own AZs.
- `single_az` (`deploy/single_az.tfvars`): all 4 instances in one cluster
  placement group in one AZ — the low-latency floor.

**multi-AZ is the default because it is the requirement.** A raft group that
loses a whole availability zone and keeps serving has to have its voters in
different AZs, so single-AZ numbers describe a deployment nobody would run for
availability. The difference is not small: the cross-AZ quorum round trip
measures 0.39 ms on this fleet and is most of braft's 601 µs p50 floor. Keeping
`single_az` around is still useful — it isolates exactly what that round trip
costs — but nothing published here is measured with it.

```sh
make deploy                      # multi_az
make deploy TOPOLOGY=single_az   # reapplies in place: only the 2 nodes whose
                                 # AZ changed get replaced, node[0] + client don't
make env                         # refresh .env with the new IPs
```

Always pass the same `TOPOLOGY` to `deploy`/`destroy`/`plan` you last deployed
with — it selects which `.tfvars` file describes the current state.

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

Aeron Cluster has no such rule because it serves no HTTP status endpoint —
there is nothing useful to reach from outside the cluster.

If a product's server binds a different port than its variable's default,
override it with `-var <name>=<port>` on `terraform apply` to match. Adding
a new product with its own port follows the same pattern — a new variable
plus a matching ingress rule.

## Adding a new raft product

1. Create a new subdirectory with your build files and a `Makefile` that
   does `-include ../.env` and `include ../common.mk` (for `SSH_USER`,
   `SSH_KEY`, `SSH_OPTS`, `NODES`).
2. Add product-specific targets there: `push` (scp binaries), `start`/`stop`
   (run the server), `client` (run your load generator), `logs`. The three
   existing Makefiles cover fairly different shapes, so one of them is
   probably close to what you need:
   - `braft/Makefile` — one TCP port per node, static peer config
   - `openraft/Makefile` — two TCP ports per node, membership configured at
     runtime via an extra `init-cluster` step
   - `aeron/Makefile` — a 100-port UDP block per node, static membership, and
     a `provision` target because it needs a JVM installed on the instances
3. Deploy/tear down the shared fleet from the repo root as above; run your
   product's own targets from its subdirectory.

Keep the reporting line in the same shape the existing three use
(`... at qps=<X> latency=<Y>` in microseconds, over a one-second rolling
window), support both load modes (below), and default `THREADS` to 100 as they
all do, so results can be read side by side. 100 outstanding requests is the
common comparison point for closed mode: every product is latency-bound there
rather than resource bound, so `qps ≈ THREADS ÷ latency` holds and the two
numbers are two views of the same measurement.

## Load modes

Every product's `make client` takes `MODE=open` (the default) or `MODE=closed`.
They answer different questions and neither substitutes for the other. Open is the
default because it is the one that can be wrong in the safe direction: it finds the
knee and cannot hide a queue, whereas a closed-loop number quietly flatters a
saturated system.

`RATE` defaults to **100k** for all three products, so a bare `make client` offers
the same load everywhere and the three are directly comparable. That single number
sits in a different place on each curve, though — inside braft's comfort zone
(~150k), past openraft's (~85k), and at a quarter of aeron's (~400k) — so a result
is only meaningful next to the rate it came from. Every run prints its offered rate
in the summary; quote it.

**`MODE=closed THREADS=N`** (opt in) keeps N requests outstanding, so offered load adapts
to how fast the cluster answers. It measures the service latency one
well-behaved client experiences. By construction it *cannot* see saturation:
past the knee it simply stops offering more load, so latency flattens and
throughput caps. Publishing only closed-loop numbers overstates resilience.

**`MODE=open RATE=R`** (default) emits on a fixed schedule at R requests/sec whether or
not replies have arrived, because real arrivals do not slow down when the system
does — they often increase. Latency is measured from each request's **scheduled**
send time, not the moment it actually went out, so any time spent waiting to
send is charged to the system. That one rule is what makes open mode immune to
coordinated omission, and it is the first thing to check in any implementation
here.

Supporting flags, shared across products: `BURST` (messages per scheduled
instant — same mean rate, clustered arrivals, for probing burst absorption),
`MAX_INFLIGHT` (bounds unanswered requests; hitting it counts as
*dropped-by-rig* and a nonzero count invalidates the run's offered-rate claim
rather than being silently skipped), `WARMUP`/`MEASURE`/`DRAIN_TIMEOUT`, `PACE`
(`spin` for a client with a spare core, `park` on a shared box), and `HDR_OUT`.

Each run ends with a summary carrying p50/p90/p99/p99.9/p99.99/max from an
HdrHistogram, achieved rate, dropped-by-rig, and — in open mode — **schedule
lag**, covered in its own section below. Closed mode instead reports a
Little's-law ratio (`throughput × mean ÷ outstanding`, explained below) and flags
deviation over 10%, a cheap check that the rig measured what it thinks it did.

### What actually differs: what triggers the next send

Not synchronous versus asynchronous RPC. The difference is **what decides when
request *i+1* goes out**:

- **Closed loop: a reply does.** Each of N senders runs a loop — build, send,
  block until the reply, record, repeat. The offered rate is an *output* of the
  run, whatever `N ÷ latency` happens to be. The number outstanding is the input,
  pinned at N.
- **Open loop: the clock does.** `t_sched(i) = t_start + i/R`, fixed before the
  run and never adjusted. The offered rate is the *input*. The number outstanding
  is the output, whatever the cluster's behaviour makes it.

braft answers the obvious objection — *doesn't its client wait for the response,
like a closed loop?* — concretely, because both modes call the same method on the
same stub and differ in one argument.

Closed mode, [braft/client.cpp:161](braft/client.cpp#L161):

```cpp
stub.compare_exchange(&cntl, &request, &response, NULL);
```

A `NULL` done-closure is brpc's synchronous form: it blocks the calling bthread
until the reply lands. So yes — in closed mode the client really does wait, on
every request. That is not an artifact to work around, it *is* the mode. Two
things follow. Latency is timed from the actual send
([client.cpp:194](braft/client.cpp#L194)), which is the honest choice here because
the sender was not trying to send any earlier — there is no waiting to charge to
anyone. And each sender is a strictly serial chain: request *i+1*'s
`expected_value` is the value request *i* returned
([client.cpp:182-190](braft/client.cpp#L182-L190)), a compare-exchange chain per
sender, on its own key. So `THREADS=100` is 100 independent serial chains with
exactly one request in flight each — that is where "100 outstanding" comes from,
and why the number outstanding cannot vary during a closed-mode run.

Open mode, [braft/client.cpp:304](braft/client.cpp#L304):

```cpp
stub.compare_exchange(&call->cntl, &call->request, &call->response,
                      brpc::NewCallback(on_open_response, call));
```

A non-`NULL` done-closure makes the identical call return immediately. The reply
arrives later on a brpc bthread worker, which runs `on_open_response` and records
`now_us() - call->scheduled_us` there ([client.cpp:236](braft/client.cpp#L236)).
The scheduler thread never touches a reply; it only ever waits on the clock. Per
request state (controller, request, response, `scheduled_us`) has to outlive the
call, so it lives in a heap-allocated `OpenCall` that the callback deletes.

**Little's law, briefly.** For any stable system, `L = λ × W`: the average number
of requests in flight equals the arrival rate times the average time each spends
in the system. It says nothing about consensus or networks — it is arithmetic that
holds whenever what goes in comes out. It is also why the two modes are duals.
Closed loop pins `L` at N, so λ and W can only trade off against each other: when
the cluster slows down, throughput falls and the client offers less. Open loop
pins λ at R, so `L` and W trade off instead: when the cluster slows down, the
backlog grows and latency grows with it. Same law, different variable held fixed,
which is why the two see different things at saturation. It doubles as a
self-check — in closed mode `throughput × mean latency ÷ outstanding` must come
out at 1.0, and a deviation over 10% means the rig is not measuring what it
thinks it is.

**Why the trigger, and not the RPC style, is the thing that matters.** You can
build an open loop out of synchronous calls by giving each request its own
thread, and plenty of load generators do. What makes a rig open-loop is that
nothing in the send path is allowed to wait on a reply; async is just how you
achieve that without one thread per in-flight request. Little's law says how many
that would be: at aeron's 640k and ~900 µs, about 580 outstanding; at braft's
190k and 11 ms, about 2,000 — the same order as braft's `MAX_INFLIGHT=2000`, and
the reason thread-per-request does not scale to these rates.

**And why it makes coordinated omission structural rather than accidental.**
Stall the leader for 500 ms mid-run:

- *Closed loop*: all N senders are blocked in `compare_exchange`. The client
  issues **nothing** for 500 ms, then records N samples of roughly 500 ms each.
  The 500 ms × R requests a real workload would have sent during the stall are
  simply absent from the sample — omitted, in coordination with the failure.
  Throughput shows a dip; the tail hardly moves.
- *Open loop*: the scheduler is not blocked, so it keeps issuing at R. Those
  500 ms × R requests all get sent, and each records a latency covering its share
  of the stall.

Measured, not asserted: under a 500 ms `kill -STOP`, braft's open mode held
`qps=1000` throughout and recorded a 614 ms maximum. Closed mode on the same
stall does the opposite — aeron's count fell from ~1500/s to 45/s and it recorded
a single sample for the whole stall instead of hundreds. Full results are in the
verification table further down. That contrast is the reason to run both modes.

### Schedule lag: the rig auditing itself

`lag = actual send time − t_sched`, recorded per request into its own
HdrHistogram during the measurement window only
([client.cpp:301](braft/client.cpp#L301)), reported as p50 / p99 / max.

It exists because open mode's central rule creates an exposure. Latency runs from
`t_sched` to the reply, and **the rig itself sits inside that interval**. If the
client is 200 µs late issuing a request, the reported latency contains 200 µs the
cluster never caused. Latency alone cannot tell "the cluster is slow" from "my
client was late." Schedule lag can, and it is the reason to trust the numbers at
all. It is not a nice-to-have: a large lag invalidates the offered-rate claim in
the same way dropped-by-rig does, because both are the rig failing to deliver
rate R.

Read it against the latency it accompanies. In these sweeps braft ran 14–19 µs at
p50 against latencies of 700 µs and up — about 2% — and openraft and aeron ran
0–1 µs. At 0–1 µs the reported figures are the cluster's, full stop. The
cautionary case is the same rigs on a 4-core laptop: 159–229 µs of lag, and
cross-mode agreement unreachable, which is why open mode wants a client with a
spare core.

**What makes it non-zero is mostly `BURST`, not pacing.** From
[sweep/braft-burst.csv](sweep/braft-burst.csv), same rates and same pacing:

| | lag p99 across runs |
|---|---|
| `BURST=1` | 1–15 µs |
| `BURST=10` | 25–42 µs |

All ten requests in a burst share one scheduled instant, so they are all measured
against the same `t_sched` — but they still have to be issued one after another,
and the tenth pays for issuing the previous nine. At a couple of µs per async brpc
issue that is the 25–42 µs. openraft and aeron read 0–1 µs because their issue
paths are cheaper (a spawn onto a runtime, and an `offer()` into a ring buffer).

The lag is a real cost, correctly attributed — those requests genuinely did go out
late, and the rule says charge it. It is also small enough to bound rig error in any
finding drawn from these numbers: 42 µs against tails measured in milliseconds.

`PACE` controls how the scheduler waits for the next instant: `spin` stays on-core
for precision, `park` sleeps in 50 µs steps while more than 150 µs remain
([client.cpp:263](braft/client.cpp#L263)) and hands the core back. On a client
box with a spare core, spin; on a shared one, park and check the lag.

### Cross-mode agreement, and what it validates

**Sanity check across modes.** Well below the knee the two modes should agree on
p50, since a lightly loaded system's service time should not depend on how
arrivals are spaced. Divergence usually means a rig bug — but not always: Aeron
diverged 5.8× because its default parking idle strategies make service time
depend on arrival *pattern*, not just rate. Check schedule lag before concluding
either way.

**Cross-mode agreement, measured on EC2.** For each product: a closed-loop run
at 28 outstanding to establish a rate well below the knee, then an open-loop run
at exactly that rate. Same 3-node multi-AZ fleet of `c6i.2xlarge`, one product at
a time so they never compete for CPU.

| Product | closed p50 | open p50 | delta | schedule lag p50 / max |
|---|---|---|---|---|
| braft | 745 µs @ 35,473/s | 804 µs @ 35,451/s | +7.9% | 1 µs / 38 µs |
| openraft | 986 µs @ 27,949/s | 1005 µs @ 27,926/s | +1.9% | 0 µs / 236 µs |
| aeron | 491 µs @ 56,840/s | 559 µs @ 56,839/s | +13.8% | 0 µs / 1453 µs |

All three inside the 15% criterion, with the rigs holding the offered rate to
within a handful of requests per second. Schedule lag of 0-1 µs at the median is
what makes the delta interpretable as the system's behaviour rather than the
rig's; the same runs on a 4-core laptop showed lag of 159-229 µs and could not
meet the criterion at all, which is why open mode wants a client with a spare
core.

Worth noting: aeron's two modes diverged **5.8×** on the laptop and agree to
13.8% here. That gap was its default parking idle strategies making service time
depend on arrival *pattern*; the EC2 configuration sets non-parking strategies
(`SPIN_IDLE`), and the arrival-pattern sensitivity goes away with it.

### The knee, and why it only shows up in open loop

Every system has a rate below which it keeps up comfortably and above which it
stops. The **knee** is that transition. Below it, a request's latency is service
time: it arrives, gets processed, leaves. Above it, requests arrive faster than
they can be retired, so each one waits behind a growing backlog and latency stops
describing the work and starts describing the queue — measured latency becomes
queue depth expressed in time.

Three signals mark it, and they don't arrive together:

1. **p50 starts climbing** while throughput still tracks the offered rate.
2. **The tail breaks first.** p99 can blow out while p50 still looks healthy —
   before its follower cache was enabled, braft's p99 went 970 → 2139 → 6999 µs
   across 65k, 75k and 85k while its p50 barely moved (646 → 694 → 788 µs). If you
   watch averages you miss that entirely. Enabling the cache removed it — the tail
   now breaks with the median rather than 30k ahead of it.
3. **`achieved` falls below `RATE`**, and drops climb. The system is now
   definitively past capacity.

Closed loop cannot show any of this. Holding N requests outstanding means the
client only sends when the system replies, so offered load is throttled by the
system's own speed — at saturation it simply stops asking for more, latency
flattens, and throughput caps. That is coordinated omission as a property of the
measurement, and it is why the cliff below is only visible in open loop.

![p50 latency vs offered rate for braft, openraft and Aeron Cluster, each flat then kneeing upward](knee-curves.svg)

One pair of axes for all three has to span a 16× range of offered rate, which
squeezes braft and openraft into the left fifth of the plot and flattens the very
thing the chart is for. So each product also gets its own, over its own range of
rate and latency, with p50 and p99 plotted together — the tail turns first, so the
two curves separating *is* the knee:

![braft: p50 flat near 800 microseconds through the comfort zone while p99 leaves it at 70k and reaches 10x its floor by 100k](knee-braft.svg)

![openraft: p50 and p99 both climb steadily from the lowest rate measured, with no flat stretch and no cliff](knee-openraft.svg)

![aeron: p50 and p99 flat from 25k all the way to 400k, then a single step up after 460k](knee-aeron.svg)

Each was measured the same way: 10 s warmup, 30 s measurement window, one run per
rate, `MODE=open`. Steps are tightest inside each comfort zone and through the
knee — 5k apart for braft between 40k and 70k, 10k for openraft, 35–40k for aeron
— so the knee falls between steps rather than being straddled by one. 49 rows in
all. The raw data, the sweep script and the chart generator are in
[sweep/](sweep/), so every number below can be rechecked and the charts rebuilt
with `python3 sweep/mkcharts.py`.

### Batching: why Aeron's open-loop latency was 5× too high

Closed loop refills its window in a tight inner loop — `while (inFlight < N)
offer()` — so requests leave back-to-back. The transport sees a run of messages
and coalesces them: one syscall, one datagram, one pass through the pipeline for
many requests. Open loop paces requests to a schedule, so by default it offers
**one message per scheduled instant**, and that coalescing never happens.

For Aeron that difference dominated everything else. Its client publishes into a
single session, so unbatched offers meant a per-message trip through the driver:

| at 100k offered | p50 | p99 |
|---|---|---|
| `BURST=1` (one per instant) | 2613 µs | 13,901 µs |
| `BURST=10` (ten per instant) | **490 µs** | **600 µs** |

Same aggregate rate — ten messages every 100 µs instead of one every 10 µs — and
they still share the burst's scheduled timestamp, so the latency definition is
unchanged. The p99 improved 23×, and p50 came in *below* the closed-loop floor of
534 µs. An earlier revision of this file reported Aeron's knee at 185k; that was
this artifact, not Aeron. With batching the knee is ~460k, two and a half times
higher.

The effect is transport-specific, which is worth knowing before comparing
products:

| Product | effect of `BURST=10` at a fixed rate | why |
|---|---|---|
| aeron | 2613 → 490 µs | one session; unbatched offers pay full driver cost each |
| braft | 1613 → 1210 µs | brpc already pipelines per channel, so less left to win |
| openraft | 1320 → 1358 µs (none) | HTTP/1.1 needs a connection per request — nothing to batch onto |

Those were all measured at 100k. Batching's *sign* is not fixed, though — for
braft it flips below ~35k, where clumped arrivals cost more than they save. The
comfort-zone section below measures that.

### Batching, pipelining and in-flight: which knob does what

"Batching" names four different things across the stack, and `BURST` is only the
outermost one. They are easy to conflate because they all trade latency for
throughput, but they do it at different layers and only two of them are ours.

1. **`BURST` — arrival shape, in the rig.** B requests share one scheduled
   instant, so the mean rate is unchanged and arrivals clump. It alters nothing
   about how the cluster works; it alters what the cluster is asked to absorb.
2. **Transport coalescing.** Several requests leave in one syscall or datagram.
   This is what Aeron's 5x was: unbatched offers each paid a full trip through
   the media driver.
3. **Replication batching.** Several client requests ride in *one*
   AppendEntries round, so a single quorum round trip commits many entries —
   braft's `raft_max_entries_size` (1024 entries), surfaced by
   `braft/run_server.sh` as `--max_entries_size`. This is the one that changes the
   cost *per request*, because the round trip is the expensive part and it gets
   divided.
4. **Apply batching.** Committed entries handed to the state machine in groups:
   braft's `raft_apply_batch` (32). Cheapest of the four here, since all three
   products' state machine operations are trivial next to consensus.

**`BURST` does not perform 2, 3 or 4 — it feeds them.** Requests arriving together
give the transport something to coalesce and the leader something to put in one
round. That is the whole reason it helps, and why the effect is transport-specific:
openraft's HTTP/1.1 opens a connection per request, so there is nothing to
coalesce onto and `BURST` does nothing for it. At very low rates it can invert and
cost a little, since a lone burst has nothing in the pipeline to be absorbed by.

**Pipelining is the orthogonal knob.** Batching puts more requests in one round
trip; pipelining puts more round trips in flight at once. braft's `PIPELINE`
(`raft_max_parallel_append_entries_rpc_num`, upstream default 1, this repo starts
nodes with 4) caps how many AppendEntries RPCs the leader may have outstanding to
a follower before it waits. Both raise throughput, and they touch latency
differently: a batched request waits for its batch and then pays one round trip,
while a pipelined request pays its own round trip but does not wait for the
previous one to finish. Batching amortizes; pipelining overlaps.

**In-flight is a consequence, not a knob.** (Reading "inlining" as in-flight /
`MAX_INFLIGHT` — if you meant something else, say so.) Nothing sets the number of
outstanding requests directly. Little's law fixes it: in open mode λ is `RATE`, so
in-flight is whatever `RATE × latency` comes to — about 50 at braft's 65k and
761 µs, about 215 at aeron's 400k and 537 µs. `MAX_INFLIGHT` is a ceiling on that
number, not a target for it.

The three interact in one way that matters in practice:

- `BURST` raises instantaneous in-flight by B at a stroke. A burst arriving while
  the cap is already reached is counted as dropped-by-rig, so `BURST` and
  `MAX_INFLIGHT` have to be set together, not independently.
- A cap set too low quietly becomes the thing being measured. Aeron at a fixed
  100k went 690 → 1487 → 2289 µs at p50 as the cap went 100 → 500 → 2000, because
  in-flight settles at whatever the cap permits.
- Too high is equally wrong on a connection-per-request transport: openraft's
  derived default of 5,500 exhausted HTTP connections and made requests fail
  outright.

| knob | layer | what it changes | effect on latency |
|---|---|---|---|
| `BURST` | rig | arrival shape | none by itself; feeds coalescing and replication batching |
| — | transport | requests per syscall/datagram | large where per-message cost is large (aeron 5x) |
| `--max_entries_size` | consensus | requests per AppendEntries round | amortizes the quorum round trip across entries |
| `--apply_batch` | state machine | entries per apply | negligible here; these state machines are trivial |
| `PIPELINE` | consensus | AppendEntries rounds in flight | overlaps round trips rather than amortizing them |
| `MAX_INFLIGHT` | rig | ceiling on outstanding requests | caps queue depth, so it also caps observed latency |

### Dropped-by-rig: what it is, and whether it matters in production

Every table here carries a drop column, and a run whose drops climb is not a run
that achieved its offered rate. The rig drops a request in exactly two places,
both in `braft/client.cpp`'s open-loop scheduler:

1. **The in-flight cap is full** ([client.cpp:317](braft/client.cpp#L317)). Before
   each send the scheduler checks `inflight >= MAX_INFLIGHT`; if so the request is
   counted as dropped and never sent.
2. **No leader is known** ([client.cpp:309](braft/client.cpp#L309)). If the leader
   cannot be resolved, the whole burst due at that instant is counted as dropped.

Nothing else counts as a drop. In particular a request that is sent and times out
is *not* dropped — it is recorded as unanswered, separately. Drops are strictly
"the rig never put this on the wire".

That is why the flat `10` in braft's drop column at every rate up to 140k is not a
rate-dependent cost: it is one `BURST=10` burst dropped by rule 2 while the client
resolves the leader at startup, and it never recurs. Worth noting as a reporting
wart: these counters are not gated on the measurement window the way the latency
histogram is ([client.cpp:145](braft/client.cpp#L145)), so warmup drops are
included in the run's total. For the startup burst that inflates every braft row
by exactly 10; at high rates, where drops are dominated by rule 1 firing
continuously, it makes little difference.

**Is shedding acceptable in real life? Yes — and that is the point of the
distinction.** A production client with a bounded outstanding-request budget (a
connection pool, a semaphore, a thread pool) that refuses to enqueue beyond it is
doing the correct thing. Unbounded queueing is how a slow dependency turns into a
cascading outage: work piles up, every request eventually times out, and the
system spends its capacity on responses nobody is waiting for any more. Shedding
early and visibly is better behaviour than queueing forever.

What is *not* acceptable is reporting a shed run as though the load had been
carried. "braft sustained 100k with a 3.1 ms p99" is a false claim if 1.5% of that
100k was never sent — the system was really asked for 98.5k, and the tail looks
good precisely because the hardest requests were the ones discarded. So drops are
fine as a client design and fatal as a benchmark result, which is why the comfort
criterion above bounds them at 0.1% rather than ignoring them.

### Comfort zones

Comfort zone here means the highest offered rate such that **every rate up to and
including it** satisfies all three of:

1. **throughput is really sustained** — achieved rate within 1% of offered,
2. **the rig placed the load** — dropped-by-rig under 0.1% of offered,
3. **the tail is still attached to the median** — p99 no more than **3x** p50.

Two details of how that is applied matter more than they look. The zone is
**contiguous from zero**, not "the highest rate that happens to pass": p99/p50 is
*not* monotonic, because past the knee p50 inflates toward p99 and the ratio
recovers. braft at 200k scores 1.3x — better than at 70k — with a p50 of 10.8 ms.
Scanning for the last passing rate would return that. And a single failing rate
whose neighbours both pass is treated as noise rather than the edge, because at one
run per rate an isolated excursion cannot be told from a real one: aeron's 290k row
shows 0.42% drops between neighbours at 0.06% and 0.04%, and without that rule it
alone would cut aeron's zone from 400k to 250k.

**The 3x is a policy choice, not a derivation — and it is the weakest thing here.**
Using a *ratio* is defensible: it is dimensionless, so it means the same thing for a
500 µs system and a 1.5 ms one, and it cannot be met by simply being slow. The
*value* is not. It was picked, and its sensitivity is not small:

| p99/p50 bound | braft | openraft | aeron |
|---|---|---|---|
| 2.0x | 140k | **30k** | 400k |
| 2.5x | 150k | **60k** | 400k |
| **3.0x** | **150k** | **85k** | **400k** |
| 3.5x–6.0x | 160k | 85k | 400k |

openraft moves by nearly 3x across a range of bounds that are all arguable, which
is worth stating plainly: openraft's plateau ratio is about 2.9, so a 3.0x bound is
the smallest round number that keeps its edge at 85k. That is uncomfortably close to
choosing the threshold to preserve an answer already published, which is the exact
failure the ratio was introduced to avoid. A reader who prefers 2.5x should read
openraft's zone as 60k.

braft is far steadier: 150k for any bound in 2.5–3.0x and 160k up to 6x, a **10k
transition band** rather than the 70–80k spread it showed before its follower cache
was enabled. Its tail and median now break together, so there is little room left
for the choice of bound to matter.

The rationale for standing at 3x, stated as reasoning rather than arithmetic: at 2x
an unlucky request waits about as long as a typical one; at 3x it waits noticeably
longer but in the same order of magnitude; past that the median has stopped
describing what the service does. A tighter SLO justifies a tighter bound — the
table above is there so you can substitute one without re-running anything.

Where each product's edge falls under the 3x default, and what binds it:

| Product | edge | p99/p50 there | first rate that fails, and why |
|---|---|---|---|
| braft | 150k | 2.2x | 160k — ratio 3.0x |
| openraft | 85k | 2.9x | 92k — drops 0.15% |
| aeron | 400k | 1.3x | 460k — drops 0.41%, and 520k confirms it |

braft and aeron are bound by their tails; openraft runs out of placed load first.

| Product | comfort zone | p50 / p99 there | p50 floor | knee | max sustained |
|---|---|---|---|---|---|
| aeron | **~400k** | 537 / 705 µs | 473 µs @ 50k | ~460k | ~626k |
| braft | **~150k** | 1404 / 3154 µs | 620 µs @ 10k † | ~160k | ~175k |
| openraft | **~85k** | 1387 / 4014 µs | 864 µs @ 10k | ~110k | ~128k |

† with the swept `BURST=10`; braft reaches 476 µs at 10k under uniform arrivals,
for reasons the burst section below covers. braft's rows are measured with
`raft_enable_append_entries_cache=true`, which this repo now sets by default — see
[braft/README.md](braft/README.md#what-fixed-brafts-tail-raft_enable_append_entries_cache)
for what it changed and why.

Aeron's comfort zone is 2.7x braft's and nearly 5x openraft's — which is the
comparison this sweep exists to establish, and it narrowed sharply once braft's
follower cache was turned on: braft's edge moved from 70k to 150k.

**Idling the cluster does not buy much latency back.** Dropping braft from its
comfort-zone rate to 10k gains 784 µs, though most of that is the knee itself; from
100k it gains 150 µs, or 19%, and aeron is *slower* at 25k than at
50k. Consensus latency is dominated by a round trip that does not get cheaper when
the machine is idle — the cross-AZ quorum hop measures 0.39 ms here against
braft's 601 µs floor — so most of each comfort zone is capacity you can spend
without paying for it in latency. openraft is the exception: it gains 523 µs, or
38%, going from 85k to 10k, but that is not a floor being approached, it is the
near-linear cost curve described below extended down.

#### aeron — flat to 400k, then a step up at 460k

| offered | achieved | p50 | p99 | dropped | of offered |
|---|---|---|---|---|---|
| 25k | 25,000 | 521 µs | 609 µs | 7 | 0.00% |
| 50k | 49,999 | 473 µs | 560 µs | 15 | 0.00% |
| 100k | 99,713 | 487 µs | 626 µs | 2,859 | 0.10% |
| 140k | 139,998 | 474 µs | 567 µs | 42 | 0.00% |
| 175k | 174,979 | 491 µs | 620 µs | 1,282 | 0.02% |
| 210k | 209,992 | 491 µs | 587 µs | 324 | 0.01% |
| 250k | 249,955 | 505 µs | 607 µs | 4,564 | 0.06% |
| 290k | 289,541 | 521 µs | 621 µs | 36,528 | 0.42% |
| 325k | 324,911 | 541 µs | 681 µs | 3,926 | 0.04% |
| 360k | 359,937 | 539 µs | 793 µs | 9,430 | 0.09% |
| 400k | 399,983 | 537 µs | 705 µs | 4,655 | 0.04% |
| 460k | 456,233 | 600 µs | 1,042 µs | 56,328 | 0.41% |
| 520k | 519,766 | 977 µs | 1,328 µs | 44,745 | 0.29% |
| 580k | **573,854** | 968 µs | 1,271 µs | **126,278** | 0.73% |
| 640k | **626,508** | 923 µs | 1,383 µs | **170,461** | 0.89% |

From 25k to 400k — a 16x change in load — p50 moves 68 µs, and it is *lower* at
50k than at 25k. There is no rate in that range at which Aeron behaves
qualitatively differently.

Its drop counts, though, do not vary smoothly: 42 at 140k, 2,859 at 100k, and
36,528 at 290k, with no ordering by rate. At one run per rate, a drop figure below
about 0.5% is run-to-run noise — a stray pause somewhere in the client JVM or the
driver — rather than a property of that rate. The counts only become signal when
they climb monotonically and stay climbing, which here starts at 580k. (An earlier
revision of this file explained the 100k row's count as JIT warmup because it ran
first in its sweep; the low-rate points refute that, since 25k also ran first in
its own sweep and dropped 7.)

#### braft — flat to 150k, then both percentiles go together

| offered | achieved | p50 | p99 | p99/p50 | dropped | of offered |
|---|---|---|---|---|---|---|
| 10k | 9,997 | 620 µs | 918 µs | 1.5x | 10 | 0.00% |
| 25k | 24,992 | 653 µs | 743 µs | 1.1x | 10 | 0.00% |
| 50k | 49,984 | 655 µs | 784 µs | 1.2x | 10 | 0.00% |
| 75k | 74,977 | 681 µs | 866 µs | 1.3x | 10 | 0.00% |
| 100k | 99,952 | 770 µs | 1,032 µs | 1.3x | 536 | 0.02% |
| 125k | 124,924 | 888 µs | 1,256 µs | 1.4x | 1,144 | 0.03% |
| 140k | 139,958 | 1,052 µs | 1,799 µs | 1.7x | 10 | 0.00% |
| 150k | 149,919 | 1,404 µs | 3,154 µs | 2.2x | 1,896 | 0.04% |
| 160k | 159,898 | 2,340 µs | 7,111 µs | 3.0x | 3,336 | 0.07% |
| 175k | **170,005** | 10,719 µs | 13,343 µs | 1.2x | **193,056** | 3.68% |
| 200k | **175,323** | 11,295 µs | 13,423 µs | 1.2x | **1,036,148** | 17.27% |

Two repeats per rate from 100k to 160k, one elsewhere. Measured with
`raft_enable_append_entries_cache=true`, braft's default in this repo since the
[tuning work](braft/README.md#what-fixed-brafts-tail-raft_enable_append_entries_cache)
found that leaving it off costs a wasted round trip whenever a pipelined
AppendEntries reaches a follower ahead of a gap in its log.

That flag changed the *shape* of this curve, not just its level. With it off,
braft had two widely separated knees — the tail turned at 75k while p50 held to
about 105k — and p99 at 100k was 6171 µs against a p50 of 970 µs, a ratio of 8.3x.
With it on, p99 at 100k is 1032 µs against 770 µs, a ratio of **1.3x**, and the two
percentiles now break together at 150–160k. The tail no longer leads the median,
because the thing that made it lead was retry traffic rather than queueing.

The remaining structure is ordinary: p99 stays within 1.2–1.7x of p50 from 25k to
140k, reaches 2.2x at 150k and 3.0x at 160k, and past 175k the rig can no longer
place the load (3.7% dropped at 175k, 17.3% at 200k) while achieved throughput
ceilings at about 175k.

**One residual quirk at the very bottom.** p99 at 10k is 918 µs against 743 µs at
25k — slightly backwards, and it is the `BURST=10` arrival shape: ten requests
sharing one scheduled instant have nothing else in the pipeline to be absorbed by, so
stragglers pay a second round trip. Under uniform arrivals 10k measures 476/534 µs,
which is braft's real floor. Outside 10k the effect is now within run-to-run noise
(704 against 743 µs at 25k), so the swept curve is a fair representation of braft at
every rate that matters. It was much larger before the follower cache was enabled,
because retries left far more slack for clumping to consume.

#### openraft — gradual, no cliff

| offered | achieved | p50 | p99 | dropped | of offered |
|---|---|---|---|---|---|
| 10k | 9,997 | 864 µs | 1,170 µs | 0 | 0.00% |
| 20k | 19,994 | 927 µs | 1,313 µs | 0 | 0.00% |
| 30k | 29,990 | 985 µs | 1,908 µs | 0 | 0.00% |
| 40k | 39,958 | 1,024 µs | 2,295 µs | 565 | 0.05% |
| 50k | 49,983 | 1,091 µs | 2,643 µs | 951 | 0.06% |
| 55k | 54,942 | 1,103 µs | 2,734 µs | 1,103 | 0.07% |
| 60k | 59,979 | 1,150 µs | 2,813 µs | 1,152 | 0.06% |
| 70k | 69,926 | 1,220 µs | 3,131 µs | 1,411 | 0.07% |
| 75k | 74,973 | 1,271 µs | 3,352 µs | 1,569 | 0.07% |
| 85k | 84,903 | 1,387 µs | 4,014 µs | 2,098 | 0.08% |
| 92k | 91,925 | 1,470 µs | 4,028 µs | 4,211 | 0.15% |
| 100k | 99,689 | 1,556 µs | 4,669 µs | 4,902 | 0.16% |
| 110k | **108,753** | 1,753 µs | 5,283 µs | 18,973 | 0.57% |
| 120k | **116,976** | 1,992 µs | 5,763 µs | 41,816 | 1.16% |
| 130k | **124,423** | 2,400 µs | 6,066 µs | 84,510 | 2.17% |
| 140k | **127,701** | 2,713 µs | 6,299 µs | **181,325** | 4.32% |

openraft has no flat stretch to find — p50 rises monotonically from the lowest
rate measured, so its knee is a change of slope rather than a cliff, and the
clearest signal is where drops take off: exactly zero below 40k, 0.08% of offered
requests at 85k, 0.6% at 110k, 4.3% at 140k. Across the comfort zone the rise is close to
linear — 7 µs of p50 and 38 µs of p99 per additional 1k requests/sec, within 75 and
314 µs of a straight line through the endpoints — so unlike the other two there is
no rate at which openraft is cheaper per unit of load than at any other. Every extra
1k of throughput has a posted price.

**Past the knee, latency stops being the signal and drops become it.** braft's p50
sits near 11 ms from 175k on, openraft's near 2.7 ms, and aeron's actually *falls*
from 977 to 923 µs between 520k and 640k — all while dropped-by-rig grows by an
order of magnitude. That is `MAX_INFLIGHT` doing its job: capping how deep the
queue may get also caps how bad latency can look, so the overload has to surface
somewhere, and it surfaces as requests the rig never placed. On any row where
`achieved` trails `offered`, read the drop column first.

Schedule lag stayed at 0–1 µs for openraft and aeron and 16–23 µs for braft in
every run above, so these are system numbers, not rig error.

**`MAX_INFLIGHT` is load-bearing, not just a safety valve.** For Aeron, latency
tracks the cap almost linearly at a fixed rate (100k, `BURST=1`: cap 100 →
690 µs, cap 500 → 1487 µs, cap 2000 → 2289 µs), because in-flight settles at
whatever the cap permits. For openraft the derived default
(`max(1000, RATE/10)`) is actively harmful: HTTP/1.1 opens a connection per
in-flight request, so a 5,500-deep window exhausts connections and requests begin
failing — with the default openraft appeared to collapse from 30k to 1.2k, and
capped at 400 it sustains 100k. Even a cap of 800 produced one run with 69,192
errors. Set it per transport, and read a large drop or error count as a signal to
check the cap before blaming the cluster.

**Closed-loop comparison at 100 outstanding**, same fleet and topology, leader
co-located with the client in every case:

| Product | qps | p50 | p99 | p99.99 | max |
|---|---|---|---|---|---|
| aeron | 186,755 | 534 µs | 659 µs | 1005 µs | 3241 µs |
| braft | 94,560 | 1016 µs | 1913 µs | 2539 µs | 3211 µs |
| openraft | 69,360 | 1350 µs | 2932 µs | 4427 µs | 5115 µs |

Read these with the caveats above: the payload sizes differ slightly, and while
braft's leader was pinned with `make transfer-leader` and aeron's with
`APPOINTED_LEADER`, openraft has no such control — its leader was node 0 by
construction, since `init-cluster.sh` initializes that node first, but nothing
enforces it.

**How the modes were verified.** Stalling the leader for 500 ms mid-run
(`kill -STOP`, then `-CONT`) is a cheap and decisive check that rule 1 works.
Open mode must keep its per-second count flat through the stall and record
samples whose latency covers it; a *gap* means latency is being measured from
the actual send and the implementation is wrong. All three products pass:

| Product | during stall (open) | max latency | schedule lag p50 |
|---|---|---|---|
| braft | `qps=1000` | 614 ms | 2 µs |
| openraft | `qps=999` | 532 ms | 0 µs |
| aeron | `qps=201` | 521 ms | 8 µs |

Closed mode on the same stall does the opposite, as designed: its count
collapses (aeron went from ~1500/s to 45/s) and it records a single sample for
the whole stall instead of hundreds. That contrast is the reason for having both
modes, made concrete.

The Little's-law check also earned its place — it caught a real bug during
development, where the one-second reporting interval straddling the end of warmup
was counted as fully measured, leaking warmup samples in and making the window
disagree with the samples it contained.

**Known asymmetry, disclosed rather than equalized.** openraft and aeron send a
64-byte value by default; braft's `CompareExchangeRequest` is three int64s,
about 30 bytes on the wire, with no padding field. At these sizes the difference
is a fraction of one MTU and is dominated by the consensus round trip being
measured, so the proto is left alone and each product prints its request size in
the run summary.

No terraform changes are needed just to add a product with different or
additional ports — the security group's self-referencing "all traffic within
the cluster" rule already covers client↔node traffic on any port, for UDP as
well as TCP. The
`raft_port` variable only matters if you want to open a port to *your own
laptop* (e.g. for stats pages), which is optional.
