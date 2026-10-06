"""POST /coder/turn (docs/locus-cui-design.md §6.1) with a fake the LLM router stream: no the LLM router needed."""

import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from furry_agent import coder_gate as gate
from furry_agent.config import ChatSettings
from furry_agent.job_lock import job_lock
from furry_agent.llm_client import LlamaRouter

SETTINGS = replace(ChatSettings(), llm_ctx=4096, llm_model="qwen")


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
    """A streaming the LLM router: records each request body and answers with the next canned stream."""

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
    app.state.llm = LlamaRouter("http://lm.test/v1", "qwen", 30)
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
    assert events[-1] == ("done", {"finish_reason": "tool_calls", "usage": events[-1][1]["usage"], "mode": "fast",
                          "model": "qwen"})
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
    assert len(lm.bodies) == 2 and events[-1][0] == "error" and events[-1][1]["code"] == "llm"
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


async def test_comfy_queue_busy_waits_then_gives_up_when_a_limit_is_set():
    # Without JOB_LOCK_TIMEOUT_S the gate waits as long as the image run works; with it, it gives up.
    lm = LM(_chunks({"content": "x"}))
    app = _app(lm, Comfy(busy=True))
    app.state.chat_settings = replace(SETTINGS, job_lock_timeout_s=0.3)
    status, raw = await _post(app, {"mode": "fast", "messages": MESSAGES})
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

    app.state.llm.reachable = reachable
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://host") as client:
        data = (await client.get("/coder/health")).json()
    assert data["ok"] and data["gate"] == "coder" and data["context"] == 4096 and data["llm"] is True


@pytest.fixture(autouse=True)
def _reset_state():
    yield
    for name in ("chat_settings", "llm", "transport", "comfy_config"):
        if hasattr(gate.app.state, name):
            delattr(gate.app.state, name)


# --- inference model (docs/host-model-selection-design.md §7) ---------------------------------------------------


async def test_turn_sends_the_picked_inference_model(monkeypatch):
    monkeypatch.delenv("DEFAULT_INFERENCE_MODEL", raising=False)
    lm = LM(_chunks({"content": "ok"}))
    status, raw = await _post(_app(lm), {"messages": [{"role": "user", "content": "hi"}],
                                         "inference_model": "bonsai-2-27b-abliterated"})
    assert status == 200
    assert lm.bodies[0]["model"] == "bonsai-2-27b-abliterated"
    done = [d for e, d in _events(raw) if e == "done"][0]
    assert done["model"] == "bonsai-2-27b-abliterated"


async def test_turn_without_a_model_keeps_the_router_default(monkeypatch):
    monkeypatch.delenv("DEFAULT_INFERENCE_MODEL", raising=False)
    lm = LM(_chunks({"content": "ok"}))
    await _post(_app(lm), {"messages": [{"role": "user", "content": "hi"}]})
    assert lm.bodies[0]["model"] == "qwen"


async def test_turn_uses_the_models_context(monkeypatch):
    # Bonsai's 8192-token window lets a prompt through that the 27B's 4096 refuses.
    monkeypatch.delenv("DEFAULT_INFERENCE_MODEL", raising=False)
    long = [{"role": "user", "content": "あ" * 5000}]
    status, raw = await _post(_app(LM()), {"messages": long})
    assert ("error", "context_overflow") in [(e, d.get("code")) for e, d in _events(raw)]
    lm = LM(_chunks({"content": "ok"}))
    status, raw = await _post(_app(lm), {"messages": long, "inference_model": "bonsai-2-27b-abliterated"})
    assert [e for e, _ in _events(raw)][-1] == "done"


@pytest.mark.parametrize("value, code", [("qwen", "unknown_model"), ("nothere", "unknown_model"), (5, "bad_request")])
async def test_unknown_inference_model_is_400(value, code):
    lm = LM()
    status, raw = await _post(_app(lm), {"messages": [{"role": "user", "content": "hi"}], "inference_model": value})
    assert status == 400 and json.loads(raw)["code"] == code
    assert lm.bodies == []  # nothing reached the router


async def test_disabled_inference_model_is_400(monkeypatch):
    monkeypatch.setenv("HOST_MODELS_DISABLE", "bonsai-2-27b-abliterated")
    status, raw = await _post(_app(LM()), {"messages": [{"role": "user", "content": "hi"}],
                                           "inference_model": "bonsai-2-27b-abliterated"})
    assert status == 400 and json.loads(raw)["code"] == "model_unavailable"


async def test_health_reports_the_defaults(monkeypatch):
    monkeypatch.setenv("DEFAULT_INFERENCE_MODEL", "bonsai-2-27b-abliterated")
    monkeypatch.setenv("DEFAULT_IMAGE_MODEL", "wulver")
    app = _app(LM())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://host") as client:
        body = (await client.get("/coder/health")).json()
    assert body["inference_default"] == "bonsai-2-27b-abliterated" and body["image_default"] == "wulver"
    assert body["model"] == "bonsai-2-27b-abliterated" and body["context"] == 8192
