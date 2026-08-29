// Copyright (c) 2018 Baidu.com, Inc. All Rights Reserved
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
//
// Load generator for the braft atomic-counter example. Supports both load
// models:
//
//  - closed loop: keep --thread_num requests outstanding, so offered load
//    adapts to how fast the cluster answers. Measures the service latency one
//    well-behaved client sees, and by construction cannot observe saturation --
//    past the knee it simply stops offering more load.
//  - open loop: emit on a fixed schedule at --rate regardless of whether
//    replies have arrived, because real arrivals do not slow down when the
//    system does. Latency is measured from each request's *scheduled* send
//    time, so time spent waiting to send is charged to the system, which is
//    what makes this immune to coordinated omission.
//
// No server-side echo or timestamp plumbing is needed: brpc binds each response
// to its call, so the scheduled (or actual) send time is simply held in the
// per-request context and stamped on arrival.

#include <gflags/gflags.h>
#include <glog/logging.h>
#include <bthread/bthread.h>
#include <brpc/channel.h>
#include <brpc/controller.h>
#include <braft/raft.h>
#include <braft/util.h>
#include <braft/route_table.h>
#include <proto/atomic.pb.h>
#include <hdr/hdr_histogram.h>

#include <atomic>
#include <cstdio>
#include <memory>
#include <string>
#include <vector>

DEFINE_bool(log_each_request, false, "Print log for each request");
DEFINE_bool(use_bthread, false, "Use bthread to send requests");
DEFINE_int32(add_percentage, 100, "Percentage of fetch_add");
DEFINE_int64(added_by, 1, "Num added to each peer");
DEFINE_int32(thread_num, 100, "Number of concurrent senders (closed mode)");
DEFINE_int32(timeout_ms, 1000, "Timeout for each request");
DEFINE_string(conf, "", "Configuration of the raft group");
DEFINE_string(group, "Atomic", "Id of the replication group");

DEFINE_string(mode, "closed", "closed: keep thread_num outstanding. open: emit at rate");
DEFINE_int64(rate, 0, "Target requests per second (open mode)");
DEFINE_int32(burst, 1, "Requests per scheduled instant, sharing its scheduled time (open mode)");
DEFINE_int32(max_inflight, 0, "Cap on unanswered requests (open mode); 0 derives one");
DEFINE_int32(warmup, 10, "Seconds of traffic discarded before measuring");
DEFINE_int32(measure, 30, "Seconds recorded");
DEFINE_int32(drain_timeout, 10, "Seconds to wait for in-flight replies after the window closes");
DEFINE_string(pace, "spin", "open mode wait strategy between sends: spin or park");
// brpc's default connection type is "single": one TCP connection to the leader,
// multiplexed, whose reads are parsed by one event-dispatcher thread on each
// end. That is a serialization point independent of consensus, and it does not
// go away by adding server threads -- one socket is handled by one dispatcher.
// "pooled" gives each in-flight RPC its own connection, so parsing spreads over
// -event_dispatcher_num threads. Empty leaves brpc's default in place.
DEFINE_string(connection_type, "", "brpc connection type: single, pooled or short");
// brpc keys its socket map on the address, so N channels to one leader share one
// connection -- and one connection means one TCP stream, where a single lost
// segment stalls everything queued behind it. That head-of-line blocking is
// invisible in p50 and dominates p99. Channels in different connection_groups
// get their own sockets, which gives a fixed pool of persistent multiplexed
// connections: the tail relief of "pooled" without a connection per in-flight
// RPC, so it does not exhaust ephemeral ports at six-figure rates.
DEFINE_int32(channels, 1, "Open mode: distinct connections to the leader, round-robin");
DEFINE_string(hdr_out, "", "Write a percentile report here");
DEFINE_string(hdr_raw_out, "",
              "Write the raw recorded buckets here as value,count -- the form several clients' "
              "histograms can be MERGED from (../sweep/merge-hdr.py). --hdr_out writes a "
              "formatted percentile report, which carries percentiles but not the counts they "
              "came from, so two of them cannot be combined. Splitting offered load across "
              "several client boxes needs this, because percentiles do not average: the mean of "
              "N clients' p50s is the p50 of their union only if every client saw the same "
              "distribution, which is the thing a split-load test sets out to measure. Mirrors "
              "sequencer's own load generator flag of the same name, so ../sweep/sweep-multi.sh "
              "drives both without knowing which product it is running.");

// One minute in microseconds: the histogram ceiling. brpc reports latency in
// microseconds, so that is the unit used throughout.
static const int64_t kHighestTrackableUs = 60L * 1000L * 1000L;

