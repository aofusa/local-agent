import pytest

from furry_agent.config import Settings
from furry_agent.families import (
    FLUX,
    SDXL,
    FamilyError,
    canonical_family,
    check_roles,
    looks_like_tag_list,
)
from furry_agent.templates import load_map

CHROMA_MAP = load_map(FLUX)
SDXL_MAP = load_map(SDXL)


@pytest.mark.parametrize("name, expected", [
    ("flux", FLUX), ("FLUX", FLUX), ("ｆｌｕｘ", FLUX), ("illustrious", SDXL), ("SDXL", SDXL), ("yiffinhell", SDXL),
    # chroma / chroma_hd are not family names (the setting value is flux); they pass through and fail to load.
    ("chroma", "chroma"), ("chroma_hd", "chroma_hd"),
    ("", None), (None, None),
])
def test_canonical_family(name, expected):
    assert canonical_family(name) == expected


def test_sdxl_accepts_every_role():
    roles = {"img_1": "pose", "img_2": "style", "img_3": "character"}
    assert check_roles(SDXL_MAP, roles) == (roles, [], None)


def test_chroma_accepts_base_only():
    assert check_roles(CHROMA_MAP, {}) == ({}, [], None)
    assert check_roles(CHROMA_MAP, {"img_1": "base"}) == ({"img_1": "base"}, [], None)


@pytest.mark.parametrize("roles, word", [
    ({"img_1": "pose"}, "ポーズ"),
    ({"img_1": "style"}, "画風参照"),
    ({"img_1": "character"}, "キャラクター参照"),
    ({"img_1": "base", "img_2": "mask"}, "マスク"),
])
def test_chroma_refuses_unsupported_references(roles, word):
    with pytest.raises(FamilyError, match=word):
        check_roles(CHROMA_MAP, roles)


def test_chroma_pose_img2img_fallback_when_configured():
    family_map = {**CHROMA_MAP, "pose_fallback": "img2img"}
    roles, notes, denoise = check_roles(family_map, {"img_1": "pose"})
    assert roles == {"img_1": "base"} and denoise == 0.65
    assert "厳密一致ではありません" in notes[0]


def test_chroma_map_keeps_pose_disabled_and_refusing():
    # WI §2.4 / §5.5: the Flux ControlNet experiment was not run, so pose stays off and refuses.
    assert CHROMA_MAP["pose_enabled"] is False
    assert CHROMA_MAP["pose_fallback"] == "refuse"


@pytest.mark.parametrize("text, expected", [
    ("masterpiece, best quality, 1boy, anthro, dragon, blue scales, harbor, sunset, looking back, smile", True),
    ("An anthropomorphic dragon man with blue scales looks back over his shoulder on the Kobe harbor at dusk. "
     "Warm low sunlight catches the scales, with cranes, ships, and gulls behind him. Clean illustrated style.", False),
    ("a fox, a cat", False),
])
def test_looks_like_tag_list(text, expected):
    assert looks_like_tag_list(text) is expected


def test_settings_ignore_the_old_model_switches(monkeypatch):
    # The image model comes from config/host_models.json; COMFY_MODEL_FAMILY / CKPT_NAME / LORAS are not read.
    monkeypatch.setenv("COMFY_MODEL_FAMILY", "flux")
    monkeypatch.setenv("CKPT_NAME", "chroma_v10HD.safetensors")
    monkeypatch.setenv("LORAS", "sdxl_style:0.8")
    monkeypatch.setenv("CHROMA_UNET_NAME", "other.safetensors")
    monkeypatch.setenv("CHROMA_VAE", "flux_ae.safetensors")
    settings = Settings.from_env()
    assert not hasattr(settings, "model_family") and not hasattr(settings, "ckpt_name")
    assert settings.model_overrides(FLUX) == {"vae_name": "flux_ae.safetensors"}
    assert settings.model_overrides(SDXL) == {}


def test_chroma_speed_overrides(monkeypatch):
    monkeypatch.setenv("CHROMA_MAX_PIXELS", "589824")
    monkeypatch.setenv("CHROMA_STEPS", "24")
    settings = Settings.from_env()
    merged = settings.plan_defaults(FLUX, CHROMA_MAP["defaults"])
    assert merged["max_pixels"] == 589824 and merged["steps"] == 24 and merged["cfg"] == 3.5
    assert settings.plan_defaults(SDXL, None) == {}


@pytest.mark.parametrize("family", ["sdxl", "flux", "krea2", "anima"])
def test_every_family_has_maps_and_style(family):
    mapping = load_map(family)
    assert mapping["prompt_style"] in ("danbooru", "prose")
    assert {"t2i_basic", "i2i_basic"} <= set(mapping["templates"])
    for entry in mapping["templates"].values():
        assert {"steps", "cfg", "sampler_name", "scheduler", "quality_prefix", "negative"} <= set(entry["slots"])


def test_new_families_refuse_pose_with_the_model_name():
    with pytest.raises(FamilyError, match="Wulver"):
        check_roles(load_map("krea2"), {"img_1": "pose"}, "Wulver (Krea 2)")
    assert check_roles(load_map("anima"), {"img_1": "base"}) == ({"img_1": "base"}, [], None)
