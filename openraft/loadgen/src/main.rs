//! Load generator for the openraft raft-kv-memstore example.
//!
//! Deliberately mirrors braft's client.cpp/run_client.sh in design and
//! configuration options (see openraft/README.md for the flag mapping and
//! why a couple of them had to be renamed or repurposed).

use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use clap::Parser;
use serde_json::Value;

#[derive(Parser, Clone, Debug)]
#[command(author, version, about = "Load generator for openraft's raft-kv-memstore example", rename_all = "snake_case")]
struct Args {
    /// Comma-separated api-addr list, e.g. 127.0.0.1:21001,127.0.0.1:21002,127.0.0.1:21003
    #[arg(long)]
    peers: String,

    /// Number of concurrent sending tasks.
    #[arg(long, default_value_t = 1)]
    thread_num: usize,

    /// Tokio runtime worker thread count (real OS threads). Analogous to
    /// braft's --bthread_concurrency: keep this >= thread_num, else tasks
    /// queue for fewer real workers than they need and measured latency
    /// includes that queueing delay instead of just the cluster's own cost.
    #[arg(long, default_value_t = 8)]
    worker_threads: usize,

    /// Print each request/response.
    #[arg(long, default_value_t = false)]
    log_each_request: bool,

    /// Percentage (0-100) of ops that do a full read+CompareAndSet increment
    /// (two RPCs) instead of the default unconditional Set (one RPC). Not
    /// RPC-count comparable to braft's numbers when non-zero -- see README.
    #[arg(long, default_value_t = 0)]
    cas_percentage: u32,

    /// Size in bytes of the value written on each Set/CompareAndSet.
    #[arg(long, default_value_t = 64)]
    value_size: usize,

    /// Shared key all tasks contend on -- matches braft-atomic's single
    /// replicated register design rather than spreading writes across a key
    /// space.
    #[arg(long, default_value = "bench")]
    key: String,

    /// Backoff before retrying after a network error.
    #[arg(long, default_value_t = 100)]
    timeout_ms: u64,
}

struct Stats {
    count: AtomicU64,
    latency_sum_us: AtomicU64,
}

/// Tracks which peer we currently believe is leader. `set_leader` is used
/// when a node tells us exactly who the leader is (ForwardToLeader);
/// `rotate` is used when the tracked leader is simply unreachable (killed,
/// network partition) and we have no better information yet -- cycle to the
/// next known peer and let the next ForwardToLeader (or success) correct us.
struct Cluster {
    peers: Vec<String>,
    leader: Mutex<String>,
}

impl Cluster {
    fn new(peers: Vec<String>) -> Self {
        let leader = Mutex::new(peers[0].clone());
        Self { peers, leader }
    }

    fn current(&self) -> String {
        self.leader.lock().unwrap().clone()
    }

    fn set_leader(&self, addr: String) {
        *self.leader.lock().unwrap() = addr;
    }

    fn rotate(&self) {
        let mut l = self.leader.lock().unwrap();
        let idx = self.peers.iter().position(|p| *p == *l).unwrap_or(0);
        *l = self.peers[(idx + 1) % self.peers.len()].clone();
    }
}

fn main() {
    let args = Args::parse();

    let rt = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(args.worker_threads)
        .enable_all()
        .build()
        .expect("failed to build tokio runtime");

    rt.block_on(run(args));
}

async fn run(args: Args) {
    let peers: Vec<String> = args.peers.split(',').map(|s| s.trim().to_string()).collect();
    assert!(!peers.is_empty(), "--peers must not be empty");

    let client = reqwest::Client::builder().no_proxy().build().expect("failed to build http client");
    let cluster = Arc::new(Cluster::new(peers));
    let running = Arc::new(AtomicBool::new(true));
    let stats = Arc::new(Stats {
        count: AtomicU64::new(0),
        latency_sum_us: AtomicU64::new(0),
    });

    let value_payload = "x".repeat(args.value_size);

    let mut workers = Vec::with_capacity(args.thread_num);
    for _ in 0..args.thread_num {
        let client = client.clone();
        let cluster = cluster.clone();
        let running = running.clone();
        let stats = stats.clone();
        let args = args.clone();
        let value_payload = value_payload.clone();
        workers.push(tokio::spawn(async move {
            worker_loop(client, cluster, running, stats, args, value_payload).await;
        }));
    }

    let printer = {
        let running = running.clone();
        let stats = stats.clone();
        let peers_display = args.peers.clone();
        tokio::spawn(async move {
            let mut interval = tokio::time::interval(Duration::from_secs(1));
            while running.load(Ordering::Relaxed) {
                interval.tick().await;
                let count = stats.count.swap(0, Ordering::Relaxed);
                let latency_sum = stats.latency_sum_us.swap(0, Ordering::Relaxed);
                let avg_latency = if count > 0 { latency_sum / count } else { 0 };
                println!(
                    "Sending Request to OpenRaft ({}) at qps={} latency={}",
                    peers_display, count, avg_latency
                );
            }
        })
    };

    tokio::signal::ctrl_c().await.expect("failed to listen for ctrl-c");
    running.store(false, Ordering::Relaxed);

    for w in workers {
        let _ = w.await;
    }
    let _ = printer.await;
}

