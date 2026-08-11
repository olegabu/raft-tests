terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = "us-east-1"
}

variable "az" {
  description = "Availability zone for single_az topology; cluster placement groups require a single AZ"
  default     = "us-east-1a"
}

variable "topology" {
  description = "single_az: all 4 instances in one cluster placement group/AZ. multi_az: raft nodes spread across node_azs, client colocated with node[0] in a 2-instance cluster placement group"
  type        = string
  default     = "single_az"
  validation {
    condition     = contains(["single_az", "multi_az"], var.topology)
    error_message = "topology must be \"single_az\" or \"multi_az\"."
  }
}

variable "node_azs" {
  description = "Per-node AZ list, used only when topology = multi_az. node_azs[0] is where the client and its cluster placement group live."
  type        = list(string)
  default     = ["us-east-1a", "us-east-1b", "us-east-1c"]
}

variable "node_instance_type" {
  description = "Raft nodes; benchmark runs with -raft_sync=false so no local NVMe needed. Switch to c6id.2xlarge to test -sync=true against instance-store NVMe."
  default     = "c6i.2xlarge"
}

variable "client_instance_type" {
  description = "Command-and-control / load generator"
  default     = "c6i.2xlarge"
}

variable "ssh_ingress_cidr" {
  description = "CIDR allowed to ssh in, e.g. 203.0.113.5/32"
  type        = string
}

variable "ssh_public_key_path" {
  default = "~/.ssh/id_rsa.pub"
}

variable "raft_port" {
  description = "Port each node's brpc server listens on; also serves braft's builtin stats/status pages over HTTP on the same port"
  default     = 8300
}

# Ubuntu 22.04 to match the glibc of binaries built on the dev machine
data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

data "aws_vpc" "default" {
  default = true
}

locals {
  node_az_list = var.topology == "multi_az" ? var.node_azs : [var.az, var.az, var.az]
  client_az    = local.node_az_list[0]
}

data "aws_subnet" "by_az" {
  for_each          = toset(local.node_az_list)
  vpc_id            = data.aws_vpc.default.id
  availability_zone = each.value

  filter {
    name   = "default-for-az"
    values = ["true"]
  }
}

resource "aws_key_pair" "bench" {
  key_name   = "raft-bench"
  public_key = file(var.ssh_public_key_path)
}

resource "aws_placement_group" "raft" {
  name     = "raft-bench"
  strategy = "cluster"
}

resource "aws_security_group" "raft" {
  name   = "raft-bench"
  vpc_id = data.aws_vpc.default.id

  ingress {
    description = "ssh"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.ssh_ingress_cidr]
  }

  ingress {
    description = "braft node stats/status pages (brpc builtin services over HTTP, same port as raft RPC)"
    from_port   = var.raft_port
    to_port     = var.raft_port
    protocol    = "tcp"
    cidr_blocks = [var.ssh_ingress_cidr]
  }

  ingress {
    description = "all traffic within the cluster"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    self        = true
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# The raft log lives under /data. On instance types with instance-store NVMe
# (c6id, for -sync=true runs) it is formatted and mounted there; otherwise
# /data is just a dir on the root EBS volume, which is fine with -sync=false.
locals {
  node_user_data = <<-EOT
    #!/bin/bash
    set -e
    apt-get update -q && apt-get install -y -q psmisc fio
    DEV=""
    for d in /dev/nvme*n1; do
      if [ -z "$(lsblk -no MOUNTPOINT $d | tr -d '[:space:]')" ]; then DEV=$d; break; fi
    done
    if [ -n "$DEV" ]; then
      mkfs.ext4 -F $DEV
      mkdir -p /data
      mount -o noatime $DEV /data
    else
      mkdir -p /data
    fi
    chown ubuntu:ubuntu /data
  EOT

  client_user_data = <<-EOT
    #!/bin/bash
    apt-get update -q && apt-get install -y -q psmisc
  EOT
}

resource "aws_instance" "node" {
  count             = 3
  ami               = data.aws_ami.ubuntu.id
  instance_type     = var.node_instance_type
  availability_zone = local.node_az_list[count.index]
  subnet_id         = data.aws_subnet.by_az[local.node_az_list[count.index]].id
  placement_group   = local.node_az_list[count.index] == local.client_az ? aws_placement_group.raft.name : null
  key_name          = aws_key_pair.bench.key_name
  user_data         = local.node_user_data

  vpc_security_group_ids = [aws_security_group.raft.id]

  root_block_device {
    volume_type = "gp3"
    volume_size = 100
  }

  tags = {
    Name = "raft-node-${count.index}"
  }
}

resource "aws_instance" "client" {
  ami               = data.aws_ami.ubuntu.id
  instance_type     = var.client_instance_type
  availability_zone = local.client_az
  subnet_id         = data.aws_subnet.by_az[local.client_az].id
  placement_group   = aws_placement_group.raft.name
  key_name          = aws_key_pair.bench.key_name
  user_data         = local.client_user_data

  vpc_security_group_ids = [aws_security_group.raft.id]

  tags = {
    Name = "raft-client"
  }
}

output "node_public_ips" {
  value = aws_instance.node[*].public_ip
}

output "node_private_ips" {
  value = aws_instance.node[*].private_ip
}

output "client_public_ip" {
  value = aws_instance.client.public_ip
}

# Rendered .env for the Makefile: `make env` writes this to ../.env
output "env_file" {
  value = <<-EOT
    NODE1=${aws_instance.node[0].public_ip}
    NODE2=${aws_instance.node[1].public_ip}
    NODE3=${aws_instance.node[2].public_ip}
    NODE1_PRIV=${aws_instance.node[0].private_ip}
    NODE2_PRIV=${aws_instance.node[1].private_ip}
    NODE3_PRIV=${aws_instance.node[2].private_ip}
    CLIENT=${aws_instance.client.public_ip}
    SSH_USER=ubuntu
    SSH_KEY=${replace(var.ssh_public_key_path, ".pub", "")}
    SSH_INGRESS_CIDR=${var.ssh_ingress_cidr}
  EOT
}
