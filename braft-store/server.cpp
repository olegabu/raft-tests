#include <fstream>
#include <bthread/bthread.h>
#include <gflags/gflags.h>
#include <butil/containers/flat_map.h>
#include <butil/logging.h>
#include <bthread/bthread.h>
#include <brpc/controller.h>
#include <brpc/server.h>
#include <braft/raft.h>
#include <braft/util.h>
#include <braft/storage.h>

DEFINE_bool(allow_absent_key, false, "Cas succeeds if the key is absent while "
                                     " exptected value is exactly 0");
DEFINE_bool(check_term, true, "Check if the leader changed to another term");
DEFINE_bool(disable_cli, false, "Don't allow raft_cli access this node");
DEFINE_bool(log_applied_task, false, "Print notice log when a task is applied");
DEFINE_int32(election_timeout_ms, 5000, 
            "Start election in such milliseconds if disconnect with the leader");
DEFINE_int32(map_capacity, 1024, "Initial capicity of value map");
DEFINE_int32(port, 8100, "Listen port of this peer");
DEFINE_int32(snapshot_interval, 30, "Interval between each snapshot");
DEFINE_string(conf, "", "Initial configuration of the replication group");
DEFINE_string(data_path, "./data", "Path of data stored on");
DEFINE_string(group, "StoreStateMachine", "Id of the replication group");

namespace example {

class StoreStateMachine;
// Implements Closure which encloses RPC stuff
class DoneClosure : public braft::Closure {
public:
    DoneClosure() = default;

    void Run() override;
};

// Implementation of example::StoreStateMachine as a braft::StateMachine.
class StoreStateMachine : public braft::StateMachine {
public:
    StoreStateMachine()
        : _node(nullptr)
        , _leader_term(-1)
    {
    }

    ~StoreStateMachine() override {
        delete _node;
    }

    // Starts this node
    int start() {
        butil::EndPoint addr(butil::my_ip(), FLAGS_port);
        braft::NodeOptions node_options;
        if (node_options.initial_conf.parse_from(FLAGS_conf) != 0) {
            LOG(ERROR) << "Fail to parse configuration `" << FLAGS_conf << '\'';
            return -1;
        }
        node_options.election_timeout_ms = FLAGS_election_timeout_ms;
        node_options.fsm = this;
        node_options.node_owns_fsm = false;
        node_options.snapshot_interval_s = FLAGS_snapshot_interval;
        std::string prefix = "local://" + FLAGS_data_path;
        node_options.log_uri = prefix + "/log";
        node_options.raft_meta_uri = prefix + "/raft_meta";
        node_options.snapshot_uri = prefix + "/snapshot";
        node_options.disable_cli = FLAGS_disable_cli;
        auto* node = new braft::Node(FLAGS_group, braft::PeerId(addr));
        if (node->init(node_options) != 0) {
            LOG(ERROR) << "Fail to init raft node";
            delete node;
            return -1;
        }
        _node = node;
        return 0;
    }

    // Shuts this node down
    void shutdown() {
        if (_node) {
            _node->shutdown(nullptr);
        }
    }

    // Blocking this thread until the node is eventually down.
    void join() {
        if (_node) {
            _node->join();
        }
    }

    void apply() {
        // Serialize request to the replicated write-ahead-log so that all the
        // peers in the group receive this request as well.
        // Notice that _value can't be modified in this routine otherwise it
        // will be inconsistent with others in this group.

        // Serialize request to IOBuf
        const int64_t term = _leader_term.load(butil::memory_order_relaxed);
        if (term < 0) {
            LOG(INFO) << "Not leader";
            return;
        }
        butil::IOBuf log;
        log.append(std::string("0123456789012345678901234567890123456789012345678901234567890123")); // 64 bytes

        // Apply this log as a braft::Task
        braft::Task task;
        task.data = &log;
        // This callback would be invoked when the task actually executed or failed
        task.done = new DoneClosure();
        if (FLAGS_check_term) {
            // ABA problem can be avoided if expected_term is set
            task.expected_term = term;
        }
        // Now the task is applied to the group, waiting for the result.
        return _node->apply(task);
    }

private:
friend class DoneClosure;

