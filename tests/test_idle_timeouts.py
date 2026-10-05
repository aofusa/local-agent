"""Idle timeouts (AGENT_IDLE_TIMEOUT_S): the agent waits as long as something keeps answering, and gives up only
after the configured time without any answer."""

import asyncio
import json
import socket
import threading
import time

import httpx
import pytest
import uvicorn
from starlette.applications import Starlette
from starlette.responses import StreamingResponse
from starlette.routing import Route

from furry_agent import chat_common, comfy_client
from furry_agent.comfy_client import ComfyClient, ComfyError
from furry_agent.config import ChatSettings, Settings, idle_timeout_from_env
from furry_agent.job_lock import job_lock
from furry_agent.llm_client import LLMError, LMStudio, OpenAICompatClient


# --- settings ----------------------------------------------------------------------------------------------------


def test_one_setting_drives_every_wait(monkeypatch):
    for name in ("AGENT_IDLE_TIMEOUT_S", "JOB_LOCK_TIMEOUT_S", "SANDBOX_WAIT_S", "SEARCH_WALL_CLOCK_S",
                 "CLAIM_TIMEOUT_S", "CONTROLLER_WALL_CLOCK_S", "SEARCH_TOTAL_TIMEOUT_S"):
        monkeypatch.delenv(name, raising=False)
    s = ChatSettings.from_env()
    assert s.idle_timeout_s == 1200.0 and s.lock_wait_s == 1200.0 and s.sandbox_wait_limit_s == 1200.0
    # Budgets over a whole run are off by default: work that keeps answering is never cut off by time.
    assert (s.search_wall_clock_s, s.claim_timeout_s, s.controller_budget_s, s.search_total_timeout_s) == (0, 0, 0, 0)
    assert Settings.from_env().timeout_s == 1200.0
    monkeypatch.setenv("AGENT_IDLE_TIMEOUT_S", "3600")
    assert ChatSettings.from_env().idle_timeout_s == 3600.0 and Settings.from_env().timeout_s == 3600.0
    monkeypatch.setenv("AGENT_IDLE_TIMEOUT_S", "1")
    assert idle_timeout_from_env() == 30.0  # floor
    # The old per-call totals are not read any more.
    monkeypatch.setenv("AGENT_IDLE_TIMEOUT_S", "")
    monkeypatch.setenv("CHAT_TIMEOUT_S", "60")
    monkeypatch.setenv("COMFYUI_TIMEOUT_S", "60")
    assert ChatSettings.from_env().idle_timeout_s == 1200.0 and Settings.from_env().timeout_s == 1200.0


def test_a_slow_server_gets_the_full_token_budget():
    # The old cap (tokens that fit in 20 minutes at the measured speed) is gone; only the context window limits.
    chat_common._speeds["http://127.0.0.1:9/v1"] = 0.1
    try:
        assert chat_common.capped(ChatSettings(), LMStudio("http://127.0.0.1:9/v1"), 3500) == 3500
    finally:
        chat_common._speeds.clear()


# --- streamed model calls ----------------------------------------------------------------------------------------


def _sse(*chunks) -> bytes:
    return b"".join(f"data: {json.dumps(c)}\n\n".encode() for c in chunks) + b"data: [DONE]\n\n"


async def test_stream_is_put_back_together():
    sent = []

    def handler(request: httpx.Request):
        sent.append(json.loads(request.content))
        body = _sse({"choices": [{"delta": {"reasoning_content": "考え"}}]},
                    {"choices": [{"delta": {"content": "こん"}}]},
                    {"choices": [{"delta": {"content": "にちは"}}]},
                    {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1",
                                                            "function": {"name": "open_page", "arguments": '{"url":'}}]}}]},
                    {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '"https://a"}'}}]},
                                  "finish_reason": "tool_calls"}]},
                    {"choices": [], "usage": {"completion_tokens": 7}})
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    client = OpenAICompatClient("http://llm.test/v1", transport=httpx.MockTransport(handler))
    reply = await client.chat([{"role": "user", "content": "hi"}], thinking=True)
    assert sent[0]["stream"] is True
    assert reply.content == "こんにちは" and reply.reasoning == "考え"
    assert reply.tool_calls[0].name == "open_page" and reply.tool_calls[0].arguments == {"url": "https://a"}
    assert reply.raw["usage"]["completion_tokens"] == 7 and reply.raw["choices"][0]["finish_reason"] == "tool_calls"


async def test_a_server_that_ignores_stream_still_works():
    def handler(request: httpx.Request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "<think>t</think>ok"}}]})

    reply = await OpenAICompatClient("http://llm.test/v1", transport=httpx.MockTransport(handler)).chat([])
    assert reply.content == "ok" and reply.reasoning == "t"


# A real HTTP server: timeouts are enforced by the network layer, which a mock transport does not do.

