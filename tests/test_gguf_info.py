"""scripts/gguf_info.py: the GGUF header reader setup-llm.ps1 uses for n-gpu-layers."""

import importlib.util
import io
import json
import struct
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("gguf_info", ROOT / "scripts" / "gguf_info.py")
gguf_info = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gguf_info)


def _string(text: str) -> bytes:
    data = text.encode("utf-8")
    return struct.pack("<Q", len(data)) + data


def _kv(key: str, kind: int, payload: bytes) -> bytes:
    return _string(key) + struct.pack("<I", kind) + payload


def _gguf(arch="qwen35", blocks=65, nextn=1, ctx=262144) -> bytes:
    tokens = b"".join(_string(t) for t in ("<s>", "a", "b"))
    kvs = [
        _kv("general.architecture", 8, _string(arch)),
        _kv("tokenizer.ggml.tokens", 9, struct.pack("<IQ", 8, 3) + tokens),  # array of strings (skipped)
        _kv("tokenizer.ggml.scores", 9, struct.pack("<IQ", 6, 3) + struct.pack("<3f", 0, 1, 2)),
        _kv(f"{arch}.block_count", 4, struct.pack("<I", blocks)),
        _kv(f"{arch}.context_length", 4, struct.pack("<I", ctx)),
        _kv("general.flag", 7, struct.pack("<?", True)),
    ]
    if nextn:
        kvs.append(_kv(f"{arch}.nextn_predict_layers", 4, struct.pack("<I", nextn)))
    return b"GGUF" + struct.pack("<IQQ", 3, 0, len(kvs)) + b"".join(kvs)


def test_reads_layers_and_skips_arrays():
    meta = gguf_info.read_metadata(io.BytesIO(_gguf()))
    assert meta["general.architecture"] == "qwen35" and meta["qwen35.block_count"] == 65
    assert "tokenizer.ggml.tokens" not in meta and meta["general.flag"] is True
    # The Qwen3.8 27B: 64 layers plus one MTP block; 0.45 of them on the GPU (LM Studio's old ratio) is 29.
    assert gguf_info.summary(meta, 0.45) == {"architecture": "qwen35", "block_count": 65, "layers": 64,
                                             "gpu_layers": 29, "context_length": 262144}


def test_full_offload_and_no_mtp():
    meta = gguf_info.read_metadata(io.BytesIO(_gguf("qwen3", 36, 0, 32768)))
    assert gguf_info.summary(meta, 1.0)["gpu_layers"] == 36
    assert gguf_info.summary(meta, 0.0)["gpu_layers"] == 0


def test_main_prints_json(tmp_path, capsys):
    path = tmp_path / "m.gguf"
    path.write_bytes(_gguf())
    assert gguf_info.main([str(path), "--offload", "0.45"]) == 0
    assert json.loads(capsys.readouterr().out)["gpu_layers"] == 29


def test_rejects_other_files():
    with pytest.raises(ValueError):
        gguf_info.read_metadata(io.BytesIO(b"PK\x03\x04" + b"\0" * 20))
