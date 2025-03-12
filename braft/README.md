## Install 

Build tools. Tested with the version installed by default on Ubuntu 22.

```sh
sudo apt install cmake g++ ninja-build flex bison pkg-config
```

Package manager vcpkg.

```sh
git clone https://github.com/microsoft/vcpkg
```

Add location to where you cloned vcpkg to your profile.

```sh
export VCPKG_ROOT='~/workspace/vcpkg'
export PATH="$VCPKG_ROOT:$PATH"
```

## Build

You may need to change paths to where your tools are installed in CMakePresets.json.

Configure as default or release.

```sh
cmake --preset default .
```

Build.

```sh
cmake --build build
```

## Run

Start a local cluster of 3 nodes and tail the log of the first one.

```sh
./run_server.sh && \
tail -f runtime/0/std.log
```

Run the client in another terminal.

```sh
./run_client.sh
```

## Results

- Processor: Intel® Core™ i7-7567U CPU @ 3.50GHz × 4
- Memory: 32.0 GiB
- Disk: SSD 1.0 TB
- OS: Ubuntu 22.04.2 LTS

Default flag is `-raft_sync=true` which means every message is persisted with `fsync`. 
Observe less than 1000 queries per second with high latency over 1 ms (millisecond).

```
client.cpp:166] Sending Request to Atomic (127.0.1.1:8300:0,127.0.1.1:8301:0,127.0.1.1:8302:0,) at qps=956 latency=1040
```

Now run with `./run_server.sh -sync=false` and observe qps around 3600 and latencies around 270 us (microseconds).

```
client.cpp:167] Sending Request to Atomic (127.0.1.1:8300:0,127.0.1.1:8301:0,127.0.1.1:8302:0,) at qps=3671 latency=268
```

Looks like the latency is bound to disk I/O.

Compare synchronous writes of small 32 byte messages with utility [fio](https://fio.readthedocs.io/) with and without fsync.

Observe average latency of 353 us with fsync: `sync (usec): min=207, max=7914, avg=353.17, stdev=170.73`.

```sh
sudo fio --name=write-latency --filename=testfile --rw=write --bs=32 --size=1M --ioengine=sync --iodepth=1 --fsync=1
```

And latency of 2.7 us witout fsync `lat (nsec): min=1586, max=4441.3k, avg=2718.36, stdev=9749.06`.

```sh
sudo fio --name=write-latency --filename=testfile --rw=write --bs=32 --size=1M --ioengine=sync --iodepth=1
```
