import pytest

from furry_agent.config import Settings
from furry_agent.families import (
    CHROMA_HD,
    SDXL,
    FamilyError,
    canonical_family,
    check_roles,
    choose_family,
    looks_like_tag_list,
)
from furry_agent.templates import load_map

CHROMA_MAP = load_map(CHROMA_HD)
SDXL_MAP = load_map(SDXL)


@pytest.mark.parametrize("name, expected", [
    ("chroma", CHROMA_HD), ("Chroma_HD", CHROMA_HD), ("chroma-hd", CHROMA_HD), ("Chroma1-HD", CHROMA_HD),
    ("ｃｈｒｏｍａ", CHROMA_HD), ("illustrious", SDXL), ("SDXL", SDXL), ("yiffinhell", SDXL), ("flux", "flux"),
    ("", None), (None, None),
])
def test_canonical_family(name, expected):
    assert canonical_family(name) == expected


@pytest.mark.parametrize("text, family, rest", [
    ("/model chroma 夕方の港", CHROMA_HD, "夕方の港"),
    ("/MODEL chroma_hd\n夕方の港", CHROMA_HD, "夕方の港"),
    ("chroma で夕方の港", CHROMA_HD, "夕方の港"),
    ("Chroma HD で、夕方の港", CHROMA_HD, "夕方の港"),
    ("クロマで夕方の港", CHROMA_HD, "夕方の港"),
    ("/model sdxl 夕方の港", SDXL, "夕方の港"),
    ("夕方の港", SDXL, "夕方の港"),
    # Vague wording never switches the family (§5.2 rule 4), and "chroma" mid-sentence is just text.
    ("リアルにして", SDXL, "リアルにして"),
    ("chromatic aberration の効いた港", SDXL, "chromatic aberration の効いた港"),
])
def test_choose_family_from_message(text, family, rest):
    choice = choose_family(text, SDXL)
    assert (choice.family, choice.text) == (family, rest)


def test_configurable_beats_message_and_default():
    choice = choose_family("/model sdxl 港", SDXL, requested="chroma")
    assert (choice.family, choice.text, choice.source) == (CHROMA_HD, "港", "configurable")
    assert choose_family("港", "chroma").family == CHROMA_HD
    assert choose_family("港", CHROMA_HD).source == "default"


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


def test_settings_family_specific_values(monkeypatch):
    monkeypatch.setenv("COMFY_MODEL_FAMILY", "chroma")
    monkeypatch.setenv("CKPT_NAME", "chroma_v10HD.safetensors")
    monkeypatch.setenv("LORAS", "sdxl_style:0.8")
    monkeypatch.setenv("CHROMA_LORAS", "")
    monkeypatch.setenv("CHROMA_VAE", "flux_ae.safetensors")
    monkeypatch.delenv("CHROMA_HD_ENABLED", raising=False)
    settings = Settings.from_env()
    assert settings.model_family == CHROMA_HD and settings.chroma_enabled
    assert settings.ckpt_for(CHROMA_HD) == "chroma_v10HD.safetensors"
    assert settings.ckpt_for(SDXL) is None  # /model sdxl uses the template's yiffInHell
    assert settings.loras_for(CHROMA_HD) == "" and settings.loras_for(SDXL) == "sdxl_style:0.8"
    assert settings.model_overrides(CHROMA_HD) == {"vae_name": "flux_ae.safetensors"}
    assert settings.model_overrides(SDXL) == {}


def test_settings_default_family_and_rollback_switch(monkeypatch):
    monkeypatch.delenv("COMFY_MODEL_FAMILY", raising=False)
    monkeypatch.setenv("CHROMA_HD_ENABLED", "0")
    settings = Settings.from_env()
    assert settings.model_family == SDXL and not settings.chroma_enabled