// Live per-second line only; the run summary comes from the histograms below.
bvar::LatencyRecorder g_latency_recorder("atomic_client");

// Recorded from many threads (closed mode senders, or brpc's bthread workers
// running the async callbacks in open mode), so recording uses the atomic
// variant rather than per-thread histograms plus a merge.
static struct hdr_histogram* g_measured = NULL;
static struct hdr_histogram* g_lag = NULL;

static std::atomic<bool> g_measuring(false);
static std::atomic<int64_t> g_inflight(0);
static std::atomic<int64_t> g_dropped(0);
static std::atomic<int64_t> g_completed(0);
static int64_t g_measure_start_us = 0;
static int64_t g_measure_end_us = 0;

static bool is_open_mode() {
    return FLAGS_mode == "open";
}

static int64_t now_us() {
    return butil::gettimeofday_us();
}

struct SendArg {
    int64_t id;
    int64_t deadline_us;
};

// Selecting the leader and building a channel per request -- which this client
// used to do inside its send loop -- is pure overhead repeated on every call.
// One channel per sender, refreshed only when the leader actually moves.
static bool refresh_channel(brpc::Channel* channel, braft::PeerId* leader,
                            const std::string& connection_group = std::string()) {
    if (braft::rtb::select_leader(FLAGS_group, leader) != 0) {
        butil::Status st = braft::rtb::refresh_leader(FLAGS_group, FLAGS_timeout_ms);
        if (!st.ok()) {
            LOG(WARNING) << "Fail to refresh_leader : " << st;
            bthread_usleep(FLAGS_timeout_ms * 1000L);
        }
        return false;
    }
    brpc::ChannelOptions options;
    if (!FLAGS_connection_type.empty()) {
        options.connection_type = FLAGS_connection_type;
    }
    options.connection_group = connection_group;
    if (channel->Init(leader->addr, &options) != 0) {
        LOG(ERROR) << "Fail to init channel to " << *leader;
        bthread_usleep(FLAGS_timeout_ms * 1000L);
        return false;
    }
    return true;
}

static void record(int64_t latency_us) {
    g_latency_recorder << latency_us;
    g_completed.fetch_add(1, std::memory_order_relaxed);
    if (g_measuring.load(std::memory_order_relaxed)) {
        if (latency_us < 1) {
            latency_us = 1;
        } else if (latency_us > kHighestTrackableUs) {
            latency_us = kHighestTrackableUs;
        }
        hdr_record_value_atomic(g_measured, latency_us);
    }
}

// ---------------------------------------------------------------------------
// Closed loop
// ---------------------------------------------------------------------------

static void* sender(void* arg) {
    SendArg* sa = (SendArg*)arg;
    int64_t value = 0;
    brpc::Channel channel;
    braft::PeerId leader;
    bool have_channel = false;

    while (!brpc::IsAskedToQuit() && now_us() < sa->deadline_us) {
        if (!have_channel) {
            have_channel = refresh_channel(&channel, &leader);
            if (!have_channel) {
                continue;
            }
        }

        example::AtomicService_Stub stub(&channel);
        brpc::Controller cntl;
        cntl.set_timeout_ms(FLAGS_timeout_ms);
        example::CompareExchangeRequest request;
        example::AtomicResponse response;
        request.set_id(sa->id);
        request.set_expected_value(value);
        request.set_new_value(value + 1);

        const int64_t sent_us = now_us();
        stub.compare_exchange(&cntl, &request, &response, NULL);

        if (cntl.Failed()) {
            LOG(WARNING) << "Fail to send request to " << leader
                         << " : " << cntl.ErrorText();
            braft::rtb::update_leader(FLAGS_group, braft::PeerId());
            have_channel = false;
            bthread_usleep(FLAGS_timeout_ms * 1000L);
            continue;
        }

        if (!response.success()) {
            if (!response.has_old_value()) {
                LOG(WARNING) << "Fail to send request to " << leader
                             << ", redirecting to "
                             << (response.has_redirect()
                                    ? response.redirect() : "nowhere");
                braft::rtb::update_leader(FLAGS_group, response.redirect());
                have_channel = false;
                continue;
            }
            if (value == 0 || response.old_value() == value + 1) {
                value = response.old_value();
            } else {
                CHECK_EQ(value, response.old_value());
                exit(-1);
            }
        } else {
            value = response.new_value();
        }

        // Actual send time is the honest start in closed mode: this sender
        // genuinely was not waiting before it sent.
        record(now_us() - sent_us);
        if (FLAGS_log_each_request) {
            LOG(INFO) << "Received response from " << leader
                      << " old_value=" << response.old_value()
                      << " new_value=" << response.new_value()
                      << " latency=" << cntl.latency_us();
            bthread_usleep(1000L * 1000L);
        }
    }
    return NULL;
}

