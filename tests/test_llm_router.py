"""LlamaRouter: the llama.cpp router's model API (GET /models, POST /models/unload) as the chat tab uses it."""

import json

import httpx
import pytest

from furry_agent import llm_client
from furry_agent.llm_client import LlamaRouter, LLMError, resident_models


class Router:
    def __init__(self, loaded=("qwen",), unload_works=True, lag=2):
        self.status = {m: "loaded" for m in loaded}
        self.status["cache/other:Q8_0"] = "unloaded"  # the router also lists models of the HF cache
        self.unload_works = unload_works
        self.lag = lag
        self.pending: dict[str, int] = {}
        self.unloads: list[str] = []
        self.paths: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(f"{request.method} {request.url.path}")
        if request.url.path == "/models/unload":
            model = json.loads(request.content)["model"]
            self.unloads.append(model)
            if self.unload_works:
                self.status[model] = "unloading"
                self.pending[model] = self.lag
            return httpx.Response(200, json={"success": True})
        if request.url.path in ("/models", "/v1/models"):
            for model, left in list(self.pending.items()):
                if left <= 0:
                    self.status[model] = "unloaded"
                    del self.pending[model]
                else:
                    self.pending[model] = left - 1
            return httpx.Response(200, json={"data": [{"id": m, "status": {"value": v}}
                                                      for m, v in self.status.items()]})
        return httpx.Response(404)

    def client(self) -> LlamaRouter:
        return LlamaRouter("http://127.0.0.1:8080/v1", "qwen", transport=httpx.MockTransport(self.handler))


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    async def no_sleep(_s):
        return None

    import asyncio
    monkeypatch.setattr(asyncio, "sleep", no_sleep)


def test_resident_models():
    payload = {"data": [{"id": "a", "status": {"value": "loaded"}}, {"id": "b", "status": {"value": "unloaded"}},
                        {"id": "c", "status": {"value": "loading"}}, {"id": "d", "status": {"value": "failed"}}]}
    assert resident_models(payload) == ["a", "c"]


async def test_native_api_is_outside_v1():
    router = Router()
    client = router.client()
    assert client.native() == "http://127.0.0.1:8080"
    assert await client.reachable()
    assert await client.loaded() == ["qwen"]
    assert router.paths == ["GET /v1/models", "GET /models"]


async def test_unload_all_waits_until_the_child_is_gone():
    router = Router(lag=3)
    assert await router.client().unload_all() == ["qwen"]
    assert router.unloads == ["qwen"]  # asked once, then polled
    assert router.status["qwen"] == "unloaded"


async def test_unload_all_raises_when_the_model_stays(monkeypatch):
    clock = iter(range(0, 10_000, 10))
    monkeypatch.setattr(llm_client.time, "monotonic", lambda: next(clock))
    router = Router(unload_works=False)
    with pytest.raises(LLMError):
        await router.client().unload_all(wait_s=30)


async def test_nothing_loaded_sends_no_unload():
    router = Router(loaded=())
    assert await router.client().unload_all() == []
    assert router.unloads == []


async def test_unreachable_router():
    def down(request):
        raise httpx.ConnectError("refused")

    client = LlamaRouter("http://127.0.0.1:9/v1", transport=httpx.MockTransport(down))
    assert not await client.reachable()
