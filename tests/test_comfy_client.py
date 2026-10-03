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


# --- HTTP contract (WI §7.2) ---------------------------------------------------

import httpx  # noqa: E402


def _mock_client(handler):
    client = ComfyClient("http://127.0.0.1:8188", timeout_s=5)
    client._http = lambda timeout=60.0: httpx.AsyncClient(
        base_url=client.base_url, transport=httpx.MockTransport(handler))
    comfy_client._OBJECT_INFO_CACHE.clear()
    return client


async def test_upload_body():
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["body"] = request.read()
        return httpx.Response(200, json={"name": "ref_abc.png", "subfolder": "furry_ja", "type": "input"})

    name = await _mock_client(handler).upload_image(b"\x89PNGDATA", "ref_abc.png", "image/png")
    assert name == "furry_ja/ref_abc.png"
    assert seen["path"] == "/upload/image"
    assert b'name="subfolder"' in seen["body"] and b"furry_ja" in seen["body"] and b"\x89PNGDATA" in seen["body"]


async def test_prompt_body_and_rejection_summary(caplog):
    caplog.set_level("ERROR", logger="furry_agent.comfy")
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.read()))
        if len(bodies) == 1:
            return httpx.Response(200, json={"prompt_id": "p9", "node_errors": {}})
        return httpx.Response(400, json={
            "error": {"message": "Prompt outputs failed validation"},
            "node_errors": {"ipa_loader": {"class_type": "IPAdapterUnifiedLoader",
                                           "errors": [{"message": "Value not in list", "details": "preset"}]}}})

    client = _mock_client(handler)
    prompt = {"user_prompt": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": "x"}}}
    assert await client.submit(prompt, "cid") == "p9"
    assert bodies[0] == {"prompt": prompt, "client_id": "cid"}
    with pytest.raises(ComfyError) as err:
        await client.submit(prompt, "cid")
    message = str(err.value)
    assert "ipa_loader (IPAdapterUnifiedLoader): Value not in list preset" in message
    assert "node_errors" not in message  # full JSON only in the log
    assert "node_errors" in caplog.text


async def test_object_info_is_cached_and_lists_loras():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"LoraLoader": {"input": {"required": {"lora_name": [["a.safetensors"]]}}},
                                         "KSampler": {}})

    client = _mock_client(handler)
    assert await client.loras() == ["a.safetensors"]
    assert await client.node_types() == {"LoraLoader", "KSampler"}
    assert calls == ["/object_info"]


async def test_input_exists_and_interrupt():
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path, dict(request.url.params), request.read()))
        if request.url.path == "/view":
            return httpx.Response(200 if request.url.params["filename"] == "have.png" else 404)
        return httpx.Response(200)

    client = _mock_client(handler)
    assert await client.input_exists("furry_ja/have.png")
    assert not await client.input_exists("furry_ja/none.png")
    assert seen[0][2] == {"filename": "have.png", "subfolder": "furry_ja", "type": "input"}
    await client.interrupt("p1")
    assert [(m, p, json.loads(b)) for m, p, _, b in seen[2:]] == [
        ("POST", "/queue", {"delete": ["p1"]}), ("POST", "/interrupt", {"prompt_id": "p1"})]