// ---------------------------------------------------------------------------
// Open loop
// ---------------------------------------------------------------------------

// Owns one in-flight async call. brpc requires the controller, request and
// response to outlive the call, so they live here and the callback deletes it.
struct OpenCall {
    brpc::Controller cntl;
    example::CompareExchangeRequest request;
    example::AtomicResponse response;
    int64_t scheduled_us;
};

static void on_open_response(OpenCall* call) {
    std::unique_ptr<OpenCall> guard(call);
    g_inflight.fetch_sub(1, std::memory_order_relaxed);

    if (call->cntl.Failed()) {
        // Leadership moved or the node is unreachable; make the next scheduled
        // send re-resolve. The request is not recorded -- it never completed.
        braft::rtb::update_leader(FLAGS_group, braft::PeerId());
        return;
    }
    if (!call->response.success() && !call->response.has_old_value()) {
        braft::rtb::update_leader(FLAGS_group, call->response.redirect());
        return;
    }

    // Measured from the scheduled instant, not the actual send: any delay in
    // getting it out is the system's and is charged to it.
    record(now_us() - call->scheduled_us);
    if (FLAGS_log_each_request) {
        LOG(INFO) << "response latency=" << (now_us() - call->scheduled_us);
    }
}

static void run_open_loop(int64_t start_us, int64_t end_us) {
    const double interval_us = 1000000.0 / (double)FLAGS_rate;
    const bool pace_spin = FLAGS_pace != "park";
    int64_t sequence = 0;
    const int nchannels = FLAGS_channels > 0 ? FLAGS_channels : 1;
    std::vector<std::unique_ptr<brpc::Channel> > channels;
    braft::PeerId leader;
    bool have_channel = false;

    while (!brpc::IsAskedToQuit()) {
        const int64_t scheduled_us = start_us + (int64_t)((double)sequence * interval_us);
        if (scheduled_us > end_us) {
            break;
        }

        const int64_t now = now_us();
        if (now < scheduled_us) {
            // Replies are handled on brpc bthread workers, not here, so this
            // thread waiting does not delay reply processing -- only the send
            // schedule depends on it. This runs on the main pthread rather than a
            // bthread, so bthread_yield/bthread_usleep are not the right tools:
            // spin tightly for precision, or usleep to hand back the core.
            if (!pace_spin && (scheduled_us - now) > 150) {
                usleep(50);
            }
            continue;
        }

        if (!have_channel) {
            channels.clear();
            bool ok = true;
            for (int c = 0; c < nchannels && ok; ++c) {
                std::unique_ptr<brpc::Channel> ch(new brpc::Channel);
                // One group per channel, so each gets its own socket.
                ok = refresh_channel(ch.get(), &leader, "ch" + std::to_string(c));
                if (ok) {
                    channels.push_back(std::move(ch));
                }
            }
            have_channel = ok;
            if (!have_channel) {
                // Could not even resolve a leader; the messages that were due
                // meanwhile were never offered, which is the rig's failure to
                // deliver rate R and must be counted, not silently skipped.
                g_dropped.fetch_add(FLAGS_burst, std::memory_order_relaxed);
                sequence += FLAGS_burst;
                continue;
            }
        }

        for (int i = 0; i < FLAGS_burst; ++i) {
            if (g_inflight.load(std::memory_order_relaxed) >= FLAGS_max_inflight) {
                g_dropped.fetch_add(1, std::memory_order_relaxed);
                ++sequence;
                continue;
            }

            OpenCall* call = new OpenCall();
            call->scheduled_us = scheduled_us;
            call->cntl.set_timeout_ms(FLAGS_timeout_ms);
            // Every request is an independent increment attempt on its own id;
            // unlike the closed-loop sender there is no read-back chain to keep,
            // so a bare compare_exchange from the scheduled sequence is enough.
            call->request.set_id(sequence % 1024);
            call->request.set_expected_value(sequence);
            call->request.set_new_value(sequence + 1);

            example::AtomicService_Stub stub(channels[sequence % nchannels].get());
            g_inflight.fetch_add(1, std::memory_order_relaxed);
            if (g_measuring.load(std::memory_order_relaxed)) {
                int64_t lag = now_us() - scheduled_us;
                hdr_record_value_atomic(g_lag, lag < 1 ? 1 : lag);
            }
            stub.compare_exchange(&call->cntl, &call->request, &call->response,
                                  brpc::NewCallback(on_open_response, call));
            ++sequence;
        }
    }

    // Drain: replies still arriving belong to requests scheduled inside the
    // window, so let them land before closing the histogram.
    const int64_t drain_deadline = now_us() + (int64_t)FLAGS_drain_timeout * 1000000L;
    while (g_inflight.load(std::memory_order_relaxed) > 0 && now_us() < drain_deadline) {
        bthread_usleep(10000);
    }
}

