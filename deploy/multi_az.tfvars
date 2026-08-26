topology = "multi_az"
node_azs = ["us-east-1a", "us-east-1b", "us-east-1c"]

# Upgraded from the c6i.2xlarge default (deploy/main.tf) for the
# <1ms-p50-at-100k / knee-beyond-100k push. Same 8-vCPU footprint (the
# account's 32-vCPU on-demand quota caps the 4-instance fleet at
# 2xlarge — c7i.8xlarge hit VcpuLimitExceeded), so the upgrade is
# per-core speed: c7a is AMD Genoa at ~3.7GHz, the fastest cores in
# this class. ~$39/day for the 4 instances vs ~$33 for c6i.2xlarge.
node_instance_type   = "c7a.2xlarge"
client_instance_type = "c7a.2xlarge"
