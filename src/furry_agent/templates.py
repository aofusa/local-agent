"""Load a registered workflow template and inject one run's values through the node map (WI §4.4, §4.8).

Templates are the reviewed API JSON files under ``workflows/<family>/``; ``workflows/maps/<family>.json``
names every slot as ``node.inputs.field``. Nothing here invents node ids: values only go to mapped slots.
The two structural edits are also map/env driven: the pose preprocessor variant (from the map) and the
LoRA chain (from the ``LORAS`` environment variable), inserted right after the checkpoint loader so it
still runs after the LLM eject.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from furry_agent.planner import GenerationPlan, template_roles

REPO_WORKFLOWS = Path(__file__).resolve().parents[2] / "workflows"
LORA_STRENGTH_RANGE = (-2.0, 2.0)


class TemplateError(ValueError):
    """The template or its map cannot serve this request (shown to the user)."""


@dataclass(frozen=True)
class LoraSpec:
    name: str
    strength_model: float = 1.0
    strength_clip: float = 1.0


def parse_loras(value: str | None) -> list[LoraSpec]:
    """``LORAS=a.safetensors:0.8, b:0.6:0.4`` -> specs. Separators: comma, semicolon or newline.

    ``name[:model_strength[:clip_strength]]``; clip strength defaults to the model strength.
    """
    specs = []
    for item in re.split(r"[,;\n]", value or ""):
        item = item.strip()
        if not item:
            continue
        parts = [p.strip() for p in item.split(":")]
        # A Windows drive-less path cannot contain ':', so everything after the first ':' is numbers.
        name, numbers = parts[0], parts[1:]
        if not name or len(numbers) > 2:
            raise TemplateError(f"LORAS の書式が不正です: {item!r}（name[:強度[:clip強度]]）")
        try:
            values = [float(n) for n in numbers]
        except ValueError as exc:
            raise TemplateError(f"LORAS の強度が数値ではありません: {item!r}") from exc
        low, high = LORA_STRENGTH_RANGE
        if any(not low <= v <= high for v in values):
            raise TemplateError(f"LORAS の強度は {low}〜{high} です: {item!r}")
        model = values[0] if values else 1.0
        clip = values[1] if len(values) > 1 else model
        specs.append(LoraSpec(name, model, clip))
    return specs


def resolve_lora_names(specs: list[LoraSpec], available: list[str]) -> list[LoraSpec]:
    """Match names against ComfyUI's lora list (case-insensitive, extension optional)."""
    by_key: dict[str, str] = {}
    for name in available:
        normalized = name.replace("\\", "/").lower()
        by_key.setdefault(normalized, name)
        stem = normalized.rsplit(".", 1)[0]
        by_key.setdefault(stem, name)
        by_key.setdefault(stem.rsplit("/", 1)[-1], name)
    resolved, missing = [], []
    for spec in specs:
        key = spec.name.replace("\\", "/").lower()
        match = by_key.get(key) or by_key.get(key.rsplit(".", 1)[0])
        if match is None:
            missing.append(spec.name)
        else:
            resolved.append(LoraSpec(match, spec.strength_model, spec.strength_clip))
    if missing:
        raise TemplateError(f"LoRA が ComfyUI に見つかりません: {', '.join(missing)}（models/loras を確認してください）")
    return resolved


@lru_cache(maxsize=8)
def _load_map(path: Path, mtime: float) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_map(family: str, workflows_dir: Path = REPO_WORKFLOWS) -> dict:
    path = workflows_dir / "maps" / f"{family}.json"
    if not path.exists():
        raise TemplateError(f"モデル系統 {family} のテンプレートはこの環境に登録されていません（{path.name} がありません）")
    return _load_map(path, path.stat().st_mtime)


def load_template(family: str, template_id: str, workflows_dir: Path = REPO_WORKFLOWS) -> tuple[dict, dict]:
    """Return (template prompt copy, its map entry)."""
    mapping = load_map(family, workflows_dir)
    entry = mapping["templates"].get(template_id)
    if entry is None:
        raise TemplateError(f"テンプレート {template_id} は {family} に未登録です")
    path = workflows_dir / entry["file"]
    prompt = json.loads(path.read_text(encoding="utf-8"))
    return prompt, {**entry, "pose_preprocessors": mapping.get("pose_preprocessors", {}),
                    "ipadapter_weight_scale": mapping.get("ipadapter_weight_scale", {}),
                    "models": mapping.get("models", {})}


def model_slots(entry: dict) -> dict[str, str]:
    """Model-file slots of a template (checkpoint / diffusion model, text encoder, VAE) -> slot path."""
    return {k: entry["slots"][k] for k in ("ckpt_name", "clip_name", "vae_name") if k in entry["slots"]}


