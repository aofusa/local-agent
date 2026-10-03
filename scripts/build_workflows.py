"""Generate workflows/furry_ja_api.json (API format), workflows/furry_ja.json (UI format), and the
multi-image role templates workflows/sdxl/<template_id>.api.json with their node map workflows/maps/sdxl.json.

Node IDs follow design doc §4.1. The system prompts are embedded from prompts/,
so re-run this script after editing a prompt:

    uv run python scripts/build_workflows.py

LMSTUDIO_MODEL / CKPT_NAME environment variables override the model key and checkpoint name.
"""

from __future__ import annotations

import copy
import itertools
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from furry_agent.planner import (  # noqa: E402
    DEFAULT_STRENGTH,
    REFERENCE_ROLES,
    default_denoise,
    select_workflow,
    template_roles,
)
from furry_agent.workflow import build_prompt  # noqa: E402
PROMPTS = ROOT / "prompts"
WORKFLOWS = ROOT / "workflows"

LMSTUDIO_URL = "http://127.0.0.1:1234/v1"
# scripts/setup-lmstudio.ps1 sets LMSTUDIO_MODEL to the key LM Studio assigned on this machine.
LMSTUDIO_MODEL = os.environ.get("LMSTUDIO_MODEL") or "huihui-qwen3.8-27b-abliterated@iq3_m"
CKPT_NAME = os.environ.get("CKPT_NAME") or "yiffInHell_yihVANTABLACK.safetensors"
REF_PLACEHOLDER = "furry_ja_ref.png"
REF_JOIN_DELIMITER = "\n\n[Reference image tags]\n"
VISION_USER_PROMPT = "Tag the reference image(s)."
QUALITY_PREFIX = "masterpiece, best quality, amazing quality, absurdres"
DEFAULT_NEGATIVE = (
    "worst quality, low quality, normal quality, lowres, blurry, jpeg artifacts, "
    "bad anatomy, bad hands, extra digits, missing fingers, deformed, "
    "watermark, signature, text, logo"
)


def _backend(auto_eject: bool) -> dict:
    return {
        "base_url": LMSTUDIO_URL,
        "model": LMSTUDIO_MODEL,
        "connect_timeout_seconds": 10,
        "read_timeout_seconds": 600,
        "stream": True,
        "max_retries": 0,
        "health_check": True,
        "auto_eject_after_run": auto_eject,
        "disable_thinking": True,
        "debug_logging": True,
    }


