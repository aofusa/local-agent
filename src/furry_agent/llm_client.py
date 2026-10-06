"""OpenAI-compatible chat client for the chat tab: the llama.cpp router (127.0.0.1:8080) and the llama-server workers.

The image graph never imports this module: on the image path only the ComfyUI workflow talks to the router
(AGENTS.md). The chat tab calls the router directly for conversation, the search plan and the final answer,
and unloads the 27B through the router's model API (POST /models/unload) before Bonsai takes the memory.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

log = logging.getLogger("furry_agent.llm")

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_THINK_OPEN = re.compile(r"<think>.*", re.DOTALL | re.IGNORECASE)
_THINK_CLOSE = re.compile(r"^.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


class LLMError(RuntimeError):
    pass


def split_thinking(text: str) -> tuple[str, str]:
    """(answer, thinking): <think> blocks are taken out of the answer and returned separately."""
    text = text or ""
    thoughts = [m.group(0)[7:-8] for m in _THINK_BLOCK.finditer(text)]
    rest = _THINK_BLOCK.sub("", text)
    if "</think>" in rest.lower():  # the opening tag was in the chat template, only the close came back
        head = _THINK_CLOSE.match(rest)
        if head:
            thoughts.append(head.group(0)[:-8])
        rest = _THINK_CLOSE.sub("", rest)
    tail = _THINK_OPEN.search(rest)
    if tail:  # cut off while still thinking
        thoughts.append(tail.group(0)[7:])
        rest = rest[:tail.start()]
    return rest.strip(), "\n".join(t.strip() for t in thoughts if t.strip())


def strip_thinking(text: str) -> str:
    text = _THINK_BLOCK.sub("", text or "")
    if "</think>" in text.lower():
        text = _THINK_CLOSE.sub("", text)
    text = _THINK_OPEN.sub("", text)
    return text.strip()


def parse_json_object(text: str) -> dict | None:
    """The first JSON object in a model reply (fences and thinking removed), or None."""
    text = _FENCE.sub("", strip_thinking(text).strip()).strip()
    start = text.find("{")
    while start != -1:
        depth, in_str, escape = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        value = json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
                    return value if isinstance(value, dict) else None
        start = text.find("{", start + 1)
    return None


@dataclass
class ToolCall:
    name: str
    arguments: dict
    id: str = ""


@dataclass
class ChatReply:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: dict = field(default_factory=dict)
    seconds: float = 0.0
    # Thinking tokens, never part of ``content`` (design doc §6.2): reasoning_content / reasoning / <think>.
    reasoning: str = ""

    @property
    def tokens_per_s(self) -> float | None:
        timings = self.raw.get("timings") or {}
        return timings.get("predicted_per_second")


def idle_timeout(idle_s: float) -> httpx.Timeout:
    """Wait as long as bytes keep coming; give up after ``idle_s`` seconds without any (httpx's read timeout is the
    time between two reads, not the whole response)."""
    return httpx.Timeout(connect=30.0, read=idle_s, write=60.0, pool=60.0)


def _message_from_stream(chunks: list[dict]) -> tuple[dict, dict]:
    """(message, raw) from the parsed chunks of a streamed completion: content, reasoning and the tool calls put
    back together; the last usage / timings / finish_reason kept in ``raw``."""
    content, reasoning = [], []
    calls: dict[int, dict] = {}
    raw: dict = {"choices": [{"finish_reason": None}]}
    for chunk in chunks:
        for key in ("usage", "timings", "model", "id"):
            if chunk.get(key):
                raw[key] = chunk[key]
        choice = (chunk.get("choices") or [{}])[0] if chunk.get("choices") else {}
        if choice.get("finish_reason"):
            raw["choices"][0]["finish_reason"] = choice["finish_reason"]
        delta = choice.get("delta") or choice.get("message") or {}
        if delta.get("content"):
            content.append(delta["content"])
        thought = delta.get("reasoning_content") or delta.get("reasoning")
        if thought:
            reasoning.append(thought)
        for call in delta.get("tool_calls") or []:
            slot = calls.setdefault(int(call.get("index", len(calls))), {"id": "", "name": "", "arguments": ""})
            slot["id"] = call.get("id") or slot["id"]
            fn = call.get("function") or {}
            slot["name"] = fn.get("name") or slot["name"]
            args = fn.get("arguments")
            if args:
                slot["arguments"] += args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
    message = {"content": "".join(content), "reasoning_content": "".join(reasoning),
               "tool_calls": [{"id": c["id"], "function": {"name": c["name"], "arguments": c["arguments"]}}
                              for _, c in sorted(calls.items())]}
    return message, raw


class OpenAICompatClient:
    """``timeout_s`` is an idle timeout: the reply is streamed, and only a stretch of ``timeout_s`` seconds without
    any byte from the server (no token, no thinking token, no keep-alive) ends the call. A long reply that keeps
    coming is never cut off; the 27B on a small machine can take many minutes."""

    def __init__(self, base_url: str, model: str = "", timeout_s: float = 1200.0, *, thinking_off: bool = True,
                 transport: httpx.AsyncBaseTransport | None = None, api_key: str = "local"):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_s = timeout_s
        self.thinking_off = thinking_off
        self._transport = transport

    def _http(self, timeout: float | None = None) -> httpx.AsyncClient:
        kwargs: dict = {"timeout": idle_timeout(timeout or self.timeout_s), "trust_env": False,
                        "headers": {"Authorization": f"Bearer {self.api_key}"}}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.AsyncClient(**kwargs)

    async def chat(self, messages: list[dict], *, max_tokens: int = 1024, temperature: float = 0.4,
                   tools: list[dict] | None = None, tool_choice: str | None = None,
                   json_mode: bool = False, json_schema: dict | None = None,
                   timeout_s: float | None = None, thinking: bool | None = None) -> ChatReply:
        """``thinking``: True = thinking tokens on (the chat tab's think mode), False = off, None = the client's
        default (off unless the client was made with thinking_off=False). ``timeout_s``: the idle timeout."""
        body: dict = {"messages": messages, "max_tokens": max_tokens, "temperature": temperature, "stream": True,
                      "stream_options": {"include_usage": True}}
        if self.model:
            body["model"] = self.model
        think = (not self.thinking_off) if thinking is None else thinking
        # llama-server reads chat_template_kwargs; the router's preset defaults to thinking off (scripts/setup-llm.ps1),
        # and the thoughts come back in reasoning_content (--reasoning-format deepseek), never in content.
        body["chat_template_kwargs"] = {"enable_thinking": think}
        if tools:
            body["tools"] = tools
            if tool_choice:
                body["tool_choice"] = tool_choice
        if json_schema:
            # Grammar-constrained output (llama-server accepts OpenAI's json_schema format).
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": "reply", "strict": True, "schema": json_schema}}
        elif json_mode:
            body["response_format"] = {"type": "json_object"}
        idle = timeout_s or self.timeout_s
        started = time.monotonic()
        data: dict = {}
        message: dict = {}
        try:
            async with self._http(idle) as http:
                async with http.stream("POST", f"{self.base_url}/chat/completions", json=body) as response:
                    if response.status_code >= 400:
                        text = (await response.aread()).decode("utf-8", "replace")
                        raise LLMError(f"HTTP {response.status_code}: {text[:300]}")
                    if "text/event-stream" not in response.headers.get("content-type", ""):
                        # A server that ignores "stream": the whole JSON at once.
                        data = json.loads(await response.aread())
                        message = ((data.get("choices") or [{}])[0]).get("message") or {}
                    else:
                        chunks = []
                        async for line in response.aiter_lines():
                            line = line.strip()
                            if not line.startswith("data:"):
                                continue
                            payload = line[5:].strip()
                            if payload == "[DONE]":
                                break
                            try:
                                chunks.append(json.loads(payload))
                            except json.JSONDecodeError:
                                continue
                        message, data = _message_from_stream(chunks)
        except httpx.TimeoutException as exc:
            raise LLMError(f"{idle:.0f} 秒間応答がありませんでした") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"{self.base_url} に接続できません: {exc!r}") from exc
        calls = []
        for call in message.get("tool_calls") or []:
            fn = call.get("function") or {}
            args = fn.get("arguments")
            if isinstance(args, str):
                args = parse_json_object(args) or {}
            calls.append(ToolCall(fn.get("name") or "", args or {}, call.get("id") or ""))
        content, inline = split_thinking(message.get("content") or "")
        reasoning = "\n".join(t for t in (str(message.get("reasoning_content") or message.get("reasoning") or "").strip(),
                                          inline) if t)
        return ChatReply(content, calls, data, time.monotonic() - started, reasoning)


class LlamaRouter(OpenAICompatClient):
    """llama-server in router mode (scripts/start-llm.ps1): the OpenAI-compatible endpoint, plus the router's model API
    (GET /models with each model's status, POST /models/unload). A request names the model of the preset
    (LLM_MODEL) and the router starts it on demand; unloading stops that child process, so its memory is free."""

    def native(self) -> str:
        parsed = urlparse(self.base_url)
        return f"{parsed.scheme}://{parsed.netloc}"

    async def reachable(self) -> bool:
        try:
            async with self._http(5) as http:
                return (await http.get(f"{self.base_url}/models")).status_code == 200
        except httpx.HTTPError:
            return False

    async def model_ids(self, timeout_s: float = 5.0) -> list[str]:
        """Every model the router offers (its preset sections, and anything else it lists)."""
        async with self._http(timeout_s) as http:
            response = await http.get(f"{self.native()}/models")
            response.raise_for_status()
            payload = response.json()
        return [str(e.get("id") or e.get("name") or "") for e in payload.get("data") or payload.get("models") or []]

    async def loaded(self) -> list[str]:
        """Models whose child process exists (loaded, loading or still unloading)."""
        async with self._http(15) as http:
            response = await http.get(f"{self.native()}/models")
            response.raise_for_status()
            payload = response.json()
        return resident_models(payload)

    async def unload_all(self, wait_s: float = 60.0) -> list[str]:
        """Unload every model and wait until the router reports none; raises LLMError if one is still resident."""
        import asyncio

        unloaded: list[str] = []
        deadline = time.monotonic() + wait_s
        asked: set[str] = set()
        while True:
            resident = await self.loaded()
            if not resident:
                log.info("LLM router: nothing loaded (unloaded=%s)", unloaded)
                return unloaded
            if time.monotonic() > deadline:
                break
            async with self._http(60) as http:
                for model in resident:
                    if model in asked:
                        continue
                    await http.post(f"{self.native()}/models/unload", json={"model": model})
                    asked.add(model)
                    unloaded.append(model)
            await asyncio.sleep(1.0)
        raise LLMError(f"LLM サーバのモデルを unload できません: {resident}")


RESIDENT = ("loaded", "loading", "unloading", "sleeping")


def resident_models(payload: dict) -> list[str]:
    """Ids of the router's models that hold memory (``status.value`` other than unloaded / failed)."""
    found = []
    for entry in payload.get("data") or payload.get("models") or []:
        status = entry.get("status") or {}
        value = status.get("value") if isinstance(status, dict) else status
        if value in RESIDENT and entry.get("id"):
            found.append(entry["id"])
    return found
