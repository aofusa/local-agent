import logging

import folder_paths
import nodes as comfy_nodes

from .lmstudio_state import ensure_unloaded
from .tag_split import DEFAULT_NEGATIVE, QUALITY_PREFIX, split_tags

log = logging.getLogger("furry_ja")


class FurryJaSplitTags:
    """`split`: parse {"positive","negative"} JSON from the LLM; never retries."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "text": ("*",),
                "quality_prefix": ("STRING", {"default": QUALITY_PREFIX}),
                "default_negative": ("STRING", {"multiline": True, "default": DEFAULT_NEGATIVE}),
            }
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("positive", "negative")
    FUNCTION = "split"
    CATEGORY = "furry_ja"

    def split(self, text, quality_prefix=QUALITY_PREFIX, default_negative=DEFAULT_NEGATIVE):
        result = split_tags(text, quality_prefix=quality_prefix, default_negative=default_negative)
        mode = "json" if result.parsed else "fallback"
        log.info("[furry_ja] split mode=%s positive=%s", mode, result.positive)
        log.info("[furry_ja] split negative=%s", result.negative)
        return {
            "ui": {"positive": [result.positive], "negative": [result.negative], "split_mode": [mode]},
            "result": (result.positive, result.negative),
        }


class FurryJaCheckpointLoaderAfterEject(comfy_nodes.CheckpointLoaderSimple):
    """`ckpt`: CheckpointLoaderSimple that waits for the eject output.

    The `after` link makes ComfyUI run this only after the LLM has returned and
    been ejected. Before loading, it verifies through LM Studio's API that no
    LLM is resident (and unloads one if the eject was skipped).
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "ckpt_name": (folder_paths.get_filename_list("checkpoints"),),
                "after": ("*",),
                "lmstudio_base_url": ("STRING", {"default": "http://127.0.0.1:1234/v1"}),
            }
        }

    FUNCTION = "load_after_eject"
    CATEGORY = "furry_ja"

    def load_after_eject(self, ckpt_name, after, lmstudio_base_url="http://127.0.0.1:1234/v1"):
        report = ensure_unloaded(lmstudio_base_url)
        log.info(
            "[furry_ja] LM Studio verified unloaded before checkpoint/KSampler: forced_unload=%s",
            report["forced_unload"],
        )
        model, clip, vae = self.load_checkpoint(ckpt_name)
        return {
            "ui": {
                "lmstudio_unloaded": [True],
                "forced_unload": list(report["forced_unload"]),
                "ckpt_name": [ckpt_name],
            },
            "result": (model, clip, vae),
        }


class FurryJaImageAfter:
    """Pass an image through only after ``after`` has run.

    Preprocessors (DWPose, depth) have no model input, so ComfyUI could run them while the
    27B is still loaded. Linking ``after`` to the checkpoint output keeps them behind the eject.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"image": ("IMAGE",), "after": ("*",)}}

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "passthrough"
    CATEGORY = "furry_ja"

    def passthrough(self, image, after):
        return (image,)


class FurryJaReleaseEncoders:
    """Unload the text encoder / CLIP-Vision once their outputs exist, right before KSampler.

    Taking the model and both conditionings as inputs makes ComfyUI run this after the prompts and
    IP-Adapter embeddings are computed. On a ~10 GB UMA budget, keeping the encoders resident next to
    SDXL + ControlNet forced a full offload of the UNet, which produced corrupt images on ROCm.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"model": ("MODEL",), "positive": ("CONDITIONING",), "negative": ("CONDITIONING",)}}

    RETURN_TYPES = ("MODEL", "CONDITIONING", "CONDITIONING")
    RETURN_NAMES = ("model", "positive", "negative")
    FUNCTION = "release"
    CATEGORY = "furry_ja"

    def release(self, model, positive, negative):
        import comfy.model_management as mm

        mm.unload_all_models()
        mm.soft_empty_cache()
        log.info("[furry_ja] encoders released before sampling")
        return (model, positive, negative)


NODE_CLASS_MAPPINGS = {
    "FurryJaSplitTags": FurryJaSplitTags,
    "FurryJaCheckpointLoaderAfterEject": FurryJaCheckpointLoaderAfterEject,
    "FurryJaImageAfter": FurryJaImageAfter,
    "FurryJaReleaseEncoders": FurryJaReleaseEncoders,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "FurryJaSplitTags": "furry_ja: Split Tags JSON",
    "FurryJaCheckpointLoaderAfterEject": "furry_ja: Load Checkpoint (after LLM eject)",
    "FurryJaImageAfter": "furry_ja: Image (after)",
    "FurryJaReleaseEncoders": "furry_ja: Release encoders before sampling",
}
