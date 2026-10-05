"""Fast / think / auto for the chat tab (docs/chat-deep-search-creative-sandbox.md §6).

The mode changes budgets and thinking tokens, not the graph: one chat graph reads ``mode`` from state.

- ``fast``: one search round without critique, one-shot writing, code is generated but never run, thinking off;
  /docs reads the planned sections in order.
- ``think``: up to 4 search rounds scored against sub-questions, outline -> draft -> revise, code runs in the
  Docker sandbox after approval, thinking tokens on (shown apart from the answer); /docs lets the leader pick the
  next sections after each wave (coverage check).
- ``auto``: like Grok's auto mode, the request picks one of the two. The rules below decide from the task and the
  wording (comparison, analysis, long writing, running code ...); when the router model was asked anyway (an
  ambiguous message), its ``deep`` flag breaks a tie. Pure functions; no model is loaded only for this choice.

A request without ``configurable.mode`` is ``fast`` (design doc §6.1); the UI sends ``auto`` by default.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from furry_agent.router import CHAT, CODE, DOCS, SEARCH, WRITE, Route

FAST, THINK, AUTO = "fast", "think", "auto"
MODES = (FAST, THINK, AUTO)
LABELS = {FAST: "速い", THINK: "思考", AUTO: "自動"}

# The user says how much effort they want.
_ASK_THINK = re.compile(r"よく考えて|じっくり|深く|しっかり|丁寧に|詳しく|徹底|網羅|慎重に|step by step|think hard",
                        re.IGNORECASE)
_ASK_FAST = re.compile(r"手短|簡単に|ざっくり|さっと|すぐに?|一言で|短く|要点だけ|ひとことで|quick|briefly",
                       re.IGNORECASE)
# The question needs several searches or a judgement over sources.
_DEEP_SEARCH = re.compile(
    r"比較|違い|vs\.?|メリット|デメリット|長所|短所|おすすめ|選び方|どれ(が|を)|評判|レビュー|調査|分析|"
    r"なぜ|理由|原因|背景|経緯|歴史|影響|見通し|予測|動向|まとめて|一覧|各社|それぞれ|根拠|検証|本当",
    re.IGNORECASE)
# Plain chat that needs reasoning rather than a quick reply.
_DEEP_CHAT = re.compile(
    r"証明|計算|解いて|解き方|導出|推論|論理|数学|方程式|確率|アルゴリズム|計画を立て|戦略|設計|"
    r"考察|分析|検討|比較|なぜ|理由|どうすれば|方法を|手順|トレードオフ|長所と短所",
    re.IGNORECASE)
# Writing that is long or needs a structure; short pieces are written in one go.
_SHORT_WRITE = re.compile(r"一文|一行|キャッチコピー|タイトル案|俳句|短歌|川柳|ひとこと|短い|ショート|\d{2,3}\s*字",
                          re.IGNORECASE)
_LONG_WRITE = re.compile(r"小説|物語|短編|長編|連載|章|プロット|脚本|シナリオ|世界観|設定|推敲|\d{4,}\s*字",
                         re.IGNORECASE)
# Code the user wants executed or checked (only think mode runs the sandbox).
_RUN = re.compile(r"実行|動かして|テスト|検証|試して|確認して|デバッグ|ベンチマーク|\brun\b|\btest\b", re.IGNORECASE)


@dataclass(frozen=True)
class ModeChoice:
    mode: str          # fast | think (resolved)
    requested: str     # fast | think | auto (what the UI sent)
    reason: str = ""

    @property
    def label(self) -> str:
        if self.requested == AUTO:
            return f"{LABELS[self.mode]}（自動: {self.reason}）" if self.reason else f"{LABELS[self.mode]}（自動）"
        return LABELS[self.mode]


def requested_mode(value) -> str:
    """configurable.mode -> fast | think | auto; anything else (or nothing) is fast."""
    value = str(value or "").strip().lower()
    return value if value in MODES else FAST


def auto_mode(route: Route, *, has_draft: bool = False, draft_status: str = "",
              router_deep: bool | None = None, compound: bool = False) -> tuple[str, str]:
    """(fast | think, reason) for one request. ``compound``: several tools in turn (router.is_compound); only
    think mode runs the control loop (docs/autonomous-controller-design.md §4)."""
    text = route.text or ""
    if _ASK_FAST.search(text):
        return FAST, "手短な回答の指定"
    if _ASK_THINK.search(text):
        return THINK, "じっくり考える指定"
    if compound:
        return THINK, "複数の道具を順に使う依頼"
    if route.kind == SEARCH:
        if len(route.urls) > 1:
            return THINK, "複数の URL"
        match = _DEEP_SEARCH.search(text)
        if match:
            return THINK, f"「{match.group(0)}」を含む調査"
        if len(text) >= 80 or text.count("、") + text.count("?") + text.count("？") >= 3:
            return THINK, "条件の多い質問"
        if router_deep:
            return THINK, "ルータが深い調査と判定"
        return FAST, "単一の事実確認"
    if route.kind == WRITE:
        if route.continuation:
            if draft_status in ("outline", "draft", "revised") or route.long:
                return THINK, "章立ての続き"
            return FAST, "続きを書く"
        if route.long:
            return THINK, "長い文章（章立て）"
        if route.needs_search:
            return THINK, "事実の確認が要る創作"
        if _SHORT_WRITE.search(text):
            return FAST, "短い文章"
        if _LONG_WRITE.search(text) or len(text) >= 60:
            return THINK, "構成が要る文章"
        return FAST, "短い文章"
    if route.kind == CODE:
        if _RUN.search(text):
            return THINK, "実行・テストの依頼"
        return FAST, "コードの生成のみ"
    if route.kind == DOCS:
        # Both modes read up to DOC_MAX_CHUNKS in waves; fast takes the planned order, think lets the leader pick.
        match = _DEEP_SEARCH.search(text) or _DEEP_CHAT.search(text)
        if match:
            return THINK, f"「{match.group(0)}」を含む読解"
        return FAST, "文書の要点"
    if route.kind == CHAT:
        match = _DEEP_CHAT.search(text)
        if match:
            return THINK, f"「{match.group(0)}」を含む問い"
        if len(text) >= 200:
            return THINK, "長い相談"
        if router_deep:
            return THINK, "ルータが推論が要ると判定"
        return FAST, "会話"
    return FAST, ""


def choose(requested: str, route: Route, **kw) -> ModeChoice:
    requested = requested_mode(requested)
    if requested != AUTO:
        return ModeChoice(requested, requested)
    mode, reason = auto_mode(route, **kw)
    return ModeChoice(mode, AUTO, reason)