async def _slow(request):
    async def body():
        for i in range(6):  # 6 tokens, one every 0.4 s: 2.4 s in total, never 1 s without a byte
            await asyncio.sleep(0.4)
            yield f"data: {json.dumps({'choices': [{'delta': {'content': str(i)}}]})}\n\n".encode()
        yield b"data: [DONE]\n\n"
    return StreamingResponse(body(), media_type="text/event-stream")


async def _stalled(request):
    async def body():
        await asyncio.sleep(3.0)  # nothing at all for 3 s
        yield b"data: [DONE]\n\n"
    return StreamingResponse(body(), media_type="text/event-stream")


@pytest.fixture(scope="module")
def llm_server():
    app = Starlette(routes=[Route("/slow/v1/chat/completions", _slow, methods=["POST"]),
                            Route("/stalled/v1/chat/completions", _stalled, methods=["POST"])])
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


async def test_a_reply_that_keeps_coming_is_not_cut_off(llm_server):
    client = OpenAICompatClient(f"{llm_server}/slow/v1", timeout_s=1.0)
    started = time.monotonic()
    reply = await client.chat([{"role": "user", "content": "x"}])
    assert reply.content == "012345" and time.monotonic() - started > 2.0  # longer than the idle timeout


async def test_silence_longer_than_the_idle_timeout_fails(llm_server):
    client = OpenAICompatClient(f"{llm_server}/stalled/v1", timeout_s=1.0)
    with pytest.raises(LLMError, match="応答がありませんでした"):
        await client.chat([{"role": "user", "content": "x"}])


# --- ComfyUI ------------------------------------------------------------------------------------------------------


class SlowWS:
    """Progress events every ``gap`` seconds, then the end of the prompt."""

    def __init__(self, n, gap):
        self.n, self.gap, self.sent = n, gap, 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def recv(self):
        await asyncio.sleep(self.gap)
        self.sent += 1
        if self.sent <= self.n:
            return json.dumps({"type": "progress", "data": {"value": self.sent, "max": self.n, "prompt_id": "p1"}})
        return json.dumps({"type": "executing", "data": {"node": None, "prompt_id": "p1"}})


def _comfy(monkeypatch, ws):
    client = ComfyClient("http://127.0.0.1:8188", timeout_s=1.0)
    monkeypatch.setattr(comfy_client.websockets, "connect", lambda *a, **k: ws)

    async def no_history(prompt_id):
        return None

    monkeypatch.setattr(client, "history", no_history)
    return client


async def test_comfyui_progress_keeps_the_wait_alive(monkeypatch):
    client = _comfy(monkeypatch, SlowWS(n=6, gap=0.4))  # 2.8 s in total, an event every 0.4 s, idle 1 s
    result = await client.wait("p1", "c1")
    assert result.done


async def test_comfyui_without_progress_times_out(monkeypatch):
    client = _comfy(monkeypatch, SlowWS(n=0, gap=10.0))
    with pytest.raises(ComfyError, match="進捗がありません"):
        await asyncio.wait_for(client.wait("p1", "c1", idle_s=0.5), timeout=20)


async def test_queue_wait_follows_comfyui_without_a_limit(monkeypatch):
    client = ComfyClient("http://127.0.0.1:8188", timeout_s=0.1)
    states = iter([True, True, True, False])

    async def busy():
        return next(states)

    monkeypatch.setattr(client, "queue_busy", busy)
    await client.wait_queue_idle(poll_s=0.05)  # longer than timeout_s, and no error


# --- the chat tab waits for the image tab --------------------------------------------------------------------------


class BusyComfy:
    def __init__(self, busy_polls):
        self.left = busy_polls

    async def queue_busy(self):
        self.left -= 1
        return self.left >= 0


async def test_chat_waits_for_a_working_image_run(monkeypatch):
    monkeypatch.setattr(chat_common.asyncio, "sleep", _fast_sleep)
    config = {"configurable": {"comfy_client": BusyComfy(busy_polls=5)}}
    token = await chat_common._lock({}, config, ChatSettings())  # no JOB_LOCK_TIMEOUT_S: wait, do not refuse
    try:
        assert job_lock.holds(token)
    finally:
        job_lock.release(token)


async def test_chat_gives_up_only_when_a_limit_is_set(monkeypatch):
    from dataclasses import replace

    from furry_agent.job_lock import JobLockBusy

    config = {"configurable": {"comfy_client": BusyComfy(busy_polls=10**6)}}
    with pytest.raises(JobLockBusy):
        await chat_common._lock({}, config, replace(ChatSettings(), job_lock_timeout_s=0.1))
    assert job_lock.holder is None


_real_sleep = asyncio.sleep


async def _fast_sleep(seconds, *a, **k):
    await _real_sleep(0)
