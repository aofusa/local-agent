"""Split the LLM response into positive / negative prompts.

Design doc §4.2: cut from the first ``{`` to the last ``}`` and ``json.loads`` it.
On failure, do not retry: the raw string becomes the positive prompt and the
negative prompt falls back to fixed quality tags.

This module has no ComfyUI imports so it can be unit-tested on its own.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

DEFAULT_NEGATIVE = (
    "worst quality, low quality, normal quality, lowres, blurry, jpeg artifacts, "
    "bad anatomy, bad hands, extra digits, missing fingers, deformed, "
    "watermark, signature, text, logo"
)

QUALITY_PREFIX = "masterpiece, best quality, amazing quality, absurdres"

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_THINK_OPEN = re.compile(r"<think>.*", re.DOTALL | re.IGNORECASE)
_THINK_CLOSE = re.compile(r"^.*?</think>", re.DOTALL | re.IGNORECASE)
_CODE_FENCE = re.compile(r"```[a-zA-Z]*")
# LLMs sometimes write "\ " or "\_" inside JSON strings; JSON only allows these escapes.
_INVALID_ESCAPE = re.compile(r'\\(?!["\\/bfnrtu])')


class LLMCallError(RuntimeError):
    """The LLM node returned an error string instead of a response."""


@dataclass(frozen=True)
class SplitResult:
    positive: str
    negative: str
    parsed: bool


def strip_think(text: str) -> str:
    """Remove ``<think>`` blocks, including an unterminated one or a dangling close tag."""
    text = _THINK_BLOCK.sub("", text)
    if re.search(r"</think>", text, re.IGNORECASE):
        text = _THINK_CLOSE.sub("", text)
    text = _THINK_OPEN.sub("", text)
    return text.strip()


def _normalize_tags(value: str) -> str:
    value = " ".join(value.split())
    parts = [p.strip() for p in value.split(",")]
    return ", ".join(p for p in parts if p)


def _merge_prefix(prefix: str, positive: str) -> str:
    """Prepend quality tags that the positive prompt does not already contain."""
    if not prefix.strip():
        return positive
    existing = {p.strip().lower() for p in positive.split(",")}
    missing = [t.strip() for t in prefix.split(",") if t.strip() and t.strip().lower() not in existing]
    if not missing:
        return positive
    return ", ".join(missing + ([positive] if positive else []))


def split_tags(
    raw: str,
    quality_prefix: str = QUALITY_PREFIX,
    default_negative: str = DEFAULT_NEGATIVE,
) -> SplitResult:
    if raw is None:
        raw = ""
    if not isinstance(raw, str):
        raw = str(raw)
    if raw.lstrip().startswith("[LM Connect Error]"):
        raise LLMCallError(raw.strip())

    text = strip_think(raw)
    positive = negative = None
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidate = _INVALID_ESCAPE.sub("", text[start : end + 1])
        try:
            data = json.loads(candidate, strict=False)
        except (ValueError, TypeError):
            data = None
        if isinstance(data, dict) and isinstance(data.get("positive"), str) and data["positive"].strip():
            positive = _normalize_tags(data["positive"])
            neg = data.get("negative")
            negative = _normalize_tags(neg) if isinstance(neg, str) and neg.strip() else default_negative

    if positive is None:
        fallback = _CODE_FENCE.sub("", text).replace("```", "")
        return SplitResult(
            positive=_merge_prefix(quality_prefix, " ".join(fallback.split())),
            negative=default_negative,
            parsed=False,
        )
    return SplitResult(positive=_merge_prefix(quality_prefix, positive), negative=negative, parsed=True)
