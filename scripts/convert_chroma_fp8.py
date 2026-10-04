"""Convert a BF16 Chroma1-HD diffusion model to fp8 (e4m3fn) once, one tensor at a time.

Loading the 17.8 GB BF16 file with weight_dtype=fp8_e4m3fn makes ComfyUI read the whole BF16 state dict
into RAM before casting (measured: >15 GB private memory), which crashed ComfyUI on a 24 GB UMA machine
next to the T5. The converted file (~8.9 GB) loads directly. The cast is the same one ComfyUI does at load:
2-D ``.weight`` tensors (linear layers) become float8_e4m3fn, everything else (norms, biases) stays as is.
The output is streamed (header first, then each tensor), so memory use stays at one tensor.

    <ComfyUI python> scripts/convert_chroma_fp8.py <src.safetensors> <dst.safetensors>

Run it with ComfyUI's python (torch + safetensors). scripts/setup-comfyui-chroma.ps1 -ConvertFp8 calls it.
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

FP8_MAX = 448.0  # largest finite float8_e4m3fn value
DTYPE_SIZE = {"F8_E4M3": 1, "BF16": 2, "F16": 2, "F32": 4, "F64": 8, "I64": 8, "I32": 4, "U8": 1, "BOOL": 1}


def should_cast(name: str, shape: list[int]) -> bool:
    return name.endswith(".weight") and len(shape) == 2


def plan_header(entries: list[tuple[str, str, list[int]]], metadata: dict[str, str]) -> tuple[bytes, list[tuple[str, str]]]:
    """(name, source dtype, shape) -> (padded header bytes, [(name, output dtype)])."""
    header: dict = {"__metadata__": metadata}
    order, offset = [], 0
    for name, dtype, shape in entries:
        out = "F8_E4M3" if should_cast(name, shape) else dtype
        size = DTYPE_SIZE[out]
        for dim in shape:
            size *= dim
        header[name] = {"dtype": out, "shape": shape, "data_offsets": [offset, offset + size]}
        order.append((name, out))
        offset += size
    raw = json.dumps(header, separators=(",", ":")).encode("utf-8")
    raw += b" " * (-len(raw) % 8)
    return raw, order


def convert(src: Path, dst: Path) -> tuple[int, int]:
    import torch
    from safetensors import safe_open

    with safe_open(str(src), framework="pt") as f:
        entries = []
        for name in f.keys():
            piece = f.get_slice(name)
            entries.append((name, piece.get_dtype(), list(piece.get_shape())))
        metadata = {**(f.metadata() or {}), "furry_ja": "fp8_e4m3fn from " + src.name}
        header, order = plan_header(entries, metadata)
        partial = dst.with_name(dst.name + ".partial")
        cast = 0
        with open(partial, "wb") as out:
            out.write(struct.pack("<Q", len(header)))
            out.write(header)
            for name, dtype in order:
                tensor = f.get_tensor(name)
                if dtype == "F8_E4M3":
                    tensor = tensor.float().clamp_(-FP8_MAX, FP8_MAX).to(torch.float8_e4m3fn)
                    cast += 1
                out.write(tensor.contiguous().view(torch.uint8).numpy().tobytes())
                del tensor
    partial.replace(dst)
    return cast, len(order)


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    if dst.exists():
        print(f"exists: {dst}")
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    cast, total = convert(src, dst)
    print(f"wrote {dst} ({cast}/{total} tensors cast to float8_e4m3fn)")


if __name__ == "__main__":
    main()
