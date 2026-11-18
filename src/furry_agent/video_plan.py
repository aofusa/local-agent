"""Size, length and machine settings of a video run (docs/image-to-video-design.md §6.2, §7).

The catalog entry (config/host_models.json "video") carries the model's own values: fps, steps, its pixel budget
(480p for Wan 2.2 A14B) and the longest clip it handles well (81 frames). .env carries what this machine can afford
(VIDEO_*): a smaller pixel budget, the default and the longest length, the VAE tiling, where the text encoder runs and
the H.264 quality. Everything here is a pure function except ``VideoSettings.from_env``.
"""

from __future__ import annotations

import math
import os
import random
from dataclasses import dataclass

from furry_agent.config import env_float, env_int

# The instruction used when the user sends only the image.
DEFAULT_INSTRUCTION = "画像の雰囲気に合う、自然で控えめな動き（耳や尾が少し動く）。カメラは固定"
# The lengths the UI offers (the ones above VIDEO_MAX_SECONDS are dropped).
SECOND_CHOICES = (2.0, 3.0, 5.0)
SIZE_MULTIPLE = 16
# Start images wider or taller than this are center-cropped to it by WanImageToVideo.
MAX_ASPECT = 3.0
TE_DEVICES = ("default", "cpu")
# No tiling: VAEDecodeTiled with tiles larger than any frame and than any clip decodes in one piece.
UNTILED = 4096

# Defaults for this machine (Radeon 890M, 9 GB visible to PyTorch; docs/image-to-video-design.md §11).
DEFAULT_MAX_PIXELS = 480 * 640  # an 832x1216 image gives 448x656
DEFAULT_SECONDS = 3.0
DEFAULT_MAX_SECONDS = 5.0
DEFAULT_VAE_TILE = 256
DEFAULT_VAE_TEMPORAL = 16
DEFAULT_CRF = 20.0


class VideoRequestError(ValueError):
    """The request cannot be turned into a run (shown to the user)."""


@dataclass(frozen=True)
class VideoSettings:
    max_pixels: int = DEFAULT_MAX_PIXELS  # 0: the model's own budget only
    seconds: float = DEFAULT_SECONDS
    max_seconds: float = DEFAULT_MAX_SECONDS
    vae_tile: int = DEFAULT_VAE_TILE  # 0: no spatial tiling
    vae_temporal: int = DEFAULT_VAE_TEMPORAL  # 0: no temporal tiling
    te_device: str = "default"
    crf: float = DEFAULT_CRF

    @classmethod
    def from_env(cls) -> "VideoSettings":
        device = os.environ.get("VIDEO_TEXT_ENCODER_DEVICE", "").strip().lower() or "default"
        if device not in TE_DEVICES:
            raise VideoRequestError(f"VIDEO_TEXT_ENCODER_DEVICE は {' / '.join(TE_DEVICES)} です（{device!r}）")
        max_seconds = env_float("VIDEO_MAX_SECONDS", DEFAULT_MAX_SECONDS, 0.5, 60.0)
        return cls(
            max_pixels=env_int("VIDEO_MAX_PIXELS", DEFAULT_MAX_PIXELS, 0, 4096 * 4096),
            seconds=min(env_float("VIDEO_SECONDS", DEFAULT_SECONDS, 0.5, 60.0), max_seconds),
            max_seconds=max_seconds,
            vae_tile=env_int("VIDEO_VAE_TILE", DEFAULT_VAE_TILE, 0, UNTILED),
            vae_temporal=env_int("VIDEO_VAE_TEMPORAL", DEFAULT_VAE_TEMPORAL, 0, UNTILED),
            te_device=device,
            crf=env_float("VIDEO_CRF", DEFAULT_CRF, 0.0, 51.0),
        )


