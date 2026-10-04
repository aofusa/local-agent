import importlib.util
import json
import struct
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("convert_chroma_fp8", ROOT / "scripts" / "convert_chroma_fp8.py")
convert = importlib.util.module_from_spec(spec)
spec.loader.exec_module(convert)


def test_only_linear_weights_are_cast():
    assert convert.should_cast("double_blocks.0.img_attn.qkv.weight", [9216, 3072])
    assert not convert.should_cast("double_blocks.0.img_attn.norm.query_norm.scale", [128])
    assert not convert.should_cast("double_blocks.0.img_attn.qkv.bias", [9216])
    assert not convert.should_cast("img_in.weight", [3072, 64, 1])  # not a 2-D linear weight


def test_header_offsets_and_alignment():
    entries = [("a.weight", "BF16", [4, 3]), ("a.bias", "BF16", [4]), ("n.scale", "F32", [2])]
    header, order = convert.plan_header(entries, {"k": "v"})
    assert len(header) % 8 == 0
    data = json.loads(header)
    assert data["__metadata__"] == {"k": "v"}
    assert data["a.weight"] == {"dtype": "F8_E4M3", "shape": [4, 3], "data_offsets": [0, 12]}
    assert data["a.bias"] == {"dtype": "BF16", "shape": [4], "data_offsets": [12, 20]}
    assert data["n.scale"]["data_offsets"] == [20, 28]
    assert order == [("a.weight", "F8_E4M3"), ("a.bias", "BF16"), ("n.scale", "F32")]


def test_roundtrip_with_torch(tmp_path):
    torch = pytest.importorskip("torch")
    from safetensors.torch import load_file, save_file

    src = tmp_path / "src.safetensors"
    tensors = {"x.weight": torch.randn(8, 4, dtype=torch.bfloat16) * 1000, "x.bias": torch.randn(8, dtype=torch.bfloat16)}
    save_file(tensors, str(src))
    dst = tmp_path / "dst.safetensors"
    assert convert.convert(src, dst) == (1, 2)
    out = load_file(str(dst))
    assert out["x.weight"].dtype == torch.float8_e4m3fn
    assert torch.equal(out["x.bias"], tensors["x.bias"])
    expected = tensors["x.weight"].float().clamp(-448, 448).to(torch.float8_e4m3fn)
    assert torch.equal(out["x.weight"].view(torch.uint8), expected.view(torch.uint8))
    (length,) = struct.unpack("<Q", dst.read_bytes()[:8])
    assert length % 8 == 0
