cmake_minimum_required(VERSION 3.22)
project(test_braft)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(unofficial-braft CONFIG REQUIRED)
find_package(unofficial-brpc CONFIG REQUIRED)
find_package(protobuf CONFIG REQUIRED)
find_package(gflags CONFIG REQUIRED)

set(PACKAGE_LIBRARIES unofficial::braft::braft-static unofficial::brpc::brpc-static protobuf::libprotobuf gflags::gflags)

add_subdirectory(proto)
include_directories(${CMAKE_CURRENT_BINARY_DIR})

add_executable(atomic_server server.cpp)
target_link_libraries(atomic_server PRIVATE proto ${PACKAGE_LIBRARIES})

add_executable(atomic_client client.cpp)
target_link_libraries(atomic_client PRIVATE proto ${PACKAGE_LIBRARIES})