def api_workflow() -> dict:
    """Full graph. The text-only run removes the reference nodes (see src/.../workflow.py)."""
    system_tags = (PROMPTS / "system_furry_tags.txt").read_text(encoding="utf-8").strip()
    system_vision = (PROMPTS / "system_vision_caption.txt").read_text(encoding="utf-8").strip()

    nodes = {
        # Auto-eject is on for the tag call (design §4.1). The vision call keeps the
        # model loaded so prompt_node does not have to reload the 27B.
        "llm_backend": ("LMConnectLMStudioBackend", "LM Studio Backend", _backend(True)),
        "llm_backend_vision": ("LMConnectLMStudioBackend", "LM Studio Backend (vision, no auto-eject)", _backend(False)),
        "user_prompt": ("PrimitiveStringMultiline", "user_prompt (日本語指示)", {"value": "夕焼けの海辺に立つ、白い毛並みの狼獣人の女性、和服"}),
        "ref_image": ("LoadImage", "ref_image", {"image": REF_PLACEHOLDER}),
        "ref_image_2": ("LoadImage", "ref_image_2", {"image": REF_PLACEHOLDER}),
        "vision": ("LMConnectVision", "vision", {
            "system_prompt": system_vision,
            "prompt": VISION_USER_PROMPT,
            "image_1": ["ref_image", 0],
            "image_2": ["ref_image_2", 0],
            "max_image_dimension": 768,
            "backend": ["llm_backend_vision", 0],
            "base_url": LMSTUDIO_URL,
            "model": "",
            "temperature": 0.2,
            "max_tokens": 200,
        }),
        "prompt_join": ("StringConcatenate", "prompt_join (指示 + 参照タグ)", {
            "string_a": ["user_prompt", 0],
            "string_b": ["vision", 0],
            "delimiter": REF_JOIN_DELIMITER,
        }),
        "prompt_node": ("LMConnectPromptWithSystem", "prompt_node", {
            "system_prompt": system_tags,
            "prompt": ["prompt_join", 0],
            "backend": ["llm_backend", 0],
            "base_url": LMSTUDIO_URL,
            "model": "",
            "temperature": 0.4,
            "max_tokens": 320,
        }),
        "eject": ("LMConnectEjectLMStudioModel", "eject", {
            "passthrough": ["prompt_node", 0],
            "base_url": LMSTUDIO_URL,
            "model": "",
            "debug_logging": True,
        }),
        "split": ("FurryJaSplitTags", "split", {
            "text": ["eject", 0],
            "quality_prefix": QUALITY_PREFIX,
            "default_negative": DEFAULT_NEGATIVE,
        }),
        "ckpt": ("FurryJaCheckpointLoaderAfterEject", "ckpt", {
            "ckpt_name": CKPT_NAME,
            "after": ["eject", 0],
            "lmstudio_base_url": LMSTUDIO_URL,
        }),
        "positive": ("CLIPTextEncode", "positive", {"text": ["split", 0], "clip": ["ckpt", 1]}),
        "negative": ("CLIPTextEncode", "negative", {"text": ["split", 1], "clip": ["ckpt", 1]}),
        "ref_scale": ("ImageScaleToTotalPixels", "ref_scale (img2img)", {
            "image": ["ref_image", 0],
            "upscale_method": "lanczos",
            "megapixels": 1.01,
            "resolution_steps": 64,
        }),
        "latent": ("EmptyLatentImage", "latent", {"width": 832, "height": 1216, "batch_size": 1}),
        "sampler": ("KSampler", "sampler", {
            "model": ["ckpt", 0],
            "seed": 0,
            "steps": 28,
            "cfg": 5.5,
            "sampler_name": "euler_ancestral",
            "scheduler": "normal",
            "positive": ["positive", 0],
            "negative": ["negative", 0],
            "latent_image": ["latent", 0],
            "denoise": 1.0,
        }),
        "decode": ("VAEDecode", "decode", {"samples": ["sampler", 0], "vae": ["ckpt", 2]}),
        "save": ("SaveImage", "save", {"images": ["decode", 0], "filename_prefix": "furry_ja/furry_ja"}),
    }
    return {
        node_id: {"class_type": cls, "inputs": inputs, "_meta": {"title": title}}
        for node_id, (cls, title, inputs) in nodes.items()
    }


# --- UI format -------------------------------------------------------------

# class -> (link inputs [(name, type)], widget names in UI order, outputs [(name, type)])
UI_SPECS = {
    "LMConnectLMStudioBackend": ([], ["base_url", "model", "connect_timeout_seconds", "read_timeout_seconds", "stream", "max_retries", "health_check", "auto_eject_after_run", "disable_thinking", "debug_logging"], [("backend", "LMC_BACKEND")]),
    "PrimitiveStringMultiline": ([], ["value"], [("STRING", "STRING")]),
    "LoadImage": ([], ["image", "upload"], [("IMAGE", "IMAGE"), ("MASK", "MASK")]),
    "LMConnectVision": ([("image_1", "IMAGE"), ("image_2", "IMAGE"), ("image_3", "IMAGE"), ("backend", "LMC_BACKEND")], ["system_prompt", "prompt", "max_image_dimension", "base_url", "model", "temperature", "max_tokens"], [("response", "STRING")]),
    "StringConcatenate": ([], ["string_a", "string_b", "delimiter"], [("STRING", "STRING")]),
    "LMConnectPromptWithSystem": ([("backend", "LMC_BACKEND")], ["system_prompt", "prompt", "base_url", "model", "temperature", "max_tokens"], [("response", "STRING")]),
    "LMConnectEjectLMStudioModel": ([("passthrough", "*")], ["base_url", "model", "debug_logging"], [("*", "*")]),
    "FurryJaSplitTags": ([("text", "*")], ["quality_prefix", "default_negative"], [("positive", "STRING"), ("negative", "STRING")]),
    "FurryJaCheckpointLoaderAfterEject": ([("after", "*")], ["ckpt_name", "lmstudio_base_url"], [("MODEL", "MODEL"), ("CLIP", "CLIP"), ("VAE", "VAE")]),
    "CLIPTextEncode": ([("clip", "CLIP")], ["text"], [("CONDITIONING", "CONDITIONING")]),
    "ImageScaleToTotalPixels": ([("image", "IMAGE")], ["upscale_method", "megapixels", "resolution_steps"], [("IMAGE", "IMAGE")]),
    "EmptyLatentImage": ([], ["width", "height", "batch_size"], [("LATENT", "LATENT")]),
    "VAEEncode": ([("pixels", "IMAGE"), ("vae", "VAE")], [], [("LATENT", "LATENT")]),
    "KSampler": ([("model", "MODEL"), ("positive", "CONDITIONING"), ("negative", "CONDITIONING"), ("latent_image", "LATENT")], ["seed", "control_after_generate", "steps", "cfg", "sampler_name", "scheduler", "denoise"], [("LATENT", "LATENT")]),
    "VAEDecode": ([("samples", "LATENT"), ("vae", "VAE")], [], [("IMAGE", "IMAGE")]),
    "SaveImage": ([("images", "IMAGE")], ["filename_prefix"], []),
}

