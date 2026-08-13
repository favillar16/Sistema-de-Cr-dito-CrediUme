import os
import sys

# Same reasoning as cas_server/__init__.py: the generated *_pb2.py files
# live directly in this package's directory and import each other with
# flat `import auth_service_pb2` statements.
sys.path.insert(0, os.path.dirname(__file__))
