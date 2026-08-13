cmake_minimum_required(VERSION 3.22)
project(braft-atomic)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Strip Release binaries at link time. braft statically links its whole
# dependency tree (brpc, protobuf, leveldb, OpenSSL, abseil), so debug info
# dominated the output: 272 MB unstripped against 15 MB stripped, and `make
# push` copies these to four hosts across availability zones. Optimization is
# unaffected -- only the symbol table goes -- but stack traces lose function
# names, so use the `debug` preset when you need to symbolize a crash.
add_link_options("$<$<CONFIG:Release>:-s>")

find_package(unofficial-braft CONFIG REQUIRED)
find_package(unofficial-brpc CONFIG REQUIRED)
find_package(protobuf CONFIG REQUIRED)
find_package(gflags CONFIG REQUIRED)

set(PACKAGE_LIBRARIES unofficial::braft::braft-static unofficial::brpc::brpc-static protobuf::libprotobuf gflags::gflags)

add_subdirectory(proto)
include_directories(${CMAKE_CURRENT_BINARY_DIR})

add_executable(atomic_server server.cpp)
target_link_libraries(atomic_server PRIVATE proto ${PACKAGE_LIBRARIES})

# Only the client needs histograms. bvar's percentiles are sampled over a
# rolling window, so they cannot produce whole-run figures; HdrHistogram can,
# and gives all three products in this repo identical percentile semantics.
find_package(hdr_histogram CONFIG REQUIRED)

add_executable(atomic_client client.cpp)
target_link_libraries(atomic_client PRIVATE proto ${PACKAGE_LIBRARIES}
    $<IF:$<TARGET_EXISTS:hdr_histogram::hdr_histogram>,hdr_histogram::hdr_histogram,hdr_histogram::hdr_histogram_static>)

add_executable(test test.cpp)
target_link_libraries(test PRIVATE proto ${PACKAGE_LIBRARIES})
