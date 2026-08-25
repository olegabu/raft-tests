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

### Phase 1: reuses `../sweep/sweep.sh` unmodified

`sweep.sh` is shared by every product in this repo and already speaks
sequencer's `client` target's exact flag names (§8.5's whole point,
per sequencer's own `client:` comment) — no sequencer-specific
handling needed. From this directory:

```sh
echo 'product,rate,achieved,p50,p90,p99,p999,max,dropped,lag' > seq.csv
../sweep/sweep.sh sequencer . "$PWD/seq.csv" 10 30 "" \
  10000 25000 40000 55000 70000 85000 100000 115000 130000 145000 160000 175000
```

Pass an absolute path for the CSV (`sweep.sh` `cd`'s into the product
directory — here, `.` is already this one). The rate list above is a
*starting point*, not a known comfort zone — sequencer adds a real
hop on top of bare braft, so its knee is somewhere at or below braft's
own ~160k, not necessarily at the same place; narrow or widen the
list once a first pass shows roughly where it turns (see
`../sweep/README.md`'s own "Rates are spaced tightest inside each
product's comfort zone and through its knee" for why the spacing
matters more than the exact endpoints).

### Phase 3: `sweep-relay.sh`, sequencer-specific

`sweep.sh`'s own CSV extraction greps bare `p50`/`p90`/etc. labels —
exactly what sequencer's relay summary deliberately does *not* use
(see `bench/load_generator/README.md`), so it has nothing to grab for
phase 3. `sweep-relay.sh` in this directory is the same idea, reading
`relay_p50_us` et al. instead, writing the same 10-column shape so
`mkcharts.py` reads it identically:

```sh
echo 'product,rate,achieved,p50,p90,p99,p999,max,dropped,lag' > seq-relay.csv
./sweep-relay.sh "$PWD/seq-relay.csv" 10 30 "RELAY_GRPC_ADDR=$(NODE1_PRIV):8501" \
  10000 25000 40000 55000 70000 85000 100000 115000 130000 145000 160000 175000
```

`$NODE1_PRIV` needs to actually be set in your shell (it's a Make
variable inside the Makefile, not exported — see this repo's own git
history for exactly the class of bug that assumption caused); export
it from `.env` first, or just paste NODE1's private IP directly —
`make -n client-relay` prints the exact address `client-relay` itself
resolves to, if in doubt.

### One sweep at a time, from a clean journal

```sh
make stop
make clean-data
make start
# ... then one of the sweeps above ...
```

Two load generators (or a relay sweep and a phase-1 sweep) against one
fleet contend for the same client instance and neither result means
anything — sweep one flavor fully before starting the other, matching
`../sweep/README.md`'s own rule. `clean-data` before a sweep isn't
strictly required (`RelayObserver` fast-skips a pre-existing journal's
backlog rather than choking on it — again, see the git history), but
it keeps every point's own `achieved` honestly describing that point's
own traffic rather than an ever-growing prior journal, and keeps
`--relay_from_sequence_number`'s default (0, from the beginning)
correct by construction instead of relying on that skip logic at all.

### Generating the chart

`../sweep/mkcharts.py`'s per-product chart is driven entirely by a
`CFG` dict keyed by product name — `python3 ../sweep/mkcharts.py
seq.csv /tmp` already writes `/tmp/knee-sequencer.svg` for any product
name present in the CSV, but the axis ranges, comfort-zone edge, and
knee-marker position for a name `CFG` doesn't recognize fall back to
whatever `KeyError` `CFG[name]` raises — add an entry for
`"sequencer"` (and `"sequencer-relay"`, for the phase-3 chart) once a
first sweep exists to read real ranges off of, following the shape of
the three already there:

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

```sh
python3 ../sweep/mkcharts.py seq.csv /tmp        # writes /tmp/knee-sequencer.svg
python3 ../sweep/mkcharts.py seq-relay.csv /tmp   # writes /tmp/knee-sequencer-relay.svg
```

The combined `knee-curves.svg` (all products on one chart) is
hardcoded to exactly the three existing products and out of scope
here — the per-product chart above is sequencer's own standalone
knee, not a fourth line squeezed onto that one.
