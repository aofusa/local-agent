import pytest

from furry_ja.llm_state import ensure_unloaded, native_base, resident_models

URL = "http://127.0.0.1:8080/v1"


def test_native_base():
    assert native_base(URL) == "http://127.0.0.1:8080"


def test_resident_models_reads_status():
    payload = {"data": [
        {"id": "qwen", "status": {"value": "loaded"}},
        {"id": "starting", "status": {"value": "loading"}},
        {"id": "idle", "status": {"value": "unloaded"}},
        {"id": "broken", "status": {"value": "failed"}},
        {"id": "cache-entry", "status": {"value": "unloaded"}, "source": "cache"},
    ]}
    assert resident_models(payload) == ["qwen", "starting"]


class FakeRouter:
    """GET /models and POST /models/unload; an unload takes ``lag`` polls before the child process is gone."""

    def __init__(self, loaded, unload_works=True, lag=1):
        self.status = {m: "loaded" for m in loaded}
        self.unload_works = unload_works
        self.lag = lag
        self.pending: dict[str, int] = {}
        self.unloads = []

    def __call__(self, url, payload=None, timeout=15.0):
        if url.endswith("/models/unload"):
            self.unloads.append(payload["model"])
            if self.unload_works:
                self.status[payload["model"]] = "unloading"
                self.pending[payload["model"]] = self.lag
            return {"success": True}
        assert url == "http://127.0.0.1:8080/models"
        for model, left in list(self.pending.items()):
            if left <= 0:
                self.status[model] = "unloaded"
                del self.pending[model]
            else:
                self.pending[model] = left - 1
        return {"data": [{"id": m, "status": {"value": v}} for m, v in self.status.items()]}


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def run(fake, **kwargs):
    clock = Clock()
    return ensure_unloaded(URL, fetch=fake, sleep=clock.sleep, clock=clock, **kwargs)


def test_already_unloaded():
    fake = FakeRouter([])
    assert run(fake) == {"llm_loaded": [], "forced_unload": [], "verified_unloaded": True}
    assert fake.unloads == []


def test_unloads_and_waits_for_the_child_to_exit():
    fake = FakeRouter(["qwen"], lag=3)
    report = run(fake)
    assert report["forced_unload"] == ["qwen"] and report["verified_unloaded"]
    assert fake.unloads == ["qwen"]  # asked once, then polled until it is gone


def test_raises_when_model_stays_loaded():
    fake = FakeRouter(["qwen"], unload_works=False)
    with pytest.raises(RuntimeError):
        run(fake, wait_s=5)
