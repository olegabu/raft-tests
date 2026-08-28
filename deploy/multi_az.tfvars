topology = "multi_az"
node_azs = ["us-east-1a", "us-east-1b", "us-east-1c"]

# c7a is AMD Genoa at ~3.7GHz, the fastest cores in this class, and
# 2xlarge measured best of the four types swept — see
# ../sequencer/README.md's "Does hardware move any of this?". Notably
# it beat the 16 vCPU variants at half the cost: doubling cores moved
# nothing, because at the knee the raft leader is ~99.6% idle.
# ~$39/day for the 4 instances; stop them when not benchmarking.
#
# The account's on-demand quota was raised from 32 to 64 vCPU on
# 2026-08-26 (an earlier c7i.8xlarge attempt hit VcpuLimitExceeded
# against the old one), so 4xlarge across four instances now fits
# exactly, with no headroom. Resize STOPPED instances in place with
# `aws ec2 modify-instance-attribute`: terraform plans this change as
# destroy-and-recreate, which would both wipe the disks and briefly
# need more vCPU than the quota allows.
# 5x8 (clients) + 3x16 (nodes) = 88 vCPU, inside the 128 the account was
# raised to on 2026-08-28. Clients are back at 8 vCPU because 4 did not
# hold the result -- p50 at 100k went 747us to 1193us, with the box only
# 47% busy, so it was thread contention between the colocated load
# generator and input gateway rather than any shortage of capacity
# (sequencer/seq-multi-4core.csv). Nodes go to 16 so that node size is
# the ONLY difference from the five-gateway arm in seq-multi.csv.
# AMD Genoa throughout: it measured lower latency than the Intel parts
# tried here (sequencer/fleet-instance-types.svg).
node_instance_type   = "c7a.4xlarge"
client_instance_type = "c7a.2xlarge"
