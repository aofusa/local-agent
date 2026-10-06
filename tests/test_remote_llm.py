"""Inference models on another host (docs/remote-llm-design.md): catalog "endpoint", RemoteLLM, the chat tab's
settings, /coder/turn and GET /models."""

import copy
import json

import httpx
import pytest

from furry_agent import coder_gate, model_catalog as mc, models_api
from furry_agent.chat_common import check_router_model, make_llm, model_info, with_inference
from furry_agent.config import ChatSettings
from furry_agent.llm_client import LlamaRouter, RemoteLLM, remote_error

from test_model_catalog import RAW

URL = "http://127.0.0.1:18090/v1"
LLAMACPP, OPENAI = "mac-bonsai-2-27b-abliterated", "mac-bonsai-2-27b-openai"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("DEFAULT_INFERENCE_MODEL", "HOST_MODELS_DISABLE", "HOST_MODELS_PATH"):
        monkeypatch.delenv(name, raising=False)


def _with_endpoint(endpoint, **extra):
    raw = copy.deepcopy(RAW)
    raw["inference"].append({"id": "x-remote", "label": "X", "endpoint": endpoint, **extra})
    return raw


def _keyed_catalog():
    return mc.parse_catalog(_with_endpoint({"kind": "openai", "url": "https://llm.example/v1/", "model": "gpt-x",
                                            "api_key": "sk-test"}))


# --- catalog ------------------------------------------------------------------------------------------------------


def test_shipped_remote_entries_are_written_in_the_catalog():
    catalog = mc.load_catalog()
    remote = catalog.inference[LLAMACPP]
    assert remote.remote and remote.endpoint.kind == "llamacpp" and remote.gguf == {}
    assert remote.endpoint.resolved() == (URL, "bonsai-2-27b-abliterated", "")
    assert catalog.inference[OPENAI].endpoint.kind == "openai"
    assert not catalog.inference["qwen3.8-27b-abliterated"].remote
    assert _keyed_catalog().inference["x-remote"].endpoint.resolved() == ("https://llm.example/v1", "gpt-x", "sk-test")


@pytest.mark.parametrize("endpoint, message", [
    ({"kind": "anthropic", "url": URL, "model": "m"}, "kind"),
    ({"kind": "openai", "model": "m"}, "url"),
    ({"kind": "openai", "url": "192.168.11.53:8090", "model": "m"}, "url"),
    ({"kind": "openai", "url": URL}, "model"),
    ({"kind": "openai", "url": URL, "model": "m", "headers": {}}, "headers"),
    ({"kind": "openai", "url_env": "X", "model": "m"}, "url_env"),
    ("http://host/v1", "オブジェクト"),
])
def test_bad_endpoints_are_refused_at_load(endpoint, message):
    with pytest.raises(mc.CatalogError, match=message):
        mc.parse_catalog(_with_endpoint(endpoint))


def test_an_endpoint_entry_cannot_also_name_a_local_gguf():
    with pytest.raises(mc.CatalogError, match="gguf"):
        mc.parse_catalog(_with_endpoint({"kind": "openai", "url": URL, "model": "m"},
                                        gguf={"catalog": "llm_model"}))


def test_remote_availability_follows_the_servers_list(monkeypatch):
    catalog = mc.load_catalog()
    local = ["qwen3.8-27b-abliterated", "bonsai-2-27b-abliterated"]
    # Not asked (None): usable as far as the catalog goes.
    assert LLAMACPP not in mc.inference_unavailable(catalog, local)
    assert LLAMACPP not in mc.inference_unavailable(catalog, local, {LLAMACPP: ["a", "bonsai-2-27b-abliterated"]})
    out = mc.inference_unavailable(catalog, local, {LLAMACPP: ["qwen"]})
    assert "bonsai-2-27b-abliterated" in out[LLAMACPP]
    out = mc.inference_unavailable(catalog, local, {LLAMACPP: "接続先に接続できません"})
    assert out[LLAMACPP] == "接続先に接続できません"
    monkeypatch.setenv("HOST_MODELS_DISABLE", LLAMACPP)
    assert "HOST_MODELS_DISABLE" in mc.inference_unavailable(catalog, local, {})[LLAMACPP]


