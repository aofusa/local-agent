"""The chat tab's control loop (docs/autonomous-controller-design.md).

    controller         the LM Studio 27B (the Ternary-Bonsai-2-27B proxy when LM Studio is down) reads the user's
                       request and the summaries of the tools used so far and returns one JSON Decision: the next
                       tool (search / write / code / image) with its request text, or the final answer
    controller_record  a tool's pipeline ended: its summary goes to ``control.trace`` (no hits, pages or file
                       bodies), the budget is checked, then back to controller or on to finish
    finish             the user-facing answer of the loop; the job lock is released

The model never owns the loop (§3): the runtime keeps the allow list of tools, the budget (CONTROLLER_MAX_STEPS,
the wall clock), the ban on the same tool with the same text, the job lock and the unloads. Tools are the
existing pipelines, entered through their first node (plan, write_brief, code_plan); their interrupts (chapter
confirmation, the sandbox approval) stay as they are. ``image`` never runs graph.py: it ends with the same
"use the image tab" message as TO_IMAGE_TAB.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from typing import Any, Literal

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END
from pydantic import BaseModel, ConfigDict

from furry_agent import search_agent as sa
from furry_agent.bonsai_select import SelectionError
from furry_agent.bonsai_worker import WorkerError
from furry_agent.chat_common import (CONTROL_RECORD, RESET, ChatState, _cleanup, _fail, _final, _held, _kwargs,
                                     _last_human, _leaders, _lmstudio, _lock, _progress, _prompt, _settings,
                                     _text_of, capped, log)
from furry_agent.chat_models import LEADER_LABEL, _leader
from furry_agent.config import ChatSettings
from furry_agent.job_lock import JobLockBusy, job_lock
from furry_agent.llm_client import LLMError
from furry_agent.router import CODE, SEARCH, WRITE, find_urls, long_request

PROMPT = "chat/controller.txt"
ENTRY = {"search": "plan", "write": "write_brief", "code": "code_plan"}
KIND = {"search": SEARCH, "write": WRITE, "code": CODE}
TOOL_LABELS = {"search": "検索", "write": "執筆", "code": "コード", "image": "画像"}
STOP_LABELS = {"final": "回答", "max_steps": "手数の上限", "wall_clock": "時間の上限", "repeat": "同じ依頼の繰り返し",
               "error": "判断の失敗", "rejected": "利用者が止めた", "image": "画像タブへの案内"}
DECISION_TOKENS = 400
SUMMARY_CHARS = 1000      # one trace entry (§6: about 1 KB)
PROMPT_SUMMARY_CHARS = 500  # what the controller reads of each entry (§8)
RESEARCH_CHARS = 1500     # the search answer handed to the writer (writing.draft_input reads 1500)
IMAGE_TAB_TEXT = ("画像の生成は画像タブで受け付けます。画面上部の「画像」タブに切り替えて送ってください。"
                  "このタブでは会話、Web 検索、文章、コードを扱います。")


class Decision(BaseModel):
    """The controller's one decision (§7). Unknown fields are dropped."""
    model_config = ConfigDict(extra="ignore")

    action: Literal["tool", "final"]
    tool: Literal["search", "write", "code", "image", "none"] = "none"
    text: str = ""      # the request for that tool
    reason: str = ""    # for the step log, never the answer
    answer: str = ""    # action=final only: the user-facing text


# --- pure helpers ------------------------------------------------------------------------------------------------


def new_control(settings: ChatSettings, now: float | None = None) -> dict:
    return {"active": True, "steps": 0, "max_steps": max(1, min(4, settings.controller_max_steps)),
            "started": time.time() if now is None else now, "max_wall_clock_s": settings.controller_budget_s,
            "decision": {}, "trace": [], "answer": "", "stop_reason": "", "research": ""}


def normalize(text: str) -> str:
    return " ".join((text or "").split()).lower()


