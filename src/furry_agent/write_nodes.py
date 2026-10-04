"""The chat tab's writing branch (docs/chat-deep-search-creative-sandbox.md §4).

    write_brief      fast: the brief is implicit. think: the 27B writes the brief, the outline and the facts it
                     does not know (JSON). A continuation keeps the thread's brief and outline.
    write_draft      the 27B writes the text (think: thinking tokens on, temperature 0.7). A continuation and the
                     next chapter start from the end of ``artifact.draft``. fast ends here.
    write_revise     think: the 27B returns find/replace edits for the differences from the brief; the draft is
                     never thrown away and rewritten (temperature 0.2)
    chapter_confirm  long requests (chapters), think only: an interrupt after each chapter, the same HITL card as
                     the image tab's confirmation. approve = next chapter, edit = next chapter with an instruction,
                     reject = stop ("続きを書いて" resumes from the next chapter)

The writer is always the LM Studio 27B; the search models and the image prompts are never used for writing.
The job lock is released before every interrupt so the image tab is not blocked while the user reads.
"""

from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END
from langgraph.types import interrupt

from furry_agent import search_agent as sa, writing
from furry_agent.chat_common import (ChatState, _cleanup, _decision, _edited_args, _fail, _final, _held, _hitl,
                                     _is_think, _lmstudio, _lock, _max_tokens, _progress, _prompt, _settings,
                                     _thought, log)
from furry_agent.job_lock import JobLockBusy
from furry_agent.llm_client import LLMError

CONTINUE_ACTION = "continue_writing"
DRAFT_TOKENS = 3000


def _task(artifact: dict, extra: list[dict] | None = None) -> dict:
    steps = []
    if artifact.get("brief") and any(v for k, v in artifact["brief"].items() if k != "language"):
        steps.append({"title": "ブリーフ", "body": writing.brief_text(artifact["brief"])})
    if artifact.get("outline") and artifact["outline"][0].get("title"):
        steps.append({"title": "アウトライン", "body": writing.outline_text(artifact["outline"])})
    if artifact.get("open"):
        steps.append({"title": "事実が分からない点（創作で埋めていません）", "body": "\n".join(f"- {o}" for o in artifact["open"])})
    if artifact.get("revision_notes"):
        steps.append({"title": "推敲メモ", "body": "\n".join(f"- {n}" for n in artifact["revision_notes"][-8:])})
    return {"kind": "write", "status": artifact.get("status"), "chapter_index": artifact.get("chapter_index", 0),
            "chapters": len(artifact.get("outline") or []) if artifact.get("long") else 0,
            "chars": len(artifact.get("draft") or ""), "steps": [*steps, *(extra or [])]}


def _chapter(artifact: dict) -> dict | None:
    """The chapter to write next (long requests only)."""
    if not artifact.get("long"):
        return None
    outline = artifact.get("outline") or []
    index = artifact.get("chapter_index", 0)
    return outline[index] if index < len(outline) else None


async def write_brief(state: ChatState, config: RunnableConfig) -> dict:
    settings = _settings(config)
    lmstudio = _lmstudio(config, settings)
    route = state["route"]
    request = route["text"]
    old = dict(state.get("artifact") or {})
    think = _is_think(state)
    token = state.get("lock_token")
    if route.get("continuation") and old.get("draft"):
        artifact = {**old, "request": request, "status": "draft"}
        note = "これまでの本文の続きを書いています…"
        if _chapter(artifact):
            note = f"第 {artifact['chapter_index'] + 1} 章を書いています…"
        return {"artifact": artifact, "messages": [_progress(state, note, task=_task(artifact))]}
    artifact = writing.new_artifact(request)
    artifact["research"] = old.get("research", "") if route.get("search_first") else ""
    artifact["long"] = bool(route.get("long")) and think
    skipped = ("速いモードでは検索せずに書きます（事実の確認が要るなら「思考」で送ってください）。\n"
               if route.get("search_skipped") else "")
    if not think:
        return {"artifact": artifact, "messages": [_progress(state, f"{skipped}本文を書いています…", task=_task(artifact))]}
    try:
        token = await _lock(state, config, settings)
        user = writing.draft_input(artifact, request)
        if artifact["long"]:
            user += "\n\n章立てが必要な依頼です。outline は章ごとに 1 要素にしてください。"
        async with _held(token):
            parsed = await sa.ask_json(lmstudio, [
                {"role": "system", "content": await _prompt(settings, "system_write_outline.txt")},
                {"role": "user", "content": user}], writing.Outline, max_tokens=1500, temperature=0.2)
    except asyncio.CancelledError:
        await asyncio.shield(_cleanup(token, lmstudio, unload=True))
        raise
    except JobLockBusy as exc:
        return _fail(state, exc, "lock")
    except (LLMError, OSError) as exc:
        await _cleanup(token, lmstudio, unload=True)
        return _fail(state, f"LM Studio に接続できないか、時間切れです（{exc}）", "write")
    brief, outline, open_ = writing.outline_from(parsed, request, artifact["long"])
    artifact.update({"brief": brief, "outline": outline, "open": open_, "status": "outline"})
    if artifact["long"] and len(outline) < 2:
        artifact["long"] = False  # the model did not split it: one piece
    log.info("write outline sections=%d long=%s fallback=%s", len(outline), artifact["long"], parsed is None)
    first = f"第 1 章「{outline[0]['title']}」" if artifact["long"] else "本文"
    text = (f"アウトライン:\n{writing.outline_text(outline)}\n\n"
            + (f"事実が分からない点（創作で埋めません）: {'、'.join(open_)}\n\n" if open_ else "")
            + f"{first}を書いています…")
    return {"artifact": artifact, "lock_token": token, "messages": [_progress(state, text, task=_task(artifact))]}


