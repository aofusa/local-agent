import json
import time

import pytest

from furry_agent import comfy_client
from furry_agent.comfy_client import ComfyClient, ComfyError


class FakeWS:
    def __init__(self, frames):
        self.frames = list(frames)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def recv(self):
        if not self.frames:
            raise TimeoutError
        frame = self.frames.pop(0)
        return frame if isinstance(frame, bytes) else json.dumps(frame)


def _client(monkeypatch, frames, history=None):
    client = ComfyClient("http://127.0.0.1:8188", timeout_s=5)
    monkeypatch.setattr(comfy_client.websockets, "connect", lambda *a, **k: FakeWS(frames))
    calls = {"n": 0}

    async def fake_history(prompt_id):
        calls["n"] += 1
        return history(calls["n"]) if callable(history) else history

    monkeypatch.setattr(client, "history", fake_history)
    return client


async def test_wait_until_split(monkeypatch):
    frames = [
        {"type": "status", "data": {}},
        b"\x00preview",
        {"type": "executing", "data": {"node": "prompt_node", "prompt_id": "p1"}},
        {"type": "executed", "data": {"node": "split", "prompt_id": "p1", "output": {"positive": ["wolf"]}}},
        {"type": "executing", "data": {"node": "ckpt", "prompt_id": "p1"}},
    ]
    client = _client(monkeypatch, frames)
    result = await client.wait("p1", "c1", until_node="split")
    assert not result.done
    assert result.outputs["split"] == {"positive": ["wolf"]}


async def test_wait_until_done_reads_history(monkeypatch):
    frames = [
        {"type": "executing", "data": {"node": "sampler", "prompt_id": "p1"}},
        {"type": "executing", "data": {"node": None, "prompt_id": "p1"}},
    ]
    entry = {"status": {"status_str": "success", "completed": True},
             "outputs": {"save": {"images": [{"filename": "a.png", "subfolder": "furry_ja", "type": "output"}]}}}
    seen = []
    client = _client(monkeypatch, frames, history=lambda n: None if n == 1 else entry)
    result = await client.wait("p1", "c1", on_event=lambda k, d: seen.append((k, d.get("node"))))
    assert result.done
    assert result.outputs["save"]["images"][0]["filename"] == "a.png"
    assert ("executing", "sampler") in seen


async def test_execution_error(monkeypatch):
    frames = [{"type": "execution_error", "data": {"prompt_id": "p1", "node_id": "ckpt", "node_type": "X",
                                                   "exception_message": "boom"}}]
    client = _client(monkeypatch, frames)
    with pytest.raises(ComfyError, match="boom"):
        await client.wait("p1", "c1")


async def test_other_prompts_are_ignored_and_history_fallback(monkeypatch):
    entry = {"status": {"status_str": "success", "completed": True}, "outputs": {"save": {"images": []}}}
    frames = [{"type": "execution_error", "data": {"prompt_id": "other", "exception_message": "not mine"}}]
    client = _client(monkeypatch, frames, history=lambda n: None if n == 1 else entry)
    result = await client.wait("p1", "c1")
    assert result.done


async def test_history_error_raises(monkeypatch):
    entry = {"status": {"status_str": "error", "completed": False, "messages": [
        ["execution_error", {"node_id": "eject", "node_type": "E", "exception_message": "lm studio down"}]]}}
    client = _client(monkeypatch, [], history=entry)
    with pytest.raises(ComfyError, match="lm studio down"):
        await client.wait("p1", "c1")


async def test_deadline(monkeypatch):
    client = _client(monkeypatch, [])
    with pytest.raises(ComfyError, match="タイムアウト"):
        await client.wait("p1", "c1", deadline=time.monotonic() - 1)


def test_queue_busy():
    assert comfy_client._queue_busy({"queue_running": [[1]], "queue_pending": []})
    assert not comfy_client._queue_busy({"queue_running": [], "queue_pending": []})
