"""Setup helper for config/host_models.json (used by the PowerShell and the macOS shell scripts alike).

    uv run python scripts/host_models.py wanted [--ids a,b]
        JSON list of the ComfyUI files the image entries need: {"folders", "name", "required", "kind"} for each
        checkpoint / diffusion model, LoRA, text encoder and VAE, and {"download": {...}} for the files the setup
        fetches from Hugging Face (config/host_models.json "downloads", per family in use).

    uv run python scripts/host_models.py preset --out tools/llm/models.ini [--offload 0.45] [--engine llamacpp|mlx]
            [--qwen-model PATH] [--qwen-mmproj PATH] [--models-dir DIR] [--sleep-idle 300]
        Write the llama.cpp router preset: one section per inference entry whose GGUF exists (the others are
        reported and left out, so the router never offers a model it cannot load). Keys are llama-server's long
        option names; the entry's own "context" and "params" go into its section. With --engine mlx (macOS),
        entries that have an MLX build downloaded get "engine = mlx" and their MLX folder instead
        (scripts/mlx_router.py serves those; the rest stay on llama-server).
        Prints the sections as JSON.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from furry_agent.model_catalog import gguf_path, image_files, load_catalog  # noqa: E402
from furry_agent.templates import load_map  # noqa: E402

FOLDERS = {"ckpt_name": ["checkpoints", "diffusion_models", "unet"], "clip_name": ["text_encoders", "clip"],
           "vae_name": ["vae"]}
# Router defaults for every section (scripts/setup-llm.ps1 before v0.12.0 wrote these for the 27B only).
COMMON = {
    "flash-attn": "on",
    "parallel": 1,
    # Vulkan on a shared-memory iGPU: without mmap the CPU-side weights go to pinned memory and the load fails
    # with ErrorOutOfDeviceMemory (measured on the Radeon 890M). Harmless on Metal.
    "load-mode": "mmap",
    "jinja": True,
    "reasoning-format": "deepseek",
    "reasoning": "off",
}


def wanted(ids: set[str] | None) -> list[dict]:
    catalog = load_catalog()
    out, seen, families = [], set(), set()
    for model in catalog.image.values():
        if ids and model.id not in ids:
            continue
        families.add(model.family)
        family_map = load_map(model.family)
        for slot, name in image_files(model, family_map).items():
            key = (slot, name)
            if key not in seen:
                seen.add(key)
                out.append({"folders": FOLDERS[slot], "name": name, "required": slot == "ckpt_name",
                            "kind": slot, "model": model.id})
        for spec in model.loras:
            if ("lora", spec.name) not in seen:
                seen.add(("lora", spec.name))
                out.append({"folders": ["loras"], "name": spec.name, "required": True, "kind": "lora",
                            "model": model.id})
    for family in sorted(families):
        for item in catalog.downloads.get(family, []):
            name = item["file"].rsplit("/", 1)[-1]
            if ("download", name) in seen:
                continue
            seen.add(("download", name))
            out.append({"download": {**item, "name": name}, "family": family})
    return out


def _gpu_layers(path: Path, offload: float) -> int:
    from gguf_info import read_metadata, summary

    with open(path, "rb") as f:
        return int(summary(read_metadata(f), offload)["gpu_layers"])


def _value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def preset(args) -> list[dict]:
    catalog = load_catalog()
    spec = json.loads((ROOT / "config" / "llm_model.json").read_text(encoding="utf-8"))
    if args.models_dir:
        os.environ["BONSAI_MODELS_DIR"] = args.models_dir
    sections, skipped = [], []
    for model in catalog.inference.values():
        if model.remote:  # served by another host (catalog "endpoint"): not a section of this router
            continue
        settings: dict = {}
        mlx_dir = ROOT / "tools" / "models" / "mlx" / model.mlx["repo"].replace("/", "--") if model.mlx else None
        if args.engine == "mlx" and mlx_dir is not None and (mlx_dir / "config.json").exists():
            settings = {"engine": "mlx", "model": mlx_dir.as_posix(), "ctx-size": model.context}
            settings.update({k: v for k, v in model.params.items()})
            sections.append({"id": model.id, "settings": settings})
            continue
        if model.gguf["catalog"] == "llm_model" and args.qwen_model:
            path = Path(args.qwen_model)
        else:
            path = gguf_path(model)
        if path is None or not path.exists():
            skipped.append({"id": model.id, "reason": f"GGUF がありません: {path}"})
            continue
        offload = model.gpu_offload if model.gpu_offload is not None else args.offload
        settings["model"] = path.resolve().as_posix()
        if model.gguf["catalog"] == "llm_model":
            mmproj = Path(args.qwen_mmproj) if args.qwen_mmproj else path.parent / spec["mmproj"]["file"]
            if model.vision and mmproj.exists():
                settings["mmproj"] = mmproj.resolve().as_posix()
        settings["ctx-size"] = model.context
        settings["n-gpu-layers"] = _gpu_layers(path, offload)
        settings.update(COMMON)
        settings.update(model.params)
        settings["sleep-idle-seconds"] = args.sleep_idle if args.sleep_idle is not None else spec.get("sleep_idle_s", 300)
        sections.append({"id": model.id, "settings": settings})
    lines = ["version = 1"]
    for section in sections:
        lines += ["", f"[{section['id']}]"] + [f"{k} = {_value(v)}" for k, v in section["settings"].items()]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    return [*sections, *({"skipped": s} for s in skipped)]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    w = sub.add_parser("wanted")
    w.add_argument("--ids", default="")
    p = sub.add_parser("preset")
    p.add_argument("--out", required=True)
    p.add_argument("--offload", type=float, default=1.0)
    p.add_argument("--engine", choices=("llamacpp", "mlx"), default="llamacpp")
    p.add_argument("--qwen-model", default="")
    p.add_argument("--qwen-mmproj", default="")
    p.add_argument("--models-dir", default="")
    p.add_argument("--sleep-idle", type=int, default=None)
    args = parser.parse_args(argv)
    if args.command == "wanted":
        ids = {x.strip() for x in args.ids.split(",") if x.strip()} or None
        result = wanted(ids)
    else:
        result = preset(args)
    sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
