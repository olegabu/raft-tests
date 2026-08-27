# fleets/ — the same sweep on different hardware

One CSV per instance type, in the 10-column shape every sweep in this
repo writes (see [../../sweep/README.md](../../sweep/README.md)).
These back the three `fleet-*.svg` charts in the parent README's
"Does hardware move any of this?" section; regenerate them with
`python3 ../../sweep/mkcharts-fleets.py`.

| file | fleet | what it holds |
|---|---|---|
| `ack-c7a-2xlarge.csv` | c7a.2xlarge, AMD Genoa, 8 vCPU | phase-1 ack sweep |
| `ack-c7a-4xlarge.csv` | c7a.4xlarge, AMD Genoa, 16 vCPU | phase-1 ack sweep |
| `ack-c6in-4xlarge.csv` | c6in.4xlarge, Intel Ice Lake, network-optimised | phase-1 ack sweep |
| `ack-c7i-4xlarge.csv` | c7i.4xlarge, Intel Sapphire Rapids | phase-1 ack sweep |
| `paired-c7a-2xlarge-brpc.csv` | c7a.2xlarge | ack **and** dissemination p50 from the *same* runs |
| `paired-c7a-2xlarge-brpc-dissemination.csv` | c7a.2xlarge | the dissemination sweep those pairs came from |
| `aeron-c6in-4xlarge.csv` | c6in.4xlarge | Aeron Cluster sweep |
| `aeron-c7i-4xlarge.csv` | c7i.4xlarge | Aeron Cluster sweep |

`paired-c7a-2xlarge-brpc.csv` is the odd one out — three columns
(`rate,ack_p50,diss_p50`) rather than the standard ten. It exists
because the standard sweep scripts extract one percentile per run,
which makes ack and dissemination numbers come from different runs and
therefore not comparable (see the parent README's observer caveat).
Its ack column was recovered from the per-rate logs
`sweep-output.sh` leaves in `/tmp`, so both figures describe identical
traffic.

**Do not merge these into one file and chart them together the way
`mkcharts.py` merges per-product CSVs.** That script averages rows
sharing a product and rate, and every file here uses the same product
name — merging would silently average four fleets into a curve that
describes none of them. `mkcharts-fleets.py` keeps them separate for
exactly this reason.
