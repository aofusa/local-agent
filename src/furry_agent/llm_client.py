"""OpenAI-compatible chat client for the chat tab: LM Studio (127.0.0.1:1234) and the llama-server workers.

The image graph never imports this module: on the image path only the ComfyUI workflow talks to LM Studio
(AGENTS.md). The chat tab calls LM Studio directly for conversation, the search plan and the final answer,
and unloads it through LM Studio's native REST API (/api/v1/models/unload) before Bonsai takes the memory.
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

    @property
    def tokens_per_s(self) -> float | None:
        timings = self.raw.get("timings") or {}
        return timings.get("predicted_per_second")


class OpenAICompatClient:
    def __init__(self, base_url: str, model: str = "", timeout_s: float = 180.0, *, thinking_off: bool = True,
                 transport: httpx.AsyncBaseTransport | None = None, api_key: str = "local"):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_s = timeout_s
        self.thinking_off = thinking_off
        self._transport = transport

    def _http(self, timeout: float | None = None) -> httpx.AsyncClient:
        kwargs: dict = {"timeout": timeout or self.timeout_s, "trust_env": False,
                        "headers": {"Authorization": f"Bearer {self.api_key}"}}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.AsyncClient(**kwargs)

    async def chat(self, messages: list[dict], *, max_tokens: int = 1024, temperature: float = 0.4,
                   tools: list[dict] | None = None, tool_choice: str | None = None,
                   json_mode: bool = False, json_schema: dict | None = None,
                   timeout_s: float | None = None) -> ChatReply:
        body: dict = {"messages": messages, "max_tokens": max_tokens, "temperature": temperature, "stream": False}
        if self.model:
            body["model"] = self.model
        if self.thinking_off:
            # llama-server reads chat_template_kwargs; LM Studio applies the per-model "thinking off" default
            # written by scripts/setup-lmstudio.ps1 and ignores unknown keys.
            body["chat_template_kwargs"] = {"enable_thinking": False}
        if tools:
            body["tools"] = tools
            if tool_choice:
                body["tool_choice"] = tool_choice
        if json_schema:
            # Grammar-constrained output (llama-server and LM Studio both accept OpenAI's json_schema format).
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": "reply", "strict": True, "schema": json_schema}}
        elif json_mode:
            body["response_format"] = {"type": "json_object"}
        started = time.monotonic()
        try:
            async with self._http(timeout_s) as http:
                response = await http.post(f"{self.base_url}/chat/completions", json=body)
        except httpx.TimeoutException as exc:
            raise LLMError(f"時間切れです（{timeout_s or self.timeout_s:.0f} 秒）") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"{self.base_url} に接続できません: {exc!r}") from exc
        if response.status_code >= 400:
            raise LLMError(f"HTTP {response.status_code}: {response.text[:300]}")
        data = response.json()
        message = ((data.get("choices") or [{}])[0]).get("message") or {}
        calls = []
        for call in message.get("tool_calls") or []:
            fn = call.get("function") or {}
            args = fn.get("arguments")
            if isinstance(args, str):
                args = parse_json_object(args) or {}
            calls.append(ToolCall(fn.get("name") or "", args or {}, call.get("id") or ""))
        return ChatReply(strip_thinking(message.get("content") or ""), calls, data, time.monotonic() - started)


class LMStudio(OpenAICompatClient):
    """LM Studio's OpenAI-compatible endpoint plus its native REST API for the loaded-model state."""

    def native(self) -> str:
        parsed = urlparse(self.base_url)
        return f"{parsed.scheme}://{parsed.netloc}/api/v1"

    async def reachable(self) -> bool:
        try:
            async with self._http(5) as http:
                return (await http.get(f"{self.base_url}/models")).status_code == 200
        except httpx.HTTPError:
            return False

    async def loaded(self) -> list[tuple[str, str]]:
        async with self._http(15) as http:
            response = await http.get(f"{self.native()}/models")
            response.raise_for_status()
            payload = response.json()
        found = []
        for entry in payload.get("models") or payload.get("data") or []:
            if entry.get("type") not in (None, "llm", "vlm"):
                continue
            for inst in entry.get("loaded_instances") or []:
                inst_id = inst.get("id") if isinstance(inst, dict) else inst
                if inst_id:
                    found.append((entry.get("key") or "?", inst_id))
        return found

    async def unload_all(self, retries: int = 3) -> list[str]:
        """Unload every LLM and verify; raises LLMError if one is still resident."""
        import asyncio

        unloaded: list[str] = []
        for attempt in range(retries + 1):
            instances = await self.loaded()
            if not instances:
                log.info("LM Studio unloaded (forced=%s)", unloaded)
                return unloaded
            if attempt == retries:
                break
            async with self._http(60) as http:
                for _key, inst_id in instances:
                    await http.post(f"{self.native()}/models/unload", json={"instance_id": inst_id})
                    unloaded.append(inst_id)
            await asyncio.sleep(1.0)
        raise LLMError(f"LM Studio のモデルを unload できません: {[i for _, i in instances]}")
