# Native Benchmark Rigs: Open-Loop and Closed-Loop, C++ and Rust

*(Repository: `sequencer` — new component under `bench/rig/`. Companion to
`02-benchmark-methodology.md` (methodology) and
`03-aeron-rig-benchmark-design.md` (the Java/Aeron-harness interop rig).
This document specifies native rigs we own end-to-end. It is written to be
implemented as specified; where a choice is left open it is marked
DECIDE.)*

## 1. Why native rigs when the Aeron rig exists

The Aeron `LoadTestRig` (doc 03) gives us third-party credibility: their
harness, their histogram semantics, directly comparable to their published
tables. It has three limits we need to cover ourselves:

1. **It is open-loop only.** It has no closed-loop mode, and the
   closed-loop question ("what does one well-behaved client experience?")
   is a number clients will ask for.
2. **It speaks gRPC to us**, which adds protocol overhead and hides the
   baidu_std fast path our real C++ clients use.
3. **It is Java**, so rig-side jitter (GC, JIT warm-up) must be managed
   rather than being absent, and it cannot double as example client code
   for our own SDK users.

The native rigs are the instruments we control: baidu_std protocol, no
managed runtime on the measurement path, and both load modes. The Rust rig
additionally serves as the seed of a Rust client SDK and as a
cross-language check that the wire contract is language-neutral (the same
argument as the multi-language journal readers in
`01-sequencer-architecture.md`).

**Second purpose — a braft-vs-openraft head-to-head.** The rigs double as
the instrument for measuring the two consensus substrates evaluated in the
project's library survey: the C++ rig's primary target is our braft-based
sequencer node; the Rust rig's primary target is a new, minimal
**openraft-based node** (§4.5) hosting the same no-op state machine behind
the same proto. No honest, methodology-stated braft-vs-openraft benchmark
exists publicly (openraft's own numbers are self-described as measured on a
minimized store and not application-representative); this produces one.

**The confound rule.** Rig language and wire protocol must never be
conflated with the target under test. A comparison cell is valid only if
**the same rig, over the same protocol, measures both targets**. The
rig-native pairings (C++/baidu_std→braft, Rust/gRPC→openraft) measure each
stack's best path and are reported as such — but the published
braft-vs-openraft table uses same-rig/same-protocol cells exclusively
(§4.1). This is why the C++ rig's gRPC mode and cross-target runs are
mandatory, not optional cross-checks.

## 2. The two modes: what each measures and why both are required

### 2.1 Open loop — "the market does not wait for you"

**Model.** Requests are emitted on a fixed schedule (target rate R),
regardless of whether prior responses have arrived. Arrival is exogenous —
exactly how an exchange experiences load: order flow is driven by market
events and client strategies, not by the venue's response speed. During
stress, real flow *increases* (cancel storms) precisely when the venue is
slowest.

**What it measures.** Sojourn time — queueing delay + service time — at a
given offered rate. Latency for each request is measured from its
**scheduled** send time (see §5.1), so every microsecond the system falls
behind schedule is charged to the system. Sweeping R locates the
saturation knee; past the knee, measured latency grows without bound
because the histogram is effectively measuring queue depth in time units.

**What it is for.** Capacity planning, tail truth (p99/p99.9/p99.99 at the
rates we claim to sustain), the knee location, and SLA verification under
load. This is the headline methodology for an exchange, and it is immune
to coordinated omission by construction.

### 2.2 Closed loop — "one disciplined client"

**Model.** N workers, each with at most W outstanding requests (default
W=1). A worker sends, waits for the response, sends again. Offered load
adapts to the system's speed.

