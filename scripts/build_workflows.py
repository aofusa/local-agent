"""Generate workflows/furry_ja_api.json (API format), workflows/furry_ja.json (UI format), and the
multi-image role templates workflows/sdxl/<template_id>.api.json with their node map workflows/maps/sdxl.json.

Node IDs follow design doc §4.1. The system prompts are embedded from prompts/,
so re-run this script after editing a prompt:

    uv run python scripts/build_workflows.py

LLM_MODEL / LLM_URL environment variables set the router's model and URL (scripts/setup-llm writes them). The
checkpoint names in the templates are only defaults: each run injects its image model's file
(config/host_models.json).
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

# The llama.cpp router (scripts/start-llm.ps1) and the model name of its preset (scripts/setup-llm.ps1 writes both
# to .env and regenerates the workflows when they differ).
LLM_URL = os.environ.get("LLM_URL") or "http://127.0.0.1:8080/v1"
LLM_MODEL = os.environ.get("LLM_MODEL") or "qwen3.8-27b-abliterated"
CKPT_NAME = "yiffInHell_yihVANTABLACK.safetensors"
REF_PLACEHOLDER = "furry_ja_ref.png"
REF_JOIN_DELIMITER = "\n\n[Reference image tags]\n"
VISION_USER_PROMPT = "Tag the reference image(s)."
QUALITY_PREFIX = "masterpiece, best quality, amazing quality, absurdres"
DEFAULT_NEGATIVE = (
    "worst quality, low quality, normal quality, lowres, blurry, jpeg artifacts, "
    "bad anatomy, bad hands, extra digits, missing fingers, deformed, "
    "watermark, signature, text, logo"
)


def _backend() -> dict:
    """LM Connect's backend node used as a plain OpenAI-compatible client of the router. Its own auto-eject speaks
    LM Studio's REST API, so it stays off: the `eject` node (FurryJaEjectLLM) unloads the model."""
    return {
        "base_url": LLM_URL,
        "model": LLM_MODEL,
        "connect_timeout_seconds": 10,
        "read_timeout_seconds": 600,
        "stream": True,
        "max_retries": 0,
        "health_check": True,
        "auto_eject_after_run": False,
        "disable_thinking": True,
        "debug_logging": True,
    }