def test_preset_writer_skips_remote_entries(tmp_path, monkeypatch):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import host_models

    monkeypatch.setenv("BONSAI_MODELS_DIR", str(tmp_path))
    args = type("A", (), {"models_dir": "", "engine": "llamacpp", "qwen_model": "", "qwen_mmproj": "", "offload": 1.0,
                          "sleep_idle": None, "out": str(tmp_path / "models.ini")})()
    out = host_models.preset(args)
    ids = {s.get("id") or s["skipped"]["id"] for s in out}
    assert LLAMACPP not in ids and OPENAI not in ids
    assert "mac-" not in (tmp_path / "models.ini").read_text(encoding="utf-8")


# --- settings of a run ----------------------------------------------------------------------------------------------


def test_with_inference_points_the_run_at_the_other_host():
    model = _keyed_catalog().inference["x-remote"]
    settings = with_inference(ChatSettings(), model)
    assert (settings.llm_url, settings.llm_model, settings.llm_api_key) == ("https://llm.example/v1", "gpt-x", "sk-test")
    assert settings.llm_remote_kind == "openai" and settings.inference_model == "x-remote"
    assert settings.llm_ctx == 4096 and settings.llm_thinking is False
    client = make_llm(settings)
    assert isinstance(client, RemoteLLM) and client.api_key == "sk-test" and not client.template_kwargs
    assert model_info(settings)["remote"] is True and "sk-test" not in json.dumps(model_info(settings))
    # Back on a local model: the router again, without the remote key.
    local = with_inference(settings, mc.load_catalog().inference["qwen3.8-27b-abliterated"])
    assert local.llm_remote_kind == "" and local.llm_api_key == ""
    assert type(make_llm(local)) is LlamaRouter


async def test_check_router_model_asks_the_remote_list(monkeypatch):
    settings = with_inference(ChatSettings(), mc.load_catalog().inference[LLAMACPP])

    async def listed(self, timeout_s=5.0):
        return ["other"]
    monkeypatch.setattr(RemoteLLM, "model_ids", listed)
    with pytest.raises(mc.ModelChoiceError, match="bonsai-2-27b-abliterated"):
        await check_router_model(None, settings)

    async def ok(self, timeout_s=5.0):
        return ["bonsai-2-27b-abliterated"]
    monkeypatch.setattr(RemoteLLM, "model_ids", ok)
    await check_router_model(None, settings)


# --- RemoteLLM ----------------------------------------------------------------------------------------------------


def _sse(*chunks):
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"


class Recorder:
    def __init__(self):
        self.requests = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "bonsai-2-27b-abliterated"}]})
        body = _sse({"choices": [{"delta": {"content": "こんにちは"}}]},
                    {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"completion_tokens": 3}})
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})


@pytest.mark.parametrize("kind, kwargs", [("llamacpp", True), ("openai", False)])
async def test_remote_chat_sends_the_key_and_only_llamacpp_fields(kind, kwargs):
    recorder = Recorder()
    client = RemoteLLM(URL, "m", 30, kind=kind, api_key="sk-test", transport=httpx.MockTransport(recorder))
    reply = await client.chat([{"role": "user", "content": "hi"}], json_schema={"type": "object"})
    assert reply.content == "こんにちは"
    request = recorder.requests[0]
    assert str(request.url) == URL + "/chat/completions"
    assert request.headers["Authorization"] == "Bearer sk-test"
    body = json.loads(request.content)
    assert ("chat_template_kwargs" in body) is kwargs
    assert body["response_format"]["json_schema"]["strict"] is kwargs
    assert await client.model_ids() == ["bonsai-2-27b-abliterated"]
    assert str(recorder.requests[-1].url) == URL + "/models"
    assert await client.reachable()


async def test_remote_memory_is_not_this_hosts():
    def refuse(request):
        raise AssertionError(f"no request expected: {request.url}")
    client = RemoteLLM(URL, "m", 30, transport=httpx.MockTransport(refuse))
    assert await client.loaded() == [] and await client.unload_all() == []
    assert client.remote and not LlamaRouter(URL).remote


def test_remote_error_reasons():
    request = httpx.Request("GET", URL)
    assert "API キー" in remote_error(httpx.HTTPStatusError("x", request=request, response=httpx.Response(401)))
    assert "HTTP 500" in remote_error(httpx.HTTPStatusError("x", request=request, response=httpx.Response(500)))
    assert remote_error(httpx.ConnectTimeout("x")) == "接続先が応答しません"
    assert remote_error(httpx.ConnectError("x")) == "接続先に接続できません"


