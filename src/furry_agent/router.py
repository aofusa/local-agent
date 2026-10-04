"""Chat tab routing: plain chat, web search, or "use the image tab" (design doc §5.3). Pure functions."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

CHAT, SEARCH, TO_IMAGE_TAB = "chat", "search", "image_tab"

_SEARCH_PREFIX = re.compile(r"^\s*/search\b\s*", re.IGNORECASE)
_SEARCH_WORDS = ("検索", "調べて", "調べる", "しらべて", "ググ", "最新", "ニュース", "web で", "ウェブで", "ネットで")
# Asking for a picture belongs to the image tab; the chat tab neither searches nor generates.
_DRAW_WORDS = ("描いて", "描け", "描く", "描写して", "絵を", "イラストを", "画像を生成", "画像を作", "画像生成",
               "draw ", "illustrate")
_URL = re.compile(r"https?://[^\s<>\"'）」』】]+")


@dataclass
class Route:
    kind: str
    text: str
    urls: list[str] = field(default_factory=list)
    reason: str = ""


def find_urls(text: str) -> list[str]:
    seen: list[str] = []
    for url in _URL.findall(text or ""):
        url = url.rstrip(".,、。)")
        if url not in seen:
            seen.append(url)
    return seen


def route(text: str, has_media: bool = False) -> Route:
    text = (text or "").strip()
    if has_media:
        return Route(TO_IMAGE_TAB, text, reason="画像や動画の添付は画像タブで受け付けます")
    explicit = _SEARCH_PREFIX.match(text)
    body = _SEARCH_PREFIX.sub("", text, count=1).strip() if explicit else text
    lowered = body.lower()
    if not explicit and any(word in lowered for word in _DRAW_WORDS):
        return Route(TO_IMAGE_TAB, body, reason="絵を描く依頼は画像タブで受け付けます")
    urls = find_urls(body)
    if explicit:
        return Route(SEARCH, body, urls, "/search")
    if urls:
        return Route(SEARCH, body, urls, "URL の要約")
    for word in _SEARCH_WORDS:
        if word in lowered:
            return Route(SEARCH, body, urls, f"「{word}」")
    return Route(CHAT, body)
