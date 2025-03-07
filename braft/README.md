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

Observe less than 1000 queries per second with latency over 1 ms.

```
I20250307 10:12:43.762640 137853932605888 client.cpp:166] Sending Request to Atomic (127.0.1.1:8300:0,127.0.1.1:8301:0,127.0.1.1:8302:0,) at qps=956 latency=1040
```