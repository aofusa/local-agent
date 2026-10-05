"""POST /coder/turn (docs/locus-cui-design.md §6.1) with a fake LM Studio stream: no LM Studio needed."""

import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from furry_agent import coder_gate as gate
from furry_agent.config import ChatSettings
from furry_agent.job_lock import job_lock
from furry_agent.llm_client import LMStudio

SETTINGS = replace(ChatSettings(), lmstudio_ctx=4096, lmstudio_model="qwen")


def _chunks(*deltas, finish="stop", usage=None):
    lines = []
    for d in deltas:
        lines.append("data: " + json.dumps({"choices": [{"delta": d, "finish_reason": None}]}))
    lines.append("data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": finish}]}))
    if usage:
        lines.append("data: " + json.dumps({"choices": [], "usage": usage}))
    lines.append("data: [DONE]")
    return ("\n\n".join(lines) + "\n\n").encode("utf-8")


class LM:
    """A streaming LM Studio: records each request body and answers with the next canned stream."""

    def __init__(self, *streams, status=200):
        self.streams, self.bodies, self.status = list(streams), [], status

    def __call__(self, request: httpx.Request):
        self.bodies.append(json.loads(request.content))
        if self.status >= 400:
            return httpx.Response(self.status, text="boom")
        return httpx.Response(200, content=self.streams.pop(0), headers={"content-type": "text/event-stream"})


class Comfy:
    def __init__(self, busy=False):
        self.busy = busy

    async def queue_busy(self):
        return self.busy


def _app(lm, comfy=None):
    app = gate.app
    app.state.chat_settings = SETTINGS
    app.state.lmstudio = LMStudio("http://lm.test/v1", "qwen", 30)
    app.state.transport = httpx.MockTransport(lm)
    app.state.comfy_config = {"configurable": {"comfy_client": comfy}}
    return app


def _events(raw: str) -> list[tuple[str, dict]]:
    out = []
    for block in raw.strip().split("\n\n"):
        event = data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if event:
            out.append((event, data))
    return out


async def _post(app, body):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://host") as client:
        response = await client.post("/coder/turn", json=body)
        return response.status_code, response.text


TOOLS = [{"name": "grep", "description": "search", "parameters": {"type": "object",
                                                                    "properties": {"pattern": {"type": "string"}}}}]
MESSAGES = [{"role": "system", "content": "sys"}, {"role": "user", "content": "find main"}]


async def test_tokens_and_done():
    lm = LM(_chunks({"content": "こん"}, {"content": "にちは"}, usage={"prompt_tokens": 12, "completion_tokens": 3}))
    status, raw = await _post(_app(lm), {"mode": "fast", "messages": MESSAGES})
    events = _events(raw)
    assert status == 200
    assert [e for e, _ in events] == ["token", "token", "done"]
    assert "".join(d["text"] for e, d in events if e == "token") == "こんにちは"
    assert events[-1][1]["finish_reason"] == "stop" and events[-1][1]["usage"] == {"input_tokens": 12,
                                                                                    "output_tokens": 3}
    assert job_lock.holder is None


