import os
import sys

# Generated *_pb2.py / *_pb2_grpc.py files live directly in this package's
# directory and import each other with flat `import auth_service_pb2`
# statements (grpc_tools.protoc's default style, no package option in the
# .proto files). Make sure that directory is importable as a top-level
# module path wherever `cas_server` gets imported from.
sys.path.insert(0, os.path.dirname(__file__))