def _after_brief(state: ChatState) -> str:
    return END if state.get("error") else "write_draft"


async def write_draft(state: ChatState, config: RunnableConfig) -> dict:
    settings = _settings(config)
    lmstudio = _lmstudio(config, settings)
    route = state["route"]
    artifact = dict(state["artifact"])
    think = _is_think(state)
    chapter = _chapter(artifact)
    continuation = bool(route.get("continuation")) and bool(artifact.get("draft"))
    instruction = artifact.pop("instruction", "")
    token = state.get("lock_token")
    try:
        token = await _lock(state, config, settings)
        user = writing.draft_input(artifact, route["text"], chapter=chapter, continuation=continuation,
                                   instruction=instruction)
        async with _held(token):
            reply = await lmstudio.chat([{"role": "system", "content": await _prompt(settings, "system_write_draft.txt")},
                                         {"role": "user", "content": user}],
                                        max_tokens=_max_tokens(state, settings, DRAFT_TOKENS, True), temperature=0.7,
                                        timeout_s=settings.chat_timeout_s, thinking=think)
    except asyncio.CancelledError:
        await asyncio.shield(_cleanup(token, lmstudio, unload=True))
        raise
    except JobLockBusy as exc:
        return _fail(state, exc, "lock")
    except (LLMError, OSError) as exc:
        await _cleanup(token, lmstudio, unload=True)
        return _fail(state, f"LM Studio に接続できないか、時間切れです（{exc}）", "write")
    piece = writing.clean_piece(reply.content)
    if not piece:
        await _cleanup(token)
        return _fail(state, "本文が空でした。もう一度送ってください", "write")
    keep = continuation or (chapter is not None and artifact.get("chapter_index", 0) > 0)
    artifact.update({"draft": writing.append_piece(artifact.get("draft", "") if keep else "", piece),
                     "last_piece": piece, "status": "draft"})
    thoughts = _thought("本文", reply)
    log.info("write draft chars=%d continuation=%s chapter=%s think=%s", len(piece), continuation,
             artifact.get("chapter_index") if chapter else None, think)
    if not think:
        # Fast: one shot, no revision. The 27B stays loaded like plain chat; the image workflow ejects it anyway.
        if chapter is not None:  # a fast "続き" of a chaptered draft writes that chapter and moves on
            artifact["chapter_index"] = artifact.get("chapter_index", 0) + 1
        await _cleanup(token)
        return {"artifact": artifact, "lock_token": None,
                "messages": [_final(state, piece, task=_task(artifact), thoughts=thoughts)]}
    return {"artifact": artifact, "lock_token": token, "thinking": thoughts,
            "messages": [_progress(state, "推敲しています（ブリーフとの差分だけを直します）…", task=_task(artifact))]}


def _after_draft(state: ChatState) -> str:
    if state.get("error") or not _is_think(state):
        return END
    return "write_revise"


