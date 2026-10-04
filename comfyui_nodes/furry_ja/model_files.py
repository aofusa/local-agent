"""Model file names shared by the loader node and scripts/setup-comfyui-chroma.ps1 (no ComfyUI imports)."""

FP8_SUFFIX = "_fp8_e4m3fn"


def fp8_sibling(name: str) -> str:
    """chroma_v10HD.safetensors -> chroma_v10HD_fp8_e4m3fn.safetensors (already converted names stay)."""
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, "safetensors"
    stem = stem.replace("\\", "/").rsplit("/", 1)[-1]
    return name if stem.endswith(FP8_SUFFIX) else f"{stem}{FP8_SUFFIX}.{ext}"
