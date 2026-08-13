"""Generate gRPC Python stubs from protos/*.proto into both cas_server and cas_client.

ES-002 documents the server-side command:
    python -m grpc_tools.protoc -I./protos --python_out=./cas_server \
        --grpc_python_out=./cas_server protos/*.proto

The PySide6 client also needs stubs to call the server, so this script runs
the equivalent command a second time with --python_out=./cas_client. Run it
from the repo root (with the venv active) any time a .proto file changes:

    python scripts/generate_protos.py
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROTOS_DIR = ROOT / "protos"
OUTPUT_DIRS = [ROOT / "cas_server", ROOT / "cas_client"]


def generate(output_dir: Path) -> None:
    proto_files = sorted(str(p) for p in PROTOS_DIR.glob("*.proto"))
    cmd = [
        sys.executable,
        "-m",
        "grpc_tools.protoc",
        f"-I{PROTOS_DIR}",
        f"--python_out={output_dir}",
        f"--grpc_python_out={output_dir}",
        *proto_files,
    ]
    print(f"Generating stubs into {output_dir} ...")
    subprocess.run(cmd, check=True, cwd=ROOT)


def main() -> None:
    for output_dir in OUTPUT_DIRS:
        output_dir.mkdir(parents=True, exist_ok=True)
        generate(output_dir)


if __name__ == "__main__":
    main()