// ---------------------------------------------------------------------------

static void print_summary() {
    const double window_s = (g_measure_end_us > g_measure_start_us)
        ? (double)(g_measure_end_us - g_measure_start_us) / 1e6 : 0.0;
    const int64_t count = g_measured->total_count;
    const double achieved = window_s > 0 ? (double)count / window_s : 0.0;
    const int64_t dropped = g_dropped.load(std::memory_order_relaxed);

    printf("\n=== summary ===\n");
    printf("mode                 %s\n", FLAGS_mode.c_str());
    printf("group                %s (%s)\n", FLAGS_group.c_str(), FLAGS_conf.c_str());
    if (is_open_mode()) {
        printf("offered rate         %ld req/s (burst %d)\n", (long)FLAGS_rate, FLAGS_burst);
        printf("max inflight         %d\n", FLAGS_max_inflight);
    } else {
        printf("outstanding          %d\n", FLAGS_thread_num);
    }
    printf("measure window       %.1f s\n", window_s);
    printf("completed            %ld\n", (long)count);
    printf("achieved rate        %.0f req/s\n", achieved);
    printf("dropped-by-rig       %ld\n", (long)dropped);
    printf("unanswered           %ld\n", (long)g_inflight.load(std::memory_order_relaxed));
    printf("latency us   p50      %ld\n", (long)hdr_value_at_percentile(g_measured, 50.0));
    printf("             p90      %ld\n", (long)hdr_value_at_percentile(g_measured, 90.0));
    printf("             p99      %ld\n", (long)hdr_value_at_percentile(g_measured, 99.0));
    printf("             p99.9    %ld\n", (long)hdr_value_at_percentile(g_measured, 99.9));
    printf("             p99.99   %ld\n", (long)hdr_value_at_percentile(g_measured, 99.99));
    printf("             max      %ld\n", (long)hdr_max(g_measured));
    printf("             mean     %ld\n", (long)hdr_mean(g_measured));

    if (is_open_mode() && g_lag->total_count > 0) {
        printf("schedule lag us       p50 %ld  p99 %ld  max %ld"
               "   (how late sends were; large values mean the rig, not the cluster)\n",
               (long)hdr_value_at_percentile(g_lag, 50.0),
               (long)hdr_value_at_percentile(g_lag, 99.0),
               (long)hdr_max(g_lag));
    }

    if (is_open_mode() && dropped > 0) {
        printf("\nWARNING: %ld requests were never sent, so an offered rate of %ld req/s was not\n"
               "actually achieved. This run cannot be reported as such.\n",
               (long)dropped, (long)FLAGS_rate);
    }

    if (!is_open_mode() && count > 0) {
        // Little's law: outstanding should equal throughput x latency. A large
        // deviation means the rig is not measuring what it thinks it is.
        const double implied = achieved * (hdr_mean(g_measured) / 1e6);
        const double ratio = implied / (double)FLAGS_thread_num;
        printf("little's law ratio   %.2f%s\n", ratio,
               (ratio < 0.9 || ratio > 1.1) ? "   WARNING: >10% off, suspect a rig bug" : "");
    }

    if (!FLAGS_hdr_raw_out.empty()) {
        FILE* f = fopen(FLAGS_hdr_raw_out.c_str(), "w");
        if (f != NULL) {
            fprintf(f, "value,count\n");
            struct hdr_iter iter;
            hdr_iter_recorded_init(&iter, g_measured);
            while (hdr_iter_next(&iter)) {
                if (iter.count > 0) {
                    fprintf(f, "%lld,%lld\n", (long long)iter.value, (long long)iter.count);
                }
            }
            fclose(f);
            printf("hdr raw              %s\n", FLAGS_hdr_raw_out.c_str());
        } else {
            fprintf(stderr, "failed to write %s\n", FLAGS_hdr_raw_out.c_str());
        }
    }

    if (!FLAGS_hdr_out.empty()) {
        FILE* f = fopen(FLAGS_hdr_out.c_str(), "w");
        if (f != NULL) {
            hdr_percentiles_print(g_measured, f, 5, 1.0, CLASSIC);
            fclose(f);
            printf("hdr report           %s\n", FLAGS_hdr_out.c_str());
        } else {
            fprintf(stderr, "failed to write %s\n", FLAGS_hdr_out.c_str());
        }
    }
}

