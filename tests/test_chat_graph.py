"""The chat tab graph end to end with fakes: no Tor, LM Studio, llama.cpp or ComfyUI needed."""

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage

from furry_agent import chat_graph
from furry_agent.bonsai_select import Catalog
from furry_agent.config import ChatSettings
from furry_agent.html_text import SearchHit
from furry_agent.job_lock import job_lock
from furry_agent.llm_client import ChatReply, ToolCall
from furry_agent.search_client import SearchResult

ROOT = Path(__file__).resolve().parent.parent
CATALOG = Catalog.load(ROOT / "config" / "search_models.json")


@pytest.fixture
def models_dir(tmp_path):
    d = tmp_path / "models"
    d.mkdir()
    raw = json.loads((ROOT / "config" / "search_models.json").read_text(encoding="utf-8"))
    for model in raw["models"]:
        model["size"] = 0  # placeholder files: skip the size check
        (d / model["file"]).write_bytes(b"gguf")
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps(raw), encoding="utf-8")
    exe = tmp_path / "llama-server.exe"
    exe.write_bytes(b"")
    return d, exe, catalog


def _settings(models_dir, **kw) -> ChatSettings:
    d, exe, catalog = models_dir
    return replace(ChatSettings(), llama_server=str(exe), models_dir=d, rank_path=d / "missing-rank.json",
                   catalog_path=catalog, logs_dir=d.parent / "logs", job_lock_timeout_s=0.2, **kw)


def _system(messages) -> str:
    return messages[0]["content"] if messages and messages[0]["role"] == "system" else ""


class FakeLLM:
    """Answers by role, recognised from the system prompt file."""

    def __init__(self, world, name):
        self.world, self.name = world, name

    async def chat(self, messages, tools=None, **kw):
        w, system = self.world, _system(messages)
        w.calls.append((self.name, system[:30]))
        if "planner of a small web research team" in system:
            return ChatReply(json.dumps({"intents": w.intents}))
        if "You filter search results" in system:
            return ChatReply('{"relevant":[1,2,3,4,5,6]}')
        if "You route a chat message" in system:
            return ChatReply(json.dumps({"search": w.route_search, "query": "rewritten query"}))
        if "critic of a web research team" in system:
            w.critiques += 1
            return ChatReply(json.dumps({"gaps": w.gaps}))
        if "reader in a web research team" in system:
            if tools:
                listed = re.findall(r"URL: (\S+)", messages[1]["content"])
                opened = sum(1 for m in messages if m["role"] == "tool")
                if opened == 0:
                    return ChatReply("", [ToolCall("open_page", {"url": listed[0]})])
                return ChatReply("十分")
            url = re.findall(r"URL: (\S+)", messages[1]["content"])[0]
            return ChatReply(json.dumps({"cards": [{"url": url, "claims": [
                {"claim": "事実", "quote": f"page text for {url}"}], "date": "2026-10-01"}]}))
        if "調査チームの統合役" in system:
            w.synth.append(self.name)
            return ChatReply("まとめた回答です [1]")
        if "ローカルの日本語アシスタント" in system:
            return ChatReply("こんにちは！")
        raise AssertionError(f"unexpected prompt: {system[:60]}")


class FakeLMStudio(FakeLLM):
    def __init__(self, world, up=True):
        super().__init__(world, "lmstudio")
        self.up = up
        self.is_loaded = False

    async def reachable(self):
        return self.up

    async def chat(self, messages, **kw):
        self.is_loaded = True
        self.world.events.append("lmstudio:chat")
        return await super().chat(messages, **kw)

    async def loaded(self):
        return [("qwen", "qwen")] if self.is_loaded else []

    async def unload_all(self):
        if self.is_loaded:
            self.world.events.append("lmstudio:unload")
        self.is_loaded = False
        return []


