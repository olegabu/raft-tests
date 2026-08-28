#!/usr/bin/env python3
"""Merge several clients' raw histograms and report aggregate percentiles.

    python3 sweep/merge-hdr.py client0.csv client1.csv ...

Each input is what sequencer's load generator writes with
--hdr_raw_out: a "value,count" line per recorded bucket. Merging is a
sum over buckets, and percentiles come off the merged distribution.

This exists because you cannot average percentiles. With load split
across N clients, the mean of their p50s is not the p50 of the union —
it is only equal when every client saw an identical distribution, which
is exactly what a split-load test is trying to find out. A straggler
client is invisible in an average and obvious in a merge, so the
per-client numbers are printed alongside.

Accuracy is bounded by the histogram's own precision (3 significant
figures), the same bound the load generator's own reported percentiles
carry, so a single-file merge reproduces that client's reported values.
"""
import sys


def load(path):
    buckets = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("value"):
                continue
            value, count = line.split(",")
            buckets.append((int(value), int(count)))
    return buckets


def percentile(sorted_buckets, total, pct):
    """Lowest value whose cumulative count reaches pct% of the total.

    Matches HdrHistogram's own convention: it walks buckets in
    ascending value and returns the first whose running total is at or
    past the requested rank.
    """
    if total == 0:
        return 0
    target = total * pct / 100.0
    seen = 0
    for value, count in sorted_buckets:
        seen += count
        if seen >= target:
            return value
    return sorted_buckets[-1][0]


def summarise(buckets):
    buckets = sorted(buckets)
    total = sum(c for _, c in buckets)
    return buckets, total


def main(paths):
    if not paths:
        print(__doc__, file=sys.stderr)
        return 2

    merged = []
    print(f"{'file':<44}{'count':>12}{'p50':>9}{'p99':>10}")
    for path in paths:
        buckets, total = summarise(load(path))
        merged.extend(buckets)
        name = path.rsplit("/", 1)[-1]
        print(f"{name:<44}{total:>12,}{percentile(buckets, total, 50):>9}"
              f"{percentile(buckets, total, 99):>10}")

    buckets, total = summarise(merged)
    if total == 0:
        print("\nno samples", file=sys.stderr)
        return 1
    print(f"\n{'MERGED (' + str(len(paths)) + ' clients)':<44}{total:>12,}"
          f"{percentile(buckets, total, 50):>9}{percentile(buckets, total, 99):>10}")
    for pct in (50.0, 90.0, 99.0, 99.9):
        print(f"  p{pct:<6g} {percentile(buckets, total, pct):>8} us")
    print(f"  max     {buckets[-1][0]:>8} us")
    # Machine-readable, for a sweep script to grep the way it greps the
    # load generator's own summary.
    print(f"merged_count={total}")
    print(f"merged_p50_us={percentile(buckets, total, 50)}")
    print(f"merged_p90_us={percentile(buckets, total, 90)}")
    print(f"merged_p99_us={percentile(buckets, total, 99)}")
    print(f"merged_p99_9_us={percentile(buckets, total, 99.9)}")
    print(f"merged_max_us={buckets[-1][0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