WIDGET_TYPES = {"value": "STRING", "string_a": "STRING", "string_b": "STRING", "prompt": "STRING", "text": "STRING"}

# Column/row layout so the UI graph reads left to right.
UI_LAYOUT = {
    "llm_backend_vision": (0, 0), "ref_image": (0, 1), "ref_image_2": (0, 2), "user_prompt": (0, 3), "llm_backend": (0, 4),
    "vision": (1, 1), "ref_scale": (1, 2), "prompt_join": (1, 3),
    "prompt_node": (2, 3), "latent_img2img": (2, 1), "latent": (2, 2),
    "eject": (3, 3), "ckpt": (3, 1),
    "split": (4, 3), "positive": (4, 1), "negative": (4, 2),
    "sampler": (5, 1), "decode": (6, 1), "save": (6, 2), "note": (5, 3),
}

NOTE = """furry_ja (design doc §4.1). Node titles are the API node IDs.
Default wiring = text only (Empty Latent, denoise 1.0). Muted nodes are the reference-image path.
img2img: unmute ref_image / vision / prompt_join / ref_scale / latent_img2img, connect
prompt_join -> prompt_node.prompt, latent_img2img -> sampler.latent_image, set denoise 0.45.
LangGraph does this automatically from workflows/furry_ja_api.json."""