def slot_value(prompt: dict, path: str):
    node_id, _, field = path.split(".", 2)
    return prompt[node_id]["inputs"][field]


def _set(prompt: dict, path: str, value) -> None:
    node_id, section, field = path.split(".", 2)
    node = prompt.get(node_id)
    if node is None or section != "inputs":
        raise TemplateError(f"ワークフロー注入に失敗: スロット {path} のノードがありません")
    node["inputs"][field] = value


def insert_loras(prompt: dict, loras: list[LoraSpec], ckpt_node: str = "ckpt") -> dict:
    """Chain LoraLoader nodes after the checkpoint and repoint every MODEL/CLIP consumer to the chain end."""
    if not loras:
        return prompt
    model, clip = [ckpt_node, 0], [ckpt_node, 1]
    ids = []
    for i, spec in enumerate(loras, start=1):
        node_id = f"lora_{i}"
        prompt[node_id] = {
            "class_type": "LoraLoader",
            "inputs": {"model": model, "clip": clip, "lora_name": spec.name,
                       "strength_model": spec.strength_model, "strength_clip": spec.strength_clip},
            "_meta": {"title": node_id},
        }
        model, clip = [node_id, 0], [node_id, 1]
        ids.append(node_id)
    for node_id, node in prompt.items():
        if node_id in ids:
            continue
        for name, value in node["inputs"].items():
            if value == [ckpt_node, 0]:
                node["inputs"][name] = list(model)
            elif value == [ckpt_node, 1]:
                node["inputs"][name] = list(clip)
    return prompt


def build_run_prompt(
    family: str,
    plan: GenerationPlan,
    images: dict[str, str],
    text: str,
    ckpt_name: str | None = None,
    loras: list[LoraSpec] | None = None,
    workflows_dir: Path = REPO_WORKFLOWS,
    models: dict[str, str] | None = None,
) -> dict:
    """Fill a template for one run. ``images`` maps role -> ComfyUI input filename.

    ``models`` overrides model-file slots other than the checkpoint (flux: clip_name, vae_name, weight_dtype).
    """
    template_id = plan["template_id"]
    prompt, entry = load_template(family, template_id, workflows_dir)
    slots = entry["slots"]
    required = template_roles(template_id)
    missing = sorted(required - set(images))
    if missing:
        raise TemplateError(f"テンプレート {template_id} の必須スロットが空です: {', '.join(missing)}")

    _set(prompt, slots["prompt"], text)
    _set(prompt, slots["seed"], int(plan["seed"]))
    if ckpt_name:
        _set(prompt, slots["ckpt_name"], ckpt_name)
    if "width" in slots:
        _set(prompt, slots["width"], int(plan["width"]))
        _set(prompt, slots["height"], int(plan["height"]))
    for slot, value in (models or {}).items():
        if slot in slots and value:
            _set(prompt, slots[slot], value)
    # Sampler settings are slots only in families that keep them in the map (flux); sdxl keeps the template.
    for slot, cast in (("steps", int), ("cfg", float), ("sampler_name", str), ("scheduler", str)):
        if slot in slots and plan.get(slot) is not None:
            _set(prompt, slots[slot], cast(plan[slot]))
    if "denoise" in slots and plan.get("denoise") is not None:
        _set(prompt, slots["denoise"], float(plan["denoise"]))
    for role, filename in images.items():
        if role not in required:
            continue
        _set(prompt, slots["base_image" if role == "base" else f"{role}_image"], filename)
    strengths = plan.get("strengths") or {}
    # Role strength -> node value. IP-Adapter weights are scaled per role (map: ipadapter_weight_scale).
    scale = entry.get("ipadapter_weight_scale", {})
    for role, slot in (("style", "style_weight"), ("character", "character_weight"), ("pose", "pose_strength")):
        if slot in slots and role in strengths:
            value = float(strengths[role]) * float(scale.get(role, 1.0))
            _set(prompt, slots[slot], round(value, 3))
    if "pose" in required:
        key = plan.get("pose_preprocessor") or "openpose"
        variant = entry["pose_preprocessors"].get(key)
        if variant is None:
            raise TemplateError(f"ポーズ前処理 {key} はこの環境に登録されていません")
        node_id = slots["pose_preprocessor"]
        image_link = prompt[node_id]["inputs"]["image"]
        prompt[node_id] = {"class_type": variant["class_type"],
                           "inputs": {"image": image_link, **copy.deepcopy(variant["inputs"])},
                           "_meta": {"title": node_id}}
        _set(prompt, slots["pose_type"], variant["union_type"])
    return insert_loras(prompt, loras or [])


def unknown_node_types(prompt: dict, known: set[str]) -> list[str]:
    """Class types in the prompt that ComfyUI does not provide (WI §6: validate before /prompt)."""
    return sorted({n["class_type"] for n in prompt.values()} - known)
