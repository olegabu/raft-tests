# sequencer

Benchmarks [sequencer](https://github.com/olegabu/sequencer) — a real
application framework, not a bespoke atomic-counter demo like
`braft`/`openraft`/`aeron` here — against this repo's own 3-node
multi-AZ fleet, following the root README's ["Adding a new raft
product"](../README.md#adding-a-new-raft-product) pattern. `APP`
selects which of sequencer's `examples/` this measures; `counter` is
its only one so far.

## The two flavors

sequencer's own `bench/load_generator/README.md` names three round
trips; this Makefile drives two of them (the third, submission to
durable-in-the-journal, needs no separate rig — see that file for why):

1. **Submission to synchronous receipt** (`make client`) — client →
   input gateway → node → (raft commit, apply, journal append) → node
   → input gateway → client. Directly comparable to
   `../braft/client.cpp`'s own number, one hop longer through
   sequencer's own input gateway on purpose — that hop is what's being
   measured (specification.md §3.3: a real deployment never bypasses
   it). **A correct run can only be at or above bare braft's own
   floor, never below it** — a number that comes back *faster* than
   braft's own comfort-zone p50 means the rig isn't measuring a real
   round trip, not that sequencer is faster than what it's built on.
2. **Submission to receipt via the relay gateway** (`make
   client-relay`) — the same run, plus a second, separately-labeled
   summary (`relay_p50_us`, not `p50` — the two summaries are printed
   in the same log on purpose and must never collide) for how long
   dissemination to a remote, relay-fed consumer takes beyond the
   synchronous ack path. No other product here has a relay-gateway
   concept to compare against; this number stands on its own.

Both flavors submit the identical request stream at the identical
rate — `client-relay` does not re-run anything, it just also reports
what the relay side saw for the same traffic `client` already
generated.

## Prerequisites

1. Deploy the shared fleet from the repo root — see the [root
   README](../README.md).
2. `make build` — builds sequencer's Release preset from a sibling
   checkout (override `SEQUENCER_DIR` if yours isn't one).
3. `make push` — copies `counter_node`/`sequencer_relay` to the nodes,
   `counter_input_gateway`/`counter_load_generator` to the client.
4. `make start` — starts all of it; confirms in `make logs`
   (`state: LEADER` in the node's own std.log, or query
   `curl http://<node>:8300/raft_stat` directly).

Re-run `push`/`start` after every `make build` — `start` restarts
cleanly (PID-file based; safe to run again against already-running
processes) but never re-copies binaries itself.

## A single, reproducible run at a representative load

`RATE` defaults to `100000` — the same offered rate `braft/Makefile`
defaults to — so a plain `make client` / `make client-relay` with no
flags is already the load-close-to-braft's-own comparison point:

```sh
make client                 # phase 1, ~40s (10s warmup + 30s measure, RATE=100000)
make client-relay           # phase 1 and phase 3 together, same run
```

Check the summary's `failed` line is `0` (see sequencer's own commit
history for why this exists — a nonzero value here means the rig
measured how fast requests were *rejected*, not how fast they
succeeded, and every other number in the run is meaningless) and, for
`client-relay`, that `relay_dropped_races` is small relative to
`relay_completed`.

Override anything `braft/Makefile`'s own `client:` target exposes the
same way: `make client RATE=40000 WARMUP=5 MEASURE=15`.

## Sweeping for the knee, and regenerating the chart

Four targets, one per step — `sweep`/`sweep-relay` write a CSV,
`chart`/`chart-relay` render it, `charts` does both renders in one
step. All of them are thin wrappers (see the Makefile itself for the
exact commands each one runs) so `make -n <target>` always shows you
the real underlying invocation, including this section's own examples
below.

### One sweep at a time, from a clean journal

```sh
make stop
make clean-data
make start
```

Two load generators (or a relay sweep and a phase-1 sweep) against one
fleet contend for the same client instance and neither result means
anything — sweep one flavor fully before starting the other, matching
`../sweep/README.md`'s own rule. `clean-data` before a sweep isn't
strictly required (`RelayObserver` fast-skips a pre-existing journal's
backlog rather than choking on it — see sequencer's own git history
for the bug that mattered until it was fixed), but it keeps every
point's own `achieved` honestly describing that point's own traffic
rather than an ever-growing prior journal, and it's what makes a
sequencer node crashing on `JournalWriter::append: index file
exhausted` (a real bug in sequencer's own journal, reproduced live
against this fleet — its index file has a fixed capacity and throws,
uncaught, once exhausted, rather than handling that gracefully; worth
fixing in sequencer itself, not something worked around here) far
less likely to land mid-sweep.

### Phase 1: `make sweep`

```sh
make sweep                                   # SWEEP_RATES's own default, into seq.csv
make sweep SWEEP_RATES="10000 40000 100000"  # a narrower/faster pass
make sweep SWEEP_CSV=/tmp/run2.csv           # a different output file
```

Wraps `../sweep/sweep.sh` unmodified — shared by every product in this
repo, and already speaks sequencer's `client` target's exact flag
names (§8.5's whole point, per sequencer's own `client:` comment), so
no sequencer-specific handling was needed there. `SWEEP_RATES`'
default is a *starting point*, not a known comfort zone — sequencer
adds a real hop on top of bare braft, so its knee is somewhere at or
below braft's own ~160k, not necessarily at the same place; narrow or
widen it once a first pass shows roughly where it turns (see
`../sweep/README.md`'s own "Rates are spaced tightest inside each
product's comfort zone and through its knee" for why the spacing
matters more than the exact endpoints).

### Phase 3: `make sweep-relay`

```sh
make sweep-relay
```

`sweep.sh`'s own CSV extraction greps bare `p50`/`p90`/etc. labels —
exactly what sequencer's relay summary deliberately does *not* use
(see `bench/load_generator/README.md`), so it has nothing to grab for
phase 3. `sweep-relay.sh` (this directory, not shared) is the same
idea, reading `relay_p50_us` et al. instead, into the same 10-column
shape so `mkcharts.py` reads it identically. Uses the same
`SWEEP_RATES`/`SWEEP_WARMUP`/`SWEEP_MEASURE` as `sweep`, writing
`SWEEP_RELAY_CSV` (default `seq-relay.csv`) instead of `SWEEP_CSV`.

### Generating the chart: `make chart` / `make chart-relay` / `make charts`

```sh
make chart          # SWEEP_CSV -> CHART_DIR/knee-sequencer.svg
make chart-relay    # SWEEP_RELAY_CSV -> CHART_DIR/knee-sequencer-relay.svg
make charts          # both
```

`../sweep/mkcharts.py`'s per-product chart is driven entirely by a
`CFG` dict keyed by product name (a `"sequencer"` entry renders
`knee-sequencer.svg` for any CSV containing `sequencer` rows,
regardless of what else is or isn't in it — mkcharts.py skips
whatever a CSV doesn't have rather than requiring all three original
products present, fixed directly for this) — but there is no
`"sequencer"`/`"sequencer-relay"` entry yet, since there's no real
sweep to read axis ranges off of until you run one. Until you add one,
`make chart` runs cleanly and reports `wrote 0 per-product chart(s)
() to .` — not an error, just nothing to draw yet. Add an entry
following the shape of the three already there once `seq.csv` exists:

```python
# mkcharts.py's CFG dict — add after "aeron":
 "sequencer":       (200000, 25000, (Y_LOW, Y_HIGH), [tick, values, here], COMFORT_RATE,
                     [(KNEE_RATE, "knee", 16)],
                     "sequencer — <one-line shape, once you've seen it>",
                     "<what the comfort-zone/knee table shows, once measured>"),
 "sequencer-relay": (200000, 25000, (Y_LOW, Y_HIGH), [tick, values, here], COMFORT_RATE,
                     [(KNEE_RATE, "knee", 16)],
                     "sequencer-relay — <one-line shape, once you've seen it>",
                     "<what the comfort-zone/knee table shows, once measured>"),
```

`(xmax, xstep, (ylow,yhigh), yticks, comfort_rate, markers, title,
subtitle)` — read the numbers off your own CSV by eye, the same way
`../sweep/README.md` describes for the existing three: "read off the
data by hand rather than fitted." Don't guess these ahead of a real
sweep; a chart drawn from invented axis ranges is worse than no chart.

The combined `knee-curves.svg` (all products on one chart) stays
scoped to whichever of the three original products a CSV actually
contains — `seq.csv` on its own never contributes a fourth line to it,
since it has no aeron/braft/openraft rows; the per-product chart above
is sequencer's own standalone knee.
