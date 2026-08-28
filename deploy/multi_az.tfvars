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
# Clients run at 4 vCPU while nodes stay at 8. The five-gateway result
# (sequencer/multi-gateway-p50.svg) was measured with 8-vCPU clients and
# 5x8 + 3x8 = 64 vCPU left no headroom at all; at 4 vCPU the same fleet
# is 5x4 + 3x8 = 44, which is what frees budget for bigger nodes once
# the quota request clears. Whether 4 cores still carries the load is
# itself the thing being checked -- each client box runs BOTH a load
# generator and an input gateway, so they compete for those cores.
node_instance_type   = "c7a.2xlarge"
client_instance_type = "c7a.xlarge"
