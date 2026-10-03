import pytest

from furry_ja import lmstudio_state
from furry_ja.lmstudio_state import ensure_unloaded, loaded_instances, native_base


def test_native_base():
    assert native_base("http://127.0.0.1:1234/v1") == "http://127.0.0.1:1234/api/v1"


def test_loaded_instances_ignores_embeddings():
    payload = {"models": [
        {"type": "llm", "key": "qwen", "loaded_instances": [{"id": "qwen"}]},
        {"type": "embedding", "key": "nomic", "loaded_instances": [{"id": "nomic"}]},
        {"type": "llm", "key": "idle", "loaded_instances": []},
    ]}
    assert loaded_instances(payload) == [("qwen", "qwen")]


class FakeLMStudio:
    def __init__(self, loaded, unload_works=True):
        self.loaded = list(loaded)
        self.unload_works = unload_works
        self.unloads = []

    def __call__(self, url, payload=None, timeout=15.0):
        if url.endswith("/models/unload"):
            self.unloads.append(payload["instance_id"])
            if self.unload_works:
                self.loaded.remove(payload["instance_id"])
            return {}
        return {"models": [{"type": "llm", "key": i, "loaded_instances": [{"id": i}]} for i in self.loaded]}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(lmstudio_state.time, "sleep", lambda s: None)


def test_already_unloaded():
    fake = FakeLMStudio([])
    report = ensure_unloaded("http://127.0.0.1:1234/v1", fetch=fake)
    assert report == {"lmstudio_loaded": [], "forced_unload": [], "verified_unloaded": True}
    assert fake.unloads == []


def test_forces_unload_when_eject_was_skipped():
    fake = FakeLMStudio(["qwen"])
    report = ensure_unloaded("http://127.0.0.1:1234/v1", fetch=fake)
    assert report["forced_unload"] == ["qwen"] and report["verified_unloaded"]


def test_raises_when_model_stays_loaded():
    fake = FakeLMStudio(["qwen"], unload_works=False)
    with pytest.raises(RuntimeError):
        ensure_unloaded("http://127.0.0.1:1234/v1", retries=2, fetch=fake)