**What it measures.** Pure request/response service latency as experienced
by a client who gates on the ack — which is a real workflow for us: the
synchronous signed admission receipt is exactly "submit, hold the receipt,
proceed." At low N this is the best-case client experience number; sweeping
N maps concurrency → throughput and yields the classic
throughput-vs-concurrency curve (Little's law: throughput ≈ N / latency).

**What it deliberately does NOT measure.** Behavior past saturation. A
closed-loop rig at the knee simply stops offering more load; latency stays
flat and throughput caps. It cannot see the queueing cliff — that is
coordinated omission as a design property, acceptable only because the
open-loop rig covers it.

### 2.3 Why both

- Open loop answers: *what happens to everyone when the market gets busy?*
- Closed loop answers: *what does one client feel when the system is
  healthy?*
- Publishing only closed-loop numbers overstates resilience (hides the
  cliff). Publishing only open-loop numbers understates the healthy-path
  experience (a client at W=1 never queues behind their own traffic).
  Venues get judged on both; we measure both.
- Cross-check: at rates well below the knee the two must agree (open-loop
  p50 ≈ closed-loop p50 at matching effective rate). Divergence below the
  knee indicates a rig bug — this agreement is an acceptance criterion
  (§9), which is itself a reason to build both: each validates the other.

## 3. Deliverables and layout

```
sequencer/
├── bench/
│   └── rig/
│       ├── README.md                  # build, run, interpret; result format
│       ├── cpp/
│       │   ├── CMakeLists.txt         # target: seq_rig
│       │   ├── src/
│       │   │   ├── main.cpp           # flag parsing, mode dispatch
│       │   │   ├── open_loop.cpp      # scheduler + in-flight tracking
│       │   │   ├── closed_loop.cpp    # worker pool
│       │   │   ├── client.cpp         # brpc baidu_std Propose client
│       │   │   ├── histogram.cpp      # HdrHistogram_c wrapper
│       │   │   └── report.cpp         # console table + HDR log + JSON
│       │   └── tests/
│       └── rust/
│           ├── Cargo.toml             # bin: seq-rig
│           ├── src/
│           │   ├── main.rs
│           │   ├── open_loop.rs
│           │   ├── closed_loop.rs
│           │   ├── client.rs          # tonic gRPC Propose client (see §4)
│           │   ├── histogram.rs       # hdrhistogram crate
│           │   └── report.rs
│           └── tests/
├── bench/
│   └── targets/
│       └── openraft-node/             # §4.5: minimal openraft + tonic node,
│           ├── Cargo.toml             #   no-op echo SM, disk-backed log store,
│           └── src/                   #   same proto, same ack semantics
└── vcpkg.json                          # + hdr_histogram
```

Both rigs implement the identical CLI (§7) and identical output formats
(§8) so `run-matrix.sh` scripts and result tooling are shared.

## 4. Wire protocols, targets, and valid comparison cells

Two targets, two rigs, two protocols:

- **braft target** — our sequencer node. Serves baidu_std (native) and
  gRPC/h2 (brpc serves both on one port).
- **openraft target** — new minimal Rust node (§4.5). Serves gRPC/h2 via
  tonic (its native path). No baidu_std (NON-GOAL: no baidu_std Rust
  implementation exists; writing one is out of scope).
- **C++ rig** — speaks baidu_std (primary) and gRPC (mandatory mode, for
  cross-target and cross-rig cells).
- **Rust rig** — speaks gRPC via `tonic` only.

### 4.1 The cell matrix

| Rig / proto | braft node | openraft node | Role |
|---|---|---|---|
| C++ / baidu_std | ✓ | — | braft native-path number (fastest real client path) |
| C++ / gRPC | ✓ | ✓ | **the comparison pair** — same rig, same proto, both targets |
| Rust / gRPC | ✓ | ✓ | **the comparison pair, cross-checked** from a second rig |
| Java Aeron rig / gRPC (doc 03) | ✓ | ✓ (optional) | third-party-harness credibility arm |

The published braft-vs-openraft table is built from the two middle rows
only. Native-path rows are published separately, labeled as each stack's
best path, never as the comparison. Every report carries braft and
openraft versions (openraft is pre-1.0; pin and state the exact release).

### 4.2 Payload

`ProposeRequest{ bytes payload }` from doc 03's `sequencer_bench.proto`;
the rig packs `{u64 scheduled_ts_ns, u64 seq, u32 checksum, padding…}` to
the configured size (min 24 B, default 32 B, plus 288 B variant to match
doc 03's matrix). Both targets' state machines echo it as the single
output; the rig validates seq + checksum on receipt and hard-fails on
mismatch.

### 4.5 The openraft target node (new deliverable)

`bench/targets/openraft-node/` — a Rust crate, deliberately minimal and
symmetric to our braft node's benchmark configuration:

- **openraft** (pinned release) + **tonic** server implementing the same
  `SequencerBench.Propose` service; 3-node cluster, static membership,
  same Terraform placements (same-AZ CPG and 1-1-1) as the braft arm.
- **State machine**: no-op echo, mirroring `examples/payload32` — apply
  returns the input as the single output. Ack semantics identical to ours:
  **reply only after commit + apply** on the leader.
- **Storage parity is the critical fairness requirement.** The comparison
  is meaningless if one side persists and the other doesn't:
  - openraft log store must be a real disk-backed store (DECIDE the
    backend at implementation — a RocksDB-based store or equivalent
    maintained openraft storage crate; **memstore is forbidden in any
    published row** and allowed only as a clearly-labeled no-durability
    diagnostic).
  - Match durability modes pairwise: braft `raft_sync=false` ↔ openraft
    async/no-fsync-per-append; braft `raft_sync=true` ↔ openraft
    fsync-per-append. The durability column from doc 03 §8 extends to
    name the openraft backend + fsync setting per row.
  - Known residual asymmetry, stated in reports rather than hidden: the
    braft node additionally writes our journal (~sub-µs mmap append) on
    the apply path; the openraft node has no journal equivalent. The cost
    is negligible at the µs scale measured but is disclosed.
- **Tuning discipline**: both targets run documented, default-plus-stated
  configuration — batching/pipelining left at each library's defaults,
  every non-default flag listed in the report. This measures the
  substrates as a builder would first meet them; tuning studies are
  follow-up work, not this deliverable.

Payload: `ProposeRequest{ bytes payload }` from doc 03's
`sequencer_bench.proto`; the rig packs `{u64 scheduled_ts_ns, u64 seq,
u32 checksum, padding…}` into `payload` to the configured size (min 24 B,
default 32 B, plus 288 B variant to match doc 03's matrix). The echo
state machine (payload32 no-op) returns it in `first_output`; the rig
validates seq + checksum on receipt and hard-fails on mismatch.

## 5. Open-loop rig specification

### 5.1 Scheduling and timestamping — the load-bearing rules

1. **Latency is measured from the scheduled send time, never the actual
   send time.** `latency_i = t_recv(i) − t_sched(i)` where
   `t_sched(i) = t_start + i / R`. Waiting-to-send is the system's fault
   and is charged to it. This single rule is what makes the rig immune to
   coordinated omission; it is the first thing to code-review.
2. **The schedule never yields to backpressure silently.** If the rig
   cannot send (socket backpressure, in-flight cap reached), it must
   count the message as *dropped-by-rig* and report the count prominently.
   A nonzero drop count invalidates the run's headline claim ("offered
   rate R") and the report must say so. It must NOT block the scheduler
   (that would quietly convert the rig to closed loop).
3. **Burst parameter** `--burst B` (default 1): emit B messages back-to-
   back every B/R seconds — same aggregate rate, clustered arrivals, for
   testing burst absorption. Scheduled time for all B messages in a burst
   is the burst's scheduled instant.
4. **Clock**: `CLOCK_MONOTONIC` (C++ `std::chrono::steady_clock`, Rust
   `Instant`), timestamps in ns. Send and receive stamped in the same
   process — no cross-machine clock comparison anywhere.
5. **Pacing implementation**: hybrid spin — sleep until ~50 µs before the
   next scheduled instant, then spin. Straight `sleep_until` has
   scheduler-quantum jitter that corrupts the schedule at ≥100 K/s;
   straight spin burns a core unnecessarily at low rates. Pin the
   scheduler thread (`--cpu-sched`).

### 5.2 Concurrency structure

- One **scheduler thread** owns the send schedule and issues async sends
  (brpc: async `CallMethod` with done-callback; tonic: spawn per-request
  onto a current-thread runtime — DECIDE at implementation: tonic
  buffered-stream vs per-request spawn, choose whichever keeps the
  scheduler thread non-blocking and measure that the rig itself is not the
  bottleneck, §9.4).
- Response callbacks record into a **lock-free SPSC ring → histogram
  thread** (or thread-local histograms merged at end — implementer's
  choice; requirement is zero locks and zero allocation on the
  measurement path after warm-up).
- **In-flight cap** `--max-inflight` (default 10 × R × expected-p99):
  safety valve so a hung server cannot exhaust rig memory; hitting it
  counts as dropped-by-rig (rule 5.1.2).

### 5.3 Run phases

`--warmup S` (default 30 s): full-rate traffic, histogram discarded.
`--measure S` (default 120 s): recorded. `--cooldown`: drain in-flight,
then report. In-flight at cutoff: responses arriving during drain are
recorded (their scheduled time is within the window); requests still
unanswered after `--drain-timeout` (default 10 s) are reported as
*unanswered* with count — never silently excluded.

## 6. Closed-loop rig specification

- `--workers N` (sweepable), `--per-worker-inflight W` (default 1).
- Each worker: synchronous send→wait→record→repeat loop. Latency =
  `t_recv − t_send_actual` (here actual send IS the honest start: the
  worker genuinely was not waiting before it).
- Workers pinned round-robin to `--cpu-workers` list; per-worker
  histograms, merged at end (no shared state on the hot path).
- Report per-run: achieved throughput (completions / measure window),
  latency distribution, and N — enabling the N-vs-throughput curve and
  the Little's-law cross-check (`throughput × mean_latency ≈ N × W`,
  report the ratio; >±10 % deviation flags a rig bug).
- Same warm-up/measure/drain phases as §5.3.

## 7. CLI (identical for both rigs, both languages)

```
seq_rig --mode {open|closed}
        --target <host:port>            # leader; no failover handling (abort on leader change)
        --proto {baidu|grpc}            # cpp: baidu default; rust: grpc only (§4)
        --rate R --burst B              # open-loop
        --workers N --per-worker-inflight W   # closed-loop
        --payload {32|288|<bytes>}
        --warmup S --measure S --drain-timeout S
        --cpu-sched X --cpu-workers X,Y,Z --cpu-callbacks X
        --hdr-out FILE --json-out FILE
        --tag KEY=VAL ...               # free-form labels carried into JSON
```

## 8. Output

1. **Console table**: mode, target, proto, rate-or-workers, payload,
   achieved rate, dropped-by-rig, unanswered, p50/p90/p99/p99.9/p99.99/max.
2. **HDR log** (`--hdr-out`): standard HdrHistogram interval-log format —
   same format the Aeron rig emits, so doc 03's result tooling reads both.
3. **JSON** (`--json-out`): full config echo + summary stats + tags, for
   the matrix scripts and CI trend tracking.

Every report carries the protocol caveat line from §4 (rust/grpc vs
cpp/baidu) and, when run against the sequencer, the `raft_sync` mode —
the durability-column rule from doc 03 §8 applies verbatim.

## 9. Acceptance criteria

1. **Self-test against `examples/echo`** (no consensus): open-loop C++ rig
   at 100 K/s, 32 B, same host → stable histogram, zero drops, p50 in the
   tens of µs; Rust rig same shape over gRPC (higher constant acceptable).
2. **Cross-mode agreement below the knee**: closed-loop at N chosen so
   achieved throughput ≈ 0.3 × knee-rate must match open-loop p50 at the
   same rate within 15 %. Divergence = rig bug (usually a timestamping or
   pacing error), not a finding.
3. **Cross-language agreement**: Rust rig vs C++ rig in `--proto grpc`
   mode (C++ rig gains a gRPC mode for exactly this check — small, via
   brpc's h2 client support; DECIDE: if brpc's h2 client path proves
   awkward, substitute a one-off grpc++ client for the check) on identical
   cells within 15 % at p50/p99.
4. **The rig is not the bottleneck**: open-loop at max target rate against
   the echo server with the server on the same machine must sustain the
   schedule with zero drops at ≥ 2 × the highest matrix rate (2 M/s
   scheduling capability for the 1 M/s cell). Verified by a loopback run.
5. **Saturation behaves correctly**: open-loop at 2 × knee against the
   3-node sequencer shows monotonically growing latency through the
   window (queue-depth measurement) — the collapse row reproduces, as in
   doc 03 §7.
6. **Coordinated-omission unit test**: inject an artificial 100 ms server
   stall (test hook in the echo server); open-loop histogram must show
   ~R × 0.1s samples ≥ their queue delay; a rig that shows a gap instead
   fails.
7. Determinism replay still passes on journals produced under rig load
   (same free integration test as doc 03 §9.5).
8. **Comparison-cell validity**: both comparison rows of §4.1 complete on
   identical hardware/placement — C++ rig/gRPC and Rust rig/gRPC each
   measuring both targets at 100 K/s, 32 B and 288 B, in both matched
   durability modes. The two rigs' results for the same (target, cell)
   agree within 15 % at p50/p99; disagreement is a rig or config-parity
   bug to resolve before any number is published.
9. **openraft node self-consistency**: the openraft target passes the same
   §9.1-style echo sanity and §9.5-style saturation-shape checks as the
   braft target — the collapse row must reproduce on both substrates (its
   knee will differ; its open-loop shape must not).

## 10. Non-goals

- No baidu_std Rust client (§4).
- No openraft tuning study — defaults-plus-stated-config only (§4.5); a
  tuned head-to-head is legitimate follow-up work with its own doc.
- No conclusion-drawing about openraft's production readiness from these
  numbers alone — chaos/fault-injection behavior (the axis the library
  survey flagged as openraft's open question) is explicitly not measured
  by a latency rig.
- No multi-target load balancing, no leader-failover handling mid-run.
- No TLS in phase 1 (add as a matrix dimension later; doc 03's ATS
  comparison shows the expected cost is ~1 µs).
- No open-loop mode with per-connection fairness modeling (multiple
  simulated clients over one rig is future work; current model = one
  aggregated arrival process, which §2.1 argues is the honest venue-level
  model anyway).
- Not a replacement for doc 03: the Aeron-rig run remains the third-party-
  credibility artifact; these rigs are the native instruments and the
  closed-loop coverage it lacks.