def video_size(width: int, height: int, max_pixels: int, multiple: int = SIZE_MULTIPLE) -> tuple[int, int]:
    """The start image's aspect ratio (at most 3:1 either way), the largest size of multiples of 16 within
    ``max_pixels``. Small images are scaled up to the budget like large ones are scaled down."""
    if width <= 0 or height <= 0:
        raise VideoRequestError("開始画像の大きさを読めません")
    aspect = min(max(width / height, 1 / MAX_ASPECT), MAX_ASPECT)
    out_h = math.sqrt(max_pixels / aspect)
    out_w = out_h * aspect
    return (max(multiple, int(out_w // multiple) * multiple), max(multiple, int(out_h // multiple) * multiple))


def frame_count(seconds: float, fps: float) -> int:
    """``seconds`` x ``fps`` rounded to 4k+1 frames (Wan's VAE packs 4 frames per latent frame), at least 5."""
    return 4 * max(1, round((seconds * fps - 1) / 4)) + 1


def clip_seconds(frames: int, fps: float) -> float:
    return round(frames / fps, 2)


def requested_seconds(value) -> float | None:
    """configurable.video_seconds -> seconds (None: not given). Anything but a positive number is refused."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise VideoRequestError(f"動画の長さは秒数で指定してください（{value!r}）")
    try:
        seconds = float(value)
    except (TypeError, ValueError) as exc:
        raise VideoRequestError(f"動画の長さは秒数で指定してください（{value!r}）") from exc
    if not math.isfinite(seconds) or seconds <= 0:
        raise VideoRequestError(f"動画の長さは 0 より大きい秒数です（{value!r}）")
    return seconds


def video_options(settings: VideoSettings) -> dict:
    """What the UI offers for the length: the default, the limit and the choices within it."""
    choices = [s for s in SECOND_CHOICES if s <= settings.max_seconds]
    if settings.seconds not in choices:
        choices = sorted({*choices, settings.seconds})
    return {"default_seconds": settings.seconds, "max_seconds": settings.max_seconds, "choices": choices}


def decode_tiles(settings: VideoSettings) -> dict:
    """VAEDecodeTiled values: the tile and its overlap (a quarter, at most 64), the frames per piece and theirs."""
    tile = settings.vae_tile or UNTILED
    temporal = settings.vae_temporal or UNTILED
    return {"vae_tile": tile, "vae_overlap": min(64, tile // 4), "vae_temporal": temporal,
            "vae_temporal_overlap": 4 if temporal >= 8 else max(1, temporal // 2)}


def plan_video(params: dict, image_size: tuple[int, int], settings: VideoSettings, seconds: float | None = None,
               seed: int | None = None) -> dict:
    """The run's values. ``params`` is the family map's defaults with the catalog entry's params on top."""
    fps = float(params["fps"])
    max_pixels = int(params["max_pixels"])
    if settings.max_pixels:
        max_pixels = min(max_pixels, settings.max_pixels)
    width, height = video_size(*image_size, max_pixels)
    notes = []
    wanted = settings.seconds if seconds is None else seconds
    if wanted > settings.max_seconds:
        notes.append(f"長さは上限の {settings.max_seconds:g} 秒にしました（VIDEO_MAX_SECONDS）")
        wanted = settings.max_seconds
    frames = frame_count(wanted, fps)
    model_frames = int(params.get("frames") or frames)
    if frames > model_frames:
        notes.append(f"このモデルの上限 {model_frames} フレーム（約 {clip_seconds(model_frames, fps):g} 秒）にしました")
        frames = model_frames
    return {
        "width": width, "height": height, "frames": frames, "fps": fps, "seconds": clip_seconds(frames, fps),
        "seed": random.randint(0, 2 ** 50) if seed is None else int(seed),
        "steps": int(params["steps"]), "split_step": int(params["split_step"]), "cfg": float(params["cfg"]),
        "shift": float(params["shift"]), "sampler_name": str(params["sampler_name"]),
        "scheduler": str(params["scheduler"]), "prompt_prefix": params.get("prompt_prefix"),
        "negative": params.get("negative"), "te_device": settings.te_device, "crf": settings.crf,
        **decode_tiles(settings), "notes": notes,
    }
