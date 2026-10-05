"""Chat tab routing: plain chat, web search, writing, code, or "use the image tab". Pure functions.

``/docs <path> [question]`` reads local documents (docs/local-doc-mapreduce-design.md §5.1); it is checked before
anything else except an attachment, so a /docs message never opens Tor.

Priority (docs/chat-deep-search-creative-sandbox.md §7): an explicit picture request goes to the image tab, then
code (write / implement / run / test a program), then writing (novel, story, setting, polish, "continue"), then
search (facts, latest, comparison, research), and the rest is plain chat. ``/search``, ``/write``, ``/code`` and
``/chat`` at the start of a message fix the kind.

``is_compound`` finds the requests that need several tools in turn (search, then write ...): in think mode they go
to the control loop (docs/autonomous-controller-design.md §4) instead of one fixed pipeline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

CHAT, SEARCH, WRITE, CODE, TO_IMAGE_TAB, DOCS = "chat", "search", "write", "code", "image_tab", "docs"
KINDS = (CHAT, SEARCH, WRITE, CODE, TO_IMAGE_TAB, DOCS)
# configurable.task values -> kind ("image" is the UI's name for the image tab).
TASKS = {"chat": CHAT, "search": SEARCH, "write": WRITE, "code": CODE, "image": TO_IMAGE_TAB}

_PREFIX = re.compile(r"^\s*/(search|write|code|chat)\b\s*", re.IGNORECASE)
_DOCS = re.compile(r"^\s*/docs(?=\s|$)\s*", re.IGNORECASE)
DOC_DEFAULT_QUESTION = "要点、決定、未決を、出典の節付きで"
_SEARCH_WORDS = ("検索", "調べて", "調べる", "しらべて", "ググ", "最新", "ニュース", "web で", "ウェブで", "ネットで")
# Asking for a picture belongs to the image tab; the chat tab neither searches nor generates.
_DRAW_WORDS = ("描いて", "描け", "描く", "描写して", "絵を", "イラストを", "画像を生成", "画像を作", "画像生成",
               "画像にして", "絵にして", "イラストにして", "画像化", "draw ", "illustrate")
_CODE = re.compile(
    r"(コード|プログラム|スクリプト|関数|クラス|ツール|cli|api)(を|も)?(書|作|組|実装|生成)"
    r"|(コード|プログラム|スクリプト)(で|にして|化)"
    r"|実装して|実行して|テストして|動かして|デバッグして|リファクタ"
    r"|\b(python|rust|cargo|pytest)\b.*(書|作|実装|実行|動か)"
    r"|write (a |some )?(script|program|code|function)|implement ",
    re.IGNORECASE)
_WRITE = re.compile(
    r"小説|物語|ストーリー|短編|長編|掌編|連載|章立て|プロット|脚本|シナリオ|あらすじを作|詩を|俳句|短歌|歌詞"
    r"|エッセイ|作文|設定を(作|考|書)|世界観|キャラ(クター)?設定|推敲|添削|リライト|校正"
    r"|続きを書|記事の下書き|下書き|文章を(書|作)|書いて|書き直して",
    re.IGNORECASE)
_CONTINUE = re.compile(r"続き|つづき|続けて|次の章|continue", re.IGNORECASE)
# A writing task that rests on real facts: search first in think mode (§4).
_FACTUAL = re.compile(r"実在|史実|事実に基づ|実話|資料|実際の|ノンフィクション|ニュースを(元|もと)|最新")
_LONG = re.compile(r"長編|連載|章立て|全\s*\d+\s*章|\d+\s*章|複数の章|chapter", re.IGNORECASE)
_URL = re.compile(r"https?://[^\s<>\"'）」』】]+")


@dataclass
class Route:
    kind: str
    text: str
    urls: list[str] = field(default_factory=list)
    reason: str = ""
    needs_search: bool = False   # write: search first (think mode only)
    long: bool = False           # write: chapter by chapter
    continuation: bool = False   # write: continue the thread's draft
    explicit: bool = False       # fixed by a prefix or configurable.task
    doc_path: str = ""           # docs: the one file or directory to read


def find_urls(text: str) -> list[str]:
    seen: list[str] = []
    for url in _URL.findall(text or ""):
        url = url.rstrip(".,、。)")
        if url not in seen:
            seen.append(url)
    return seen


def _search_word(lowered: str) -> str | None:
    return next((word for word in _SEARCH_WORDS if word in lowered), None)


def _write(body: str, reason: str, has_draft: bool, explicit: bool = False) -> Route:
    lowered = body.lower()
    return Route(WRITE, body, find_urls(body), reason,
                 needs_search=bool(_search_word(lowered) or _FACTUAL.search(body)),
                 long=bool(_LONG.search(body)), continuation=has_draft and bool(_CONTINUE.search(body)),
                 explicit=explicit)


def split_doc_args(body: str) -> tuple[str, str]:
    """``<path> [question]`` -> (path, question). A path with spaces is quoted ("...", '...' or 「...」); without a
    question the default asks for the main points, decisions and open items (§5.1)."""
    body = (body or "").strip()
    if body[:1] in ('"', "'", "「"):
        close = {'"': '"', "'": "'", "「": "」"}[body[0]]
        end = body.find(close, 1)
        if end > 0:
            return body[1:end].strip(), body[end + 1:].strip() or DOC_DEFAULT_QUESTION
    parts = re.split(r"[\s　]+", body, maxsplit=1)
    path = parts[0] if parts else ""
    question = parts[1].strip() if len(parts) > 1 else ""
    return path, question or DOC_DEFAULT_QUESTION


# --- compound requests (docs/autonomous-controller-design.md §4) -------------------------------------------------
# A document to produce (writing in the controller's sense: a guide or a memo is writing too).
_DOC_OUTPUT = re.compile(
    r"小説|物語|記事|手順書|マニュアル|ガイド|レポート|報告書|メモ|議事録|解説文|説明文|ブログ|エッセイ|まとめ記事"
    r"|文章|下書き|資料に(まとめ|して)|文書に(まとめ|して)|表に(まとめ|して)")
# The result of one tool decides the next one ("調べてから書く", "動くか試して直して").
_CONNECTIVE = re.compile(
    r"(調べ|検索し|確認し|ググっ|試し|実行し|動かし)てから"
    r"|(調べ|検索し)て.{0,24}(書|まと|作|手順書|メモ|記事|レポート|資料|文章)"
    r"|根拠を(確認|調べ|確かめ)"
    r"|確認して(メモ|まとめ|書|記録)"
    r"|動くか(試|確か|確認)"
    r"|(試|実行|テスト)して.{0,8}直して"
    r"|結果を(見て|もとに|元に|踏まえ)"
    r"|それを(もとに|元に|踏まえ)"
    r"|(その|した)上で|したうえで|そのうえで")


def long_request(text: str) -> bool:
    """Writing in chapters (章立て, 全 N 章 ...)."""
    return bool(_LONG.search(text or ""))


def compound_kinds(text: str) -> list[str]:
    """Every tool kind the message asks for, in the controller's order (search, write, code)."""
    body = (text or "").strip()
    lowered = body.lower()
    kinds = []
    if _search_word(lowered) or find_urls(body):
        kinds.append(SEARCH)
    if _DOC_OUTPUT.search(body):
        kinds.append(WRITE)
    if _CODE.search(body):
        kinds.append(CODE)
    return kinds


