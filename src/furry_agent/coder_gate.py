"""The model gate for the cirka CUI (docs/locus-cui-design.md §6.1): ``POST /coder/turn``.

One stateless completion turn: the CUI sends its messages and tool definitions, the gate passes them to the LM
Studio 27B (OpenAI-compatible tool calling) and streams the reply back as server-sent events. The gate never runs
a tool, never rewrites the system prompt and keeps nothing (no thread, no source text in a store): the CUI's
session file is the history. Tools run on the machine where the CUI was started.

    event: status     {"state": "waiting", "holder": "image"}   while the shared job lock is held elsewhere
    event: thinking   {"text": "..."}                             think mode only, never part of the answer
    event: token      {"text": "..."}
    event: tool_call  {"id": "call_1", "name": "grep", "arguments": "{...}"}   after the stream ended
    event: done       {"finish_reason": "tool_calls" | "stop" | "length", "usage": {...}}
    event: error      {"message": "...", "code": "context_overflow" | "lmstudio" | "busy" | "bad_request"}

The turn holds ``job_lock`` (tab "coder") like the chat tab, so it never runs next to an image generation or a
search; it waits while the other tab works (the CUI shows the wait, it is not an error). LM Studio stays on
loopback: only this process talks to it. Mounted on the LangGraph server through langgraph.json ``http.app``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from furry_agent import modes
from furry_agent.chat_common import _image_tab_busy, prompt_tokens
from furry_agent.config import ChatSettings
from furry_agent.job_lock import job_lock
from furry_agent.llm_client import LMStudio, idle_timeout
from furry_agent.router import CHAT, Route as ChatRoute

log = logging.getLogger("furry_agent.coder")

ROLES = ("system", "user", "assistant", "tool")
DEFAULT_MAX_TOKENS = 2048
MIN_ROOM = 256          # less room than this for the reply: the CUI must compact (context_overflow)
STATUS_EVERY_S = 5.0


class GateError(ValueError):
    def __init__(self, message: str, code: str = "bad_request"):
        super().__init__(message)
        self.code = code


@dataclass
class Turn:
    messages: list[dict]
    tools: list[dict]
    mode: str
    max_tokens: int
    temperature: float = 0.2


@dataclass
class _Call:
    id: str = ""
    name: str = ""
    arguments: str = ""


@dataclass
class Collected:
    """What one streamed reply came to: the answer, the thinking and the assembled tool calls."""
    text: str = ""
    thinking: str = ""
    calls: dict[int, _Call] = field(default_factory=dict)
    finish_reason: str = ""
    usage: dict = field(default_factory=dict)


def openai_tools(tools: list[Any]) -> list[dict]:
    """{name, description, parameters} (the CUI's form) or OpenAI's {type: function, function: {...}}."""
    out = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            raise GateError("tools の要素はオブジェクトです")
        fn = tool.get("function") if tool.get("type") == "function" else tool
        if not isinstance(fn, dict) or not str(fn.get("name") or "").strip():
            raise GateError("tool に name がありません")
        params = fn.get("parameters") or {"type": "object", "properties": {}}
        out.append({"type": "function", "function": {"name": str(fn["name"]), "description": str(fn.get("description") or ""),
                                                      "parameters": params}})
    return out


def clean_messages(messages: Any) -> list[dict]:
    """The chat messages as LM Studio reads them; anything else is refused (never silently changed)."""
    if not isinstance(messages, list) or not messages:
        raise GateError("messages がありません")
    out = []
    for m in messages:
        if not isinstance(m, dict) or m.get("role") not in ROLES:
            raise GateError("messages の role は system / user / assistant / tool のどれかです")
        msg: dict[str, Any] = {"role": m["role"], "content": m.get("content") if m.get("content") is not None else ""}
        if not isinstance(msg["content"], str):
            raise GateError("content は文字列です")
        if m["role"] == "assistant" and m.get("tool_calls"):
            calls = []
            for c in m["tool_calls"]:
                fn = c.get("function") or c
                args = fn.get("arguments", "{}")
                calls.append({"id": str(c.get("id") or ""), "type": "function",
                              "function": {"name": str(fn.get("name") or ""),
                                           "arguments": args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)}})
            msg["tool_calls"] = calls
        if m["role"] == "tool":
            msg["tool_call_id"] = str(m.get("tool_call_id") or "")
        out.append(msg)
    return out


def resolve_mode(requested: Any, messages: list[dict]) -> str:
    """fast | think; auto uses the chat tab's rules on the last user message."""
    mode = modes.requested_mode(requested if requested is not None else modes.AUTO)
    if mode != modes.AUTO:
        return mode
    last = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    return modes.auto_mode(ChatRoute(CHAT, last))[0]


def parse_turn(body: Any) -> Turn:
    if not isinstance(body, dict):
        raise GateError("JSON オブジェクトを送ってください")
    messages = clean_messages(body.get("messages"))
    tools = openai_tools(body.get("tools") or [])
    try:
        max_tokens = int(body.get("max_tokens") or DEFAULT_MAX_TOKENS)
        temperature = float(body.get("temperature", 0.2))
    except (TypeError, ValueError) as exc:
        raise GateError("max_tokens / temperature は数値です") from exc
    return Turn(messages, tools, resolve_mode(body.get("mode"), messages), max(1, max_tokens),
                min(max(temperature, 0.0), 1.5))


def budget(turn: Turn, settings: ChatSettings, client: LMStudio) -> tuple[int, int]:
    """(max_tokens, prompt estimate). The 27B runs with LMSTUDIO_CONTEXT (4096 on this machine): the prompt plus
    the tool schemas plus the reply must fit. There is no time cap: the stream has an idle timeout only."""
    estimate = prompt_tokens(turn.messages)
    if turn.tools:
        estimate += prompt_tokens([{"content": json.dumps(turn.tools, ensure_ascii=False)}])
    room = settings.lmstudio_ctx - estimate - 48
    if room < MIN_ROOM:
        raise GateError(f"文脈が足りません（推定 {estimate} トークン、窓 {settings.lmstudio_ctx}）。会話を圧縮してください",
                        "context_overflow")
    return min(turn.max_tokens, room), estimate


def sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")


def apply_chunk(chunk: dict, out: Collected) -> tuple[str, str]:
    """Fold one streamed chunk into ``out``; returns (token text, thinking text) to forward now."""
    if chunk.get("usage"):
        out.usage = chunk["usage"]
    choice = (chunk.get("choices") or [{}])[0] if chunk.get("choices") else {}
    delta = choice.get("delta") or {}
    if choice.get("finish_reason"):
        out.finish_reason = choice["finish_reason"]
    text = delta.get("content") or ""
    thinking = delta.get("reasoning_content") or delta.get("reasoning") or ""
    out.text += text
    out.thinking += thinking
    for call in delta.get("tool_calls") or []:
        index = int(call.get("index", len(out.calls)))
        slot = out.calls.setdefault(index, _Call())
        slot.id = call.get("id") or slot.id
        fn = call.get("function") or {}
        slot.name = fn.get("name") or slot.name
        args = fn.get("arguments")
        if args:
            slot.arguments += args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
    return text, thinking


async def stream_lmstudio(client: LMStudio, turn: Turn, max_tokens: int, timeout_s: float,
                          transport: httpx.AsyncBaseTransport | None = None) -> AsyncIterator[dict]:
    """The parsed chunks of one streamed Chat Completions call. A refused start (HTTP error) is retried once."""
    think = turn.mode == modes.THINK
    body: dict[str, Any] = {"messages": turn.messages, "max_tokens": max_tokens, "temperature": turn.temperature,
                            "stream": True, "stream_options": {"include_usage": True},
                            "chat_template_kwargs": {"enable_thinking": think}, **client.thinking_body(think)}
    if client.model:
        body["model"] = client.model
    if turn.tools:
        body["tools"] = turn.tools
        body["tool_choice"] = "auto"
    kwargs: dict[str, Any] = {"timeout": idle_timeout(timeout_s), "trust_env": False}
    if transport is not None:
        kwargs["transport"] = transport
    for attempt in (1, 2):
        async with httpx.AsyncClient(**kwargs) as http:
            try:
                async with http.stream("POST", f"{client.base_url}/chat/completions", json=body) as response:
                    if response.status_code >= 400:
                        detail = (await response.aread()).decode("utf-8", "replace")[:300]
                        if attempt == 1:
                            log.info("coder turn: LM Studio refused (HTTP %s); once more", response.status_code)
                            continue
                        raise GateError(f"LM Studio が拒否しました（HTTP {response.status_code}: {detail}）", "lmstudio")
                    async for line in response.aiter_lines():
                        line = line.strip()
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            return
                        try:
                            yield json.loads(data)
                        except json.JSONDecodeError:
                            continue
                    return
            except httpx.TimeoutException as exc:
                raise GateError(f"LM Studio が {timeout_s:.0f} 秒間応答しませんでした", "lmstudio") from exc
            except httpx.HTTPError as exc:
                raise GateError(f"LM Studio に接続できません: {exc!r}", "lmstudio") from exc


async def _acquire(settings: ChatSettings, comfy_config: dict | None) -> AsyncIterator[str | dict]:
    """Yields status dicts while waiting, then the lock token (str). The other job is waited for while it works
    (the holder renews its lease; the image run has its own idle timeout); JOB_LOCK_TIMEOUT_S, when set, bounds
    the wait with GateError("busy"). The status events keep the CUI's idle timer from running out."""
    limit = settings.job_lock_timeout_s
    started = time.monotonic()
    last_status = 0.0
    while True:
        token = job_lock.try_acquire("coder")
        if token:
            if not await _image_tab_busy(comfy_config, settings):
                yield token
                return
            job_lock.release(token)
            holder = "image"
        else:
            holder = job_lock.holder or "?"
        now = time.monotonic()
        if limit is not None and now - started > limit:
            raise GateError(f"{holder} の処理が終わりません（{limit:.0f} 秒待ちました）", "busy")
        if now - last_status >= STATUS_EVERY_S:
            last_status = now
            yield {"state": "waiting", "holder": holder, "waited_s": round(now - started)}
        await asyncio.sleep(0.5)


async def run_turn(turn: Turn, settings: ChatSettings, *, client: LMStudio | None = None,
                   transport: httpx.AsyncBaseTransport | None = None,
                   comfy_config: dict | None = None) -> AsyncIterator[bytes]:
    """The SSE body of one turn. Tool calls are sent only after the stream ended (a broken stream sends none)."""
    client = client or LMStudio(settings.lmstudio_url, settings.lmstudio_model, settings.idle_timeout_s)
    token = None
    started = time.monotonic()
    try:
        max_tokens, estimate = budget(turn, settings, client)
        async for item in _acquire(settings, comfy_config):
            if isinstance(item, str):
                token = item
            else:
                yield sse("status", item)
        out = Collected()
        async for chunk in stream_lmstudio(client, turn, max_tokens, settings.idle_timeout_s, transport):
            job_lock.renew(token)
            text, thinking = apply_chunk(chunk, out)
            if thinking:
                yield sse("thinking", {"text": thinking})
            if text:
                yield sse("token", {"text": text})
        for n, call in sorted(out.calls.items()):
            yield sse("tool_call", {"id": call.id or f"call_{n + 1}", "name": call.name,
                                    "arguments": call.arguments or "{}"})
        finish = out.finish_reason or ("tool_calls" if out.calls else "stop")
        usage = {"input_tokens": out.usage.get("prompt_tokens", estimate),
                 "output_tokens": out.usage.get("completion_tokens", 0)}
        # Sizes only: the workspace's text is never logged on the host.
        log.info("coder turn mode=%s messages=%d tools=%d calls=%d finish=%s tokens=%s seconds=%.1f", turn.mode,
                 len(turn.messages), len(turn.tools), len(out.calls), finish, usage["output_tokens"],
                 time.monotonic() - started)
        yield sse("done", {"finish_reason": finish, "usage": usage, "mode": turn.mode})
    except GateError as exc:
        log.info("coder turn failed code=%s: %s", exc.code, exc)
        yield sse("error", {"message": str(exc), "code": exc.code})
    finally:
        if token:
            job_lock.release(token)


# --- HTTP --------------------------------------------------------------------------------------------------------


def _settings(request: Request) -> ChatSettings:
    return getattr(request.app.state, "chat_settings", None) or ChatSettings.from_env()


async def turn_endpoint(request: Request):
    settings = _settings(request)
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse({"error": "JSON を送ってください", "code": "bad_request"}, status_code=400)
    try:
        turn = parse_turn(body)
    except GateError as exc:
        return JSONResponse({"error": str(exc), "code": exc.code}, status_code=400)
    state = request.app.state
    stream = run_turn(turn, settings, client=getattr(state, "lmstudio", None),
                      transport=getattr(state, "transport", None), comfy_config=getattr(state, "comfy_config", None))
    return StreamingResponse(stream, media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def health_endpoint(request: Request):
    settings = _settings(request)
    client = getattr(request.app.state, "lmstudio", None) or LMStudio(settings.lmstudio_url, settings.lmstudio_model, 5)
    return JSONResponse({"ok": True, "gate": "coder", "version": 1, "lmstudio": await client.reachable(),
                         "model": settings.lmstudio_model, "context": settings.lmstudio_ctx,
                         "busy": job_lock.holder, "graphs": ["agent", "chat"]})


app = Starlette(routes=[Route("/coder/turn", turn_endpoint, methods=["POST"]),
                        Route("/coder/health", health_endpoint, methods=["GET"])])
