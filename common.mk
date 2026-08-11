# Shared by the root Makefile and each product's Makefile (e.g. braft/Makefile).
# Included with a path relative to whoever includes it (`include common.mk`
# from root, `include ../common.mk` from a product directory).

SSH_USER ?= ubuntu
SSH_KEY  ?= ~/.ssh/id_rsa
SSH_OPTS  = -i $(SSH_KEY) -o StrictHostKeyChecking=accept-new

NODES = $(NODE1) $(NODE2) $(NODE3)
