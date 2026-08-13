package io.raftbench.aeron;

import io.aeron.cluster.client.AeronCluster;
import io.aeron.cluster.client.EgressListener;
import io.aeron.cluster.codecs.EventCode;
import io.aeron.driver.MediaDriver;
import io.aeron.driver.ThreadingMode;
import io.aeron.logbuffer.Header;
import org.HdrHistogram.Histogram;
import org.HdrHistogram.HistogramLogWriter;
import org.HdrHistogram.SingleWriterRecorder;
import org.agrona.DirectBuffer;
import org.agrona.concurrent.ShutdownSignalBarrier;
import org.agrona.concurrent.UnsafeBuffer;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.locks.LockSupport;

/**
 * Load generator for an Aeron Cluster running the echo service.
 *
 * Supports both load models:
 *
 *  - closed loop: hold --thread_num requests outstanding, so offered load
 *    adapts to how fast the cluster answers. Measures the service latency one
 *    well-behaved client sees. Cannot observe saturation: past the knee it
 *    simply stops offering more load.
 *  - open loop: emit on a fixed schedule at --rate regardless of whether
 *    replies have arrived, because real arrivals do not slow down when the
 *    system does. Latency is measured from each message's *scheduled* send
 *    time, so time spent waiting to send is charged to the system, which is
 *    what makes this immune to coordinated omission.
 *
 * Both modes carry the send (or scheduled) timestamp in the payload, which the
 * echo service returns verbatim. That is required here rather than a design
 * choice: egress arrives as a stream on the session with no framework-level
 * link back to a particular ingress message, so the correlation and the
 * timestamp have to travel in the message itself.
 */
public final class Loadgen implements EgressListener
{
    private static final int CORRELATION_OFFSET = 0;
    private static final int TIMESTAMP_OFFSET = 8;
    private static final int MIN_VALUE_SIZE = 16;

    /** Client-facing port offset within each node's 100-port block; see ClusterConfig. */
    private static final int CLIENT_FACING_PORT_OFFSET = 2;
    private static final int PORTS_PER_NODE = 100;


    private static final long HIGHEST_TRACKABLE_NANOS = TimeUnit.MINUTES.toNanos(1);

    private final Config config;

    /** Touched only by the sender/poller thread (onMessage runs from its pollEgress call). */
    private int inFlight;
    private long nextCorrelationId;
    private long expectedCorrelationId;
    private long mismatches;

    /** Single writer (sender thread); the reporter thread is the only reader. */
    private final SingleWriterRecorder recorder =
        new SingleWriterRecorder(1, HIGHEST_TRACKABLE_NANOS, 3);
    private final Histogram measured = new Histogram(1, HIGHEST_TRACKABLE_NANOS, 3);
    /**
     * How late each send actually was against its scheduled instant. Without this
     * there is no way to tell a rig that cannot keep the schedule from a cluster
     * that is genuinely slow -- both inflate latency measured from scheduled time.
     */
    private final Histogram scheduleLag = new Histogram(1, HIGHEST_TRACKABLE_NANOS, 3);
    private final AtomicLong completed = new AtomicLong();
    private final AtomicLong droppedByRig = new AtomicLong();
    private final AtomicBoolean running = new AtomicBoolean(true);

    /**
     * The reporter reads the recorder on a one-second tick, so the interval that
     * straddles the end of warmup contains a mix of warm and cold samples. It is
     * discarded rather than attributed to either phase, and the measurement window
     * starts at that tick -- otherwise the window and the samples in it describe
     * different spans of time and Little's law comes out wrong for no real reason.
     */
    private volatile long warmupEndNanos;
    private volatile long measureStartNanos;
    private volatile long measureEndNanos;
    private final Thread[] reporterHolder = new Thread[1];

    private Loadgen(final Config config)
    {
        this.config = config;
    }

    private static final class Config
    {
        String hostnames;
        int portBase;
        String egressHost;
        String mode;
        int threadNum;
        long rate;
        int burst;
        int maxInflight;
        int valueSize;
        long warmupSeconds;
        long measureSeconds;
        long drainTimeoutSeconds;
        boolean paceSpin;
        boolean logEachRequest;
        String hdrOut;
        String endpoints;

        boolean isOpen()
        {
            return "open".equals(mode);
        }
    }

