# exchange on the fleet

Fleet harness for [`exchange`](https://github.com/opensequencer/exchange): a
deterministic central limit order book replicated by `sequencer`,
reached over FIX 4.4.

A sibling of `braft/`, `openraft/`, `aeron/` and `sequencer/` — one
directory per product under test. It is **not** `sequencer/Makefile
APP=exchange`: that variable selects among sequencer's own `examples/`,
and exchange is a separate repository with its own build tree, its own
binaries, and an instrument that has to exist before an order can be
sent.

## What is measured

A `NewOrderSingle` to its **first ExecutionReport**, delivered from the
journal (`exchange/docs/spec.md` §7). Not the propose receipt: a FIX
session gateway never answers an order synchronously, which is the
property that makes a `ResendRequest` answerable and makes two replicas
agree on what a client was told.

The reference to beat or explain is **`sequencer-fix`: zero drops
through 400k**. That arm is the same gateway, the same journal and the
same delivery path, carrying an eight-byte counter instead of a
matching engine — so the difference between the two curves is the cost
of the order book on the apply thread, and very little else. Nothing
else in this repository isolates that.

## The order flow

Every client sends a seven-message cycle that nets to zero: place/hit,
place/cancel, place/replace/hit — five `35=D`, one `35=F`, one `35=G`,
two matches. It exercises all three order-entry paths and leaves depth
bounded however long the ladder runs. The reasoning, and the two
earlier shapes that were wrong, are in
`exchange/bench/exchange_fix_requester.hpp`; the invariants are
asserted by `exchange/tests/load_generator_shape_test.cpp`.

Each client box **must** get a distinct `--client_id`. It goes in the
high bits of every ClOrdID, and the exchange rejects a duplicate live
ClOrdID from one CompID — sharing one turns most of a sweep into
rejects, which still complete and so still look like a measurement.

## Running one

```sh
make build push push-multi        # from the exchange checkout's release preset
make start start-fix              # 3 nodes, then the FIX gateways
make add-instrument               # REQUIRED: orders for an unknown symbol are rejected
make checksums                    # what is actually on each host
make sweep-fix                    # the ladder -> exchange-fix.csv
make chart                        # -> ../knee-exchange-fix.svg
make stop-fix stop
```

Then stop the fleet from the repo root — `make stop-instances`, and
confirm every instance reads `stopped`. It costs roughly **$79/day**
while it runs.

`make add-instrument` is safe to repeat: a second one is rejected
(`InstrumentExists`) and leaves the first standing. It must be re-run
after `make clean-data`, which wipes the journal the instrument lives
in.

## Before trusting a number

- `make checksums` — a stale remote binary produces real-looking
  results for code that is not under test.
- Confirm the ladder's `lag` column is small. A large schedule lag
  means the rig was late and the row is about the rig, not the
  exchange.
- `ulimit -n 65536` is applied at every launch site here. braft holds
  one fd per raft log segment and truncates only on snapshot; past the
  1024 default it latches the node into `ERROR` and never recovers,
  while still running and listening, so the sweep keeps writing rows of
  zeros. See `../sequencer/README.md`.
- The chart config for `exchange-fix` in `../sweep/mkcharts.py` is
  provisional: axes only, no knee annotation, because no sweep has run.
  Fill it in from the first one.
