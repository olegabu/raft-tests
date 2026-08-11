find_package(protobuf CONFIG REQUIRED)
include_directories((${Protobuf_INCLUDE_DIRS}))
protobuf_generate_cpp(PROTO_SRCS PROTO_HDRS atomic.proto)
add_library(proto ${PROTO_SRCS} ${PROTO_HDRS})