    public static void main(final String[] args)
    {
        final Config c = new Config();
        c.hostnames = required(args, "--hostnames");
        c.portBase = Integer.parseInt(arg(args, "--port_base", "9000"));
        c.egressHost = arg(args, "--egress_host", "localhost");
        c.mode = arg(args, "--mode", "closed");
        c.threadNum = Integer.parseInt(arg(args, "--thread_num", "100"));
        c.rate = Long.parseLong(arg(args, "--rate", "0"));
        c.burst = Integer.parseInt(arg(args, "--burst", "1"));
        c.maxInflight = Integer.parseInt(arg(args, "--max_inflight", "0"));
        c.valueSize = Integer.parseInt(arg(args, "--value_size", "64"));
        c.warmupSeconds = Long.parseLong(arg(args, "--warmup", "10"));
        c.measureSeconds = Long.parseLong(arg(args, "--measure", "30"));
        c.drainTimeoutSeconds = Long.parseLong(arg(args, "--drain_timeout", "10"));
        c.paceSpin = !"park".equals(arg(args, "--pace", "spin"));
        c.logEachRequest = flag(args, "--log_each_request");
        c.hdrOut = arg(args, "--hdr_out", null);

        if (!"open".equals(c.mode) && !"closed".equals(c.mode))
        {
            throw new IllegalArgumentException("--mode must be open or closed");
        }
        if (c.valueSize < MIN_VALUE_SIZE)
        {
            throw new IllegalArgumentException(
                "--value_size must be at least " + MIN_VALUE_SIZE + " (correlation id + timestamp)");
        }
        if (c.isOpen())
        {
            if (c.rate < 1)
            {
                throw new IllegalArgumentException("--rate is required and must be >= 1 in open mode");
            }
            if (c.burst < 1)
            {
                throw new IllegalArgumentException("--burst must be >= 1");
            }
            if (c.maxInflight < 1)
            {
                // Bounds rig memory without tripping on ordinary jitter: roughly ten times
                // the steady-state in-flight count Little's law implies at this rate for a
                // 10 ms p99, floored so low rates still have room.
                c.maxInflight = (int)Math.max(1000, c.rate / 10);
            }
        }
        else if (c.threadNum < 1)
        {
            throw new IllegalArgumentException("--thread_num must be >= 1");
        }

        c.endpoints = ingressEndpoints(c.hostnames.split(","), c.portBase);

        final Loadgen loadgen = new Loadgen(c);

        try (MediaDriver mediaDriver = MediaDriver.launchEmbedded(new MediaDriver.Context()
                .threadingMode(ThreadingMode.SHARED)
                .dirDeleteOnStart(true)
                .dirDeleteOnShutdown(true));
            AeronCluster cluster = AeronCluster.connect(new AeronCluster.Context()
                .egressListener(loadgen)
                // Must be this host's own reachable address, not localhost: the
                // cluster sends egress back to whatever is named here, so a
                // loopback address silently never gets replies from remote nodes.
                .egressChannel("aeron:udp?endpoint=" + c.egressHost + ":0")
                .aeronDirectoryName(mediaDriver.aeronDirectoryName())
                .ingressChannel("aeron:udp")
                .ingressEndpoints(c.endpoints)))
        {
            System.out.println("connected to " + c.endpoints +
                " as session " + cluster.clusterSessionId() +
                ", leader member " + cluster.leaderMemberId());
            loadgen.run(cluster);
        }
    }

    private void run(final AeronCluster cluster)
    {
        final UnsafeBuffer buffer = new UnsafeBuffer(ByteBuffer.allocateDirect(config.valueSize));

        final long startNanos = System.nanoTime();
        warmupEndNanos = startNanos + TimeUnit.SECONDS.toNanos(config.warmupSeconds);
        final long endNanos = warmupEndNanos + TimeUnit.SECONDS.toNanos(config.measureSeconds);

        startReporter();
        startShutdownWatcher();

        if (config.isOpen())
        {
            runOpenLoop(cluster, buffer, startNanos, endNanos);
        }
        else
        {
            runClosedLoop(cluster, buffer, endNanos);
        }

        // Drain: replies still arriving belong to requests already scheduled inside
        // the window, so record them. Whatever never comes back is reported as
        // unanswered rather than quietly left out of the histogram.
        final long drainDeadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(config.drainTimeoutSeconds);
        while (inFlight > 0 && System.nanoTime() < drainDeadline)
        {
            cluster.pollEgress();
            Thread.onSpinWait();
        }

        // Stop the reporter and join it so this thread becomes the recorder's only
        // reader, then take one final interval. That last partial second belongs in
        // the measurement, and closing the window at the same instant keeps the
        // sample count and the window length describing the same span.
        running.set(false);
        final Thread reporter = reporterHolder[0];
        if (null != reporter)
        {
            try
            {
                reporter.interrupt();
                reporter.join(2000);
            }
            catch (final InterruptedException ignore)
            {
                Thread.currentThread().interrupt();
            }
        }

        if (measureStartNanos > 0)
        {
            measured.add(recorder.getIntervalHistogram());
        }
        measureEndNanos = System.nanoTime();

        printSummary();
    }