# --- /coder/turn --------------------------------------------------------------------------------------------------

NO_COMFY = {"configurable": {"comfy_client": None}}


async def test_coder_turn_streams_from_the_remote_model():
    recorder = Recorder()
    settings = with_inference(ChatSettings(), mc.load_catalog().inference[LLAMACPP])
    turn = coder_gate.Turn([{"role": "user", "content": "hi"}], [], "fast", 256, 0.2, LLAMACPP)
    chunks = [c async for c in coder_gate.run_turn(turn, settings, transport=httpx.MockTransport(recorder),
                                                   comfy_config=NO_COMFY)]
    text = b"".join(chunks).decode("utf-8")
    assert "event: token" in text and "こんにちは" in text and "event: done" in text
    request = recorder.requests[0]
    assert str(request.url) == URL + "/chat/completions"
    body = json.loads(request.content)
    assert body["model"] == "bonsai-2-27b-abliterated" and "chat_template_kwargs" in body


async def test_coder_turn_to_an_openai_service_sends_its_key_and_no_template_kwargs():
    recorder = Recorder()
    settings = with_inference(ChatSettings(), _keyed_catalog().inference["x-remote"])
    turn = coder_gate.Turn([{"role": "user", "content": "hi"}], [], "think", 256, 0.2, "x-remote")
    _ = [c async for c in coder_gate.run_turn(turn, settings, transport=httpx.MockTransport(recorder),
                                              comfy_config=NO_COMFY)]
    request = recorder.requests[0]
    assert request.headers["Authorization"] == "Bearer sk-test"
    body = json.loads(request.content)
    assert "chat_template_kwargs" not in body and body["model"] == "gpt-x"


# --- GET /models --------------------------------------------------------------------------------------------------


class Comfy:
    async def object_info(self, max_age_s=300.0):
        return {}


async def _models(monkeypatch, remote_answer):
    async def fake_router(url):
        return ["qwen3.8-27b-abliterated"]

    async def fake_remote(self, timeout_s=5.0):
        if isinstance(remote_answer, Exception):
            raise remote_answer
        return remote_answer
    monkeypatch.setattr(models_api, "_router_models", fake_router)
    monkeypatch.setattr(RemoteLLM, "model_ids", fake_remote)
    app = coder_gate.app
    app.state.comfy_client = Comfy()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://host") as client:
            body = (await client.get("/models")).json()
    finally:
        del app.state.comfy_client
    return {m["id"]: m for m in body["inference"]}


async def test_models_lists_a_reachable_remote_as_available(monkeypatch):
    inference = await _models(monkeypatch, ["bonsai-2-27b-abliterated"])
    remote = inference[LLAMACPP]
    assert remote["available"] is True
    assert remote["remote"] == {"kind": "llamacpp", "host": "127.0.0.1:18090", "model": "bonsai-2-27b-abliterated"}
    assert inference[OPENAI]["remote"]["kind"] == "openai"
    assert "remote" not in inference["qwen3.8-27b-abliterated"]


async def test_models_never_shows_an_api_key(monkeypatch, tmp_path):
    path = tmp_path / "host_models.json"
    path.write_text(json.dumps(_with_endpoint({"kind": "openai", "url": "https://llm.example/v1", "model": "gpt-x",
                                               "api_key": "sk-secret"})), encoding="utf-8")
    monkeypatch.setenv("HOST_MODELS_PATH", str(path))
    inference = await _models(monkeypatch, ["gpt-x", "bonsai-2-27b-abliterated"])
    assert inference["x-remote"]["available"] is True and "sk-secret" not in json.dumps(inference)


async def test_models_gives_the_reason_a_remote_cannot_be_used(monkeypatch):
    request = httpx.Request("GET", URL)
    inference = await _models(monkeypatch, httpx.HTTPStatusError("x", request=request, response=httpx.Response(401)))
    assert inference[LLAMACPP]["available"] is False and "API キー" in inference[LLAMACPP]["reason"]
    inference = await _models(monkeypatch, httpx.ConnectError("x"))
    assert inference[LLAMACPP]["reason"] == "接続先に接続できません"
    inference = await _models(monkeypatch, ["other-model"])
    assert "bonsai-2-27b-abliterated" in inference[LLAMACPP]["reason"]
