import copy
import json
from pathlib import Path

import pytest

from furry_agent import model_catalog as mc
from furry_agent.templates import load_map

ROOT = Path(__file__).resolve().parent.parent
RAW = json.loads((ROOT / "config" / "host_models.json").read_text(encoding="utf-8"))
MAPS = {family: load_map(family) for family in ("sdxl", "flux", "krea2", "anima")}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("DEFAULT_INFERENCE_MODEL", "DEFAULT_IMAGE_MODEL", "HOST_MODELS_DISABLE", "HOST_MODELS_PATH"):
        monkeypatch.delenv(name, raising=False)


def _raw(**changes):
    raw = copy.deepcopy(RAW)
    for path, value in changes.items():
        kind, index, key = path.split("__")
        raw[kind][int(index)][key] = value
    return raw


def test_shipped_catalog_lists_the_requested_models():
    catalog = mc.load_catalog()
    assert list(catalog.inference) == ["qwen3.8-27b-abliterated", "bonsai-2-27b-abliterated",
                                       "remote-llamacpp", "openai-compatible"]
    assert [m.id for m in catalog.inference.values() if not m.remote] == list(catalog.inference)[:2]
    files = {m.ckpt for m in catalog.image.values()}
    assert files == {
        "yiffInHell_yihVANTABLACK.safetensors", "yiffInHell_yihMETLLICTETR.safetensors",
        "yiffInHell_yihxxxTENDEDV20.safetensors", "indigoFurryMixXL_cknoobEPS11.safetensors",
        "indigoFurryMixAnima_v10.safetensors",
        "chroma_v10HD.safetensors", "wulverKrea2_v05_fp8.safetensors", "rekemono_v100.safetensors"}
    # Every SDXL model uses the LoRA that LORAS had before the catalog.
    for model in catalog.image.values():
        if model.family == "sdxl":
            assert model.lora_text == ["novabeast xl v1 rank64 pony.safetensors:1"]
        else:
            assert model.loras == ()
    assert catalog.inference["bonsai-2-27b-abliterated"].thinking == "off"


def test_defaults_are_the_first_entries_or_the_env(monkeypatch):
    catalog = mc.load_catalog()
    assert mc.default_ids(catalog) == {"inference": "qwen3.8-27b-abliterated", "image": "yiffinhell-vantablack"}
    monkeypatch.setenv("DEFAULT_IMAGE_MODEL", "wulver")
    assert mc.resolve_image(None, catalog).id == "wulver"
    assert mc.resolve_image("", catalog).id == "wulver"
    assert mc.resolve_image("chroma-hd", catalog).family == "flux"


def test_unknown_and_partial_ids_are_refused():
    catalog = mc.load_catalog()
    for value in ("qwen", "Qwen 3.8 27B abliterated", "QWEN3.8-27B-ABLITERATED"):
        with pytest.raises(mc.ModelChoiceError) as info:
            mc.resolve_inference(value, catalog)
        assert info.value.code == "unknown_model"
    with pytest.raises(mc.ModelChoiceError) as info:
        mc.resolve_image(3, catalog)
    assert info.value.code == "bad_request"


def test_disabled_and_unavailable_ids(monkeypatch):
    catalog = mc.load_catalog()
    monkeypatch.setenv("HOST_MODELS_DISABLE", "wulver, rekemono")
    with pytest.raises(mc.ModelChoiceError) as info:
        mc.resolve_image("wulver", catalog)
    assert info.value.code == "model_unavailable"
    with pytest.raises(mc.ModelChoiceError, match="プリセット"):
        mc.resolve_inference("bonsai-2-27b-abliterated", catalog,
                             mc.inference_unavailable(catalog, ["qwen3.8-27b-abliterated"]))


@pytest.mark.parametrize("loras", [
    ["novabeast.safetensors"], ["a:0"], ["a:2.5"], ["a:-1"], ["a:x"], ["a:0.5:0.5"], "a:1", [3],
])
def test_bad_loras_are_refused_at_load(loras):
    with pytest.raises(mc.CatalogError):
        mc.parse_catalog(_raw(image__0__loras=loras))


