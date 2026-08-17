# sweep — the rate sweeps behind the knee charts

`knee-sweep.csv` is the raw output of the three open-loop rate sweeps whose
numbers and charts appear in the [root README](../README.md#the-knee-and-why-it-only-shows-up-in-open-loop).
61 rows; a rate may repeat, and repeats are averaged for the charts, all latencies in microseconds:

```
product,rate,achieved,p50,p90,p99,p999,max,dropped,lag
```

`achieved` is the measured throughput, `dropped` is dropped-by-rig over the whole
run, and `lag` is the p50 schedule lag — how late the rig was in placing each
request. A lag that isn't small invalidates the row: the offered rate wasn't
actually offered.

Rates are spaced tightest inside each product's comfort zone and through its
knee, which is where the shape of the curve is decided; past the knee they widen
out. Every row was collected with a 10 s warmup and a 30 s measurement window,
**one run per rate**, against the same 3-node multi-AZ fleet with the leader
co-located with the client. Per-product `make client` flags are in the root README's comfort-zone
section — they are not uniform, because `MAX_INFLIGHT` and `BURST` have to be set
per transport.

braft's rows were re-measured on a second fleet (same instance types, same
multi-AZ layout) with **two repeats per rate through the knee**, after the first
fleet was destroyed; `braft-knee-runs.csv` holds those runs individually, with a
`rep` column. `mkcharts.py` averages repeated rates. The two fleets agree on shape
but not exactly on level — the second is slightly faster (626 µs p50 at 55k against
748 µs) — so braft's curve is entirely second-fleet rather than mixed, while
openraft's and aeron's remain first-fleet. Do not compare braft's absolute floor
against theirs to two significant figures.

`braft-tuning.csv` is the first tuning ladder: 21 runs at a fixed 100k varying
`event_dispatcher_num`, connection type, channel count, pipeline depth, server
concurrency and the in-flight cap. All of it negative. `braft-ae-cache.csv` is the
round that found the fix — `raft_enable_append_entries_cache`, which took p99 at 100k
from 6171 µs to about 1030 µs. Both are summarised in
[braft/README.md](../braft/README.md#what-fixed-brafts-tail-raft_enable_append_entries_cache).

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
