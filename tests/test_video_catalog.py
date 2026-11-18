"""Video entries of config/host_models.json (docs/image-to-video-design.md §6.3): parsing, the per-expert LoRAs in
the image format, defaults, availability and the setup's file list."""

import copy
import importlib.util
import json
from pathlib import Path

import pytest

from furry_agent import model_catalog as mc
from furry_agent.templates import load_map

ROOT = Path(__file__).resolve().parent.parent
RAW = json.loads((ROOT / "config" / "host_models.json").read_text(encoding="utf-8"))
DISTILL_HIGH = "wan2.2_i2v_A14b_high_noise_lora_rank64_lightx2v_4step_1022.safetensors"
DISTILL_LOW = "wan2.2_i2v_A14b_low_noise_lora_rank64_lightx2v_4step_1022.safetensors"
FURRY_HIGH = "Furry Enhancer Wan2.2 V3 High Noise I2V.safetensors"
FURRY_LOW = "Furry Enhancer Wan2.2 V3 Low Noise I2V .safetensors"
MAPS = {"wan22": load_map("wan22")}


@pytest.fixture(autouse=True)
def env(monkeypatch):
    for name in ("DEFAULT_VIDEO_MODEL", "HOST_MODELS_DISABLE"):
        monkeypatch.delenv(name, raising=False)


def _entry(**changes):
    entry = copy.deepcopy(next(v for v in RAW["video"] if v["id"] == "wan22-i2v-a14b-q3"))
    entry.update(changes)
    return entry


def _parse(*entries):
    return mc.parse_catalog({"inference": [], "image": [], "video": list(entries)})


def test_bundled_video_entries():
    catalog = mc.load_catalog()
    assert list(catalog.video) == ["wan22-i2v-a14b-q3", "wan22-i2v-a14b-q4"]  # Q3_K_M first: the default here
    for model in catalog.video.values():
        assert model.family == "wan22"
        assert model.experts["high"].endswith(".gguf") and model.experts["low"].endswith(".gguf")
        # The distill LoRA first, then the kemono LoRA, on each expert.
        assert [s.name for s in model.loras_for("high")] == [DISTILL_HIGH, FURRY_HIGH]
        assert [s.name for s in model.loras_for("low")] == [DISTILL_LOW, FURRY_LOW]
        assert model.params["steps"] == 4 and model.params["split_step"] == 2 and model.params["fps"] == 16.0
    assert catalog.video["wan22-i2v-a14b-q3"].experts["high"] == "Wan2.2-I2V-A14B-HighNoise-Q3_K_M.gguf"


def test_lora_text_names_the_expert():
    model = mc.load_catalog().video["wan22-i2v-a14b-q3"]
    assert model.lora_text == [f"high: {DISTILL_HIGH}:1", f"high: {FURRY_HIGH}:1", f"low: {DISTILL_LOW}:1",
                               f"low: {FURRY_LOW}:1"]


def test_empty_loras_mean_no_kemono_lora():
    model = _parse(_entry(loras={"high": [], "low": []})).video["wan22-i2v-a14b-q3"]
    assert [s.name for s in model.loras_for("high")] == [DISTILL_HIGH]
    without = _parse({k: v for k, v in _entry().items() if k != "loras"}).video["wan22-i2v-a14b-q3"]
    assert [s.name for s in without.loras_for("low")] == [DISTILL_LOW]


def test_one_expert_only():
    model = _parse(_entry(loras={"low": [f"{FURRY_LOW}:0.8"]})).video["wan22-i2v-a14b-q3"]
    assert [s.name for s in model.loras_for("high")] == [DISTILL_HIGH]
    assert model.loras_for("low")[-1].strength_model == 0.8


@pytest.mark.parametrize("changes, message", [
    ({"unknown": 1}, "unknown"),
    ({"family": "ltx"}, "family"),
    ({"experts": {"high": "a.gguf"}}, "experts"),
    ({"experts": {"high": "a.gguf", "low": ""}}, "experts"),
    ({"loras": [f"{FURRY_LOW}:1.0"]}, "high"),  # not split by expert
    ({"loras": {"low": [FURRY_LOW]}}, "<ファイル>:<強度>"),  # no weight
    ({"loras": {"low": [f"{FURRY_LOW}:3"]}}, "2 以下"),
    ({"loras": {"middle": []}}, "high"),
    ({"models": {"unet_name": "x"}}, "models"),
    ({"params": {"steps": 4, "split_step": 4}}, "split_step"),
    ({"params": {"steps": 1}}, "steps"),
    ({"params": {"frames": 80}}, r"4k\+1"),
    ({"params": {"fps": 0}}, "fps"),
    ({"params": {"denoise": 0.5}}, "denoise"),
])
def test_malformed_entries_are_refused(changes, message):
    with pytest.raises(mc.CatalogError, match=message):
        _parse(_entry(**changes))


