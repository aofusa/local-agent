import logging

import folder_paths
import nodes as comfy_nodes

from .llm_state import ensure_unloaded
from .model_files import fp8_sibling
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
            },
            # "prose" for Chroma1-HD: keep English sentences, drop weight syntax and quality buzzwords.
            "optional": {"prompt_style": (["tags", "prose"], {"default": "tags"})},
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("positive", "negative")
    FUNCTION = "split"
    CATEGORY = "furry_ja"

    def split(self, text, quality_prefix=QUALITY_PREFIX, default_negative=DEFAULT_NEGATIVE, prompt_style="tags"):
        result = split_tags(text, quality_prefix=quality_prefix, default_negative=default_negative,
                            prompt_style=prompt_style)
        mode = "json" if result.parsed else "fallback"
        log.info("[furry_ja] split mode=%s positive=%s", mode, result.positive)
        log.info("[furry_ja] split negative=%s", result.negative)
        return {
            "ui": {"positive": [result.positive], "negative": [result.negative], "split_mode": [mode]},
            "result": (result.positive, result.negative),
        }


class FurryJaEjectLLM:
    """`eject`: unload the LLM from the llama.cpp router once the prompt node has returned, and verify it is gone.

    ``passthrough`` is the LLM's reply; returning it only after the unload keeps everything downstream (split, ckpt)
    behind the eject. The router stops the model's child process, so its memory is free for the checkpoint.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {"passthrough": ("*",)},
            "optional": {
                "base_url": ("STRING", {"default": "http://127.0.0.1:8080/v1"}),
                "model": ("STRING", {"default": ""}),
                "debug_logging": ("BOOLEAN", {"default": False}),
            },
        }

    RETURN_TYPES = ("*",)
    FUNCTION = "passthrough_eject"
    CATEGORY = "furry_ja"

    def passthrough_eject(self, passthrough, base_url="http://127.0.0.1:8080/v1", model="", debug_logging=False):
        report = ensure_unloaded(base_url)
        log.info("[furry_ja] eject: LLM router unloaded %s (verified=%s)", report["forced_unload"],
                 report["verified_unloaded"])
        return (passthrough,)


class FurryJaCheckpointLoaderAfterEject(comfy_nodes.CheckpointLoaderSimple):
    """`ckpt`: CheckpointLoaderSimple that waits for the eject output.

    The `after` link makes ComfyUI run this only after the LLM has returned and
    been ejected. Before loading, it verifies through the LLM router's API that no
    LLM is resident (and unloads one if the eject was skipped).
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "ckpt_name": (folder_paths.get_filename_list("checkpoints"),),
                "after": ("*",),
                "llm_base_url": ("STRING", {"default": "http://127.0.0.1:8080/v1"}),
            }
        }

    FUNCTION = "load_after_eject"
    CATEGORY = "furry_ja"

    def load_after_eject(self, ckpt_name, after, llm_base_url="http://127.0.0.1:8080/v1"):
        report = ensure_unloaded(llm_base_url)
        log.info(
            "[furry_ja] LLM router verified unloaded before checkpoint/KSampler: forced_unload=%s",
            report["forced_unload"],
        )
        model, clip, vae = self.load_checkpoint(ckpt_name)
        return {
            "ui": {
                "llm_unloaded": [True],
                "forced_unload": list(report["forced_unload"]),
                "ckpt_name": [ckpt_name],
            },
            "result": (model, clip, vae),
        }


WEIGHT_DTYPES = ["default", "fp8_e4m3fn", "fp8_e4m3fn_fast", "fp8_e5m2"]
def _diffusion_model_names():
    """Diffusion-model-only files may sit in diffusion_models (UNETLoader) or checkpoints (Civitai downloads)."""
    names = folder_paths.get_filename_list("diffusion_models") + folder_paths.get_filename_list("checkpoints")
    return list(dict.fromkeys(names))


