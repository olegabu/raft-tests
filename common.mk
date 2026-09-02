# Shared by the root Makefile and each product's Makefile (e.g. braft/Makefile).
# Included with a path relative to whoever includes it (`include common.mk`
# from root, `include ../common.mk` from a product directory).

SSH_USER ?= ubuntu
SSH_KEY  ?= ~/.ssh/id_rsa
SSH_OPTS  = -i $(SSH_KEY) -o StrictHostKeyChecking=accept-new

NODES = $(NODE1) $(NODE2) $(NODE3)

## The hardware a sweep ran on, written into every row that goes through
## sweep/sweep-multi.sh.
##
## Latency is meaningless without it, and a filename does not survive
## being copied or charted. braft-multi.csv sat here showing bare
## consensus plateauing at ~245k with nothing recording which fleet
## produced it, so comparing it against a sequencer sweep meant
## guessing. Override when the fleet changes:
##   make sweep-multi FLEET=c7a.8xl-nodes/c7a.4xl-clients
FLEET ?= nodes-3x-c7a.4xlarge-multiaz/clients-5x-c7a.2xlarge