    /**
     * Keeps `--thread_num` requests outstanding. Latency is measured from the actual
     * send, which is honest here: a worker genuinely was not waiting before it sent.
     */
    private void runClosedLoop(final AeronCluster cluster, final UnsafeBuffer buffer, final long endNanos)
    {
        long idleCount = 0;

        while (running.get() && System.nanoTime() < endNanos)
        {
            boolean progress = false;

            while (inFlight < config.threadNum)
            {
                if (!offer(cluster, buffer, System.nanoTime()))
                {
                    break;
                }
                progress = true;
            }

            if (cluster.pollEgress() > 0)
            {
                progress = true;
            }

            if (progress)
            {
                idleCount = 0;
            }
            else
            {
                if (++idleCount % 1000 == 0)
                {
                    cluster.sendKeepAlive();
                }
                Thread.onSpinWait();
            }
        }
    }

    /**
     * Emits on a fixed schedule, never yielding to backpressure. Two rules carry the
     * whole method: the timestamp written into each message is its *scheduled* instant
     * rather than the moment it actually went out, and a message that cannot be sent
     * is counted as dropped-by-rig instead of delaying the schedule -- blocking here
     * would quietly turn this into a closed-loop run.
     */
    private void runOpenLoop(
        final AeronCluster cluster, final UnsafeBuffer buffer, final long startNanos, final long endNanos)
    {
        final double intervalNanos = 1_000_000_000.0d / config.rate;
        long sequence = 0;
        long nextScheduledNanos = startNanos;

        while (running.get())
        {
            final long now = System.nanoTime();

            if (nextScheduledNanos > endNanos)
            {
                break;
            }

            if (now >= nextScheduledNanos)
            {
                // All messages in a burst share the burst's scheduled instant.
                for (int i = 0; i < config.burst; i++)
                {
                    if (inFlight >= config.maxInflight || !offer(cluster, buffer, nextScheduledNanos))
                    {
                        droppedByRig.incrementAndGet();
                    }
                    else
                    {
                        scheduleLag.recordValue(
                            Math.max(1, Math.min(System.nanoTime() - nextScheduledNanos, HIGHEST_TRACKABLE_NANOS)));
                    }
                    sequence++;
                }
                nextScheduledNanos = startNanos + (long)(sequence * intervalNanos);
                cluster.pollEgress();
                continue;
            }

            // This thread both paces sends and is the only caller of pollEgress, so how
            // it waits decides how quickly replies are even noticed -- any wait is
            // charged to the cluster. Thread.sleep is far too coarse (measured p50 891us
            // against 153us for the same work in closed mode), but a hot spin starves
            // the client's own media driver, which is the thread that has to receive the
            // datagram, and on a CPU-constrained box that is worse still (3-12ms).
            // Hence --pace: spin when the client has a core to spare, which is the
            // benchmark configuration, and park when sharing a machine.
            cluster.pollEgress();
            if (config.paceSpin)
            {
                Thread.onSpinWait();
            }
            else if (nextScheduledNanos - System.nanoTime() > 150_000)
            {
                // ~50us granularity in practice, so replies are still seen promptly
                // while the driver thread gets scheduled.
                LockSupport.parkNanos(50_000);
            }
            else
            {
                Thread.onSpinWait();
            }
        }
    }

    /** Writes correlation id + timestamp and offers. Returns false on back pressure. */
    private boolean offer(final AeronCluster cluster, final UnsafeBuffer buffer, final long stampNanos)
    {
        buffer.putLong(CORRELATION_OFFSET, nextCorrelationId);
        buffer.putLong(TIMESTAMP_OFFSET, stampNanos);

        if (cluster.offer(buffer, 0, config.valueSize) > 0)
        {
            if (config.logEachRequest)
            {
                System.out.println(">>> id=" + nextCorrelationId);
            }
            nextCorrelationId++;
            inFlight++;
            return true;
        }

        return false;
    }

