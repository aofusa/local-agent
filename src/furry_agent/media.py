"""Extract the Japanese instruction and reference media from a chat message.

agent-chat-ui sends ``{"type": "image", "mimeType": ..., "data": <base64>, "metadata": {"name", "role", "strength"}}``
blocks. ``metadata.role`` / ``metadata.strength`` carry the per-image role chosen in the UI (WI §4.10).
LangChain-style blocks (``mime_type``/``base64``, ``image_url`` data URLs) are accepted too.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import re
from dataclasses import dataclass, field

from PIL import Image, UnidentifiedImageError

MAX_IMAGES = 4
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_SIDE = 4096
IMAGE_ROLES = ("auto", "style", "pose", "character", "base", "mask")
IMAGE_MIME_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
_DATA_URL = re.compile(r"^data:(?P<mime>[\w/+.-]+);base64,(?P<data>.+)$", re.DOTALL)
_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}


class MediaError(ValueError):
    """The user's input cannot be processed; the message is shown in the chat."""


@dataclass
class Media:
    mime: str
    data: bytes
    name: str
    role: str = "auto"
    strength: float | None = None
    width: int = 0
    height: int = 0

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()

    @property
    def extension(self) -> str:
        return _EXT.get(self.mime, "bin")


@dataclass
class Request:
    text: str
    images: list[Media] = field(default_factory=list)


def _role_and_strength(block: dict) -> tuple[str, float | None]:
    meta = block.get("metadata") or {}
    role = str(meta.get("role") or "auto").lower()
    if role not in IMAGE_ROLES:
        raise MediaError(f"画像の役割 {role!r} は使えません（{' / '.join(IMAGE_ROLES)}）")
    strength = meta.get("strength")
    if strength in (None, ""):
        return role, None
    try:
        return role, float(strength)
    except (TypeError, ValueError) as exc:
        raise MediaError(f"画像の強度が数値ではありません: {strength!r}") from exc


def _dimensions(raw: bytes, name: str) -> tuple[int, int]:
    if len(raw) > MAX_IMAGE_BYTES:
        raise MediaError(f"{name} は {len(raw) // (1024 * 1024)} MB です。1 枚 10 MB までにしてください")
    try:
        with Image.open(io.BytesIO(raw)) as image:
            width, height = image.size
    except (UnidentifiedImageError, OSError) as exc:
        raise MediaError(f"{name} を画像として読み取れませんでした") from exc
    if max(width, height) > MAX_IMAGE_SIDE:
        raise MediaError(f"{name} は {width}x{height} です。辺 {MAX_IMAGE_SIDE} px 以下にしてください")
    return width, height


def _block_payload(block: dict) -> tuple[str | None, str | None, str | None]:
    """Return (mime, base64 data, name) for a media block, or Nones."""
    btype = block.get("type")
    meta = block.get("metadata") or {}
    name = meta.get("name") or meta.get("filename")
    if btype == "image_url":
        url = block.get("image_url")
        url = url.get("url") if isinstance(url, dict) else url
        m = _DATA_URL.match(url or "")
        return (m.group("mime"), m.group("data"), name) if m else (None, None, name)
    if btype in ("image", "file", "video"):
        mime = block.get("mimeType") or block.get("mime_type")
        data = block.get("data") or block.get("base64")
        if data is None and isinstance(block.get("url"), str):
            m = _DATA_URL.match(block["url"])
            if m:
                mime, data = mime or m.group("mime"), m.group("data")
        return mime, data, name
    return None, None, name


def parse_request(content) -> Request:
    if isinstance(content, str):
        return Request(text=content.strip())
    texts: list[str] = []
    images: list[Media] = []
    for block in content or []:
        if isinstance(block, str):
            texts.append(block)
            continue
        if block.get("type") == "text":
            texts.append(block.get("text", ""))
            continue
        mime, data, name = _block_payload(block)
        if mime is None:
            continue
        if mime.startswith("video/"):
            raise MediaError(
                f"動画入力は未対応です（ComfyUI に VideoHelperSuite が未導入のため対象外）。画像 0〜{MAX_IMAGES} 枚で送ってください。"
            )
        if mime not in IMAGE_MIME_TYPES:
            raise MediaError(f"対応していない添付形式です: {mime}（PNG / JPEG / WebP / GIF の画像のみ）")
        try:
            raw = base64.b64decode(data, validate=False)
        except (binascii.Error, TypeError) as exc:
            raise MediaError("添付画像のデータを読み取れませんでした") from exc
        role, strength = _role_and_strength(block)
        images.append(Media(mime=mime, data=raw, name=name or f"ref_{len(images) + 1}.{_EXT[mime]}",
                            role=role, strength=strength))
    if len(images) > MAX_IMAGES:
        raise MediaError(f"参照画像は 1 メッセージ {MAX_IMAGES} 枚までです（{len(images)} 枚添付されています）。"
                         f"{MAX_IMAGES + 1} 枚目以降を外して送り直してください")
    for i, media in enumerate(images, start=1):
        media.width, media.height = _dimensions(media.data, f"画像{i}")
    return Request(text="\n".join(t.strip() for t in texts if t.strip()), images=images)