def args_hash(tool: str, text: str) -> str:
    return hashlib.sha256(f"{tool}\n{normalize(text)}".encode("utf-8")).hexdigest()


def remaining(control: dict) -> int:
    return max(0, int(control.get("max_steps", 0)) - int(control.get("steps", 0)))


def over_clock(control: dict, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    return now - float(control.get("started") or now) >= float(control.get("max_wall_clock_s") or 0)


def repeated(control: dict, tool: str, text: str) -> bool:
    digest = args_hash(tool, text)
    return any(entry.get("args_hash") == digest for entry in control.get("trace") or [])


def controller_input(request: str, control: dict) -> str:
    """The user message of one decision: the request, the trace summaries (500 characters each), the steps left.
    Raw pages, card lists, full stdout and image bytes are never part of it (§8)."""
    lines = [f"利用者の依頼:\n{request.strip()[:2000]}", "", "これまでの道具の結果（古い順）:"]
    trace = control.get("trace") or []
    if not trace:
        lines.append("（まだ道具を使っていません）")
    for n, entry in enumerate(trace, start=1):
        status = "成功" if entry.get("ok") else "失敗"
        lines.append(f"{n}. {entry.get('tool')}「{entry.get('text', '')[:120]}」→ {status}: "
                     f"{(entry.get('summary') or '')[:PROMPT_SUMMARY_CHARS]}")
    left = remaining(control)
    lines += ["", f"残りの手数: {left}" + ("（0 なので action は final だけ）" if left == 0 else "")]
    return "\n".join(lines)


def check(decision: Decision | None, control: dict) -> tuple[str, str]:
    """(what to do, why): ("tool", "") runs the tool; ("final", "") answers; ("stop", reason) ends the loop."""
    if decision is None:
        return "stop", "error"
    if decision.action == "final":
        return ("final", "") if decision.answer.strip() else ("stop", "error")
    if decision.tool == "none" or not decision.text.strip():
        return "stop", "error"
    if remaining(control) <= 0:
        return "stop", "max_steps"
    if repeated(control, decision.tool, decision.text):
        return "stop", "repeat"
    if decision.tool == "image":
        return "stop", "image"
    return "tool", ""


def control_task(control: dict) -> dict:
    """The task_trace of the loop's own messages (the UI's step list): every decision's tool and reason."""
    steps = []
    for n, entry in enumerate(control.get("trace") or [], start=1):
        label = TOOL_LABELS.get(entry.get("tool"), entry.get("tool"))
        steps.append({"title": f"{n}. {label}（{'成功' if entry.get('ok') else '失敗'}）",
                      "body": f"理由: {entry.get('reason') or '―'}\n依頼: {entry.get('text', '')}\n"
                              f"結果: {(entry.get('summary') or '')[:300]}"})
    decision = control.get("decision") or {}
    if decision and decision.get("action") == "tool" and not control.get("stop_reason") and \
            len(steps) == int(control.get("steps", 0)):
        steps.append({"title": f"{len(steps) + 1}. {TOOL_LABELS.get(decision.get('tool'), decision.get('tool'))}（実行中）",
                      "body": f"理由: {decision.get('reason') or '―'}\n依頼: {decision.get('text', '')}"})
    if control.get("stop_reason"):
        steps.append({"title": "終了", "body": STOP_LABELS.get(control["stop_reason"], control["stop_reason"])})
    return {"kind": "control", "steps": steps, "step": int(control.get("steps", 0)),
            "max_steps": control.get("max_steps"), "stop_reason": control.get("stop_reason") or ""}


def summarize(state: ChatState, tool: str) -> tuple[str, bool, str]:
    """(summary, ok, research) of the tool that just ended. Only what the controller needs (§9)."""
    error = state.get("error")
    last = state["messages"][-1] if state.get("messages") else None
    shown = ""
    if last is not None and getattr(last, "id", None) == state.get("progress_id"):
        shown, _ = _text_of(last)
    if tool == "search":
        search = state.get("search") or {}
        answer = search.get("answer") or ""
        ok = not error and bool(answer.strip())
        urls = []
        for card in state.get("cards") or []:
            if card != RESET and card.get("url") and card["url"] not in urls:
                urls.append(card["url"])
        summary = (shown or answer or error or "")[:SUMMARY_CHARS - 200]
        if urls:
            summary += "\n出典: " + " ".join(urls[:5])
        stop = search.get("stop_reason")
        if stop:
            summary += f"\n（検索の停止理由: {stop}）"
        return summary[:SUMMARY_CHARS], ok, (shown or answer)[:RESEARCH_CHARS] if ok else ""
    if tool == "write":
        artifact = state.get("artifact") or {}
        draft = artifact.get("draft") or ""
        ok = not error and bool(draft)
        summary = f"状態: {artifact.get('status', '')}、{len(draft)} 字。冒頭: {draft[:800]}" if draft else \
            f"本文なし（{error or shown[:300]}）"
        return summary[:SUMMARY_CHARS], ok, ""
    if tool == "code":
        code = state.get("code") or {}
        if code.get("last_exit") is not None or code.get("runs"):
            summary = (f"実行 {code.get('round', 0)} 回、成功={bool(code.get('last_ok'))}、step={code.get('last_step')}、"
                       f"終了コード {code.get('last_exit')}"
                       + ("、時間切れ" if code.get("timed_out") else "")
                       + f"\nstdout 末尾: {(code.get('stdout_tail') or '')[-400:]}"
                       + f"\nstderr 末尾: {(code.get('stderr_tail') or '')[-400:]}")
            ok = bool(code.get("last_ok")) and not error
        else:
            files = ", ".join(f.get("path", "") for f in code.get("files") or [])
            reason = code.get("skipped") or ("実行は却下されました" if error == "rejected" else
                                             "実行していません" if not error else f"失敗: {error}")
            summary = f"ファイル: {files or 'なし'}（{code.get('artifact_dir', '')}）。{reason}"
            ok = bool(code.get("files")) and not error
        return summary[:SUMMARY_CHARS], ok, ""
    return (shown or error or "")[:SUMMARY_CHARS], not error, ""


def fallback_answer(control: dict) -> str:
    """The text of a loop that stopped without a final answer from the model."""
    trace = control.get("trace") or []
    if not trace:
        return "次の手を決められませんでした（判断モデルの応答を読めません）。/search、/write、/code で道具を指定して送り直してください。"
    last = trace[-1]
    if last.get("tool") == "write" and last.get("ok"):
        return "本文は上のメッセージに書きました。"
    if last.get("tool") == "code" and last.get("ok"):
        return "コードと実行結果は上のメッセージのとおりです。"
    return "ここまでの結果:\n\n" + (last.get("summary") or "（結果がありません）")[:800]


def final_text(control: dict) -> str:
    """The loop's last message. After writing, the draft is already a message of its own: it is never posted
    again (§9 write), only a short note."""
    reason = control.get("stop_reason") or "final"
    if reason == "image":
        return IMAGE_TAB_TEXT
    trace = control.get("trace") or []
    answer = (control.get("answer") or "").strip() if reason == "final" else ""
    wrote = next((e for e in reversed(trace) if e.get("tool") == "write" and e.get("ok")), None)
    if wrote is not None and trace and trace[-1] is wrote:
        note = "本文は上のメッセージに書きました。"
        if answer and len(answer) <= 300:
            note = f"{answer}\n\n{note}" if note not in answer else answer
        answer = note
    if not answer:
        answer = fallback_answer(control)
    if reason not in ("final", "image"):
        answer += f"\n\n（{STOP_LABELS.get(reason, reason)}で終了しました）"
    return answer


# --- nodes --------------------------------------------------------------------------------------------------------


def _request(state: ChatState) -> str:
    human = _last_human(state)
    return _text_of(human)[0] if human is not None else ""


async def _decide(state: ChatState, config: RunnableConfig, settings: ChatSettings, token: str,
                  control: dict) -> tuple[Decision | None, str]:
    """One decision with the 27B (thinking off, JSON); the proxy leader when LM Studio is down. A final without
    an answer is asked once more."""
    lmstudio = _lmstudio(config, settings)
    messages = [{"role": "system", "content": await _prompt(settings, PROMPT)},
                {"role": "user", "content": controller_input(_request(state), control)}]
    proxy = False
    if await lmstudio.reachable():
        client, label = lmstudio, LEADER_LABEL
    else:
        client, label = await _leader(config, settings, token, "synthesize")
        proxy = True
    try:
        async with _held(token):
            decision = await sa.ask_json(client, messages, Decision,
                                         max_tokens=capped(settings, client, DECISION_TOKENS), temperature=0.2)
            if decision is not None and decision.action == "final" and not decision.answer.strip():
                log.info("controller: final without an answer; asking once more")
                decision = await sa.ask_json(client, messages, Decision,
                                             max_tokens=capped(settings, client, DECISION_TOKENS), temperature=0.2)
    finally:
        if proxy:
            # The next tool loads its own models (the planner, the readers): the proxy does not stay.
            leader = _leaders.pop(token, None)
            if leader is not None:
                await leader[0].stop()
    return decision, label


async def controller(state: ChatState, config: RunnableConfig) -> dict:
    settings = _settings(config)
    lmstudio = _lmstudio(config, settings)
    control = dict(state.get("control") or {})
    token = state.get("lock_token")
    if over_clock(control):
        control["stop_reason"] = "wall_clock"
        return {"control": control}
    try:
        token = await _lock(state, config, settings)
        decision, label = await _decide(state, config, settings, token, control)
    except asyncio.CancelledError:
        await asyncio.shield(_cleanup(token, lmstudio, unload=True))
        raise
    except JobLockBusy as exc:
        return _fail(state, exc, "lock")
    except (LLMError, WorkerError, SelectionError, OSError) as exc:
        log.warning("controller: no decision: %s", exc)
        decision, label = None, ""
    what, why = check(decision, control)
    control["decision"] = decision.model_dump() if decision else {}
    log.info("controller step=%d/%d decider=%s action=%s tool=%s -> %s %s", control.get("steps", 0),
             control.get("max_steps", 0), label, decision.action if decision else None,
             decision.tool if decision else None, what, why)
    if what == "final":
        control.update({"answer": decision.answer.strip(), "stop_reason": "final"})
        return {"control": control, "lock_token": token}
    if what == "stop":
        if why == "error" and decision is not None and decision.action == "final":
            # A final without an answer twice: the last summary is the answer (§7).
            control.update({"answer": fallback_answer(control), "stop_reason": "final"})
        else:
            control["stop_reason"] = why
        return {"control": control, "lock_token": token}
    tool, text = decision.tool, decision.text.strip()
    route = {"kind": KIND[tool], "text": text, "urls": find_urls(text), "reason": f"制御: {decision.reason[:120]}",
             "explicit": False, "needs_search": False, "continuation": False, "long": False,
             "claim_verify": settings.claim_verify, "claim_fail_open": settings.claim_fail_open,
             "search_first": False, "search_skipped": False}
    update: dict[str, Any] = {}
    if tool == "search":
        # A fresh search state per call: the cards of an earlier search are not mixed into this one.
        update = {"search": {}, "hits": [RESET], "cards": [RESET], "logs": [RESET], "evidence": [RESET],
                  "claims": [], "claim_audit": [], "verify_error": None}
    elif tool == "write":
        research = control.get("research") or ""
        # A search already ran in this loop: the writer reads its answer and never searches again (§9).
        route.update({"long": long_request(text), "search_first": bool(research),
                      "search_skipped": any(e.get("tool") == "search" for e in control.get("trace") or [])})
        update = {"artifact": {**(state.get("artifact") or {}), "research": research}}
    note = f"{TOOL_LABELS[tool]}を使います（{decision.reason[:120] or '次の手'}）…"
    view = {**state, "control": control}
    return {**update, "control": control, "route": {**(state.get("route") or {}), **route}, "lock_token": token,
            "error": None, "messages": [_progress(view, note, task=control_task(control))]}


def _after_controller(state: ChatState) -> str:
    if state.get("error"):
        return END
    control = state.get("control") or {}
    if control.get("stop_reason"):
        return "finish"
    return ENTRY[(control.get("decision") or {}).get("tool")]


async def controller_record(state: ChatState, config: RunnableConfig) -> dict:
    """A tool's pipeline ended (success, failure, or the user's reject): record it and check the budget."""
    settings = _settings(config)
    control = dict(state.get("control") or {})
    decision = control.get("decision") or {}
    tool = decision.get("tool") or ""
    summary, ok, research = summarize(state, tool)
    entry = {"tool": tool, "text": (decision.get("text") or "")[:300], "reason": (decision.get("reason") or "")[:200],
             "args_hash": args_hash(tool, decision.get("text") or ""), "summary": summary, "ok": ok}
    control["trace"] = [*(control.get("trace") or []), entry]
    control["steps"] = int(control.get("steps", 0)) + 1
    if research:
        control["research"] = research
    token = state.get("lock_token")
    if token and job_lock.holds(token):
        # Every tool gives the lock back at its end; a failure path that did not is cleaned up here.
        await _cleanup(token, _lmstudio(config, settings), unload=True)
    error = state.get("error")
    if error in ("stopped", "rejected"):
        control["stop_reason"] = "rejected"
    elif over_clock(control):
        control["stop_reason"] = "wall_clock"
    log.info("controller_record tool=%s ok=%s steps=%d/%d stop=%s", tool, ok, control["steps"],
             control.get("max_steps", 0), control.get("stop_reason") or "-")
    # The tool's last message keeps its id (it stays in the thread as it was); the loop's next messages get a
    # new one so they never replace it.
    progress_id = f"progress-{uuid.uuid4()}"
    view = {**state, "control": control, "progress_id": progress_id}
    text = ("結果を確認して次の手を考えています…" if not control.get("stop_reason") else "まとめています…")
    return {"control": control, "error": None, "lock_token": None, "progress_id": progress_id,
            "messages": [AIMessage(id=progress_id, content=text,
                                   additional_kwargs=_kwargs(view, task=control_task(control)))]}


def _after_record(state: ChatState) -> str:
    return "finish" if (state.get("control") or {}).get("stop_reason") else "controller"


async def finish(state: ChatState, config: RunnableConfig) -> dict:
    control = dict(state.get("control") or {})
    control.setdefault("stop_reason", "final")
    text = final_text(control)
    # Like plain chat, the 27B stays loaded (LM Studio's TTL unloads it; the image workflow ejects it anyway).
    await _cleanup(state.get("lock_token"))
    log.info("controller finished stop=%s steps=%d tools=%s", control.get("stop_reason"), control.get("steps", 0),
             [e.get("tool") for e in control.get("trace") or []])
    return {"control": control, "lock_token": None,
            "messages": [_final({**state, "control": control}, text, task=control_task(control))]}


def add_nodes(builder: Any) -> None:
    builder.add_node("controller", controller)
    builder.add_node(CONTROL_RECORD, controller_record)
    builder.add_node("finish", finish)
    builder.add_conditional_edges("controller", _after_controller, [*ENTRY.values(), "finish", END])
    builder.add_conditional_edges(CONTROL_RECORD, _after_record, ["controller", "finish"])
    builder.add_edge("finish", END)