class FurryJaDiffusionLoaderAfterEject:
    """`ckpt` for the Chroma1-HD family: diffusion model + text encoder + VAE, loaded after the LLM eject.

    Same gate as FurryJaCheckpointLoaderAfterEject (MODEL, CLIP, VAE outputs, `after` link, the LLM router check),
    so the node ids, the LoRA insertion and the unload verification stay the same as the SDXL templates.
    The parts are the ones UNETLoader, CLIPLoader and VAELoader would load.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "unet_name": (_diffusion_model_names(),),
                "weight_dtype": (WEIGHT_DTYPES, {"default": "default"}),
                "clip_name": (folder_paths.get_filename_list("text_encoders"),),
                "clip_type": ("STRING", {"default": "chroma"}),
                "vae_name": (folder_paths.get_filename_list("vae"),),
                "after": ("*",),
                "llm_base_url": ("STRING", {"default": "http://127.0.0.1:8080/v1"}),
            }
        }

    RETURN_TYPES = ("MODEL", "CLIP", "VAE")
    FUNCTION = "load_after_eject"
    CATEGORY = "furry_ja"

    def load_after_eject(self, unet_name, weight_dtype, clip_name, clip_type, vae_name, after,
                         llm_base_url="http://127.0.0.1:8080/v1"):
        import comfy.sd
        import torch

        report = ensure_unloaded(llm_base_url)
        log.info(
            "[furry_ja] LLM router verified unloaded before diffusion model/KSampler: forced_unload=%s",
            report["forced_unload"],
        )
        path = (folder_paths.get_full_path("diffusion_models", unet_name)
                or folder_paths.get_full_path_or_raise("checkpoints", unet_name))
        # scripts/setup-comfyui-chroma.ps1 writes <name>_fp8_e4m3fn.safetensors next to the models: casting the
        # 17.8 GB BF16 file at load time needs the whole BF16 state dict in RAM first.
        if weight_dtype.startswith("fp8_e4m3fn"):
            converted = fp8_sibling(unet_name)
            converted_path = folder_paths.get_full_path("diffusion_models", converted)
            if converted_path:
                log.info("[furry_ja] using pre-converted %s for %s", converted, unet_name)
                path = converted_path
        options = {}
        if weight_dtype == "fp8_e4m3fn":
            options["dtype"] = torch.float8_e4m3fn
        elif weight_dtype == "fp8_e4m3fn_fast":
            options["dtype"] = torch.float8_e4m3fn
            options["fp8_optimizations"] = True
        elif weight_dtype == "fp8_e5m2":
            options["dtype"] = torch.float8_e5m2
        model = comfy.sd.load_diffusion_model(path, model_options=options)
        (clip,) = comfy_nodes.CLIPLoader().load_clip(clip_name, type=clip_type)
        (vae,) = comfy_nodes.VAELoader().load_vae(vae_name)
        log.info("[furry_ja] loaded %s (%s) + %s (%s) + %s", unet_name, weight_dtype, clip_name, clip_type, vae_name)
        return {
            "ui": {
                "llm_unloaded": [True],
                "forced_unload": list(report["forced_unload"]),
                "ckpt_name": [unet_name],
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
    "FurryJaEjectLLM": FurryJaEjectLLM,
    "FurryJaCheckpointLoaderAfterEject": FurryJaCheckpointLoaderAfterEject,
    "FurryJaDiffusionLoaderAfterEject": FurryJaDiffusionLoaderAfterEject,
    "FurryJaImageAfter": FurryJaImageAfter,
    "FurryJaReleaseEncoders": FurryJaReleaseEncoders,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "FurryJaSplitTags": "furry_ja: Split Tags JSON",
    "FurryJaEjectLLM": "furry_ja: Eject LLM (llama.cpp router)",
    "FurryJaCheckpointLoaderAfterEject": "furry_ja: Load Checkpoint (after LLM eject)",
    "FurryJaDiffusionLoaderAfterEject": "furry_ja: Load Diffusion Model + T5 + VAE (after LLM eject)",
    "FurryJaImageAfter": "furry_ja: Image (after)",
    "FurryJaReleaseEncoders": "furry_ja: Release encoders before sampling",
}