async fn worker_loop(
    client: reqwest::Client,
    cluster: Arc<Cluster>,
    running: Arc<AtomicBool>,
    stats: Arc<Stats>,
    args: Args,
    value_payload: String,
) {
    let mut counter: u64 = 0;
    while running.load(Ordering::Relaxed) {
        counter = counter.wrapping_add(1);
        let do_cas = args.cas_percentage > 0 && (counter % 100) < args.cas_percentage as u64;

        let result = if do_cas {
            do_compare_and_set(&client, &cluster, &args, &value_payload).await
        } else {
            do_set(&client, &cluster, &args, &value_payload).await
        };

        match result {
            Ok(elapsed) => {
                stats.count.fetch_add(1, Ordering::Relaxed);
                stats.latency_sum_us.fetch_add(elapsed.as_micros() as u64, Ordering::Relaxed);
            }
            Err(e) => {
                if args.log_each_request {
                    eprintln!("request failed: {e}");
                }
                tokio::time::sleep(Duration::from_millis(args.timeout_ms)).await;
            }
        }
    }
}

/// POST /write {"Set": {...}}, following ForwardToLeader redirects until it
/// lands on the current leader. Mirrors braft::rtb::update_leader + retry.
async fn do_set(client: &reqwest::Client, cluster: &Arc<Cluster>, args: &Args, value: &str) -> Result<Duration, String> {
    let req = types_kv::Request::set(args.key.clone(), value.to_string());
    post_with_forwarding(client, cluster, "write", &req, args.log_each_request).await
}

/// Full read+CompareAndSet increment: read current version, then attempt to
/// write current-value-as-u64 + 1 conditioned on that version. Retries the
/// whole cycle on a version mismatch (Ok(None) response) or ForwardToLeader.
/// Two round trips, not one -- see the --cas_percentage doc comment.
async fn do_compare_and_set(
    client: &reqwest::Client,
    cluster: &Arc<Cluster>,
    args: &Args,
    value: &str,
) -> Result<Duration, String> {
    let started = Instant::now();
    // Bounded generously (not just 10) so a transport hiccup against the
    // tracked leader -- which counts as one iteration here, same as a CAS
    // mismatch or redirect -- has room to rotate through peers and recover
    // within a single call instead of always bubbling an Err up to the
    // caller's outer sleep-and-retry.
    for _ in 0..(cluster.peers.len() * 4).max(10) {
        let (current_value, expected_version) = match read_current(client, cluster, &args.key, args.log_each_request).await {
            Ok(v) => v,
            Err(_) => continue,
        };

        // CompareAndSet has no "create if absent" semantics -- expected_version=0
        // never matches a genuinely nonexistent key. Lazily initialize it with a
        // plain Set, then loop back to read the real version it was created with.
        let Some(current_value) = current_value else {
            let _ = do_set(client, cluster, args, "0").await;
            continue;
        };
        let next_value = match current_value.parse::<u64>() {
            Ok(n) => (n + 1).to_string(),
            Err(_) => value.to_string(),
        };

        let req = types_kv::Request::compare_and_set(args.key.clone(), expected_version, next_value);
        let body = build_body(&req);
        let resp = match send(client, cluster, "write", &body, args.log_each_request).await {
            Ok(v) => v,
            Err(_) => continue,
        };

        if let Some(new_leader) = extract_forward_to_leader(&resp) {
            cluster.set_leader(new_leader);
            continue;
        }
        // A CAS mismatch surfaces as Ok(null) data.value -- not an Err, so
        // just retry the read+CAS cycle against the now-current version.
        if is_cas_mismatch(&resp) {
            continue;
        }
        return Ok(started.elapsed());
    }
    Err("compare_and_set: exceeded retry budget".to_string())
}

