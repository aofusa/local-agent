import pytest

from furry_ja.model_files import fp8_sibling


@pytest.mark.parametrize("name, expected", [
    ("chroma_v10HD.safetensors", "chroma_v10HD_fp8_e4m3fn.safetensors"),
    ("sub\Chroma1-HD.safetensors", "Chroma1-HD_fp8_e4m3fn.safetensors"),
    ("chroma_v10HD_fp8_e4m3fn.safetensors", "chroma_v10HD_fp8_e4m3fn.safetensors"),
    ("noext", "noext_fp8_e4m3fn.safetensors"),
])
def test_fp8_sibling(name, expected):
    assert fp8_sibling(name) == expected
