#!/bin/bash
# One-time cluster formation for the openraft raft-kv-memstore example.
# Unlike braft, there's no static -conf peer list -- membership is entirely
# runtime-configured: init node 1 as the sole member, add the other two as
# learners, then promote all three to voters. Re-run only if the cluster is
# torn down and recreated.
set -euo pipefail

NODE1_API="${1:?usage: init-cluster.sh <node1_api_addr> <node2_id:api:raft> <node3_id:api:raft>}"
NODE2_SPEC="${2:?missing node2 spec, e.g. 2:1.2.3.4:21001:1.2.3.4:22001}"
NODE3_SPEC="${3:?missing node3 spec, e.g. 3:1.2.3.5:21001:1.2.3.5:22001}"

add_learner() {
	local spec="$1"
	IFS=':' read -r id api_ip api_port raft_ip raft_port <<<"$spec"
	curl -sf -X POST "http://${NODE1_API}/add-learner" \
		-H 'Content-Type: application/json' \
		-d "{\"node_id\":${id},\"api_addr\":\"${api_ip}:${api_port}\",\"raft_addr\":\"${raft_ip}:${raft_port}\"}"
	echo
}

echo "=== init ==="
curl -sf -X POST "http://${NODE1_API}/init" -H 'Content-Type: application/json' -d '[]'
echo

echo "=== add-learner (node 2) ==="
add_learner "$NODE2_SPEC"

echo "=== add-learner (node 3) ==="
add_learner "$NODE3_SPEC"

echo "=== change-membership ==="
curl -sf -X POST "http://${NODE1_API}/change-membership" -H 'Content-Type: application/json' -d '[1,2,3]'
echo
