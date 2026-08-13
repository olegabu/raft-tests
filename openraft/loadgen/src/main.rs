//! Load generator for the openraft raft-kv-memstore example.
//!
//! Supports both load models:
//!
//! - closed loop: hold --thread_num requests outstanding, so offered load adapts
//!   to how fast the cluster answers. Measures the service latency one
//!   well-behaved client sees, and by construction cannot observe saturation --
//!   past the knee it simply stops offering more load.
//! - open loop: emit on a fixed schedule at --rate regardless of whether replies
//!   have arrived, because real arrivals do not slow down when the system does.
//!   Latency is measured from each request's *scheduled* send time, so time spent
//!   waiting to send is charged to the system, which is what makes this immune to
//!   coordinated omission.
//!
//! No server-side echo or timestamp plumbing is needed: HTTP binds each reply to
//! its request, so the scheduled (or actual) send time is simply held here in the
//! task that issued it.

use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use clap::Parser;
use hdrhistogram::Histogram;
use serde_json::Value;
use tokio::sync::mpsc::{unbounded_channel, UnboundedReceiver, UnboundedSender};
use tokio::sync::{oneshot, Semaphore};

/// One minute, in nanoseconds: the histogram ceiling.
const HIGHEST_TRACKABLE_NANOS: u64 = 60_000_000_000;

#[derive(Parser, Clone, Debug)]
#[command(author, version, about = "Load generator for openraft's raft-kv-memstore example", rename_all = "snake_case")]
struct Args {
    /// Comma-separated api-addr list, e.g. 127.0.0.1:21001,127.0.0.1:21002,127.0.0.1:21003
    #[arg(long)]
    peers: String,

    /// closed: keep --thread_num requests outstanding. open: emit at --rate.
    #[arg(long, default_value = "closed")]
    mode: String,

    /// Number of concurrent sending tasks (closed mode).
    #[arg(long, default_value_t = 100)]
    thread_num: usize,

    /// Target requests per second (open mode).
    #[arg(long, default_value_t = 0)]
    rate: u64,

    /// Requests emitted back-to-back per scheduled instant (open mode). All of
    /// them share the burst's scheduled time, so the aggregate rate is unchanged
    /// while arrivals are clustered.
    #[arg(long, default_value_t = 1)]
    burst: u64,

    /// Cap on unanswered requests (open mode). Bounds rig memory if the cluster
    /// stalls; hitting it counts as dropped-by-rig, never as a silent skip.
    #[arg(long, default_value_t = 0)]
    max_inflight: usize,

    /// Tokio runtime worker thread count (real OS threads).
    #[arg(long, default_value_t = 8)]
    worker_threads: usize,

    /// Seconds of traffic discarded before measurement begins.
    #[arg(long, default_value_t = 10)]
    warmup: u64,

    /// Seconds recorded.
    #[arg(long, default_value_t = 30)]
    measure: u64,

    /// Seconds to wait for in-flight replies after the window closes.
    #[arg(long, default_value_t = 10)]
    drain_timeout: u64,

    /// Print each request/response.
    #[arg(long, default_value_t = false)]
    log_each_request: bool,

    /// Percentage of ops doing a full read+CompareAndSet increment (two round
    /// trips) instead of an unconditional Set.
    #[arg(long, default_value_t = 0)]
    cas_percentage: u32,

    /// Size in bytes of the value written on each op.
    #[arg(long, default_value_t = 64)]
    value_size: usize,

    /// The single key every sender contends on.
    #[arg(long, default_value = "bench")]
    key: String,

    /// Backoff after a network error.
    #[arg(long, default_value_t = 100)]
    timeout_ms: u64,

    /// How the open-mode scheduler waits for the next scheduled instant. `spin`
    /// holds a core for precision; `park` yields it, for a shared machine.
    #[arg(long, default_value = "spin")]
    pace: String,

    /// Write an HdrHistogram percentile report here.
    #[arg(long)]
    hdr_out: Option<String>,
}

impl Args {
    fn is_open(&self) -> bool {
        self.mode == "open"
    }
}

/// One completed request. `lag_ns` is how late the send actually was against its
/// scheduled instant (open mode only) -- without it there is no way to tell a rig
/// that cannot keep the schedule from a cluster that is genuinely slow, since both
/// inflate latency measured from scheduled time.
struct Sample {
    latency_ns: u64,
    lag_ns: u64,
}

struct Stats {
    measured: Histogram<u64>,
    lag: Histogram<u64>,
    measure_start: Option<Instant>,
    measure_end: Option<Instant>,
}