    public void onMessage(
        final long clusterSessionId,
        final long timestamp,
        final DirectBuffer buffer,
        final int offset,
        final int length,
        final Header header)
    {
        final long correlationId = buffer.getLong(offset + CORRELATION_OFFSET);
        final long stampNanos = buffer.getLong(offset + TIMESTAMP_OFFSET);
        final long latencyNanos = System.nanoTime() - stampNanos;

        inFlight--;
        completed.incrementAndGet();
        recorder.recordValue(Math.max(1, Math.min(latencyNanos, HIGHEST_TRACKABLE_NANOS)));

        // A session's messages are echoed back in order, so a gap means a reply
        // was lost or double counted. Surface it instead of averaging it in.
        if (correlationId != expectedCorrelationId)
        {
            mismatches++;
            if (config.logEachRequest)
            {
                System.err.println(
                    "correlation mismatch: expected " + expectedCorrelationId + " got " + correlationId);
            }
            expectedCorrelationId = correlationId + 1;
        }
        else
        {
            expectedCorrelationId++;
        }

        if (config.logEachRequest)
        {
            System.out.println("<<< id=" + correlationId + " latency_us=" + (latencyNanos / 1000));
        }
    }

    public void onSessionEvent(
        final long correlationId,
        final long clusterSessionId,
        final long leadershipTermId,
        final int leaderMemberId,
        final EventCode code,
        final String detail)
    {
        System.out.println("session event: code=" + code + " leader=" + leaderMemberId + " detail=" + detail);
    }

    public void onNewLeader(
        final long clusterSessionId,
        final long leadershipTermId,
        final int leaderMemberId,
        final String ingressEndpoints)
    {
        System.out.println(
            "new leader: member=" + leaderMemberId + " termId=" + leadershipTermId + " endpoints=" + ingressEndpoints);
    }

    /**
     * Sole reader of the recorder, so interval reads and the accumulated measurement
     * histogram cannot race. Intervals before warmup ends are printed but discarded.
     */
    private void startReporter()
    {
        final Thread reporter = new Thread(() ->
        {
            while (running.get())
            {
                try
                {
                    Thread.sleep(1000);
                }
                catch (final InterruptedException ignore)
                {
                    return;
                }

                final Histogram interval = recorder.getIntervalHistogram();
                final long now = System.nanoTime();
                final boolean measuring = measureStartNanos > 0;

                if (measuring)
                {
                    measured.add(interval);
                }
                else if (now >= warmupEndNanos)
                {
                    // Straddles the boundary: discard, and open the window here.
                    measureStartNanos = now;
                }

                final long count = interval.getTotalCount();
                final long meanMicros = count > 0 ? (long)(interval.getMean() / 1000) : 0;

                System.out.println(
                    "Sending Request to AeronCluster (" + config.endpoints + ") at qps=" + count +
                    " latency=" + meanMicros + (measuring ? "" : " [warmup]"));
            }
        }, "reporter");

        reporter.setDaemon(true);
        reporterHolder[0] = reporter;
        reporter.start();
    }

    private void startShutdownWatcher()
    {
        final Thread watcher = new Thread(() ->
        {
            new ShutdownSignalBarrier().await();
            running.set(false);
        }, "shutdown-watcher");

        watcher.setDaemon(true);
        watcher.start();
    }

