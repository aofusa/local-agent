"""The furry_ja video nodes (comfyui_nodes/furry_ja/nodes.py) with ComfyUI's modules stubbed: loaders check the LLM
router before loading, GGUF experts go through ComfyUI-GGUF, and the release node frees finished models."""

import importlib
import logging
import sys
import types

import pytest

GB = 1024 ** 3


class _Patcher:
    def __init__(self, inner, size=3 * GB):
        self.model = inner
        self._size = size

    def model_size(self):
        return self._size


class _Loaded:
    def __init__(self, patcher):
        self.model = patcher


@pytest.fixture
def comfy(monkeypatch):
    calls = []
    lists = {"text_encoders": ["umt5_xxl_fp8_e4m3fn_scaled.safetensors"], "vae": ["wan_2.1_vae.safetensors"],
             "diffusion_models": ["expert.safetensors"], "unet_gguf": ["high.gguf", "low.gguf"], "checkpoints": []}

    def get_filename_list(name):
        if name not in lists:
            raise KeyError(name)
        return list(lists[name])

    folder_paths = types.ModuleType("folder_paths")
    folder_paths.get_filename_list = get_filename_list
    folder_paths.get_full_path = lambda *a: None
    folder_paths.get_full_path_or_raise = lambda *a: "x"

    class UnetLoaderGGUF:
        def load_unet(self, name):
            calls.append(("gguf", name))
            return (_Patcher(object()),)

    class UNETLoader:
        def load_unet(self, name, weight_dtype):
            calls.append(("unet", name, weight_dtype))
            return (_Patcher(object()),)

    class CLIPLoader:
        def load_clip(self, name, type="stable_diffusion", device="default"):
            calls.append(("clip", name, type, device))
            return ("CLIP",)

    class VAELoader:
        def load_vae(self, name):
            calls.append(("vae", name))
            return ("VAE",)

    nodes = types.ModuleType("nodes")
    nodes.CheckpointLoaderSimple = type("CheckpointLoaderSimple", (), {})
    nodes.UNETLoader, nodes.CLIPLoader, nodes.VAELoader = UNETLoader, CLIPLoader, VAELoader
    nodes.NODE_CLASS_MAPPINGS = {"UnetLoaderGGUF": UnetLoaderGGUF}

    model_base = types.ModuleType("comfy.model_base")
    model_base.BaseModel = type("BaseModel", (), {})
    mm = types.ModuleType("comfy.model_management")
    mm.current_loaded_models = []

    def unload_all_models():
        calls.append(("unload_all",))
        mm.current_loaded_models.clear()

    mm.unload_all_models = unload_all_models
    mm.cleanup_models_gc = lambda: calls.append(("cleanup_gc",))
    mm.soft_empty_cache = lambda: calls.append(("empty_cache",))
    mm.get_torch_device = lambda: "cuda:0"
    mm.get_free_memory = lambda device: 8 * GB
    comfy_pkg = types.ModuleType("comfy")
    comfy_pkg.model_management, comfy_pkg.model_base = mm, model_base
    psutil = types.ModuleType("psutil")
    psutil.virtual_memory = lambda: types.SimpleNamespace(available=6 * GB)

    for name, module in {"folder_paths": folder_paths, "nodes": nodes, "comfy": comfy_pkg, "comfy.model_base": model_base,
                         "comfy.model_management": mm, "psutil": psutil}.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.delitem(sys.modules, "furry_ja.nodes", raising=False)
    module = importlib.import_module("furry_ja.nodes")

    def ensure_unloaded(url):
        calls.append(("router", url))
        return {"forced_unload": [], "verified_unloaded": True}

    monkeypatch.setattr(module, "ensure_unloaded", ensure_unloaded)
    yield types.SimpleNamespace(module=module, calls=calls, mm=mm, BaseModel=model_base.BaseModel, lists=lists,
                                mappings=nodes.NODE_CLASS_MAPPINGS)
    sys.modules.pop("furry_ja.nodes", None)