def test_lora_weight_is_model_and_clip():
    catalog = mc.parse_catalog(_raw(image__0__loras=["KemonoStyleAV1:0.8", "b.safetensors:2"]))
    specs = catalog.image["yiffinhell-vantablack"].loras
    assert [(s.name, s.strength_model, s.strength_clip) for s in specs] == [
        ("KemonoStyleAV1", 0.8, 0.8), ("b.safetensors", 2.0, 2.0)]


@pytest.mark.parametrize("change", [
    {"image__0__id": "Bad Id"}, {"image__0__family": "sd15"}, {"image__0__prompt_style": "prose"},
    {"image__0__params": {"clip_skip": 2}}, {"image__0__params": {"steps": "many"}},
    {"inference__1__thinking": "maybe"}, {"inference__1__gguf": {"catalog": "lmstudio"}},
])
def test_malformed_entries_are_refused(change):
    with pytest.raises(mc.CatalogError):
        mc.parse_catalog(_raw(**change))


def test_duplicate_ids_are_refused():
    raw = copy.deepcopy(RAW)
    raw["image"].append(dict(raw["image"][0]))
    with pytest.raises(mc.CatalogError, match="重複"):
        mc.parse_catalog(raw)


def test_empty_ckpt_and_missing_files_are_unavailable():
    catalog = mc.parse_catalog(_raw(image__1__ckpt=""))
    offered = {
        "sdxl": {"ckpt_name": ["yiffInHell_yihVANTABLACK.safetensors"]},
        "flux": {"ckpt_name": ["chroma_v10HD.safetensors"], "clip_name": ["t5xxl_fp8_e4m3fn.safetensors"],
                 "vae_name": ["ae.safetensors"]},
        "krea2": {"ckpt_name": ["wulverKrea2_v05_fp8.safetensors"], "clip_name": [], "vae_name": []},
    }
    out = mc.image_unavailable(catalog, MAPS, offered, ["novabeast xl v1 rank64 pony.safetensors"])
    assert "yiffinhell-vantablack" not in out and "chroma-hd" not in out
    assert out["yiffinhell-metallictetra"] == "ckpt が空です"
    assert "rekemono_v100.safetensors" in out["rekemono"]
    assert "qwen3vl_4b_fp8_scaled.safetensors" in out["wulver"] and "qwen_image_vae.safetensors" in out["wulver"]
    # A missing LoRA makes the model unavailable too (no run without its LoRA).
    out = mc.image_unavailable(catalog, MAPS, offered, [])
    assert "novabeast" in out["yiffinhell-vantablack"]
    # ComfyUI not reachable: nothing is refused for files (the run checks again before it queues).
    assert mc.image_unavailable(catalog, MAPS, None, None) == {"yiffinhell-metallictetra": "ckpt が空です"}


def test_env_ckpt_and_loras_are_not_read(monkeypatch):
    monkeypatch.setenv("CKPT_NAME", "other.safetensors")
    monkeypatch.setenv("LORAS", "other_lora:0.5")
    monkeypatch.setenv("COMFY_MODEL_FAMILY", "flux")
    model = mc.resolve_image(None, mc.load_catalog())
    assert model.ckpt == "yiffInHell_yihVANTABLACK.safetensors" and model.family == "sdxl"
    assert model.lora_text == ["novabeast xl v1 rank64 pony.safetensors:1"]


def test_inference_availability_falls_back_to_files(tmp_path, monkeypatch):
    catalog = mc.load_catalog()
    monkeypatch.setenv("BONSAI_MODELS_DIR", str(tmp_path))
    out = mc.inference_unavailable(catalog, None)
    assert "bonsai-2-27b-abliterated" in out
    (tmp_path / "Ternary-Bonsai-2-27B-PTQ1_0-abliterated.gguf").write_bytes(b"x")
    assert "bonsai-2-27b-abliterated" not in mc.inference_unavailable(catalog, None)
    out = mc.inference_unavailable(catalog, ["qwen3.8-27b-abliterated", "bonsai-2-27b-abliterated"])
    assert set(out) == {"remote-llamacpp", "openai-compatible"}  # their URL is not in .env


def test_every_image_family_has_its_map():
    catalog = mc.load_catalog()
    assert {m.family for m in catalog.image.values()} <= set(MAPS)
    for model in catalog.image.values():
        assert MAPS[model.family]["prompt_style"] == model.prompt_style