    private void printSummary()
    {
        final double windowSeconds =
            measureStartNanos > 0 ? (measureEndNanos - measureStartNanos) / 1e9d : 0;
        final long count = measured.getTotalCount();
        final double achieved = windowSeconds > 0 ? count / windowSeconds : 0;
        final long dropped = droppedByRig.get();

        System.out.println();
        System.out.println("=== summary ===");
        System.out.println("mode                 " + config.mode);
        System.out.println("endpoints            " + config.endpoints);
        if (config.isOpen())
        {
            System.out.println("offered rate         " + config.rate + " msg/s (burst " + config.burst + ")");
            System.out.println("max inflight         " + config.maxInflight);
        }
        else
        {
            System.out.println("outstanding          " + config.threadNum);
        }
        System.out.println("request payload      " + config.valueSize + " bytes");
        System.out.println("measure window       " + String.format("%.1f", windowSeconds) + " s");
        System.out.println("completed            " + count);
        System.out.println("achieved rate        " + String.format("%.0f", achieved) + " msg/s");
        System.out.println("dropped-by-rig       " + dropped);
        System.out.println("unanswered           " + inFlight);
        System.out.println("correlation mismatch " + mismatches);
        System.out.println("latency us   p50      " + measured.getValueAtPercentile(50.0) / 1000);
        System.out.println("             p90      " + measured.getValueAtPercentile(90.0) / 1000);
        System.out.println("             p99      " + measured.getValueAtPercentile(99.0) / 1000);
        System.out.println("             p99.9    " + measured.getValueAtPercentile(99.9) / 1000);
        System.out.println("             p99.99   " + measured.getValueAtPercentile(99.99) / 1000);
        System.out.println("             max      " + measured.getMaxValue() / 1000);
        System.out.println("             mean     " + (long)(measured.getMean() / 1000));

        if (config.isOpen() && scheduleLag.getTotalCount() > 0)
        {
            System.out.println("schedule lag us       p50 " + scheduleLag.getValueAtPercentile(50.0) / 1000 +
                "  p99 " + scheduleLag.getValueAtPercentile(99.0) / 1000 +
                "  max " + scheduleLag.getMaxValue() / 1000 +
                "   (how late sends were; large values mean the rig, not the cluster)");
        }

        if (config.isOpen() && dropped > 0)
        {
            System.out.println();
            System.out.println("WARNING: " + dropped + " messages were never sent, so an offered rate of " +
                config.rate + " msg/s was not actually achieved. This run cannot be reported as such.");
        }

        if (!config.isOpen() && count > 0)
        {
            // Little's law: outstanding should equal throughput x latency. A large
            // deviation means the rig is not measuring what it thinks it is.
            final double implied = achieved * (measured.getMean() / 1e9d);
            final double ratio = implied / config.threadNum;
            System.out.println("little's law ratio   " + String.format("%.2f", ratio) +
                (Math.abs(ratio - 1.0) > 0.10 ? "   WARNING: >10% off, suspect a rig bug" : ""));
        }

        if (inFlight > 0)
        {
            System.out.println();
            System.out.println("WARNING: " + inFlight + " requests never received a reply within the " +
                config.drainTimeoutSeconds + "s drain window.");
        }

        writeHdrLog();
    }

    private void writeHdrLog()
    {
        if (null == config.hdrOut)
        {
            return;
        }

        try (FileOutputStream out = new FileOutputStream(new File(config.hdrOut)))
        {
            final HistogramLogWriter writer = new HistogramLogWriter(out);
            writer.outputComment("mode=" + config.mode + " endpoints=" + config.endpoints +
                " payload=" + config.valueSize);
            writer.outputLogFormatVersion();
            writer.outputLegend();
            writer.outputIntervalHistogram(measured);
            System.out.println("hdr log              " + config.hdrOut);
        }
        catch (final IOException e)
        {
            System.err.println("failed to write " + config.hdrOut + ": " + e);
        }
    }

    /** Builds AeronCluster's "0=host:port,1=host:port,..." ingress endpoint list. */
    private static String ingressEndpoints(final String[] hostnames, final int portBase)
    {
        final StringBuilder sb = new StringBuilder();

        for (int i = 0; i < hostnames.length; i++)
        {
            if (i > 0)
            {
                sb.append(',');
            }
            sb.append(i).append('=').append(hostnames[i].trim()).append(':')
                .append(portBase + (i * PORTS_PER_NODE) + CLIENT_FACING_PORT_OFFSET);
        }

        return sb.toString();
    }

    private static String required(final String[] args, final String name)
    {
        final String value = arg(args, name, null);
        if (null == value)
        {
            throw new IllegalArgumentException(name + " is required");
        }

        return value;
    }

    private static String arg(final String[] args, final String name, final String defaultValue)
    {
        for (int i = 0; i < args.length - 1; i++)
        {
            if (name.equals(args[i]))
            {
                return args[i + 1];
            }
        }

        return defaultValue;
    }

    private static boolean flag(final String[] args, final String name)
    {
        for (final String arg : args)
        {
            if (name.equals(arg))
            {
                return true;
            }
        }

        return false;
    }
}