    // @braft::StateMachine
    void on_apply(braft::Iterator& iter) override {
        // A batch of tasks is committed, which must be processed through
        // |iter|
        for (; iter.valid(); iter.next()) {
            // This guard helps invoke iter.done()->Run() asynchronously to
            // avoid that callback blocks the StateMachine.
            braft::AsyncClosureGuard done_guard(iter.done());

            // Parse data
            const butil::IOBuf& data = iter.data();
            auto dataSize = data.size();

            DoneClosure* c = nullptr;
            if (iter.done()) {
                c = dynamic_cast<DoneClosure*>(iter.done());
            }

            // The purpose of following logs is to help you understand the way
            // this StateMachine works.
            // Remove these logs in performance-sensitive servers.
            LOG_IF(INFO, FLAGS_log_applied_task) 
                    << "Handled operation "
                    << " at log_index=" << iter.index()
                    << " dataSize=" << dataSize;
        }
    }

    void on_snapshot_save(braft::SnapshotWriter* writer, braft::Closure* done) override {
        LOG(INFO) << "Not implemented";
    }

    int on_snapshot_load(braft::SnapshotReader* reader) override {
        LOG(INFO) << "Not implemented";
        return 0;
    }

    void on_leader_start(int64_t term) override {
        _leader_term.store(term, butil::memory_order_release);
        LOG(INFO) << "Node becomes leader";
    }

    void on_leader_stop(const butil::Status& status) override {
        _leader_term.store(-1, butil::memory_order_release);
        LOG(INFO) << "Node stepped down : " << status;
    }

    void on_shutdown() override {
        LOG(INFO) << "This node is down";
    }
    void on_error(const ::braft::Error& e) override {
        LOG(ERROR) << "Met raft error " << e;
    }
    void on_configuration_committed(const ::braft::Configuration& conf) override {
        LOG(INFO) << "Configuration of this group is " << conf;
    }
    void on_stop_following(const ::braft::LeaderChangeContext& ctx) override {
        LOG(INFO) << "Node stops following " << ctx;
    }
    void on_start_following(const ::braft::LeaderChangeContext& ctx) override {
        LOG(INFO) << "Node start following " << ctx;
    }

    // end of @braft::StateMachine

    braft::Node* volatile _node;
    butil::atomic<int64_t> _leader_term;
};

void DoneClosure::Run() {
    std::unique_ptr<DoneClosure> self_guard(this);
    if (status().ok()) {
        return;
    }
}

}  // namespace example

int main(int argc, char* argv[]) {
    gflags::ParseCommandLineFlags(&argc, &argv, true);
    butil::AtExitManager exit_manager;

    // Generally you only need one Server.
    brpc::Server server;
    example::StoreStateMachine storeStateMachine;

    // raft can share the same RPC server. Notice the second parameter, because
    // adding services into a running server is not allowed and the listen
    // address of this server is impossible to get before the server starts. You
    // have to specify the address of the server.
    if (braft::add_service(&server, FLAGS_port) != 0) {
        LOG(ERROR) << "Fail to add raft service";
        return -1;
    }

    // It's recommended to start the server before StoreStateMachine is started to avoid
    // the case that it becomes the leader while the service is unreachable by
    // clients.
    // Notice the default options of server is used here. Check out details from
    // the doc of brpc if you would like change some options;
    if (server.Start(FLAGS_port, NULL) != 0) {
        LOG(ERROR) << "Fail to start Server";
        return -1;
    }

    // It's ok to start StoreStateMachine;
    if (storeStateMachine.start() != 0) {
        LOG(ERROR) << "Fail to start StoreStateMachine";
        return -1;
    }

    LOG(INFO) << "StoreStateMachine service is running on " << server.listen_address();
    // Wait until 'CTRL-C' is pressed. then Stop() and Join() the service
    while (!brpc::IsAskedToQuit()) {
        storeStateMachine.apply();
        sleep(1);
    }

    LOG(INFO) << "StoreStateMachine service is going to quit";

    // Stop counter before server
    storeStateMachine.shutdown();
    server.Stop(0);

    // Wait until all the processing tasks are over.
    storeStateMachine.join();
    server.Join();
    return 0;
}
