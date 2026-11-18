"""Size, length and machine settings of a video run (src/furry_agent/video_plan.py)."""

import math

import pytest

from furry_agent.video_plan import (
    UNTILED,
    VideoRequestError,
    VideoSettings,
    clip_seconds,
    decode_tiles,
    frame_count,
    plan_video,
    requested_seconds,
    video_options,
    video_size,
)

WAN = {"steps": 4, "split_step": 2, "cfg": 1.0, "shift": 5.0, "sampler_name": "euler", "scheduler": "simple",
       "fps": 16, "max_pixels": 832 * 480, "frames": 81, "prompt_prefix": "", "negative": "静止"}


@pytest.mark.parametrize("size, budget, expected", [
    ((832, 1216), 480 * 640, (448, 656)),  # the image tab's portrait
    ((1216, 832), 480 * 640, (656, 448)),
    ((1024, 1024), 480 * 640, (544, 544)),
    ((256, 256), 480 * 640, (544, 544)),  # small images are scaled up to the budget
    ((4000, 500), 480 * 640, (960, 320)),  # wider than 3:1 is cropped to 3:1
    ((1216, 832), 832 * 480, (752, 512)),
])
def test_video_size_keeps_the_aspect_within_the_budget(size, budget, expected):
    width, height = video_size(*size, budget)
    assert (width, height) == expected
    assert width % 16 == 0 and height % 16 == 0 and width * height <= budget


def test_video_size_refuses_an_empty_image():
    with pytest.raises(VideoRequestError):
        video_size(0, 100, 480 * 640)


@pytest.mark.parametrize("seconds, frames", [(3, 49), (5, 81), (2, 33), (0.1, 5), (3.1, 49)])
def test_frame_count_is_4k_plus_1(seconds, frames):
    assert frame_count(seconds, 16) == frames
    assert (frames - 1) % 4 == 0


def test_clip_seconds():
    assert clip_seconds(49, 16) == 3.06


@pytest.mark.parametrize("value, expected", [(None, None), ("", None), ("3", 3.0), (5, 5.0), (2.5, 2.5)])
def test_requested_seconds(value, expected):
    assert requested_seconds(value) == expected


@pytest.mark.parametrize("value", [True, -1, 0, "abc", math.nan, math.inf, [3]])
def test_requested_seconds_refuses_non_lengths(value):
    with pytest.raises(VideoRequestError):
        requested_seconds(value)


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("VIDEO_MAX_PIXELS", "200000")
    monkeypatch.setenv("VIDEO_SECONDS", "9")
    monkeypatch.setenv("VIDEO_MAX_SECONDS", "4")
    monkeypatch.setenv("VIDEO_VAE_TILE", "0")
    monkeypatch.setenv("VIDEO_VAE_TEMPORAL", "8")
    monkeypatch.setenv("VIDEO_TEXT_ENCODER_DEVICE", "CPU")
    monkeypatch.setenv("VIDEO_CRF", "23")
    settings = VideoSettings.from_env()
    assert settings.max_pixels == 200000
    assert settings.seconds == 4 and settings.max_seconds == 4  # the default length never exceeds the limit
    assert (settings.vae_tile, settings.vae_temporal, settings.te_device, settings.crf) == (0, 8, "cpu", 23.0)


def test_settings_defaults_without_env(monkeypatch):
    for name in ("VIDEO_MAX_PIXELS", "VIDEO_SECONDS", "VIDEO_MAX_SECONDS", "VIDEO_VAE_TILE", "VIDEO_VAE_TEMPORAL",
                 "VIDEO_TEXT_ENCODER_DEVICE", "VIDEO_CRF"):
        monkeypatch.delenv(name, raising=False)
    assert VideoSettings.from_env() == VideoSettings()


def test_settings_refuse_an_unknown_text_encoder_device(monkeypatch):
    monkeypatch.setenv("VIDEO_TEXT_ENCODER_DEVICE", "gpu")
    with pytest.raises(VideoRequestError, match="VIDEO_TEXT_ENCODER_DEVICE"):
        VideoSettings.from_env()


def test_video_options():
    assert video_options(VideoSettings(seconds=3, max_seconds=5)) == {
        "default_seconds": 3, "max_seconds": 5, "choices": [2.0, 3.0, 5.0]}
    assert video_options(VideoSettings(seconds=3, max_seconds=3))["choices"] == [2.0, 3.0]
    assert video_options(VideoSettings(seconds=4, max_seconds=4))["choices"] == [2.0, 3.0, 4.0]


def test_decode_tiles():
    assert decode_tiles(VideoSettings(vae_tile=256, vae_temporal=16)) == {
        "vae_tile": 256, "vae_overlap": 64, "vae_temporal": 16, "vae_temporal_overlap": 4}
    assert decode_tiles(VideoSettings(vae_tile=128, vae_temporal=0)) == {
        "vae_tile": 128, "vae_overlap": 32, "vae_temporal": UNTILED, "vae_temporal_overlap": 4}
    assert decode_tiles(VideoSettings(vae_tile=0))["vae_tile"] == UNTILED


def test_plan_video_for_this_machine():
    run = plan_video(WAN, (832, 1216), VideoSettings(), seed=42)
    assert (run["width"], run["height"], run["frames"], run["seconds"]) == (448, 656, 49, 3.06)
    assert (run["steps"], run["split_step"], run["cfg"], run["shift"]) == (4, 2, 1.0, 5.0)
    assert run["seed"] == 42 and run["notes"] == [] and run["fps"] == 16.0
    assert run["negative"] == "静止" and run["te_device"] == "default" and run["crf"] == 20.0


def test_plan_video_caps_the_length():
    run = plan_video(WAN, (832, 1216), VideoSettings(max_seconds=5), seconds=10)
    assert run["frames"] == 81 and "上限の 5 秒" in run["notes"][0]
    long_model = {**WAN, "frames": 49}
    run = plan_video(long_model, (832, 1216), VideoSettings(max_seconds=10), seconds=8)
    assert run["frames"] == 49 and "49 フレーム" in run["notes"][0]


def test_plan_video_uses_the_model_budget_when_the_machine_sets_none():
    run = plan_video(WAN, (1216, 832), VideoSettings(max_pixels=0))
    assert (run["width"], run["height"]) == (752, 512)


def test_plan_video_draws_a_seed():
    assert isinstance(plan_video(WAN, (512, 512), VideoSettings())["seed"], int)