def test_registered(comfy):
    for name in ("FurryJaTextEncoderAfter", "FurryJaVAELoaderAfter", "FurryJaVideoModelAfter", "FurryJaReleaseAfter"):
        assert name in comfy.module.NODE_CLASS_MAPPINGS and name in comfy.module.NODE_DISPLAY_NAME_MAPPINGS


def test_video_model_names_list_gguf_and_safetensors(comfy):
    names = comfy.module.FurryJaVideoModelAfter.INPUT_TYPES()["required"]["unet_name"][0]
    assert names == ["high.gguf", "low.gguf", "expert.safetensors"]
    del comfy.lists["unet_gguf"]  # ComfyUI-GGUF missing: only safetensors
    assert comfy.module.FurryJaVideoModelAfter.INPUT_TYPES()["required"]["unet_name"][0] == ["expert.safetensors"]


def test_gguf_expert_loads_through_comfyui_gguf_after_the_router_check(comfy):
    out = comfy.module.FurryJaVideoModelAfter().load_after("high.gguf", "default", after="latent")
    assert comfy.calls == [("router", "http://127.0.0.1:8080/v1"), ("gguf", "high.gguf")]
    assert out["ui"]["unet_name"] == ["high.gguf"] and out["ui"]["size_gb"] == [3.0]
    assert out["ui"]["llm_unloaded"] == [True]


def test_safetensors_expert_uses_unet_loader(comfy):
    comfy.module.FurryJaVideoModelAfter().load_after("expert.safetensors", "fp8_e4m3fn", after="latent")
    assert comfy.calls[-1] == ("unet", "expert.safetensors", "fp8_e4m3fn")


def test_gguf_without_comfyui_gguf_is_refused(comfy):
    comfy.mappings.clear()
    with pytest.raises(RuntimeError, match="setup-video-models"):
        comfy.module.FurryJaVideoModelAfter().load_after("high.gguf", "default", after="latent")


def test_text_encoder_checks_the_router_first(comfy):
    out = comfy.module.FurryJaTextEncoderAfter().load_after("umt5_xxl_fp8_e4m3fn_scaled.safetensors", "wan", "cpu",
                                                            after="json")
    assert comfy.calls == [("router", "http://127.0.0.1:8080/v1"),
                           ("clip", "umt5_xxl_fp8_e4m3fn_scaled.safetensors", "wan", "cpu")]
    assert out["result"] == ("CLIP",)


def test_vae_loader_waits_for_after(comfy):
    assert comfy.module.FurryJaVAELoaderAfter().load_after("wan_2.1_vae.safetensors", after="cond") == ("VAE",)


def test_release_passes_values_through_and_frees_models(comfy):
    vae = _Patcher(type("WanVAE", (), {})())
    comfy.mm.current_loaded_models[:] = [_Loaded(None), _Loaded(vae)]  # a collected patcher and the live VAE
    out = comfy.module.FurryJaReleaseAfter().release("cond+", "cond-", stage="te")
    assert out["result"] == ("cond+", "cond-")
    assert [c[0] for c in comfy.calls] == ["cleanup_gc", "unload_all", "empty_cache"]
    assert out["ui"]["alive_before"] == ["WanVAE"] and out["ui"]["diffusion_before"] == []
    assert out["ui"]["alive_after"] == []
    assert out["ui"]["free_vram_gb"] == [8.0] and out["ui"]["free_ram_gb"] == [6.0]
    assert out["ui"]["stage"] == ["te"]


def test_release_reports_a_diffusion_model_still_referenced(comfy, caplog):
    expert = _Patcher(type("WAN22", (comfy.BaseModel,), {})())
    comfy.mm.current_loaded_models[:] = [_Loaded(expert)]
    with caplog.at_level(logging.WARNING, logger="furry_ja"):
        out = comfy.module.FurryJaReleaseAfter().release("latent", stage="high")
    assert out["ui"]["diffusion_before"] == ["WAN22"]
    assert out["result"] == ("latent", None)
    assert "--cache-none" in caplog.text