/// Tracks which peer we currently believe is leader. `set_leader` is used when a
/// node tells us exactly who the leader is (ForwardToLeader); `rotate` is used
/// when the tracked leader is simply unreachable and we have no better
/// information -- cycle to the next known peer.
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
    let mut args = Args::parse();

    if args.mode != "open" && args.mode != "closed" {
        panic!("--mode must be open or closed");
    }
    if args.is_open() {
        if args.rate < 1 {
            panic!("--rate is required and must be >= 1 in open mode");
        }
        if args.burst < 1 {
            panic!("--burst must be >= 1");
        }
        if args.max_inflight < 1 {
            // Roughly ten times the steady-state in-flight count Little's law
            // implies at this rate for a 10 ms p99, floored so low rates still
            // have room. Trips only on real pathology, not ordinary jitter.
            args.max_inflight = std::cmp::max(1000, (args.rate / 10) as usize);
        }
    } else if args.thread_num < 1 {
        panic!("--thread_num must be >= 1");
    }

    let peers: Vec<String> = args.peers.split(',').map(|s| s.trim().to_string()).collect();
    assert!(!peers.is_empty(), "--peers must not be empty");

    let rt = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(args.worker_threads)
        .enable_all()
        .build()
        .expect("failed to build tokio runtime");

    let client = reqwest::Client::builder().no_proxy().build().expect("failed to build http client");
    let cluster = Arc::new(Cluster::new(peers));
    let value_payload = "x".repeat(args.value_size);
    let dropped = Arc::new(AtomicU64::new(0));
    // Requests that were sent but failed. Without this they vanish from the
    // summary entirely -- neither completed nor dropped -- which makes a run
    // that mostly errored look merely slow.
    let errored = Arc::new(AtomicU64::new(0));

    let stats = Arc::new(Mutex::new(Stats {
        measured: Histogram::new_with_bounds(1, HIGHEST_TRACKABLE_NANOS, 3).unwrap(),
        lag: Histogram::new_with_bounds(1, HIGHEST_TRACKABLE_NANOS, 3).unwrap(),
        measure_start: None,
        measure_end: None,
    }));

    let start = Instant::now();
    let warmup_end = start + Duration::from_secs(args.warmup);
    let end = warmup_end + Duration::from_secs(args.measure);

    let (tx, rx) = unbounded_channel::<Sample>();
    let (done_tx, done_rx) = oneshot::channel::<()>();

    rt.spawn(collector(rx, stats.clone(), warmup_end, args.peers.clone(), done_tx));

    if args.is_open() {
        run_open(
            &rt,
            client,
            cluster,
            Arc::new(args.clone()),
            Arc::new(value_payload),
            tx,
            dropped.clone(),
            errored.clone(),
            start,
            end,
        );
    } else {
        rt.block_on(run_closed(client, cluster, args.clone(), value_payload, tx, errored.clone(), end));
    }

    // All senders are gone now, so the collector sees the channel close, folds in
    // the final partial interval and closes the window at the same instant -- the
    // sample count and the window length then describe the same span.
    let _ = rt.block_on(done_rx);

    print_summary(
        &args,
        &stats.lock().unwrap(),
        dropped.load(Ordering::Relaxed),
        errored.load(Ordering::Relaxed),
    );
}

/// Sole owner of the histograms. The one-second tick that straddles the end of
/// warmup holds a mix of warm and cold samples, so it is discarded rather than
/// attributed to either phase, and the measurement window opens at that tick.
async fn collector(
    mut rx: UnboundedReceiver<Sample>,
    stats: Arc<Mutex<Stats>>,
    warmup_end: Instant,
    endpoints: String,
    done: oneshot::Sender<()>,
) {
    let mut interval = Histogram::<u64>::new_with_bounds(1, HIGHEST_TRACKABLE_NANOS, 3).unwrap();
    let mut interval_lag = Histogram::<u64>::new_with_bounds(1, HIGHEST_TRACKABLE_NANOS, 3).unwrap();
    let mut ticker = tokio::time::interval(Duration::from_secs(1));
    ticker.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
    let mut measuring = false;

    loop {
        tokio::select! {
            maybe = rx.recv() => {
                match maybe {
                    Some(s) => {
                        let _ = interval.record(s.latency_ns.clamp(1, HIGHEST_TRACKABLE_NANOS));
                        if s.lag_ns > 0 {
                            let _ = interval_lag.record(s.lag_ns.clamp(1, HIGHEST_TRACKABLE_NANOS));
                        }
                    }
                    None => break,
                }
            }
            _ = ticker.tick() => {
                let now = Instant::now();
                if measuring {
                    let mut st = stats.lock().unwrap();
                    let _ = st.measured.add(&interval);
                    let _ = st.lag.add(&interval_lag);
                } else if now >= warmup_end {
                    measuring = true;
                    stats.lock().unwrap().measure_start = Some(now);
                }

                let count = interval.len();
                let mean_us = if count > 0 { (interval.mean() / 1000.0) as u64 } else { 0 };
                println!(
                    "Sending Request to OpenRaft ({}) at qps={} latency={}{}",
                    endpoints, count, mean_us,
                    if measuring { "" } else { " [warmup]" }
                );
                interval.reset();
                interval_lag.reset();
            }
        }
    }

    let mut st = stats.lock().unwrap();
    if st.measure_start.is_some() {
        let _ = st.measured.add(&interval);
        let _ = st.lag.add(&interval_lag);
    }
    st.measure_end = Some(Instant::now());
    drop(st);
    let _ = done.send(());
}

