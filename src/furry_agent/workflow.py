"""Load workflows/furry_ja_api.json and adapt it for one run.

Only the inputs that design doc §4.1 lets the caller rewrite are touched:
the Japanese instruction, reference image filenames, the seed, and (for
img2img) the latent side. The checkpoint name can be overridden by CKPT_NAME.
Reference nodes are removed ("bypassed") when there is no reference image.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

FIXED_NODE_IDS = (
    "llm_backend", "user_prompt", "ref_image", "vision", "prompt_node", "eject", "split",
    "ckpt", "positive", "negative", "latent", "sampler", "decode", "save",
)
REFERENCE_NODES = ("ref_image", "ref_image_2", "vision", "prompt_join", "ref_scale", "llm_backend_vision")
TXT2IMG_DENOISE = 1.0
IMG2IMG_DENOISE = 0.45
MAX_REFERENCE_IMAGES = 2
DEFAULT_WORKFLOW = Path(__file__).resolve().parents[2] / "workflows" / "furry_ja_api.json"


def load_workflow(path: Path | str = DEFAULT_WORKFLOW) -> dict:
    workflow = json.loads(Path(path).read_text(encoding="utf-8"))
    missing = [n for n in FIXED_NODE_IDS if n not in workflow]
    if missing:
        raise ValueError(f"workflow is missing fixed node ids: {missing}")
    return workflow


def build_prompt(
    workflow: dict,
    user_text: str,
    seed: int,
    ref_filenames: list[str] | None = None,
    ckpt_name: str | None = None,
) -> dict:
    """Return a new API prompt for this run; ``workflow`` is not modified."""
    refs = list(ref_filenames or [])
    if len(refs) > MAX_REFERENCE_IMAGES:
        raise ValueError(f"at most {MAX_REFERENCE_IMAGES} reference images are supported")

    prompt = copy.deepcopy(workflow)
    prompt["user_prompt"]["inputs"]["value"] = user_text
    prompt["sampler"]["inputs"]["seed"] = int(seed)
    if ckpt_name:
        prompt["ckpt"]["inputs"]["ckpt_name"] = ckpt_name

    if not refs:
        # Text only: no Load Image / Vision, Empty Latent, denoise 1.0.
        for node_id in REFERENCE_NODES:
            prompt.pop(node_id, None)
        prompt["prompt_node"]["inputs"]["prompt"] = ["user_prompt", 0]
        prompt["sampler"]["inputs"]["denoise"] = TXT2IMG_DENOISE
        return prompt

    prompt["ref_image"]["inputs"]["image"] = refs[0]
    if len(refs) == 2:
        prompt["ref_image_2"]["inputs"]["image"] = refs[1]
    else:
        prompt.pop("ref_image_2", None)
        prompt["vision"]["inputs"].pop("image_2", None)

    # img2img: the first reference keeps identity through VAE Encode.
    prompt["latent"] = {
        "class_type": "VAEEncode",
        "inputs": {"pixels": ["ref_scale", 0], "vae": ["ckpt", 2]},
        "_meta": {"title": "latent (img2img)"},
    }
    prompt["sampler"]["inputs"]["denoise"] = IMG2IMG_DENOISE
    return prompt
