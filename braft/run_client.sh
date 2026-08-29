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
DEFINE_boolean clean 1 'Remove old "runtime" dir before running'
DEFINE_integer add_percentage 100 'Percentage of fetch_add operation'
DEFINE_integer bthread_concurrency '8' 'Number of worker pthreads'
DEFINE_integer event_dispatcher_num '1' 'brpc event dispatcher threads (socket read/parse)'
DEFINE_string connection_type '' 'brpc connection type: single (default), pooled, short'
DEFINE_integer channels 1 'Open mode: distinct connections to the leader, round-robin'
DEFINE_integer server_port 8300 "Port of the first server"
DEFINE_integer server_num '3' 'Number of servers'
DEFINE_integer thread_num 1 'Number of sending thread'
DEFINE_string log_each_request 'false' 'Print log for each request'
DEFINE_string valgrind 'false' 'Run in valgrind'
DEFINE_string use_bthread "true" "Use bthread to send request"
DEFINE_string peers '' 'Comma-separated ip:port:index list; overrides the locally built peer list'
DEFINE_string mode 'closed' 'closed: keep thread_num outstanding. open: emit at rate'
DEFINE_integer rate 0 'Target requests per second (open mode)'
DEFINE_integer burst 1 'Requests per scheduled instant (open mode)'
DEFINE_integer max_inflight 0 'Cap on unanswered requests (open mode); 0 derives one'
DEFINE_integer warmup 10 'Seconds discarded before measuring'
DEFINE_integer measure 30 'Seconds recorded'
DEFINE_integer drain_timeout 10 'Seconds to wait for in-flight replies after the window'
DEFINE_string pace 'spin' 'open mode wait strategy between sends: spin or park'
DEFINE_string hdr_out '' 'Write a percentile report here'
DEFINE_string hdr_raw_out '' 'Write raw value,count buckets here, for merging across clients'

FLAGS "$@" || exit 1

# hostname prefers ipv6
IP=`hostname -i | awk '{print $NF}'`

if [ "$FLAGS_valgrind" == "true" ] && [ $(which valgrind) ] ; then
    VALGRIND="valgrind --tool=memcheck --leak-check=full"
fi

raft_peers=""
for ((i=0; i<$FLAGS_server_num; ++i)); do
    raft_peers="${raft_peers}${IP}:$((${FLAGS_server_port}+i)):0,"
done

if [ -n "$FLAGS_peers" ]; then
    raft_peers="$FLAGS_peers"
fi

export TCMALLOC_SAMPLE_PARAMETER=524288

${VALGRIND} ./build/atomic_client \
        --add_percentage=${FLAGS_add_percentage} \
        --bthread_concurrency=${FLAGS_bthread_concurrency} \
        --conf="${raft_peers}" \
        --log_each_request=${FLAGS_log_each_request} \
        --thread_num=${FLAGS_thread_num} \
        --use_bthread=${FLAGS_use_bthread} \
        --mode=${FLAGS_mode} \
        --rate=${FLAGS_rate} \
        --burst=${FLAGS_burst} \
        --max_inflight=${FLAGS_max_inflight} \
        --warmup=${FLAGS_warmup} \
        --measure=${FLAGS_measure} \
        --drain_timeout=${FLAGS_drain_timeout} \
        --pace=${FLAGS_pace} \
        --event_dispatcher_num=${FLAGS_event_dispatcher_num} \
        --connection_type="${FLAGS_connection_type}" \
        --channels=${FLAGS_channels} \
        --hdr_out="${FLAGS_hdr_out}" \
        --hdr_raw_out="${FLAGS_hdr_raw_out}" \