/// Closed loop: N tasks, each sending one request at a time. Latency is measured
/// from the actual send, which is honest here -- the task genuinely was not
/// waiting before it sent.
async fn run_closed(
    client: reqwest::Client,
    cluster: Arc<Cluster>,
    args: Args,
    value_payload: String,
    tx: UnboundedSender<Sample>,
    errored: Arc<AtomicU64>,
    end: Instant,
) {
    let mut handles = Vec::with_capacity(args.thread_num);

    for _ in 0..args.thread_num {
        let client = client.clone();
        let cluster = cluster.clone();
        let args = args.clone();
        let value = value_payload.clone();
        let tx = tx.clone();
        let errored = errored.clone();

        handles.push(tokio::spawn(async move {
            let mut counter: u64 = 0;
            while Instant::now() < end {
                counter = counter.wrapping_add(1);
                let do_cas = args.cas_percentage > 0 && (counter % 100) < args.cas_percentage as u64;

                let sent = Instant::now();
                let result = if do_cas {
                    do_compare_and_set(&client, &cluster, &args, &value).await
                } else {
                    do_set(&client, &cluster, &args, &value).await
                };

                match result {
                    Ok(()) => {
                        let _ = tx.send(Sample { latency_ns: sent.elapsed().as_nanos() as u64, lag_ns: 0 });
                    }
                    Err(e) => {
                        errored.fetch_add(1, Ordering::Relaxed);
                        if args.log_each_request {
                            eprintln!("request failed: {e}");
                        }
                        tokio::time::sleep(Duration::from_millis(args.timeout_ms)).await;
                    }
                }
            }
        }));
    }

    drop(tx);
    for h in handles {
        let _ = h.await;
    }
}

