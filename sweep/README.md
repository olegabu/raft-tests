# sweep — the rate sweeps behind the knee charts

**Current fleet (c7a.2xlarge, AMD Genoa ~3.7 GHz).** Each product now
writes its own CSV next to its Makefile, produced by that product's own
`make sweep`:

| file | product |
|---|---|
| `../braft/braft.csv` | braft |
| `../openraft/openraft.csv` | openraft |
| `../aeron/aeron.csv` | aeron |
| `../sequencer/seq.csv`, `seq-relay.csv`, `seq-output.csv` | sequencer's five round trips |

`make charts` in the repo root renders every figure from exactly those
files. All latencies are microseconds; the columns are:

```
product,rate,achieved,p50,p90,p99,p999,max,dropped,lag
```

`achieved` is the measured throughput, `dropped` is dropped-by-rig over the whole
run, and `lag` is the p50 schedule lag — how late the rig was in placing each
request. A lag that isn't small invalidates the row: the offered rate wasn't
actually offered.

Rates are spaced tightest inside each product's comfort zone and through its
knee, which is where the shape of the curve is decided; past the knee they widen
out.

## Never mix fleets in one chart

`knee-sweep-c6i.csv` is the previous fleet's combined sweep
(c6i.2xlarge), kept for provenance. It is **deliberately not** an input
to `make charts`.

`mkcharts.py` averages rows that share a product *and* a rate — that is
how repeated runs at one rate get combined. Feed it two fleets' rows for
the same product and it will happily average those too, producing a
curve that describes neither: doing exactly this drew a braft "knee" at
165k that appears in neither dataset (the c6i rows turn there, the c7a
rows are still flat). Chart an old fleet's file on its own if you want
its shape.

The same caution applies to prose: this repo's older braft findings were
measured on earlier fleets, and their absolute numbers should not be
compared against the current CSVs to two significant figures. The three
c6i-era fleets already disagreed on level while agreeing on shape (55k
p50: 748 µs, 626 µs, 660 µs).

`braft-tuning.csv` is the first tuning ladder: 21 runs at a fixed 100k varying
`event_dispatcher_num`, connection type, channel count, pipeline depth, server
concurrency and the in-flight cap. All of it negative. `braft-ae-cache.csv` is the
round that found the fix — `raft_enable_append_entries_cache`, which took p99 at 100k
from 6171 µs to about 1030 µs. `braft-pipeline-cache.csv` is the second tuning round,
on the third fleet: `PIPELINE=8` paired against `PIPELINE=8`+`AE_CACHE_SIZE=16`
and against `EVENT_DISPATCHERS=2`/`SERVER_CONCURRENCY=6`, `PIPELINE=8` alone
adopted as the result. All three are summarised in
[braft/README.md](../braft/README.md#what-fixed-brafts-tail-raft_enable_append_entries_cache)
and [braft/README.md](../braft/README.md#deciding-pipeline).

`braft-burst.csv` is a separate, smaller experiment: braft at 10k–50k under
`BURST=1` and `BURST=10`, two or three repeats per point, plus one 40 s-warmup
control. It exists because the main sweep's braft curve shows p99 *falling* as
load rises below 35k, and this is what identified the cause as the arrival shape
rather than braft. Columns add `burst`, `warmup`, `rep` and the full percentile
spectrum (`p90`, `p999`, `p9999`, `mean`, `lag_p99`).

## Reproducing

Deploy the fleet and start a product's cluster as its own README describes, then
from the repo root:

```sh
echo 'product,rate,achieved,p50,p90,p99,p999,max,dropped,lag' > out.csv
sweep/sweep.sh braft braft "$PWD/out.csv" 10 30 "BURST=10 MAX_INFLIGHT=2000" \
  10000 20000 30000 40000 45000 50000 55000 60000 65000 70000 85000 \
  100000 115000 130000 145000 160000 175000 190000
```

Pass an absolute path for the CSV — `sweep.sh` cd's into the product directory.
Sweep one product at a time: two load generators against one fleet contend for
the client instance and neither result means anything.

Then regenerate the four SVGs in the repo root:

```sh
python3 sweep/mkcharts.py                 # defaults to knee-sweep.csv, writes to the repo root
python3 sweep/mkcharts.py out.csv /tmp    # or from your own run, somewhere else
```

One run per rate is the main limitation of this data, and no rate was repeated,
so there is no per-point error bar here. The latency percentiles are at least
mutually corroborating — adjacent rates agree to within a few percent and the
curves are smooth — but the drop counts are not: aeron recorded 42 drops at 140k
and 36,528 at 290k with nothing between them to explain it. Treat any single drop
figure under ~0.5% as noise, and repeat a rate before drawing a conclusion from
one row.

The comfort-zone edges in `mkcharts.py`'s `CFG` follow the root README's
criterion, whose tail bound (p99 <= 3x p50) is a policy choice with a published
sensitivity table — openraft's edge in particular moves from 60k to 85k between a
2.5x and a 3.0x bound. If you change the bound, change `CFG` and the root README
together; nothing recomputes them from the CSV.

`mkcharts.py` has no dependencies — it emits SVG text directly. Per-product axis
ranges, comfort-zone edges and knee positions are the `CFG` table near the bottom;
they are read off the data by hand rather than fitted, so re-check them against
any new sweep instead of assuming they still hold.

`../knee-braft-before-ae-cache.svg` is the one chart in the repo root
`mkcharts.py` does not produce: it is
[commit `59e4e51`'s `knee-braft.svg`](https://github.com/olegabu/raft-tests/blob/59e4e51/knee-braft.svg),
restored verbatim as a separate file rather than regenerated, so it shows exactly
what shipped before `AE_CACHE`/`PIPELINE` existed as settings. It is a fixed
historical artifact — regenerating `knee-braft.svg` from a new sweep never touches
it, and it should not be treated as reproducible from the current CSV.