async fn read_current(
    client: &reqwest::Client,
    cluster: &Arc<Cluster>,
    key: &str,
    log_each_request: bool,
) -> Result<(Option<String>, u64), String> {
    let body = serde_json::to_vec(key).map_err(|e| e.to_string())?;
    let resp = send_raw(client, cluster, "read", body, log_each_request).await?;
    // The body is wrapped as {"Ok": <Response>} (or {"Err": ...}, though /read's
    // error type is Infallible so that never actually happens) -- unwrap the
    // envelope before deserializing, else a missing top-level `value` field
    // silently defaults Option<VersionedValue> to None instead of erroring.
    let envelope: Value = serde_json::from_slice(&resp).map_err(|e| e.to_string())?;
    let ok = envelope.get("Ok").ok_or_else(|| format!("read: unexpected response {envelope}"))?;
    let parsed: types_kv::Response = serde_json::from_value(ok.clone()).map_err(|e| e.to_string())?;
    match parsed.value {
        Some(vv) => Ok((Some(vv.value), vv.version)),
        None => Ok((None, 0)),
    }
}

/// Retries against ForwardToLeader redirects (same tracked leader, told who
/// the real leader is) for up to `forwarding` rounds, each round allowed
/// `unreachable` attempts against successive peers if the tracked leader is
/// simply down (connection error, no redirect to react to).
async fn post_with_forwarding<Req: serde::Serialize>(
    client: &reqwest::Client,
    cluster: &Arc<Cluster>,
    path: &str,
    req: &Req,
    log_each_request: bool,
) -> Result<Duration, String> {
    let body = build_body(req);
    let started = Instant::now();
    for _ in 0..(cluster.peers.len() * 4).max(8) {
        match send(client, cluster, path, &body, log_each_request).await {
            Ok(resp) => {
                if let Some(new_leader) = extract_forward_to_leader(&resp) {
                    cluster.set_leader(new_leader);
                    continue;
                }
                return Ok(started.elapsed());
            }
            // Transport-level failure (leader unreachable): send_raw already
            // rotated to the next peer, just retry.
            Err(_) => continue,
        }
    }
    Err(format!("{path}: exceeded forwarding retry budget"))
}

fn build_body<Req: serde::Serialize>(req: &Req) -> Vec<u8> {
    serde_json::to_vec(req).expect("failed to serialize request")
}

async fn send(
    client: &reqwest::Client,
    cluster: &Arc<Cluster>,
    path: &str,
    body: &[u8],
    log_each_request: bool,
) -> Result<Value, String> {
    let raw = send_raw(client, cluster, path, body.to_vec(), log_each_request).await?;
    serde_json::from_slice(&raw).map_err(|e| e.to_string())
}

async fn send_raw(
    client: &reqwest::Client,
    cluster: &Arc<Cluster>,
    path: &str,
    body: Vec<u8>,
    log_each_request: bool,
) -> Result<Vec<u8>, String> {
    let addr = cluster.current();
    let url = format!("http://{addr}/{path}");
    if log_each_request {
        println!(">>> POST {url}: {}", String::from_utf8_lossy(&body));
    }
    let resp = client.post(&url).header("Content-Type", "application/json").body(body).send().await.map_err(|e| {
        // Tracked leader is unreachable (killed, partitioned, still
        // electing) -- we have no redirect to react to, so just cycle to
        // the next known peer rather than hammering the same dead address.
        cluster.rotate();
        format!("{url}: {e}")
    })?;
    let bytes = resp.bytes().await.map_err(|e| e.to_string())?.to_vec();
    if log_each_request {
        println!("<<< {}", String::from_utf8_lossy(&bytes));
    }
    Ok(bytes)
}

/// Looks for {"Err":{"ForwardToLeader":{"leader_node":{"data": "<api_addr>", ...}}}}
/// and returns the new leader's api_addr if present.
fn extract_forward_to_leader(resp: &Value) -> Option<String> {
    resp.get("Err")?
        .get("ForwardToLeader")?
        .get("leader_node")?
        .get("data")?
        .as_str()
        .map(|s| s.to_string())
}

/// A CAS version mismatch is not an Err -- it's Ok with a write response
/// whose inner `data.value` is null.
fn is_cas_mismatch(resp: &Value) -> bool {
    resp.get("Ok").map(|ok| ok.get("data").and_then(|d| d.get("value")).map(|v| v.is_null()).unwrap_or(false)).unwrap_or(false)
}