/// Open loop. The schedule runs on a dedicated OS thread so it is not subject to
/// the async timer's granularity, and it never blocks: a request that cannot be
/// admitted is counted as dropped-by-rig and the schedule moves on, because
/// waiting here would quietly turn this into a closed-loop run. Requests are
/// executed on the tokio pool, so unlike a single-threaded rig the scheduler's
/// spinning does not delay reply handling.
#[allow(clippy::too_many_arguments)]
fn run_open(
    rt: &tokio::runtime::Runtime,
    client: reqwest::Client,
    cluster: Arc<Cluster>,
    args: Arc<Args>,
    value_payload: Arc<String>,
    tx: UnboundedSender<Sample>,
    dropped: Arc<AtomicU64>,
    errored: Arc<AtomicU64>,
    start: Instant,
    end: Instant,
) {
    let handle = rt.handle().clone();
    let sem = Arc::new(Semaphore::new(args.max_inflight));
    let interval_ns = 1_000_000_000f64 / args.rate as f64;
    let running = Arc::new(AtomicBool::new(true));

    {
        let running = running.clone();
        let sem = sem.clone();
        let max_inflight = args.max_inflight;
        let drain = Duration::from_secs(args.drain_timeout);
        let tx_for_scheduler = tx.clone();
        let pace_spin = args.pace != "park";

        let scheduler = std::thread::Builder::new()
            .name("scheduler".to_string())
            .spawn(move || {
                let mut sequence: u64 = 0;

                loop {
                    let scheduled = start + Duration::from_nanos((sequence as f64 * interval_ns) as u64);
                    if scheduled > end || !running.load(Ordering::Relaxed) {
                        break;
                    }

                    let now = Instant::now();
                    if now < scheduled {
                        // Requests run on the tokio pool, so unlike a single-threaded
                        // rig this thread holding a core does not delay reply handling
                        // -- spinning here costs only the core itself.
                        if pace_spin || scheduled.saturating_duration_since(now) < Duration::from_micros(150) {
                            std::hint::spin_loop();
                        } else {
                            std::thread::sleep(Duration::from_micros(50));
                        }
                        continue;
                    }

                    for _ in 0..args.burst {
                        match sem.clone().try_acquire_owned() {
                            Ok(permit) => {
                                let lag_ns = Instant::now().saturating_duration_since(scheduled).as_nanos() as u64;
                                let client = client.clone();
                                let cluster = cluster.clone();
                                // Arc clones: deep-copying Args and the payload here
                                // put several heap allocations per request on the one
                                // thread that has to keep the schedule, which showed up
                                // as milliseconds of schedule lag above ~30k req/s.
                                let args2 = Arc::clone(&args);
                                let value = Arc::clone(&value_payload);
                                let tx = tx_for_scheduler.clone();
                                let errored = errored.clone();
                                let seq = sequence;

                                handle.spawn(async move {
                                    let do_cas = args2.cas_percentage > 0
                                        && (seq % 100) < args2.cas_percentage as u64;
                                    let result = if do_cas {
                                        do_compare_and_set(&client, &cluster, &args2, value.as_str()).await
                                    } else {
                                        do_set(&client, &cluster, &args2, value.as_str()).await
                                    };

                                    if result.is_err() {
                                        errored.fetch_add(1, Ordering::Relaxed);
                                    }
                                    if result.is_ok() {
                                        // Measured from the scheduled instant, not the
                                        // actual send: any delay getting it out is the
                                        // system's, and is charged to it.
                                        let latency_ns =
                                            Instant::now().saturating_duration_since(scheduled).as_nanos() as u64;
                                        let _ = tx.send(Sample { latency_ns, lag_ns: lag_ns.max(1) });
                                    }
                                    drop(permit);
                                });
                            }
                            Err(_) => {
                                dropped.fetch_add(1, Ordering::Relaxed);
                            }
                        }
                        sequence += 1;
                    }
                }

                // Drain: wait for in-flight requests, bounded, then release.
                let deadline = Instant::now() + drain;
                while sem.available_permits() < max_inflight && Instant::now() < deadline {
                    std::thread::sleep(Duration::from_millis(10));
                }
            })
            .expect("failed to spawn scheduler thread");

        drop(tx);
        let _ = scheduler.join();
    }
}

fn print_summary(args: &Args, stats: &Stats, dropped: u64, errored: u64) {
    let window = match (stats.measure_start, stats.measure_end) {
        (Some(s), Some(e)) => e.saturating_duration_since(s).as_secs_f64(),
        _ => 0.0,
    };
    let count = stats.measured.len();
    let achieved = if window > 0.0 { count as f64 / window } else { 0.0 };

    println!();
    println!("=== summary ===");
    println!("mode                 {}", args.mode);
    println!("endpoints            {}", args.peers);
    if args.is_open() {
        println!("offered rate         {} req/s (burst {})", args.rate, args.burst);
        println!("max inflight         {}", args.max_inflight);
    } else {
        println!("outstanding          {}", args.thread_num);
    }
    println!("request payload      {} bytes value", args.value_size);
    println!("measure window       {:.1} s", window);
    println!("completed            {count}");
    println!("achieved rate        {achieved:.0} req/s");
    println!("dropped-by-rig       {dropped}");
    println!("errored              {errored}");
    println!("latency us   p50      {}", stats.measured.value_at_quantile(0.50) / 1000);
    println!("             p90      {}", stats.measured.value_at_quantile(0.90) / 1000);
    println!("             p99      {}", stats.measured.value_at_quantile(0.99) / 1000);
    println!("             p99.9    {}", stats.measured.value_at_quantile(0.999) / 1000);
    println!("             p99.99   {}", stats.measured.value_at_quantile(0.9999) / 1000);
    println!("             max      {}", stats.measured.max() / 1000);
    println!("             mean     {}", (stats.measured.mean() / 1000.0) as u64);

    if args.is_open() && stats.lag.len() > 0 {
        println!(
            "schedule lag us       p50 {}  p99 {}  max {}   (how late sends were; large values mean the rig, not the cluster)",
            stats.lag.value_at_quantile(0.50) / 1000,
            stats.lag.value_at_quantile(0.99) / 1000,
            stats.lag.max() / 1000
        );
    }

    if args.is_open() && dropped > 0 {
        println!();
        println!(
            "WARNING: {dropped} requests were never sent, so an offered rate of {} req/s was not \
             actually achieved. This run cannot be reported as such.",
            args.rate
        );
    }

    if !args.is_open() && count > 0 {
        // Little's law: outstanding should equal throughput x latency. A large
        // deviation means the rig is not measuring what it thinks it is.
        let implied = achieved * (stats.measured.mean() / 1e9);
        let ratio = implied / args.thread_num as f64;
        print!("little's law ratio   {ratio:.2}");
        if (ratio - 1.0).abs() > 0.10 {
            print!("   WARNING: >10% off, suspect a rig bug");
        }
        println!();
    }

    if let Some(path) = &args.hdr_out {
        write_percentile_report(path, &stats.measured);
    }
}

