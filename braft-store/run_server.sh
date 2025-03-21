#!/bin/bash

# The alias for printing to stderr
alias error=">&2 echo store: "

# hostname prefers ipv6
IP=`hostname -i | awk '{print $NF}'`

raft_peers=""
for ((i=0; i<3; ++i)); do
    raft_peers="${raft_peers}${IP}:$((8300+i)):0,"
done

export TCMALLOC_SAMPLE_PARAMETER=524288

for ((i=0; i<3; ++i)); do
    mkdir -p runtime/$i
    cp ./build/store_server runtime/$i
    cd runtime/$i
    ./store_server \
        -bthread_concurrency=18\
        -raft_max_segment_size=8388608 \
        -raft_sync=false \
        -batch_size=1000 \
        -sleep_microseconds=1000 \
        -port=$((8300+i)) -conf="${raft_peers}" > std.log 2>&1 &
    cd ../..
done