class FakeServer:
    def __init__(self, world, selection, port):
        self.world, self.selection, self.port = world, selection, port
        self.alive, self.boot_s = False, 0.1

    async def start(self, timeout):
        if self.selection.model.id in self.world.crash:
            raise chat_graph.WorkerError("crash")
        self.alive = True
        self.world.started.append(self.selection.model.id)
        self.world.events.append(f"start:{self.selection.model.id}")
        self.world.live.add(id(self))

    async def stop(self):
        if self.alive:
            self.world.events.append(f"stop:{self.selection.model.id}")
        self.alive = False
        self.world.live.discard(id(self))

    def client(self, timeout=0):
        return FakeLLM(self.world, self.selection.model.id)


class FakeSearchClient:
    def __init__(self, world):
        self.world = world

    def allow(self, url):
        pass

    async def search(self, query, tool="web", fetch_pages=None):
        self.world.searches.append((tool, query))
        if self.world.no_hits:
            return SearchResult(query, "ddg_lite", [], [], "ddg_lite: 0 件")
        slug = re.sub(r"\W+", "-", query.lower())
        return SearchResult(query, "ddg_lite", [SearchHit(f"title {slug}", f"https://{slug}.example/{i}", "snip")
                                                for i in range(2)])

    async def fetch(self, url):
        self.world.fetched.append(url)

        class P:
            title, text = "T", f"... page text for {url} ..."
        p = P()
        p.url = url
        return p


class FakeComfy:
    def __init__(self, busy=False):
        self.busy = busy

    async def queue_busy(self):
        return self.busy


class World:
    def __init__(self, free_mb=14000):
        self.calls, self.events, self.started, self.searches, self.fetched, self.synth = [], [], [], [], [], []
        self.live: set = set()
        self.crash: set = set()
        self.free_mb = free_mb
        self.intents = [{"tool": "web", "q": "alpha"}, {"tool": "news", "q": "beta"}]
        self.gaps: list = []
        self.critiques = 0
        self.no_hits = False
        self.route_search = True


def _config(world, settings, lmstudio=None, comfy=None):
    lm = lmstudio or FakeLMStudio(world)
    world.lm = lm

    async def tor(_settings):
        world.events.append("tor")
        return "running"

    return {"configurable": {
        "chat_settings": settings, "lmstudio": lm, "comfy_client": comfy or FakeComfy(),
        "server_factory": lambda selection, port: FakeServer(world, selection, port),
        "search_factory": lambda tag: FakeSearchClient(world), "free_memory": lambda: world.free_mb - (13000 if lm.is_loaded else 0),
        "ensure_tor": tor, "thread_id": "t"}}


async def _run(text, world, settings, **kw):
    state = await chat_graph.graph.ainvoke({"messages": [HumanMessage(content=text)]}, _config(world, settings, **kw))
    return state, state["messages"][-1]


async def test_hello_does_not_start_llama_server_or_tor(models_dir):
    world = World()
    state, message = await _run("こんにちは", world, _settings(models_dir))
    assert message.content == "こんにちは！"
    assert world.started == [] and "tor" not in world.events
    assert job_lock.holder is None


async def test_image_attachment_is_sent_to_the_image_tab(models_dir):
    world = World()
    content = [{"type": "text", "text": "これ"}, {"type": "image", "mimeType": "image/png", "data": "AAAA"}]
    state = await chat_graph.graph.ainvoke({"messages": [HumanMessage(content=content)]},
                                           _config(world, _settings(models_dir)))
    assert "画像" in state["messages"][-1].content and world.calls == []


async def test_search_proxy_mode_unloads_27b_before_readers_and_cleans_up(models_dir):
    world = World(free_mb=14000)
    settings = _settings(models_dir)
    state, message = await _run("/search ROG Ally X", world, settings)
    events = world.events
    # The 27B planned first, then was unloaded before any Bonsai started (it does not fit with the readers).
    assert events[0] == "tor" and events[1] == "lmstudio:chat"
    assert events.index("lmstudio:unload") < events.index("start:bonsai-4b")
    assert ("web", "alpha") in world.searches and ("news", "beta") in world.searches
    assert world.started.count("ternary-8b") == 2  # two parallel readers
    assert world.started[-1] == "bonsai-2-27b-abliterated"  # proxy leader for critique + synthesis
    assert world.started.count("bonsai-2-27b-abliterated") == 1  # kept between critique and synthesis
    assert world.synth == ["bonsai-2-27b-abliterated"] and world.critiques == 1
    assert world.live == set() and job_lock.holder is None
    assert message.content.startswith("まとめた回答です [1]") and "**参照**" in message.content
    trace = message.additional_kwargs["search_trace"]
    assert trace["mode"] == "proxy"
    assert trace["roles"]["planner"].startswith("Qwen3.8 27B")
    assert trace["roles"]["synthesizer"].startswith("Ternary-Bonsai-2-27B abliterated")
    assert [i["tool"] for i in trace["intents"]] == ["web", "news"]
    assert all(i["opened"] for i in trace["intents"])
    assert trace["claims_confirmed"] >= 2