def is_compound(decision: Route) -> bool:
    """A request for the control loop: not fixed by a prefix, not media, /docs or a draft's continuation, and two
    tool kinds at once or a connective that makes the next tool depend on a result (§4). The mode is checked by
    the caller (fast never enters the loop)."""
    if decision.explicit or decision.continuation or decision.kind in (TO_IMAGE_TAB, DOCS):
        return False
    kinds = compound_kinds(decision.text)
    return len(kinds) >= 2 or (len(kinds) == 1 and bool(_CONNECTIVE.search(decision.text or "")))


def route(text: str, has_media: bool = False, has_draft: bool = False, task: str | None = None) -> Route:
    """``has_draft``: the thread has a writing artifact ("続き" continues it). ``task``: configurable.task."""
    text = (text or "").strip()
    if has_media:
        return Route(TO_IMAGE_TAB, text, reason="画像や動画の添付は画像タブで受け付けます")
    docs = _DOCS.match(text)
    if docs or (task or "").strip().lower() == DOCS:
        body = _DOCS.sub("", text, count=1).strip() if docs else text
        path, question = split_doc_args(body)
        return Route(DOCS, question, reason="/docs", explicit=True, doc_path=path)
    prefix = _PREFIX.match(text)
    body = _PREFIX.sub("", text, count=1).strip() if prefix else text
    forced = prefix.group(1).lower() if prefix else TASKS.get((task or "").strip().lower(), "")
    if forced == SEARCH:
        return Route(SEARCH, body, find_urls(body), "/search", explicit=True)
    if forced == WRITE:
        return _write(body, "/write", has_draft, True)
    if forced == CODE:
        return Route(CODE, body, find_urls(body), "/code", explicit=True)
    if forced == CHAT:
        return Route(CHAT, body, reason="/chat", explicit=True)
    if forced == TO_IMAGE_TAB:
        return Route(TO_IMAGE_TAB, body, reason="画像の依頼は画像タブで受け付けます", explicit=True)
    lowered = body.lower()
    if any(word in lowered for word in _DRAW_WORDS):
        return Route(TO_IMAGE_TAB, body, reason="絵を描く依頼は画像タブで受け付けます")
    match = _CODE.search(body)
    if match:
        return Route(CODE, body, find_urls(body), f"「{match.group(0)}」")
    match = _WRITE.search(body)
    if match:
        return _write(body, f"「{match.group(0)}」", has_draft)
    if has_draft and _CONTINUE.search(body):
        return _write(body, "続き", has_draft)
    urls = find_urls(body)
    if urls:
        return Route(SEARCH, body, urls, "URL の要約")
    word = _search_word(lowered)
    if word:
        return Route(SEARCH, body, urls, f"「{word}」")
    return Route(CHAT, body)
