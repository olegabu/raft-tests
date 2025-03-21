cmake_minimum_required(VERSION 3.22)
project(braft-store)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(unofficial-braft CONFIG REQUIRED)
find_package(unofficial-brpc CONFIG REQUIRED)
find_package(protobuf CONFIG REQUIRED)
find_package(gflags CONFIG REQUIRED)

set(PACKAGE_LIBRARIES unofficial::braft::braft-static unofficial::brpc::brpc-static protobuf::libprotobuf gflags::gflags)

include_directories(${CMAKE_CURRENT_BINARY_DIR})

add_executable(store_server server.cpp)
target_link_libraries(store_server PRIVATE proto ${PACKAGE_LIBRARIES})