async def test_search_resident_mode_keeps_27b_and_never_starts_large_bonsai(models_dir):
    world = World(free_mb=60000)  # plenty of memory: the readers fit next to the 27B
    state, message = await _run("/search something", world, _settings(models_dir))
    assert "bonsai-2-27b-abliterated" not in world.started and "bonsai-2-27b" not in world.started
    assert world.synth == ["lmstudio"]
    assert world.events[-1] == "lmstudio:unload"  # unloaded after the final answer
    assert message.additional_kwargs["search_trace"]["mode"] == "resident"


async def test_zero_results_do_not_call_a_leader(models_dir):
    world = World()
    world.no_hits = True
    state, message = await _run("/search nothing", world, _settings(models_dir))
    assert "検索結果がありません" in message.content
    assert world.synth == [] and world.critiques == 0
    assert "ternary-8b" not in world.started and world.live == set()


async def test_critique_adds_one_more_round_only(models_dir):
    world = World()
    world.gaps = [{"q": "gamma", "tool": "web"}, {"q": "alpha"}]  # "alpha" was already searched
    state, message = await _run("/search topic", world, _settings(models_dir))
    assert world.searches.count(("web", "gamma")) == 1 and world.searches.count(("web", "alpha")) == 1
    assert world.critiques == 1
    trace = message.additional_kwargs["search_trace"]
    assert [i["round"] for i in trace["intents"]] == [0, 0, 1]


async def test_lm_studio_down_plans_with_local_proxy(models_dir):
    world = World()
    lm = FakeLMStudio(world, up=False)
    state, message = await _run("/search offline", world, _settings(models_dir), lmstudio=lm)
    assert "lmstudio:chat" not in world.events
    assert world.started[0] == "bonsai-2-27b-abliterated"  # planner = proxy leader, kept for synthesis
    assert world.started.count("bonsai-2-27b-abliterated") == 1
    assert world.synth == ["bonsai-2-27b-abliterated"]


async def test_proxy_start_failure_tries_next_candidate(models_dir):
    world = World()
    world.crash = {"bonsai-2-27b-abliterated"}
    state, message = await _run("/search topic", world, _settings(models_dir))
    assert world.synth == ["bonsai-2-27b"]


async def test_image_tab_busy_refuses_chat(models_dir):
    world = World()
    state, message = await _run("こんにちは", world, _settings(models_dir), comfy=FakeComfy(busy=True))
    assert "画像タブが実行中" in message.content and job_lock.holder is None


async def test_lock_held_by_image_refuses_search(models_dir):
    world = World()
    token = job_lock.try_acquire("image")
    try:
        state, message = await _run("/search x", world, _settings(models_dir))
    finally:
        job_lock.release(token)
    assert "画像タブが実行中" in message.content and world.started == []


async def test_ambiguous_question_asks_the_router(models_dir):
    world = World()
    world.route_search = False
    state, message = await _run("Python の GIL って何？", world, _settings(models_dir))
    assert world.started == ["qwen3-1.7b-heretic"] and message.content == "こんにちは！"
    world = World()
    state, message = await _run("ROG Ally X の発売日はいつ？", world, _settings(models_dir))
    assert world.started[0] == "qwen3-1.7b-heretic" and world.synth


async def test_missing_llama_server_is_reported(models_dir):
    world = World()
    settings = replace(_settings(models_dir), llama_server="")
    state, message = await _run("/search x", world, settings)
    assert "setup-llamacpp" in message.content and job_lock.holder is None