def test_duplicate_id_is_refused():
    with pytest.raises(mc.CatalogError, match="重複"):
        _parse(_entry(), _entry())


def test_default_and_resolve(monkeypatch):
    catalog = mc.load_catalog()
    assert mc.default_ids(catalog)["video"] == "wan22-i2v-a14b-q3"
    monkeypatch.setenv("DEFAULT_VIDEO_MODEL", "wan22-i2v-a14b-q4")
    assert mc.resolve_video(None, catalog).id == "wan22-i2v-a14b-q4"
    assert mc.resolve_video("wan22-i2v-a14b-q3", catalog).id == "wan22-i2v-a14b-q3"
    with pytest.raises(mc.ModelChoiceError, match="動画モデル"):
        mc.resolve_video("wan22", catalog)
    with pytest.raises(mc.ModelChoiceError, match="使えません"):
        mc.resolve_video("wan22-i2v-a14b-q4", catalog, {"wan22-i2v-a14b-q4": "ComfyUI にファイルがありません"})


def _offered(*names):
    files = ["Wan2.2-I2V-A14B-HighNoise-Q3_K_M.gguf", "Wan2.2-I2V-A14B-LowNoise-Q3_K_M.gguf", *names]
    return {"wan22": {"high_name": files, "low_name": files,
                      "clip_name": ["umt5_xxl_fp8_e4m3fn_scaled.safetensors"],
                      "vae_name": ["wan_2.1_vae.safetensors"]}}


LORAS = [DISTILL_HIGH, DISTILL_LOW, FURRY_HIGH, FURRY_LOW]
NODES = {"FurryJaTextEncoderAfter", "FurryJaVAELoaderAfter", "FurryJaVideoModelAfter", "FurryJaReleaseAfter",
         "UnetLoaderGGUF"}


def test_availability():
    catalog = mc.load_catalog()
    out = mc.video_unavailable(catalog, MAPS, _offered(), LORAS, NODES)
    assert "wan22-i2v-a14b-q3" not in out
    assert "Wan2.2-I2V-A14B-HighNoise-Q4_K_M.gguf" in out["wan22-i2v-a14b-q4"]


def test_availability_without_comfyui_refuses_nothing():
    assert mc.video_unavailable(mc.load_catalog(), MAPS, None, None, None) == {}


def test_missing_comfyui_gguf_or_furry_ja_nodes():
    out = mc.video_unavailable(mc.load_catalog(), MAPS, _offered(), LORAS, NODES - {"UnetLoaderGGUF"})
    assert "UnetLoaderGGUF" in out["wan22-i2v-a14b-q3"] and "setup-video-models" in out["wan22-i2v-a14b-q3"]


def test_missing_kemono_lora_makes_the_model_unusable():
    out = mc.video_unavailable(mc.load_catalog(), MAPS, _offered(), LORAS[:3], NODES)
    assert "Furry Enhancer Wan2.2 V3 Low Noise I2V" in out["wan22-i2v-a14b-q3"]


def test_disabled(monkeypatch):
    monkeypatch.setenv("HOST_MODELS_DISABLE", "wan22-i2v-a14b-q3")
    out = mc.video_unavailable(mc.load_catalog(), MAPS, _offered(), LORAS, NODES)
    assert "HOST_MODELS_DISABLE" in out["wan22-i2v-a14b-q3"]


def _host_models():
    spec = importlib.util.spec_from_file_location("host_models", ROOT / "scripts" / "host_models.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_setup_fetches_the_default_model_and_imports_the_kemono_loras(monkeypatch):
    monkeypatch.setenv("DEFAULT_VIDEO_MODEL", "wan22-i2v-a14b-q3")
    items = _host_models().video_wanted(None)
    downloads = {i["download"]["name"]: i["download"] for i in items if "download" in i}
    imports = {i["name"]: i for i in items if "download" not in i}
    assert set(downloads) == {"Wan2.2-I2V-A14B-HighNoise-Q3_K_M.gguf", "Wan2.2-I2V-A14B-LowNoise-Q3_K_M.gguf",
                              "umt5_xxl_fp8_e4m3fn_scaled.safetensors", "wan_2.1_vae.safetensors", DISTILL_HIGH,
                              DISTILL_LOW}
    assert all(len(d["sha256"]) == 64 and d["size"] > 0 for d in downloads.values())
    assert downloads[DISTILL_LOW]["folder"] == "loras"
    assert set(imports) == {FURRY_HIGH, FURRY_LOW} and imports[FURRY_LOW]["folders"] == ["loras"]


def test_setup_refuses_an_unknown_id():
    with pytest.raises(SystemExit, match="wan22"):
        _host_models().video_wanted({"wan22"})