def ui_workflow(api: dict) -> dict:
    api = json.loads(json.dumps(api))
    # Text-only default wiring for people who open the file in ComfyUI.
    api["prompt_node"]["inputs"]["prompt"] = ["user_prompt", 0]
    api["latent_img2img"] = {
        "class_type": "VAEEncode",
        "inputs": {"pixels": ["ref_scale", 0], "vae": ["ckpt", 2]},
        "_meta": {"title": "latent (img2img: VAE Encode)"},
    }
    muted = {"ref_image", "ref_image_2", "vision", "prompt_join", "ref_scale", "latent_img2img", "llm_backend_vision"}

    ids = {node_id: i + 1 for i, node_id in enumerate(api)}
    links, nodes = [], []
    link_lookup: dict[tuple[int, str], int] = {}
    out_links: dict[tuple[int, int], list[int]] = {}

    for node_id, node in api.items():
        cls = node["class_type"]
        link_inputs, _, _ = UI_SPECS[cls]
        input_names = [n for n, _ in link_inputs]
        input_names += [k for k in node["inputs"] if k in WIDGET_TYPES and k not in input_names]
        for name in input_names:
            src = node["inputs"].get(name)
            if not isinstance(src, list):
                continue
            src_id, slot = ids[src[0]], src[1]
            link_id = len(links) + 1
            src_type = UI_SPECS[api[src[0]]["class_type"]][2][slot][1]
            links.append([link_id, src_id, slot, ids[node_id], None, src_type])
            link_lookup[(ids[node_id], name)] = link_id
            out_links.setdefault((src_id, slot), []).append(link_id)

    for node_id, node in api.items():
        cls = node["class_type"]
        link_inputs, widgets, outputs = UI_SPECS[cls]
        nid = ids[node_id]
        inputs = []
        for name, typ in link_inputs:
            inputs.append({"name": name, "type": typ, "link": link_lookup.get((nid, name))})
        for name in widgets:
            if isinstance(node["inputs"].get(name), list) and name not in dict(link_inputs):
                inputs.append({"name": name, "type": WIDGET_TYPES.get(name, "STRING"), "widget": {"name": name}, "link": link_lookup.get((nid, name))})
        for inp in links:
            if inp[3] == nid:
                inp[4] = next(k for k, x in enumerate(inputs) if x["link"] == inp[0])
        widget_values = []
        for name in widgets:
            if name == "upload":
                widget_values.append("image")
            elif name == "control_after_generate":
                widget_values.append("randomize")
            else:
                value = node["inputs"].get(name)
                widget_values.append("" if isinstance(value, list) else value)
        col, row = UI_LAYOUT[node_id]
        nodes.append({
            "id": nid,
            "type": cls,
            "title": node["_meta"]["title"],
            "pos": [40 + col * 420, 40 + row * 330],
            "size": [380, 280],
            "flags": {},
            "order": nid,
            "mode": 2 if node_id in muted else 0,
            "inputs": inputs,
            "outputs": [
                {"name": name, "type": typ, "links": out_links.get((nid, i), []), "slot_index": i}
                for i, (name, typ) in enumerate(outputs)
            ],
            "properties": {"Node name for S&R": cls, "furry_ja_id": node_id},
            "widgets_values": widget_values,
        })

    note_id = len(nodes) + 1
    col, row = UI_LAYOUT["note"]
    nodes.append({
        "id": note_id, "type": "Note", "title": "README", "pos": [40 + col * 420, 40 + row * 330],
        "size": [420, 220], "flags": {}, "order": note_id, "mode": 0, "inputs": [], "outputs": [],
        "properties": {}, "widgets_values": [NOTE],
    })
    return {
        "last_node_id": note_id,
        "last_link_id": len(links),
        "nodes": nodes,
        "links": links,
        "groups": [],
        "config": {},
        "extra": {"furry_ja": "generated by scripts/build_workflows.py"},
        "version": 0.4,
    }


# --- Multi-image role templates (work instruction WI-IMG-MULTI-REF-001 §4.4) ---------------

MODEL_FAMILY = "sdxl"
CONTROLNET_NAME = "controlnet-union-sdxl-1.0-promax.safetensors"
IPADAPTER_PRESET = "PLUS (high strength)"
# style: IP-Adapter "style transfer" only feeds the style blocks, so the subject is not copied.
# character: linear; the pose comes from ControlNet / the text, not from this image.
IPADAPTER_WEIGHT_TYPE = {"character": "linear", "style": "style transfer"}
ROLE_SECTION = {
    "base": "\n\n[Base image tags]\n",
    "character": "\n\n[Character reference tags]\n",
    "pose": "\n\n[Pose reference tags]\n",
    "style": "\n\n[Style reference tags]\n",
}
DWPOSE_INPUTS = {
    "detect_hand": "enable", "detect_body": "enable", "detect_face": "enable", "resolution": 1024,
    "bbox_detector": "yolox_l.torchscript.pt", "pose_estimator": "dw-ll_ucoco_384_bs5.torchscript.pt",
    "scale_stick_for_xinsr_cn": "enable",
}
# Preprocessor variants the planner may pick (never free text). union_type is SetUnionControlNetType.type.
POSE_PREPROCESSORS = {
    "openpose": {"class_type": "DWPreprocessor", "inputs": DWPOSE_INPUTS, "union_type": "openpose"},
    "dwpose": {"class_type": "DWPreprocessor", "inputs": DWPOSE_INPUTS, "union_type": "openpose"},
    "depth": {"class_type": "DepthAnythingV2Preprocessor",
              "inputs": {"ckpt_name": "depth_anything_v2_vits.pth", "resolution": 1024}, "union_type": "depth"},
    "canny": {"class_type": "Canny", "inputs": {"low_threshold": 0.4, "high_threshold": 0.8},
              "union_type": "canny/lineart/anime_lineart/mlsd"},
}


