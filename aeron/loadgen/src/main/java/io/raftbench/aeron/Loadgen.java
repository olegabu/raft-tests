package io.raftbench.aeron;

import io.aeron.cluster.client.AeronCluster;
import io.aeron.cluster.client.EgressListener;
import io.aeron.cluster.codecs.EventCode;
import io.aeron.driver.MediaDriver;
import io.aeron.driver.ThreadingMode;
import io.aeron.logbuffer.Header;
import org.agrona.DirectBuffer;
import org.agrona.concurrent.ShutdownSignalBarrier;
import org.agrona.concurrent.UnsafeBuffer;

import java.nio.ByteBuffer;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;

/**
 * Closed-loop load generator for an Aeron Cluster running the echo service.
 *
 * Keeps --thread_num messages outstanding and measures the achieved rate and
 * mean round-trip latency, reporting once a second in the same line shape the
 * braft and openraft load generators use so the three can be read side by side.
 *
 * An AeronCluster session is single-threaded by design, so concurrency here is
 * a window of outstanding messages driven by one thread, rather than N threads
 * each with one request outstanding. The comparable quantity across the three
 * products is the number of outstanding requests, which is what --thread_num
 * sets in all of them; see aeron/README.md.
 */
public final class Loadgen implements EgressListener
{
    private static final int CORRELATION_OFFSET = 0;
    private static final int TIMESTAMP_OFFSET = 8;
    private static final int MIN_VALUE_SIZE = 16;

    /** Client-facing port offset within each node's 100-port block; see ClusterConfig. */
    private static final int CLIENT_FACING_PORT_OFFSET = 2;
    private static final int PORTS_PER_NODE = 100;

    private final int window;
    private final int valueSize;
    private final boolean logEachRequest;
    private final String endpoints;

    /** Touched only by the sender/poller thread (onMessage runs from its pollEgress call). */
    private int inFlight;
    private long nextCorrelationId;
    private long expectedCorrelationId;
    private long mismatches;

    /** Single writer (sender thread), read by the reporter thread. */
    private final AtomicLong completed = new AtomicLong();
    private final AtomicLong latencySumNanos = new AtomicLong();
    private final AtomicBoolean running = new AtomicBoolean(true);

    private Loadgen(final int window, final int valueSize, final boolean logEachRequest, final String endpoints)
    {
        this.window = window;
        this.valueSize = valueSize;
        this.logEachRequest = logEachRequest;
        this.endpoints = endpoints;
    }

    public static void main(final String[] args)
    {
        final String hostnames = required(args, "--hostnames");
        final int portBase = Integer.parseInt(arg(args, "--port_base", "9000"));
        final String egressHost = arg(args, "--egress_host", "localhost");
        final int window = Integer.parseInt(arg(args, "--thread_num", "1"));
        final int valueSize = Integer.parseInt(arg(args, "--value_size", "64"));
        final boolean logEachRequest = flag(args, "--log_each_request");

        if (valueSize < MIN_VALUE_SIZE)
        {
            throw new IllegalArgumentException(
                "--value_size must be at least " + MIN_VALUE_SIZE + " (correlation id + timestamp)");
        }
        if (window < 1)
        {
            throw new IllegalArgumentException("--thread_num must be at least 1");
        }

        final String endpoints = ingressEndpoints(hostnames.split(","), portBase);
        final Loadgen loadgen = new Loadgen(window, valueSize, logEachRequest, endpoints);

        try (MediaDriver mediaDriver = MediaDriver.launchEmbedded(new MediaDriver.Context()
                .threadingMode(ThreadingMode.SHARED)
                .dirDeleteOnStart(true)
                .dirDeleteOnShutdown(true));
            AeronCluster cluster = AeronCluster.connect(new AeronCluster.Context()
                .egressListener(loadgen)
                // Must be this host's own reachable address, not localhost: the
                // cluster sends egress back to whatever is named here, so a
                // loopback address silently never gets replies from remote nodes.
                .egressChannel("aeron:udp?endpoint=" + egressHost + ":0")
                .aeronDirectoryName(mediaDriver.aeronDirectoryName())
                .ingressChannel("aeron:udp")
                .ingressEndpoints(endpoints)))
        {
            // Which member is leader matters for interpreting the numbers: in a
            // multi-AZ cluster a client co-located with the leader pays one
            // fewer cross-AZ hop per request than one talking to a remote leader.
            System.out.println("connected to " + endpoints +
                " as session " + cluster.clusterSessionId() +
                ", leader member " + cluster.leaderMemberId());
            loadgen.run(cluster);
        }
    }

    private void run(final AeronCluster cluster)
    {
        final UnsafeBuffer buffer = new UnsafeBuffer(ByteBuffer.allocateDirect(valueSize));

        startReporter();
        startShutdownWatcher();

        long idleCount = 0;
        while (running.get())
        {
            boolean progress = false;

            while (inFlight < window)
            {
                buffer.putLong(CORRELATION_OFFSET, nextCorrelationId);
                buffer.putLong(TIMESTAMP_OFFSET, System.nanoTime());

                if (cluster.offer(buffer, 0, valueSize) > 0)
                {
                    if (logEachRequest)
                    {
                        System.out.println(">>> id=" + nextCorrelationId);
                    }
                    nextCorrelationId++;
                    inFlight++;
                    progress = true;
                }
                else
                {
                    // Back pressure or not connected -- drain egress before retrying.
                    break;
                }
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
                // While stalled (e.g. mid leader election) the session still has
                // to be kept alive, or the cluster times it out and the run dies
                // without an obvious cause.
                if (++idleCount % 1000 == 0)
                {
                    cluster.sendKeepAlive();
                }
                Thread.onSpinWait();
            }
        }

        if (mismatches > 0)
        {
            System.err.println("correlation mismatches: " + mismatches);
        }
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
        final long sentAtNanos = buffer.getLong(offset + TIMESTAMP_OFFSET);
        final long latencyNanos = System.nanoTime() - sentAtNanos;

        inFlight--;
        completed.incrementAndGet();
        latencySumNanos.addAndGet(latencyNanos);

        // A session's messages are echoed back in order, so a gap means a reply
        // was lost or double counted. Surface it instead of averaging it in.
        if (correlationId != expectedCorrelationId)
        {
            mismatches++;
            if (logEachRequest)
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

        if (logEachRequest)
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

                final long count = completed.getAndSet(0);
                final long sumNanos = latencySumNanos.getAndSet(0);
                final long meanMicros = count > 0 ? (sumNanos / count) / 1000 : 0;

                System.out.println(
                    "Sending Request to AeronCluster (" + endpoints + ") at qps=" + count +
                    " latency=" + meanMicros);
            }
        }, "reporter");

        reporter.setDaemon(true);
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
