#!/bin/bash

# Copyright (c) 2018 Baidu.com, Inc. All Rights Reserved
# 
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# 
#     http://www.apache.org/licenses/LICENSE-2.0
# 
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# source shflags from current directory
mydir="${BASH_SOURCE%/*}"
if [[ ! -d "$mydir" ]]; then mydir="$PWD"; fi
. $mydir/shflags

# define command-line flags
DEFINE_integer bthread_concurrency '18' 'Number of worker pthreads'
DEFINE_string sync 'false' 'fsync each time'
DEFINE_string valgrind 'false' 'Run in valgrind'
DEFINE_integer max_segment_size '8388608' 'Max segment size'
DEFINE_integer max_entries_size '1024' 'Max number of entries in one AppendEntriesRequest'
DEFINE_integer max_body_size '524288' 'Max byte size of one AppendEntriesRequest'
DEFINE_integer max_parallel_append_entries_rpc_num '1' 'Max number of parallel AppendEntries RPCs in flight per follower'
DEFINE_integer apply_batch '32' 'Max number of tasks applied to the FSM in a single batch'
DEFINE_integer fsm_caller_commit_batch '512' 'Max number of logs committed to the FSM in a single batch'
DEFINE_integer max_append_buffer_size '262144' 'Max byte size of the log append buffer before flushing to LogStorage'
DEFINE_integer server_num '3' 'Number of servers'
DEFINE_boolean clean 1 'Remove old "runtime" dir before running'
DEFINE_integer port 8300 "Port of the first server"
DEFINE_string peers '' 'Comma-separated ip:port:index list; overrides the locally built peer list'

# parse the command-line
FLAGS "$@" || exit 1
eval set -- "${FLAGS_ARGV}"

# The alias for printing to stderr
alias error=">&2 echo atomic: "

# hostname prefers ipv6
IP=`hostname -i | awk '{print $NF}'`

if [ "$FLAGS_valgrind" == "true" ] && [ $(which valgrind) ] ; then
    VALGRIND="valgrind --tool=memcheck --leak-check=full"
fi

raft_peers=""
for ((i=0; i<$FLAGS_server_num; ++i)); do
    raft_peers="${raft_peers}${IP}:$((${FLAGS_port}+i)):0,"
done

if [ -n "$FLAGS_peers" ]; then
    raft_peers="$FLAGS_peers"
fi

if [ "$FLAGS_clean" == "0" ]; then
    rm -rf runtime
fi

export TCMALLOC_SAMPLE_PARAMETER=524288

for ((i=0; i<$FLAGS_server_num; ++i)); do
    mkdir -p runtime/$i
    cp ./build/atomic_server runtime/$i
    cd runtime/$i
    ${VALGRIND} ./atomic_server \
        -bthread_concurrency=${FLAGS_bthread_concurrency}\
        -raft_max_segment_size=${FLAGS_max_segment_size} \
        -raft_max_entries_size=${FLAGS_max_entries_size} \
        -raft_max_body_size=${FLAGS_max_body_size} \
        -raft_max_parallel_append_entries_rpc_num=${FLAGS_max_parallel_append_entries_rpc_num} \
        -raft_apply_batch=${FLAGS_apply_batch} \
        -raft_fsm_caller_commit_batch=${FLAGS_fsm_caller_commit_batch} \
        -raft_max_append_buffer_size=${FLAGS_max_append_buffer_size} \
        -raft_sync=${FLAGS_sync} \
        -port=$((${FLAGS_port}+i)) -conf="${raft_peers}" > std.log 2>&1 &
    cd ../..
done