def template_ids() -> list[str]:
    ids = []
    for extra in ((), ("base",), ("base", "mask")):
        for n in range(len(REFERENCE_ROLES) + 1):
            for refs in itertools.combinations(REFERENCE_ROLES, n):
                ids.append(select_workflow(refs + extra))
    return ids


def _node(cls: str, title: str, inputs: dict) -> dict:
    return {"class_type": cls, "inputs": inputs, "_meta": {"title": title}}


def role_template(template_id: str, api: dict | None = None) -> tuple[dict, dict]:
    """Return (API prompt, slot map) for one template. Slots are "node.inputs.field" paths."""
    api = api or api_workflow()
    roles = template_roles(template_id)
    slots = {"prompt": "user_prompt.inputs.value", "seed": "sampler.inputs.seed", "ckpt_name": "ckpt.inputs.ckpt_name"}

    if template_id in ("t2i_basic", "i2i_basic"):
        # The phase-1 graphs, produced by the same code the agent used before templates existed.
        refs = [REF_PLACEHOLDER] if template_id == "i2i_basic" else []
        prompt = build_prompt(api, api["user_prompt"]["inputs"]["value"], 0, refs)
        if refs:
            slots.update(base_image="ref_image.inputs.image", denoise="sampler.inputs.denoise")
        else:
            slots.update(width="latent.inputs.width", height="latent.inputs.height")
        return prompt, slots

    prompt = copy.deepcopy(api)
    prompt.pop("ref_image_2")
    prompt["vision"]["inputs"].pop("image_2")
    prompt["prompt_node"]["inputs"]["system_prompt"] = (PROMPTS / "system_furry_tags_roles.txt").read_text(encoding="utf-8").strip()
    vision_inputs = copy.deepcopy(prompt["vision"]["inputs"])
    previous = "user_prompt"
    if "base" in roles:
        prompt["prompt_join"]["inputs"]["delimiter"] = ROLE_SECTION["base"]
        prompt["latent"] = _node("VAEEncode", "latent (img2img)", {"pixels": ["ref_scale", 0], "vae": ["ckpt", 2]})
        prompt["sampler"]["inputs"]["denoise"] = default_denoise(roles)
        slots.update(base_image="ref_image.inputs.image", denoise="sampler.inputs.denoise")
        previous = "prompt_join"
    else:
        for node_id in ("ref_image", "vision", "prompt_join", "ref_scale"):
            prompt.pop(node_id)
        slots.update(width="latent.inputs.width", height="latent.inputs.height")

    for role in REFERENCE_ROLES:
        if role not in roles:
            continue
        system = (PROMPTS / f"system_vision_{role}.txt").read_text(encoding="utf-8").strip()
        prompt[f"{role}_image"] = _node("LoadImage", f"{role}_image", {"image": REF_PLACEHOLDER})
        prompt[f"vision_{role}"] = _node("LMConnectVision", f"vision_{role}", {
            **vision_inputs,
            "system_prompt": system,
            "prompt": f"Tag the {role} reference image.",
            "image_1": [f"{role}_image", 0],
        })
        prompt[f"prompt_join_{role}"] = _node("StringConcatenate", f"prompt_join_{role}", {
            "string_a": [previous, 0], "string_b": [f"vision_{role}", 0], "delimiter": ROLE_SECTION[role],
        })
        previous = f"prompt_join_{role}"
        slots[f"{role}_image"] = f"{role}_image.inputs.image"
    prompt["prompt_node"]["inputs"]["prompt"] = [previous, 0]
    if not any(n["class_type"] == "LMConnectVision" for n in prompt.values()):
        prompt.pop("llm_backend_vision")

    # IP-Adapter for identity / style. Its loader takes the checkpoint MODEL, so it runs after the eject.
    model = ["ckpt", 0]
    if {"character", "style"} & roles:
        prompt["ipa_loader"] = _node("IPAdapterUnifiedLoader", "ipa_loader", {"model": ["ckpt", 0], "preset": IPADAPTER_PRESET})
        model = ["ipa_loader", 0]
        for role in ("character", "style"):
            if role not in roles:
                continue
            prompt[f"ipa_{role}"] = _node("IPAdapterAdvanced", f"ipa_{role}", {
                "model": model, "ipadapter": ["ipa_loader", 1], "image": [f"{role}_image", 0],
                "weight": DEFAULT_STRENGTH[role], "weight_type": IPADAPTER_WEIGHT_TYPE[role],
                "combine_embeds": "concat", "start_at": 0.0, "end_at": 1.0, "embeds_scaling": "V only",
            })
            model = [f"ipa_{role}", 0]
            slots[f"{role}_weight"] = f"ipa_{role}.inputs.weight"
    prompt["sampler"]["inputs"]["model"] = model

    # ControlNet for pose. The preprocessor input is gated on the checkpoint so it never runs next to the LLM.
    if "pose" in roles:
        pre = POSE_PREPROCESSORS["openpose"]
        prompt["pose_gate"] = _node("FurryJaImageAfter", "pose_gate", {"image": ["pose_image", 0], "after": ["ckpt", 0]})
        prompt["pose_preprocess"] = _node(pre["class_type"], "pose_preprocess", {"image": ["pose_gate", 0], **pre["inputs"]})
        prompt["pose_cn"] = _node("DiffControlNetLoader", "pose_cn", {"model": ["ckpt", 0], "control_net_name": CONTROLNET_NAME})
        prompt["pose_union"] = _node("SetUnionControlNetType", "pose_union", {"control_net": ["pose_cn", 0], "type": pre["union_type"]})
        prompt["pose_apply"] = _node("ControlNetApplyAdvanced", "pose_apply", {
            "positive": ["positive", 0], "negative": ["negative", 0], "control_net": ["pose_union", 0],
            "image": ["pose_preprocess", 0], "strength": DEFAULT_STRENGTH["pose"],
            "start_percent": 0.0, "end_percent": 1.0, "vae": ["ckpt", 2],
        })
        prompt["pose_preview"] = _node("PreviewImage", "pose_preview", {"images": ["pose_preprocess", 0]})
        prompt["sampler"]["inputs"]["positive"] = ["pose_apply", 0]
        prompt["sampler"]["inputs"]["negative"] = ["pose_apply", 1]
        slots.update(pose_strength="pose_apply.inputs.strength", pose_type="pose_union.inputs.type",
                     pose_preprocessor="pose_preprocess")

    # Inpaint: white in the mask image is the area to change.
    if "mask" in roles:
        prompt["mask_image"] = _node("LoadImage", "mask_image", {"image": REF_PLACEHOLDER})
        prompt["mask_channel"] = _node("ImageToMask", "mask_channel", {"image": ["mask_image", 0], "channel": "red"})
        prompt["latent_mask"] = _node("SetLatentNoiseMask", "latent_mask", {"samples": ["latent", 0], "mask": ["mask_channel", 0]})
        prompt["sampler"]["inputs"]["latent_image"] = ["latent_mask", 0]
        slots["mask_image"] = "mask_image.inputs.image"
    return prompt, slots


def node_map(api: dict | None = None) -> tuple[dict, dict[str, dict]]:
    api = api or api_workflow()
    templates, prompts = {}, {}
    for template_id in template_ids():
        prompt, slots = role_template(template_id, api)
        prompts[template_id] = prompt
        templates[template_id] = {
            "file": f"{MODEL_FAMILY}/{template_id}.api.json",
            "roles": sorted(template_roles(template_id)),
            "slots": slots,
        }
    mapping = {"family": MODEL_FAMILY, "pose_preprocessors": POSE_PREPROCESSORS, "templates": templates}
    return mapping, prompts


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    api = api_workflow()
    _write_json(WORKFLOWS / "furry_ja_api.json", api)
    _write_json(WORKFLOWS / "furry_ja.json", ui_workflow(api))
    mapping, prompts = node_map(api)
    for template_id, prompt in prompts.items():
        _write_json(WORKFLOWS / mapping["templates"][template_id]["file"], prompt)
    _write_json(WORKFLOWS / "maps" / f"{MODEL_FAMILY}.json", mapping)
    print("wrote furry_ja_api.json, furry_ja.json,", len(prompts), f"templates and maps/{MODEL_FAMILY}.json under", WORKFLOWS)


if __name__ == "__main__":
    main()
