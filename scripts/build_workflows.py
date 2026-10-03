"""Generate workflows/furry_ja_api.json (API format) and workflows/furry_ja.json (UI format).

Node IDs follow design doc §4.1. The system prompts are embedded from prompts/,
so re-run this script after editing a prompt:

    uv run python scripts/build_workflows.py
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROMPTS = ROOT / "prompts"
WORKFLOWS = ROOT / "workflows"

LMSTUDIO_URL = "http://127.0.0.1:1234/v1"
LMSTUDIO_MODEL = "huihui-qwen3.8-27b-abliterated@iq3_m"
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


def main() -> None:
    WORKFLOWS.mkdir(exist_ok=True)
    api = api_workflow()
    (WORKFLOWS / "furry_ja_api.json").write_text(json.dumps(api, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (WORKFLOWS / "furry_ja.json").write_text(json.dumps(ui_workflow(api), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("wrote", WORKFLOWS / "furry_ja_api.json", "and", WORKFLOWS / "furry_ja.json")


if __name__ == "__main__":
    main()