int main(int argc, char* argv[]) {
    gflags::ParseCommandLineFlags(&argc, &argv, true);
    butil::AtExitManager exit_manager;

    if (FLAGS_mode != "open" && FLAGS_mode != "closed") {
        LOG(ERROR) << "--mode must be open or closed";
        return -1;
    }
    if (is_open_mode()) {
        if (FLAGS_rate < 1) {
            LOG(ERROR) << "--rate is required and must be >= 1 in open mode";
            return -1;
        }
        if (FLAGS_burst < 1) {
            LOG(ERROR) << "--burst must be >= 1";
            return -1;
        }
        if (FLAGS_max_inflight < 1) {
            // Roughly ten times the steady-state in-flight count Little's law
            // implies at this rate for a 10 ms p99, floored so low rates still
            // have room. Trips only on real pathology, not ordinary jitter.
            FLAGS_max_inflight = std::max((int64_t)1000, FLAGS_rate / 10);
        }
    }

    if (hdr_init(1, kHighestTrackableUs, 3, &g_measured) != 0 ||
        hdr_init(1, kHighestTrackableUs, 3, &g_lag) != 0) {
        LOG(ERROR) << "Fail to allocate histograms";
        return -1;
    }

    if (braft::rtb::update_configuration(FLAGS_group, FLAGS_conf) != 0) {
        LOG(ERROR) << "Fail to register configuration " << FLAGS_conf
                   << " of group " << FLAGS_group;
        return -1;
    }

    const int64_t start_us = now_us();
    const int64_t warmup_end_us = start_us + (int64_t)FLAGS_warmup * 1000000L;
    const int64_t end_us = warmup_end_us + (int64_t)FLAGS_measure * 1000000L;

    std::vector<bthread_t> tids;
    std::vector<pthread_t> pids;
    std::vector<SendArg> args;

    if (!is_open_mode()) {
        for (int i = 1; i <= FLAGS_thread_num; ++i) {
            SendArg arg = { i, end_us };
            args.push_back(arg);
        }
        if (!FLAGS_use_bthread) {
            pids.resize(FLAGS_thread_num);
            for (int i = 0; i < FLAGS_thread_num; ++i) {
                if (pthread_create(&pids[i], NULL, sender, &args[i]) != 0) {
                    LOG(ERROR) << "Fail to create pthread";
                    return -1;
                }
            }
        } else {
            tids.resize(FLAGS_thread_num);
            for (int i = 0; i < FLAGS_thread_num; ++i) {
                if (bthread_start_background(&tids[i], NULL, sender, &args[i]) != 0) {
                    LOG(ERROR) << "Fail to create bthread";
                    return -1;
                }
            }
        }
    }

    // Reporter: one line a second, and it flips the measuring flag once warmup
    // is over. Unlike the histograms, the live line is deliberately taken from
    // bvar so it keeps working during warmup too.
    bthread_t reporter;
    struct ReporterArg { int64_t warmup_end_us; };
    static ReporterArg rarg;
    rarg.warmup_end_us = warmup_end_us;
    bthread_start_background(&reporter, NULL, [](void* a) -> void* {
        ReporterArg* ra = (ReporterArg*)a;
        while (!brpc::IsAskedToQuit()) {
            sleep(1);
            const bool warm = now_us() >= ra->warmup_end_us;
            if (warm && !g_measuring.exchange(true, std::memory_order_relaxed)) {
                // Open the window at the same instant recording starts, so the
                // sample count and the window length describe the same span.
                g_measure_start_us = now_us();
            }
            LOG_IF(INFO, !FLAGS_log_each_request)
                    << "Sending Request to " << FLAGS_group
                    << " (" << FLAGS_conf << ')'
                    << " at qps=" << g_latency_recorder.qps(1)
                    << " latency=" << g_latency_recorder.latency(1)
                    << (warm ? "" : " [warmup]");
        }
        return NULL;
    }, &rarg);

    if (is_open_mode()) {
        run_open_loop(start_us, end_us);
    } else {
        for (int i = 0; i < FLAGS_thread_num; ++i) {
            if (!FLAGS_use_bthread) {
                pthread_join(pids[i], NULL);
            } else {
                bthread_join(tids[i], NULL);
            }
        }
    }

    g_measure_end_us = now_us();
    print_summary();
    return 0;
}