def api_workflow() -> dict:
    """Full graph. The text-only run removes the reference nodes (see src/.../workflow.py)."""
    system_tags = (PROMPTS / "system_furry_tags.txt").read_text(encoding="utf-8").strip()
    system_vision = (PROMPTS / "system_vision_caption.txt").read_text(encoding="utf-8").strip()

    nodes = {
        # The vision call and the tag call share the loaded 27B; `eject` unloads it after the tag call (design §4.1).
        "llm_backend": ("LMConnectLMStudioBackend", "LLM Backend (llama.cpp router)", _backend()),
        "llm_backend_vision": ("LMConnectLMStudioBackend", "LLM Backend (vision)", _backend()),
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
            "base_url": LLM_URL,
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
            "base_url": LLM_URL,
            "model": "",
            "temperature": 0.4,
            "max_tokens": 320,
        }),
        "eject": ("FurryJaEjectLLM", "eject", {
            "passthrough": ["prompt_node", 0],
            "base_url": LLM_URL,
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
            "llm_base_url": LLM_URL,
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
    "FurryJaEjectLLM": ([("passthrough", "*")], ["base_url", "model", "debug_logging"], [("*", "*")]),
    "FurryJaSplitTags": ([("text", "*")], ["quality_prefix", "default_negative"], [("positive", "STRING"), ("negative", "STRING")]),
    "FurryJaCheckpointLoaderAfterEject": ([("after", "*")], ["ckpt_name", "llm_base_url"], [("MODEL", "MODEL"), ("CLIP", "CLIP"), ("VAE", "VAE")]),
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
# Slots every family has: the sampler (sdxl keeps its template values unless the catalog entry sets them) and the
# split node's quality prefix / default negative.
SAMPLER_SLOTS = {"steps": "sampler.inputs.steps", "cfg": "sampler.inputs.cfg",
                 "sampler_name": "sampler.inputs.sampler_name", "scheduler": "sampler.inputs.scheduler"}
QUALITY_SLOTS = {"quality_prefix": "split.inputs.quality_prefix", "negative": "split.inputs.default_negative"}
CONTROLNET_NAME = "controlnet-union-sdxl-1.0-promax.safetensors"
IPADAPTER_PRESET = "PLUS (high strength)"
# style: IP-Adapter "style transfer" only feeds the style blocks, so the subject is not copied.
# character: linear; the pose comes from ControlNet / the text, not from this image.
IPADAPTER_WEIGHT_TYPE = {"character": "linear", "style": "style transfer"}
# IP-Adapter weight = role strength x scale. Measured on yiffInHell (Illustrious): character at weight 0.85
# burned colors; 0.4 kept the identity cleanly. Style transfer stayed clean at 0.55-0.8.
IPADAPTER_WEIGHT_SCALE = {"character": 0.5, "style": 1.0}
ROLE_SECTION = {
    "base": "\n\n[Base image tags]\n",
    "character": "\n\n[Character reference tags]\n",
    "pose": "\n\n[Pose reference tags]\n",
    "style": "\n\n[Style reference tags]\n",
}
# bbox_detector None: the whole image is one figure (the YOLOX person detector often misses furry
# characters). The ONNX pose estimator runs on the CPU through OpenCV when onnxruntime is absent:
# the TorchScript models crashed intermittently on the ROCm (Windows) GPU with corrupt tensor shapes.
DWPOSE_INPUTS = {
    "detect_hand": "enable", "detect_body": "enable", "detect_face": "enable", "resolution": 1024,
    "bbox_detector": "None", "pose_estimator": "dw-ll_ucoco_384.onnx",
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
    slots.update(SAMPLER_SLOTS)
    slots.update(QUALITY_SLOTS)

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
                "weight": round(DEFAULT_STRENGTH[role] * IPADAPTER_WEIGHT_SCALE[role], 3),
                "weight_type": IPADAPTER_WEIGHT_TYPE[role],
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

    # Free the text encoder / CLIP-Vision before KSampler loads SDXL (+ ControlNet).
    if {"character", "style", "pose"} & roles:
        sampler = prompt["sampler"]["inputs"]
        prompt["release"] = _node("FurryJaReleaseEncoders", "release", {
            "model": sampler["model"], "positive": sampler["positive"], "negative": sampler["negative"],
        })
        sampler.update(model=["release", 0], positive=["release", 1], negative=["release", 2])

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
    mapping = {"family": MODEL_FAMILY, "label": "SDXL", "prompt_style": "danbooru", "pose_preprocessors": POSE_PREPROCESSORS,
               "ipadapter_weight_scale": IPADAPTER_WEIGHT_SCALE, "templates": templates}
    return mapping, prompts


# --- Diffusion-transformer families: Chroma1-HD (flux), Krea 2 (krea2), Anima (anima) ------------------------
#
# Chroma follows the official ComfyUI_Chroma1-HD_T2I-workflow.json (workflows/reference/): CLIPLoader type chroma,
# T5TokenizerOptions, ModelSamplingAuraFlow shift 1.0, euler + beta, Flux VAE, EmptySD3LatentImage. The official
# SamplerCustomAdvanced chain is replaced by KSampler (WI §2.3) so `sampler` keeps its id, seed slot and denoise.
# Krea 2 and Anima follow ComfyUI's blueprints "Text to Image (Krea-2 Turbo)" / "Text to Image (Anima)":
# UNETLoader + CLIPLoader (krea2 / stable_diffusion) + qwen_image_vae, EmptyLatentImage, KSampler.
# The LLM part is the same as sdxl (prompt_node -> eject -> split -> ckpt); `ckpt` is FurryJaDiffusionLoaderAfterEject
# (diffusion model + text encoder + VAE after the eject), so every node id stays the same. Only the system prompt,
# the encoders and the sampler defaults differ; the model's own values come from config/host_models.json.

CHROMA_FAMILY = "flux"
CHROMA_UNET = "chroma_v10HD.safetensors"
CHROMA_MODELS = {
    "unet_name": CHROMA_UNET,
    # The 17.8 GB BF16 file is cast to fp8 at load: ~8.9 GB, which fits next to the T5 on a 24 GB UMA machine.
    "weight_dtype": "fp8_e4m3fn",
    "clip_name": "t5xxl_fp8_e4m3fn.safetensors",
    "clip_type": "chroma",
    "vae_name": "ae.safetensors",
}
CHROMA_DEFAULTS = {
    "width": 1024, "height": 1024, "steps": 28, "cfg": 3.5, "sampler_name": "euler", "scheduler": "beta",
    # Sizes are multiples of 64 (also of 16), at most ~1 MP: 1024x1024, 832x1216, 1216x832.
    "size_min": 512, "size_max": 1216, "max_pixels": 1024 * 1024,
}
# Comparison presets (WI §5.4); not used by default.
CHROMA_PRESETS = {"quality": {"steps": 40, "cfg": 3.0}, "speed": {"steps": 26, "cfg": 3.8}}
CHROMA_NEGATIVE = "low quality, ugly, unfinished, out of focus, deformed, blurry, smudged, flat colors"
ANIMA_QUALITY = "masterpiece, best quality, very aesthetic, score_8, furry"
ANIMA_NEGATIVE = ("worst quality, low quality, score_1, score_2, score_3, blurry, jpeg artifacts, sepia, "
                  "human, bad anatomy, bad hands, watermark, signature, text")

DIT_FAMILIES = {
    CHROMA_FAMILY: {
        "label": "Chroma1-HD",
        "system": "system_chroma_prose.txt", "prompt_style": "prose", "split_style": "prose",
        "quality_prefix": "", "negative": CHROMA_NEGATIVE, "temperature": 0.3, "max_tokens": 400,
        "models": CHROMA_MODELS, "defaults": CHROMA_DEFAULTS, "presets": CHROMA_PRESETS,
        "t5": True, "sampling": ("ModelSamplingAuraFlow", "model_sampling (shift 1.0)", {"shift": 1.0}),
        "latent": "EmptySD3LatentImage", "save_prefix": "furry_ja/chroma",
        "model_setup_hint": "scripts\\setup-comfyui-chroma.ps1 を実行するか、.env の CHROMA_* を確認してください",
    },
    # Krea 2 (Wulver v0.5 is a turbo finetune): 8 steps, CFG 1 (the negative does nothing), euler / simple,
    # shift 1.15 is ComfyUI's default for the model, Qwen3-VL-4B text encoder (CLIPLoader type krea2).
    "krea2": {
        "label": "Krea 2",
        "system": "system_krea2_prose.txt", "prompt_style": "prose", "split_style": "prose",
        "quality_prefix": "", "negative": "low quality, blurry, deformed", "temperature": 0.3, "max_tokens": 480,
        "models": {"unet_name": "wulverKrea2_v05_fp8.safetensors", "weight_dtype": "default",
                   "clip_name": "qwen3vl_4b_fp8_scaled.safetensors", "clip_type": "krea2",
                   "vae_name": "qwen_image_vae.safetensors"},
        "defaults": {"width": 1024, "height": 1024, "steps": 8, "cfg": 1.0, "sampler_name": "euler",
                     "scheduler": "simple", "size_min": 512, "size_max": 1536, "max_pixels": 1024 * 1024},
        "t5": False, "sampling": None, "latent": "EmptyLatentImage", "save_prefix": "furry_ja/krea2",
        "model_setup_hint": "scripts の setup-image-models を実行して Krea 2 のテキストエンコーダと VAE を入れてください",
    },
    # Anima (Cosmos-Predict2 2B, Qwen3 0.6B text encoder): Danbooru tags plus natural language, score tags,
    # er_sde / simple, CFG 4, ~1 MP.
    "anima": {
        "label": "Anima",
        "system": "system_furry_tags.txt", "prompt_style": "danbooru", "split_style": "tags",
        "quality_prefix": ANIMA_QUALITY, "negative": ANIMA_NEGATIVE, "temperature": 0.4, "max_tokens": 320,
        "models": {"unet_name": "indigoFurryMixAnima_v10.safetensors", "weight_dtype": "default",
                   "clip_name": "qwen_3_06b_base.safetensors", "clip_type": "stable_diffusion",
                   "vae_name": "qwen_image_vae.safetensors"},
        "defaults": {"width": 832, "height": 1216, "steps": 28, "cfg": 4.0, "sampler_name": "er_sde",
                     "scheduler": "simple", "size_min": 512, "size_max": 1536, "max_pixels": 1536 * 1536},
        "t5": False, "sampling": None, "latent": "EmptyLatentImage", "save_prefix": "furry_ja/anima",
        "model_setup_hint": "scripts の setup-image-models を実行して Anima のテキストエンコーダと VAE を入れてください",
    },
}


def dit_template(family: str, template_id: str) -> tuple[dict, dict]:
    """Return (API prompt, slot map) for a diffusion-transformer family's t2i_basic / i2i_basic."""
    spec = DIT_FAMILIES[family]
    system = (PROMPTS / spec["system"]).read_text(encoding="utf-8").strip()
    system_vision = (PROMPTS / "system_vision_caption.txt").read_text(encoding="utf-8").strip()
    d = spec["defaults"]
    clip = ["t5_options", 0] if spec["t5"] else ["ckpt", 1]
    nodes = {
        "llm_backend": ("LMConnectLMStudioBackend", "LLM Backend (llama.cpp router)", _backend()),
        "user_prompt": ("PrimitiveStringMultiline", "user_prompt (日本語指示)", {"value": "夕方の神戸港を背景に、青い鱗のケモノのお兄さんが振り返っている"}),
        "prompt_node": ("LMConnectPromptWithSystem", "prompt_node", {
            "system_prompt": system, "prompt": ["user_prompt", 0], "backend": ["llm_backend", 0],
            "base_url": LLM_URL, "model": "", "temperature": spec["temperature"], "max_tokens": spec["max_tokens"],
        }),
        "eject": ("FurryJaEjectLLM", "eject", {
            "passthrough": ["prompt_node", 0], "base_url": LLM_URL, "model": "", "debug_logging": True,
        }),
        "split": ("FurryJaSplitTags", "split", {
            "text": ["eject", 0], "quality_prefix": spec["quality_prefix"], "default_negative": spec["negative"],
            "prompt_style": spec["split_style"],
        }),
        "ckpt": ("FurryJaDiffusionLoaderAfterEject", "ckpt", {
            **spec["models"], "after": ["eject", 0], "llm_base_url": LLM_URL,
        }),
    }
    if spec["t5"]:
        # Official: "min_padding 1 is the official way".
        nodes["t5_options"] = ("T5TokenizerOptions", "t5_options", {"clip": ["ckpt", 1], "min_padding": 1, "min_length": 0})
    nodes["positive"] = ("CLIPTextEncode", "positive", {"text": ["split", 0], "clip": clip})
    nodes["negative"] = ("CLIPTextEncode", "negative", {"text": ["split", 1], "clip": clip})
    model = ["ckpt", 0]
    if spec["sampling"]:
        cls, title, inputs = spec["sampling"]
        nodes["model_sampling"] = (cls, title, {"model": ["ckpt", 0], **inputs})
        model = ["model_sampling", 0]
    nodes.update({
        # Drop the text encoder before the diffusion model samples.
        "release": ("FurryJaReleaseEncoders", "release", {
            "model": model, "positive": ["positive", 0], "negative": ["negative", 0],
        }),
        "latent": (spec["latent"], "latent", {"width": d["width"], "height": d["height"], "batch_size": 1}),
        "sampler": ("KSampler", "sampler", {
            "model": ["release", 0], "seed": 0, "steps": d["steps"], "cfg": d["cfg"],
            "sampler_name": d["sampler_name"], "scheduler": d["scheduler"],
            "positive": ["release", 1], "negative": ["release", 2], "latent_image": ["latent", 0], "denoise": 1.0,
        }),
        "decode": ("VAEDecode", "decode", {"samples": ["sampler", 0], "vae": ["ckpt", 2]}),
        "save": ("SaveImage", "save", {"images": ["decode", 0], "filename_prefix": spec["save_prefix"]}),
    })
    prompt = {node_id: _node(cls, title, inputs) for node_id, (cls, title, inputs) in nodes.items()}
    slots = {
        "prompt": "user_prompt.inputs.value", "seed": "sampler.inputs.seed", "ckpt_name": "ckpt.inputs.unet_name",
        "weight_dtype": "ckpt.inputs.weight_dtype", "clip_name": "ckpt.inputs.clip_name",
        "vae_name": "ckpt.inputs.vae_name", "steps": "sampler.inputs.steps", "cfg": "sampler.inputs.cfg",
        "sampler_name": "sampler.inputs.sampler_name", "scheduler": "sampler.inputs.scheduler",
    }
    slots.update(QUALITY_SLOTS)
    if template_id == "t2i_basic":
        slots.update(width="latent.inputs.width", height="latent.inputs.height")
        return prompt, slots
    if template_id != "i2i_basic":
        raise ValueError(f"{family} has no template {template_id}")
    # img2img: the base image is captioned (tags) for the LLM, which rewrites everything in the family's style.
    prompt.update({
        "llm_backend_vision": _node("LMConnectLMStudioBackend", "LLM Backend (vision)", _backend()),
        "ref_image": _node("LoadImage", "ref_image", {"image": REF_PLACEHOLDER}),
        "vision": _node("LMConnectVision", "vision", {
            "system_prompt": system_vision, "prompt": VISION_USER_PROMPT, "image_1": ["ref_image", 0],
            "max_image_dimension": 768, "backend": ["llm_backend_vision", 0], "base_url": LLM_URL,
            "model": "", "temperature": 0.2, "max_tokens": 200,
        }),
        "prompt_join": _node("StringConcatenate", "prompt_join (指示 + 参照タグ)", {
            "string_a": ["user_prompt", 0], "string_b": ["vision", 0], "delimiter": REF_JOIN_DELIMITER,
        }),
        "ref_scale": _node("ImageScaleToTotalPixels", "ref_scale (img2img)", {
            "image": ["ref_image", 0], "upscale_method": "lanczos", "megapixels": 1.0, "resolution_steps": 64,
        }),
        "latent": _node("VAEEncode", "latent (img2img)", {"pixels": ["ref_scale", 0], "vae": ["ckpt", 2]}),
    })
    prompt["prompt_node"]["inputs"]["prompt"] = ["prompt_join", 0]
    prompt["sampler"]["inputs"]["denoise"] = default_denoise({"base"})
    slots.update(base_image="ref_image.inputs.image", denoise="sampler.inputs.denoise")
    return prompt, slots


def chroma_template(template_id: str) -> tuple[dict, dict]:
    return dit_template(CHROMA_FAMILY, template_id)


def dit_map(family: str) -> tuple[dict, dict[str, dict]]:
    spec = DIT_FAMILIES[family]
    templates, prompts = {}, {}
    for template_id in ("t2i_basic", "i2i_basic"):
        prompt, slots = dit_template(family, template_id)
        prompts[template_id] = prompt
        templates[template_id] = {"file": f"{family}/{template_id}.api.json",
                                  "roles": sorted(template_roles(template_id)), "slots": slots}
    mapping = {
        "family": family,
        "label": spec["label"],
        # Model files and sampler defaults live here, not in code (WI §4). The catalog entry
        # (config/host_models.json) names the diffusion model and its own sampler values.
        "models": spec["models"],
        "defaults": spec["defaults"],
        **({"presets": spec["presets"]} if spec.get("presets") else {}),
        # Only the base image (img2img) is supported. ControlNet / IP-Adapter are not verified on these models.
        "supported_roles": ["base"],
        "pose_enabled": False,
        "pose_fallback": "refuse",
        "pose_fallback_denoise": 0.65,
        "node_setup_hint": "ComfyUI を再起動して furry_ja ノード（FurryJaDiffusionLoaderAfterEject）を読み込んでください",
        "model_setup_hint": spec["model_setup_hint"],
        "templates": templates,
    }
    mapping["prompt_style"] = spec["prompt_style"]
    return mapping, prompts


def chroma_map() -> tuple[dict, dict[str, dict]]:
    return dit_map(CHROMA_FAMILY)


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
    for family in DIT_FAMILIES:
        mapping, prompts = dit_map(family)
        for template_id, prompt in prompts.items():
            _write_json(WORKFLOWS / mapping["templates"][template_id]["file"], prompt)
        _write_json(WORKFLOWS / "maps" / f"{family}.json", mapping)
        print("wrote", len(prompts), f"templates and maps/{family}.json")


if __name__ == "__main__":
    main()
