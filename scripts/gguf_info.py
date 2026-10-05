"""Print a GGUF file's architecture and layer count as JSON (used by scripts/setup-llm.ps1).

    uv run python scripts/gguf_info.py <model.gguf> [--offload 0.45]

{"architecture": "qwen35", "block_count": 65, "layers": 64, "gpu_layers": 29}

``layers`` leaves out the next-token-prediction (MTP) blocks llama.cpp does not run; ``gpu_layers`` is the
``--offload`` share of them (the llama-server ``n-gpu-layers`` of the router preset).
Only the header is read: the tensor data is never touched.
"""

from __future__ import annotations

import json
import struct
import sys
from typing import BinaryIO

# GGUF value types -> struct format (strings and arrays are handled separately).
_SCALARS = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}
_STRING, _ARRAY = 8, 9


def _read(f: BinaryIO, fmt: str):
    size = struct.calcsize(fmt)
    data = f.read(size)
    if len(data) != size:
        raise ValueError("truncated GGUF header")
    return struct.unpack(fmt, data)[0]


def _string(f: BinaryIO) -> str:
    return f.read(_read(f, "<Q")).decode("utf-8", "replace")


def _value(f: BinaryIO, kind: int, keep: bool):
    if kind in _SCALARS:
        return _read(f, _SCALARS[kind])
    if kind == _STRING:
        return _string(f)
    if kind == _ARRAY:
        item, count = _read(f, "<I"), _read(f, "<Q")
        if item in _SCALARS and not keep:
            f.seek(struct.calcsize(_SCALARS[item]) * count, 1)
            return None
        values = [_value(f, item, keep) for _ in range(count)]
        return values if keep else None
    raise ValueError(f"unknown GGUF value type {kind}")


def read_metadata(f: BinaryIO, wanted=None) -> dict:
    """Key/value metadata of a GGUF stream (arrays are skipped unless their key is in ``wanted``)."""
    if f.read(4) != b"GGUF":
        raise ValueError("not a GGUF file")
    version = _read(f, "<I")
    if version < 2:
        raise ValueError(f"GGUF v{version} is not supported")
    _read(f, "<Q")  # tensor count
    count = _read(f, "<Q")
    meta = {}
    for _ in range(count):
        key = _string(f)
        kind = _read(f, "<I")
        value = _value(f, kind, keep=wanted is not None and key in wanted)
        if kind != _ARRAY or value is not None:
            meta[key] = value
    return meta


def summary(meta: dict, offload: float) -> dict:
    arch = meta.get("general.architecture", "")
    blocks = int(meta.get(f"{arch}.block_count", 0))
    layers = blocks - int(meta.get(f"{arch}.nextn_predict_layers", 0) or 0)
    gpu = max(0, min(layers, round(layers * offload)))
    return {"architecture": arch, "block_count": blocks, "layers": layers, "gpu_layers": gpu,
            "context_length": int(meta.get(f"{arch}.context_length", 0))}


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    offload = 1.0
    if "--offload" in argv:
        i = argv.index("--offload")
        offload = float(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    with open(argv[0], "rb") as f:
        print(json.dumps(summary(read_metadata(f), offload)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