async def test_tool_calls_are_assembled_and_sent_after_the_stream():
    lm = LM(_chunks({"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "grep", "arguments": '{"pat'}}]},
                    {"tool_calls": [{"index": 0, "function": {"arguments": 'tern":"main"}'}}]},
                    {"tool_calls": [{"index": 1, "id": "c2", "function": {"name": "read_file", "arguments": "{}"}}]},
                    finish="tool_calls"))
    status, raw = await _post(_app(lm), {"mode": "fast", "messages": MESSAGES, "tools": TOOLS})
    events = _events(raw)
    calls = [d for e, d in events if e == "tool_call"]
    assert calls == [{"id": "c1", "name": "grep", "arguments": '{"pattern":"main"}'},
                     {"id": "c2", "name": "read_file", "arguments": "{}"}]
    assert events[-1] == ("done", {"finish_reason": "tool_calls", "usage": events[-1][1]["usage"], "mode": "fast"})
    sent = lm.bodies[0]
    assert sent["stream"] is True and sent["tool_choice"] == "auto"
    assert sent["tools"][0] == {"type": "function", "function": {"name": "grep", "description": "search",
                                                                 "parameters": TOOLS[0]["parameters"]}}
    assert sent["messages"] == MESSAGES  # the CUI's system prompt goes through unchanged


async def test_think_mode_streams_thinking_apart_from_the_answer():
    lm = LM(_chunks({"reasoning": "考え中"}, {"content": "答え"}))
    status, raw = await _post(_app(lm), {"mode": "think", "messages": MESSAGES})
    events = _events(raw)
    assert ("thinking", {"text": "考え中"}) in events and ("token", {"text": "答え"}) in events
    assert lm.bodies[0]["reasoning_effort"] == "medium"
    assert lm.bodies[0]["chat_template_kwargs"] == {"enable_thinking": True}


async def test_assistant_tool_calls_and_tool_results_go_back_to_the_model():
    lm = LM(_chunks({"content": "ok"}))
    history = [*MESSAGES, {"role": "assistant", "content": "", "tool_calls": [
        {"id": "c1", "name": "grep", "arguments": {"pattern": "x"}}]},
               {"role": "tool", "tool_call_id": "c1", "content": "src/main.rs:1: fn main"}]
    await _post(_app(lm), {"mode": "fast", "messages": history, "tools": TOOLS})
    sent = lm.bodies[0]["messages"]
    assert sent[2]["tool_calls"] == [{"id": "c1", "type": "function",
                                      "function": {"name": "grep", "arguments": '{"pattern": "x"}'}}]
    assert sent[3] == {"role": "tool", "content": "src/main.rs:1: fn main", "tool_call_id": "c1"}


async def test_context_overflow_is_an_error_event_and_no_call():
    lm = LM()
    big = [{"role": "user", "content": "あ" * 5000}]
    status, raw = await _post(_app(lm), {"messages": big})
    events = _events(raw)
    assert events == [("error", events[0][1])] and events[0][1]["code"] == "context_overflow"
    assert lm.bodies == [] and job_lock.holder is None


async def test_max_tokens_fit_the_context():
    lm = LM(_chunks({"content": "x"}))
    await _post(_app(lm), {"mode": "fast", "messages": MESSAGES, "max_tokens": 100000})
    assert lm.bodies[0]["max_tokens"] <= 4096 - 48


async def test_bad_request_is_400():
    app = _app(LM())
    assert (await _post(app, {"messages": []}))[0] == 400
    assert (await _post(app, {"messages": [{"role": "boss", "content": "x"}]}))[0] == 400
    assert (await _post(app, {"messages": MESSAGES, "tools": [{"description": "no name"}]}))[0] == 400


async def test_lm_studio_refusal_is_retried_once_then_reported():
    lm = LM(status=500)
    status, raw = await _post(_app(lm), {"mode": "fast", "messages": MESSAGES})
    events = _events(raw)
    assert len(lm.bodies) == 2 and events[-1][0] == "error" and events[-1][1]["code"] == "lmstudio"
    assert not any(e == "tool_call" for e, _ in events) and job_lock.holder is None


async def test_waits_for_the_image_tab_and_reports_the_wait(monkeypatch):
    monkeypatch.setattr(gate, "STATUS_EVERY_S", 0.0)
    lm = LM(_chunks({"content": "done"}))
    token = job_lock.try_acquire("image")

    async def release():
        await asyncio.sleep(0.8)
        job_lock.release(token)

    task = asyncio.create_task(release())
    status, raw = await _post(_app(lm), {"mode": "fast", "messages": MESSAGES})
    await task
    events = _events(raw)
    assert events[0][0] == "status" and events[0][1]["holder"] == "image"
    assert events[-1][0] == "done" and job_lock.holder is None


async def test_comfy_queue_busy_waits_then_gives_up(monkeypatch):
    monkeypatch.setattr(gate, "LOCK_WAIT_S", 0.3)
    lm = LM(_chunks({"content": "x"}))
    status, raw = await _post(_app(lm, Comfy(busy=True)), {"mode": "fast", "messages": MESSAGES})
    events = _events(raw)
    assert events[-1][0] == "error" and events[-1][1]["code"] == "busy" and lm.bodies == []
    assert job_lock.holder is None


def test_auto_mode_follows_the_chat_rules():
    assert gate.resolve_mode("auto", [{"role": "user", "content": "このアルゴリズムの計算量を証明して"}]) == "think"
    assert gate.resolve_mode(None, [{"role": "user", "content": "ls して"}]) == "fast"
    assert gate.resolve_mode("think", MESSAGES) == "think"


async def test_health():
    app = _app(LM())

    async def reachable():
        return True

    app.state.lmstudio.reachable = reachable
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://host") as client:
        data = (await client.get("/coder/health")).json()
    assert data["ok"] and data["gate"] == "coder" and data["context"] == 4096 and data["lmstudio"] is True


@pytest.fixture(autouse=True)
def _reset_state():
    yield
    for name in ("chat_settings", "lmstudio", "transport", "comfy_config"):
        if hasattr(gate.app.state, name):
            delattr(gate.app.state, name)