async def write_revise(state: ChatState, config: RunnableConfig) -> dict:
    settings = _settings(config)
    lmstudio = _lmstudio(config, settings)
    artifact = dict(state["artifact"])
    token = state.get("lock_token")
    piece = artifact.get("last_piece", "")
    revision = None
    try:
        token = await _lock(state, config, settings)
        async with _held(token):
            # The draft and the brief go in; only edits come back (never a full rewrite, §4.2).
            revision = await sa.ask_json(lmstudio, [
                {"role": "system", "content": await _prompt(settings, "system_write_revise.txt")},
                {"role": "user", "content": writing.revise_input(artifact, piece)}],
                writing.Revision, max_tokens=1200, temperature=0.2)
    except asyncio.CancelledError:
        await asyncio.shield(_cleanup(token, lmstudio, unload=True))
        raise
    except JobLockBusy as exc:
        return _fail(state, exc, "lock")
    except (LLMError, OSError) as exc:
        log.info("revise skipped: %s", exc)
    revised, notes, applied = writing.apply_edits(piece, revision)
    draft = artifact.get("draft", "")
    if piece and draft.endswith(piece):
        draft = draft[: len(draft) - len(piece)] + revised
    artifact.update({"draft": draft, "last_piece": revised,
                     "revision_notes": [*(artifact.get("revision_notes") or []), *notes][-20:]})
    log.info("write revise edits=%d applied=%d", len(revision.edits) if revision else 0, applied)
    chapter = _chapter(artifact)
    # The writer is done for now: the lock goes back before the user reads (and before any interrupt).
    await _cleanup(token)
    if chapter is not None:
        index = artifact.get("chapter_index", 0)
        total = len(artifact["outline"])
        artifact["chapter_index"] = index + 1
        more = index + 1 < total
        artifact["status"] = "draft" if more else "done"
        chapter_message = _final(state, revised, task=_task(artifact), message_id=f"{state['progress_id']}-ch{index + 1}")
        status = (f"第 {index + 1} 章（全 {total} 章）を書きました。続きを書くか確認してください。"
                  if more else f"全 {total} 章を書きました（{len(draft)} 字）。")
        return {"artifact": artifact, "lock_token": None,
                "messages": [chapter_message, _progress(state, status, task=_task(artifact))]}
    artifact["status"] = "revised"
    return {"artifact": artifact, "lock_token": None, "messages": [_final(state, revised, task=_task(artifact))]}


def _after_revise(state: ChatState) -> str:
    if state.get("error"):
        return END
    artifact = state.get("artifact") or {}
    return "chapter_confirm" if artifact.get("long") and _chapter(artifact) else END


async def chapter_confirm(state: ChatState, config: RunnableConfig) -> dict:
    artifact = dict(state["artifact"])
    index = artifact.get("chapter_index", 0)
    chapter = (artifact.get("outline") or [{}])[index] if index < len(artifact.get("outline") or []) else {}
    response = interrupt(_hitl(CONTINUE_ACTION, {"instruction": ""}, (
        f"第 {index} 章を書きました。第 {index + 1} 章「{chapter.get('title', '')}」へ進むなら承認、"
        "次の章への指示があれば instruction に書いて送信（編集）、ここで止めるなら却下してください。"
        "止めたあとも「続きを書いて」で次の章から再開できます。")))
    decision = _decision(response)
    kind = decision.get("type")
    if kind == "approve":
        return {"artifact": artifact, "messages": [_progress(state, f"第 {index + 1} 章を書いています…", task=_task(artifact))]}
    if kind == "edit":
        artifact["instruction"] = str(_edited_args(decision).get("instruction", "")).strip()[:1000]
        return {"artifact": artifact, "messages": [_progress(state, f"指示を反映して第 {index + 1} 章を書いています…",
                                                             task=_task(artifact))]}
    log.info("writing stopped by the user at chapter %d", index)
    return {"artifact": artifact, "error": "stopped",
            "messages": [_progress(state, f"第 {index} 章で止めました。「続きを書いて」で第 {index + 1} 章から再開できます。",
                                   task=_task(artifact))]}


def _after_confirm(state: ChatState) -> str:
    return END if state.get("error") else "write_draft"


def add_nodes(builder: Any) -> None:
    builder.add_node("write_brief", write_brief)
    builder.add_node("write_draft", write_draft)
    builder.add_node("write_revise", write_revise)
    builder.add_node("chapter_confirm", chapter_confirm)
    builder.add_conditional_edges("write_brief", _after_brief, ["write_draft", END])
    builder.add_conditional_edges("write_draft", _after_draft, ["write_revise", END])
    builder.add_conditional_edges("write_revise", _after_revise, ["chapter_confirm", END])
    builder.add_conditional_edges("chapter_confirm", _after_confirm, ["write_draft", END])
