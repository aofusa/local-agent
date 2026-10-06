"""The macOS router (furry_agent.mlx_router): preset parsing, child command lines, thinking split, and the router
API with a fake child server."""

import asyncio
import json

import httpx
import pytest

from furry_agent import mlx_router as mr

PRESET = """version = 1

[qwen3.8-27b-abliterated]
engine = mlx
model = /m/qwen-mlx
ctx-size = 4096
reasoning = off
temp = 0.4
repeat-penalty = 1.1
sleep-idle-seconds = 300

[bonsai-2-27b-abliterated]
model = /m/bonsai.gguf
ctx-size = 8192
n-gpu-layers = 64
jinja = true
flash-attn = on
reasoning = off
temp = 0.7
top-k = 20
sleep-idle-seconds = 300
"""


@pytest.fixture
def sections(tmp_path):
    path = tmp_path / "models.ini"
    path.write_text(PRESET, encoding="utf-8")
    return mr.read_preset(path)


def test_preset_sections_and_engines(sections):
    assert [(s.id, s.engine) for s in sections] == [("qwen3.8-27b-abliterated", "mlx"),
                                                    ("bonsai-2-27b-abliterated", "llamacpp")]
    assert sections[0].thinking_default is False


def test_llama_child_gets_the_section_as_options(sections):
    argv = mr.llama_args(sections[1], "llama-server", 18300, "key")
    assert argv[:9] == ["llama-server", "--host", "127.0.0.1", "--port", "18300", "--api-key", "key", "--alias",
                        "bonsai-2-27b-abliterated"]
    assert ["-m", "/m/bonsai.gguf"] == argv[9:11]
    assert "--jinja" in argv and ["--top-k", "20"] == argv[argv.index("--top-k"):argv.index("--top-k") + 2]
    assert "--sleep-idle-seconds" not in argv and "--engine" not in argv  # router-only keys
    assert "127.0.0.1" == argv[argv.index("--host") + 1]  # loopback only


def test_mlx_child_and_its_sampling(sections):
    assert mr.mlx_args(sections[0], "py", 18301) == ["py", "-m", "mlx_lm", "server", "--model", "/m/qwen-mlx",
                                                     "--host", "127.0.0.1", "--port", "18301"]
    assert mr.sampling(sections[0]) == {"temperature": 0.4, "repetition_penalty": 1.1}


@pytest.mark.parametrize("chunks, answer, thought", [
    (["<think>plan</think>答え"], "答え", "plan"),
    (["<thi", "nk>pl", "an</th", "ink>答", "え"], "答え", "plan"),
    (["no thinking"], "no thinking", ""),
])
def test_think_splitter(chunks, answer, thought):
    splitter = mr.ThinkSplitter()
    a = t = ""
    for c in chunks:
        x, y = splitter.feed(c)
        a, t = a + x, t + y
    assert (a, t) == (answer, thought)


def test_thinking_is_hidden_unless_asked():
    chunk = {"choices": [{"delta": {"content": "<think>x</think>y"}}]}
    assert mr.rewrite_chunk(json.loads(json.dumps(chunk)), mr.ThinkSplitter(), False)["choices"][0]["delta"] == {
        "content": "y"}
    shown = mr.rewrite_chunk(json.loads(json.dumps(chunk)), mr.ThinkSplitter(), True)["choices"][0]["delta"]
    assert shown == {"content": "y", "reasoning_content": "x"}


class FakeProcess:
    def __init__(self):
        self.alive = True

    def poll(self):
        return None if self.alive else 0

    def terminate(self):
        self.alive = False

    def kill(self):
        self.alive = False

    def wait(self, timeout=None):
        return 0


async def test_router_api_loads_one_model_at_a_time(sections, monkeypatch):
    router = mr.Router(sections, llama_server="llama-server", mlx_python="py", logs_dir=None)
    spawned = []

    def spawn(section):
        spawned.append(section.id)
        return mr.Child(section, FakeProcess(), 1, "")

    async def ready(child):
        child.ready = True

    monkeypatch.setattr(router, "_spawn", spawn)
    monkeypatch.setattr(router, "_wait_ready", ready)
    app = mr.make_app(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://r") as client:
        models = (await client.get("/v1/models")).json()["data"]
        assert {m["id"]: m["status"]["value"] for m in models} == {
            "qwen3.8-27b-abliterated": "unloaded", "bonsai-2-27b-abliterated": "unloaded"}
        await client.post("/models/load", json={"model": "qwen3.8-27b-abliterated"})
        first = router.child
        await client.post("/models/load", json={"model": "bonsai-2-27b-abliterated"})
        assert spawned == ["qwen3.8-27b-abliterated", "bonsai-2-27b-abliterated"]
        assert not first.process.alive  # the previous model was stopped before the next one loaded
        status = {m["id"]: m["status"]["value"] for m in (await client.get("/models")).json()["data"]}
        assert status["bonsai-2-27b-abliterated"] == "loaded"
        await client.post("/models/unload", json={"model": "bonsai-2-27b-abliterated"})
        assert router.child is None
        r = await client.post("/models/load", json={"model": "gpt"})
        assert r.status_code == 404


def test_mlx_request_gets_thinking_off_and_the_sections_sampling(sections):
    router = mr.Router(sections, llama_server="", mlx_python="", logs_dir=None)
    child = mr.Child(sections[0], FakeProcess(), 1)
    body, thinking = router.prepare(child, {"model": "qwen3.8-27b-abliterated", "temperature": 0.2})
    assert thinking is False and body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["temperature"] == 0.2 and body["repetition_penalty"] == 1.1 and body["model"] == "/m/qwen-mlx"
    body, thinking = router.prepare(child, {"chat_template_kwargs": {"enable_thinking": True}})
    assert thinking is True
    llama = mr.Child(sections[1], FakeProcess(), 1)
    body, _ = router.prepare(llama, {"model": "bonsai-2-27b-abliterated"})
    assert "chat_template_kwargs" not in body  # llama-server applies its own reasoning settings
    asyncio.run(asyncio.sleep(0))