fn write_percentile_report(path: &str, hist: &Histogram<u64>) {
    use std::io::Write;

    match std::fs::File::create(path) {
        Ok(mut f) => {
            let _ = writeln!(f, "# latency percentiles, microseconds");
            let _ = writeln!(f, "Value\tPercentile\tTotalCount");
            for v in hist.iter_quantiles(5) {
                let _ = writeln!(
                    f,
                    "{}\t{:.6}\t{}",
                    v.value_iterated_to() / 1000,
                    v.quantile_iterated_to(),
                    v.count_since_last_iteration()
                );
            }
            println!("hdr report           {path}");
        }
        Err(e) => eprintln!("failed to write {path}: {e}"),
    }
}

/// POST /write {"Set": {...}}, following ForwardToLeader redirects until it
/// lands on the current leader.
async fn do_set(client: &reqwest::Client, cluster: &Arc<Cluster>, args: &Args, value: &str) -> Result<(), String> {
    let req = types_kv::Request::set(args.key.clone(), value.to_string());
    post_with_forwarding(client, cluster, "write", &req, args.log_each_request).await
}

/// Full read+CompareAndSet increment: read current version, then write
/// current-value-as-u64 + 1 conditioned on that version. Two round trips, not
/// one -- see the --cas_percentage doc comment.
async fn do_compare_and_set(
    client: &reqwest::Client,
    cluster: &Arc<Cluster>,
    args: &Args,
    value: &str,
) -> Result<(), String> {
    // Bounded generously so a transport hiccup against the tracked leader --
    // which counts as one iteration here, same as a CAS mismatch or redirect --
    // has room to rotate through peers and recover within a single call.
    for _ in 0..(cluster.peers.len() * 4).max(10) {
        let (current_value, expected_version) =
            match read_current(client, cluster, &args.key, args.log_each_request).await {
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
        return Ok(());
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
    // The body is wrapped as {"Ok": <Response>} -- unwrap the envelope before
    // deserializing, else a missing top-level `value` field silently defaults
    // Option<VersionedValue> to None instead of erroring.
    let envelope: Value = serde_json::from_slice(&resp).map_err(|e| e.to_string())?;
    let ok = envelope.get("Ok").ok_or_else(|| format!("read: unexpected response {envelope}"))?;
    let parsed: types_kv::Response = serde_json::from_value(ok.clone()).map_err(|e| e.to_string())?;
    match parsed.value {
        Some(vv) => Ok((Some(vv.value), vv.version)),
        None => Ok((None, 0)),
    }
}

/// Retries against ForwardToLeader redirects for a bounded number of rounds, each
/// round also tolerating the tracked leader simply being down (connection error,
/// no redirect to react to).
async fn post_with_forwarding<Req: serde::Serialize>(
    client: &reqwest::Client,
    cluster: &Arc<Cluster>,
    path: &str,
    req: &Req,
    log_each_request: bool,
) -> Result<(), String> {
    let body = build_body(req);
    for _ in 0..(cluster.peers.len() * 4).max(8) {
        match send(client, cluster, path, &body, log_each_request).await {
            Ok(resp) => {
                if let Some(new_leader) = extract_forward_to_leader(&resp) {
                    cluster.set_leader(new_leader);
                    continue;
                }
                return Ok(());
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
        // Tracked leader is unreachable (killed, partitioned, still electing) --
        // we have no redirect to react to, so cycle to the next known peer
        // rather than hammering the same dead address.
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

/// A CAS version mismatch is not an Err -- it's Ok with a write response whose
/// inner `data.value` is null.
fn is_cas_mismatch(resp: &Value) -> bool {
    resp.get("Ok")
        .map(|ok| ok.get("data").and_then(|d| d.get("value")).map(|v| v.is_null()).unwrap_or(false))
        .unwrap_or(false)
}
