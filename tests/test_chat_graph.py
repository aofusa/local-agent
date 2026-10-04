"""The chat tab graph end to end with fakes: no Tor, LM Studio, llama.cpp, ComfyUI or Docker needed."""

import json
import re
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from furry_agent import chat_graph, sandbox
from furry_agent.bonsai_select import Catalog
from furry_agent.config import ChatSettings
from furry_agent.html_text import SearchHit
from furry_agent.job_lock import job_lock
from furry_agent.llm_client import ChatReply, ToolCall
from furry_agent.search_client import SearchResult

ROOT = Path(__file__).resolve().parent.parent
CATALOG = Catalog.load(ROOT / "config" / "search_models.json")


@pytest.fixture(autouse=True)
def _no_shared_log_handler():
    """ingest installs the logs/furry_agent.log handler once per process; drop it so the image tests that
    check their own logs_dir still see it created."""
    yield
    import logging

    logger = logging.getLogger("furry_agent")
    logger.handlers[:] = [h for h in logger.handlers if not getattr(h, "_furry_agent", False)]


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
    kw.setdefault("code_dir", d.parent / "code")
    return replace(ChatSettings(), llama_server=str(exe), models_dir=d, rank_path=d / "missing-rank.json",
                   catalog_path=catalog, logs_dir=d.parent / "logs", job_lock_timeout_s=0.2, sandbox_wait_s=0.5,
                   **kw)


def _system(messages) -> str:
    return messages[0]["content"] if messages and messages[0]["role"] == "system" else ""


CODE_REPLY = """SPEC: 1 から 3 を表示する
FILE: main.py
```python
for i in range(1, 4):
    print(i)
```
COMMAND: python main.py
"""


class FakeLLM:
    """Answers by role, recognised from the system prompt file."""

    def __init__(self, world, name):
        self.world, self.name = world, name

    async def chat(self, messages, tools=None, **kw):
        w, system = self.world, _system(messages)
        w.calls.append((self.name, system[:30]))
        w.thinking.append((self.name, system[:30], kw.get("thinking")))
        user = messages[-1]["content"] if messages else ""
        if "planner of a small web research team in deep mode" in system:
            if w.plan_fail:
                return ChatReply("broken")
            return ChatReply(json.dumps({"goal": "ゴール", "subquestions": w.subquestions, "intents": w.deep_intents}))
        if "planner of a small web research team" in system:
            if w.plan_fail:
                return ChatReply("broken")
            return ChatReply(json.dumps({"intents": w.intents}))
        if "You filter search results" in system:
            return ChatReply('{"relevant":[1,2,3,4,5,6]}')
        if "You route a chat message" in system:
            return ChatReply(json.dumps({"kind": w.route_kind, "query": "rewritten query", "deep": w.route_deep}))
        if "critic of a web research team" in system:
            w.critiques += 1
            card_ids = re.findall(r"^- (\d+\.\d+) \[", user, re.MULTILINE)
            w.critic_inputs.append(user)
            return ChatReply(json.dumps(w.reflect(w.critiques, card_ids)))
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
            w.synth_inputs.append(user)
            return ChatReply("まとめた回答です [1]", reasoning="統合の思考" if kw.get("thinking") else "")
        if "書く前の設計" in system:
            w.writer_inputs.append(("outline", user))
            outline = [{"id": f"s{i}", "title": f"第{i}章", "beats": ["出来事"]} for i in range(1, w.chapters + 1)]
            return ChatReply(json.dumps({"brief": {"genre": "SF", "pov": "一人称", "length": "約1000字",
                                                   "must": ["猫"], "must_not": [], "language": "ja"},
                                         "outline": outline, "open": []}))
        if "本文だけを書きます" in system:
            w.writer_inputs.append(("draft", user))
            w.drafts += 1
            return ChatReply(f"本文{w.drafts}。猫が歩いた。", reasoning="本文の思考" if kw.get("thinking") else "")
        if "差分だけ直します" in system:
            w.writer_inputs.append(("revise", user))
            return ChatReply(json.dumps({"notes": ["表現を整えた"], "edits": [{"find": "猫が歩いた", "replace": "猫が駆けた"}]}))
        if "careful programmer" in system:
            w.code_inputs.append(user)
            return ChatReply(w.code_replies.pop(0) if w.code_replies else CODE_REPLY)
        if "ローカルの日本語アシスタント" in system:
            return ChatReply("こんにちは！", reasoning="会話の思考" if kw.get("thinking") else "")
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
        slug = "same" if self.world.same_urls else re.sub(r"\W+", "-", query.lower())
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


class FakeDocker:
    """sandbox's runner: records every docker CLI call."""

    def __init__(self, world, *, up=True, image=True, exits=None, timeout=False, desktop=False):
        self.world, self.up, self.image, self.exits, self.timeout = world, up, image, list(exits or [0]), timeout
        self.desktop = desktop

    def __call__(self, argv, timeout_s):
        self.world.docker.append(list(argv))
        verb = argv[1]
        if verb == "desktop":
            if argv[2] == "start":
                self.up = True
            elif argv[2] == "stop":
                self.up = False
            return subprocess.CompletedProcess(argv, 0 if self.desktop else 1, b"", b"")
        if verb == "version":
            return subprocess.CompletedProcess(argv, 0 if self.up else 1, b"linux" if self.up else b"", b"")
        if verb == "image":
            return subprocess.CompletedProcess(argv, 0 if self.image else 1, b"sha", b"")
        if verb == "run":
            self.world.job_lock_during_run.append(job_lock.holder)
            if self.timeout:
                raise subprocess.TimeoutExpired(argv, timeout_s, output=b"partial", stderr=b"")
            code = self.exits.pop(0) if self.exits else 0
            return subprocess.CompletedProcess(argv, code, b"1\n2\n3\n", b"" if code == 0 else b"Traceback: boom")
        return subprocess.CompletedProcess(argv, 0, b"", b"")


def _answered(n, card_ids):
    return {"subquestions": [{"id": "q1", "status": "answered", "evidence_card_ids": card_ids[:1]},
                             {"id": "q2", "status": "answered", "evidence_card_ids": card_ids[-1:]}],
            "next_intents": [], "stop": True, "stop_reason": "sufficient"}


class World:
    def __init__(self, free_mb=14000):
        self.calls, self.events, self.started, self.searches, self.fetched, self.synth = [], [], [], [], [], []
        self.thinking, self.synth_inputs, self.critic_inputs, self.writer_inputs, self.code_inputs = [], [], [], [], []
        self.docker, self.job_lock_during_run = [], []
        self.live: set = set()
        self.crash: set = set()
        self.free_mb = free_mb
        self.intents = [{"tool": "web", "q": "alpha"}, {"tool": "news", "q": "beta"}]
        self.subquestions = [{"id": "q1", "question": "A は何か"}, {"id": "q2", "question": "B はいつか"}]
        self.deep_intents = [{"tool": "web", "q": "alpha", "subquestion_id": "q1"},
                             {"tool": "news", "q": "beta", "subquestion_id": "q2"}]
        self.reflect = _answered
        self.critiques = 0
        self.no_hits = False
        self.same_urls = False
        self.plan_fail = False
        self.route_kind = "SEARCH"
        self.route_deep = False
        self.chapters = 1
        self.drafts = 0
        self.code_replies: list[str] = []


def _config(world, settings, lmstudio=None, comfy=None, mode=None, task=None, docker=None, thread="t"):
    lm = lmstudio or FakeLMStudio(world)
    world.lm = lm

    async def tor(_settings):
        world.events.append("tor")
        return "running"

    conf = {"chat_settings": settings, "lmstudio": lm, "comfy_client": comfy or FakeComfy(),
            "server_factory": lambda selection, port: FakeServer(world, selection, port),
            "search_factory": lambda tag: FakeSearchClient(world),
            "free_memory": lambda: world.free_mb - (13000 if lm.is_loaded else 0),
            "ensure_tor": tor, "thread_id": thread, "sandbox_runner": docker or FakeDocker(world)}
    if mode:
        conf["mode"] = mode
    if task:
        conf["task"] = task
    return {"configurable": conf}


async def _run(text, world, settings, **kw):
    state = await chat_graph.graph.ainvoke({"messages": [HumanMessage(content=text)]}, _config(world, settings, **kw))
    return state, state["messages"][-1]


def _hitl_graph():
    return chat_graph.builder.compile(checkpointer=InMemorySaver())


def _interrupt(state):
    interrupts = state.get("__interrupt__") or []
    return interrupts[0].value if interrupts else None


# --- chat / routing ------------------------------------------------------------------------------------------------


async def test_hello_does_not_start_llama_server_or_tor(models_dir):
    world = World()
    state, message = await _run("こんにちは", world, _settings(models_dir))
    assert message.content == "こんにちは！"
    assert world.started == [] and "tor" not in world.events
    assert job_lock.holder is None
    assert state["mode"] == "fast"  # no configurable.mode -> fast


async def test_image_attachment_is_sent_to_the_image_tab(models_dir):
    world = World()
    content = [{"type": "text", "text": "これ"}, {"type": "image", "mimeType": "image/png", "data": "AAAA"}]
    state = await chat_graph.graph.ainvoke({"messages": [HumanMessage(content=content)]},
                                           _config(world, _settings(models_dir)))
    assert "画像" in state["messages"][-1].content and world.calls == []


async def test_draw_request_stays_with_the_image_tab(models_dir):
    world = World()
    state, message = await _run("猫の獣人を描いて", world, _settings(models_dir), mode="think")
    assert "画像" in message.content and world.calls == [] and state["route"]["kind"] == "image_tab"


async def test_image_tab_busy_refuses_chat(models_dir):
    world = World()
    state, message = await _run("こんにちは", world, _settings(models_dir), comfy=FakeComfy(busy=True))
    assert "画像タブが実行中" in message.content and job_lock.holder is None


async def test_lock_held_by_image_refuses_search(models_dir):
    world = World()
    token = job_lock.try_acquire("image")
    try:
        state, message = await _run("/search x", world, _settings(models_dir), mode="think")
    finally:
        job_lock.release(token)
    assert "画像タブが実行中" in message.content and world.started == []


async def test_ambiguous_question_asks_the_router(models_dir):
    world = World()
    world.route_kind = "CHAT"
    state, message = await _run("Python の GIL って何？", world, _settings(models_dir))
    assert world.started == ["qwen3-1.7b-heretic"] and message.content == "こんにちは！"
    world = World()
    state, message = await _run("ROG Ally X の発売日はいつ？", world, _settings(models_dir))
    assert world.started[0] == "qwen3-1.7b-heretic" and world.synth


async def test_router_can_send_to_write_and_never_to_the_image_tab(models_dir):
    world = World()
    world.route_kind = "WRITE"
    state, message = await _run("夏の思い出についてまとめて", world, _settings(models_dir))
    assert state["route"]["kind"] == "write" and message.content.startswith("本文1")
    world = World()
    world.route_kind = "TO_IMAGE_TAB"
    state, message = await _run("夏の思い出についてまとめて", world, _settings(models_dir))
    assert state["route"]["kind"] == "chat" and message.content == "こんにちは！"


async def test_auto_mode_uses_the_router_deep_flag(models_dir):
    world = World()
    world.route_kind, world.route_deep = "CHAT", True
    state, message = await _run("この件をどう考えたらいい？", world, _settings(models_dir), mode="auto")
    assert state["mode"] == "think" and state["mode_info"]["requested"] == "auto"
    assert message.additional_kwargs["chat_mode"]["mode"] == "think"


async def test_missing_llama_server_is_reported(models_dir):
    world = World()
    settings = replace(_settings(models_dir), llama_server="")
    state, message = await _run("/search x", world, settings)
    assert "setup-llamacpp" in message.content and job_lock.holder is None


# --- modes and thinking --------------------------------------------------------------------------------------------


async def test_fast_chat_has_thinking_off_and_no_thinking_block(models_dir):
    world = World()
    state, message = await _run("こんにちは", world, _settings(models_dir), mode="fast")
    assert world.thinking[-1][2] is False
    assert "thinking" not in message.additional_kwargs


async def test_think_chat_turns_thinking_on_and_keeps_it_out_of_the_answer(models_dir):
    world = World()
    state, message = await _run("こんにちは", world, _settings(models_dir), mode="think")
    assert world.thinking[-1][2] is True
    assert message.content == "こんにちは！" and "思考" not in message.content
    assert message.additional_kwargs["thinking"] == [{"stage": "回答", "text": "会話の思考"}]
    assert message.additional_kwargs["chat_mode"]["mode"] == "think"


async def test_unknown_mode_is_fast(models_dir):
    world = World()
    state, _ = await _run("こんにちは", world, _settings(models_dir), mode="turbo")
    assert state["mode"] == "fast"


# --- search: fast -------------------------------------------------------------------------------------------------


async def test_fast_search_is_one_intent_one_round_without_critique(models_dir):
    world = World()
    state, message = await _run("/search ROG Ally X", world, _settings(models_dir))
    assert world.critiques == 0 and world.searches == [("web", "alpha")]
    assert world.synth and world.live == set() and job_lock.holder is None
    trace = message.additional_kwargs["search_trace"]
    assert trace["stop_reason"] == "budget" and trace["round"] == 0 and trace["chat_mode"] == "fast"
    assert all("思考" not in t for t in message.content.splitlines())


async def test_search_proxy_mode_unloads_27b_before_readers_and_cleans_up(models_dir):
    world = World(free_mb=14000)
    state, message = await _run("/search ROG Ally X", world, _settings(models_dir), mode="think")
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
    assert trace["stop_reason"] == "sufficient" and trace["goal"] == "ゴール"
    assert [s["status"] for s in trace["subquestions"]] == ["answered", "answered"]
    assert trace["adopted"] and trace["pages_read"] == 2


async def test_search_resident_mode_keeps_27b_and_never_starts_large_bonsai(models_dir):
    world = World(free_mb=60000)  # plenty of memory: the readers fit next to the 27B
    state, message = await _run("/search something", world, _settings(models_dir), mode="think")
    assert "bonsai-2-27b-abliterated" not in world.started and "bonsai-2-27b" not in world.started
    assert world.synth == ["lmstudio"]
    assert world.events[-1] == "lmstudio:unload"  # unloaded after the final answer
    assert message.additional_kwargs["search_trace"]["mode"] == "resident"
    # Thinking only on the LM Studio 27B, and only for the free-text answer (JSON calls stay off).
    synth_thinking = [t for t in world.thinking if "統合役" in t[1]]
    assert synth_thinking[-1][2] is True
    assert message.additional_kwargs["thinking"] == [{"stage": "統合", "text": "統合の思考"}]


async def test_zero_results_do_not_call_a_leader(models_dir):
    world = World()
    world.no_hits = True
    state, message = await _run("/search nothing", world, _settings(models_dir), mode="think")
    assert "検索結果がありません" in message.content
    assert world.synth == [] and world.critiques == 0
    assert "ternary-8b" not in world.started and world.live == set()
    assert message.additional_kwargs["search_trace"]["stop_reason"] == "no_hits"


# --- search: think rounds -------------------------------------------------------------------------------------------


def _open_q2_until(last_round):
    """q1 answered at once; q2 stays open (with a new query each time) until ``last_round``."""
    def reflect(n, card_ids):
        if n >= last_round:
            return _answered(n, card_ids)
        return {"subquestions": [{"id": "q1", "status": "answered", "evidence_card_ids": card_ids[:1]},
                                 {"id": "q2", "status": "open"}],
                "next_intents": [{"tool": "web", "q": f"gamma {n}", "subquestion_id": "q2"}],
                "stop": False, "stop_reason": "need_more"}
    return reflect


async def test_think_goes_on_while_open_and_new_cards_come(models_dir):
    world = World()
    world.reflect = _open_q2_until(3)
    state, message = await _run("/search topic", world, _settings(models_dir), mode="think")
    assert world.critiques == 3
    assert ("web", "gamma 1") in world.searches and ("web", "gamma 2") in world.searches
    trace = message.additional_kwargs["search_trace"]
    assert trace["round"] == 3 and trace["stop_reason"] == "sufficient"
    assert [i["round"] for i in trace["intents"]] == [0, 0, 1, 2]
    assert len(world.fetched) == len(set(world.fetched))  # no URL read twice


async def test_think_stops_at_max_rounds_with_budget(models_dir):
    world = World()
    world.reflect = _open_q2_until(99)
    state, message = await _run("/search topic", world, _settings(models_dir, search_max_rounds=2), mode="think")
    trace = message.additional_kwargs["search_trace"]
    assert world.critiques == 2 and trace["round"] == 2 and trace["stop_reason"] == "budget"
    assert "未解決の下位問い" in message.content and "B はいつか" in message.content
    assert "停止理由: 予算の上限" in message.content


async def test_think_stops_at_the_page_limit(models_dir):
    world = World()
    world.reflect = _open_q2_until(99)
    state, message = await _run("/search topic", world, _settings(models_dir, search_max_pages=2), mode="think")
    trace = message.additional_kwargs["search_trace"]
    assert world.critiques == 1 and trace["stop_reason"] == "budget" and trace["pages_read"] >= 2


async def test_think_stops_on_the_wall_clock(models_dir):
    world = World()
    world.reflect = _open_q2_until(99)
    state, message = await _run("/search topic", world, _settings(models_dir, search_wall_clock_s=0.0), mode="think")
    assert world.critiques == 1 and message.additional_kwargs["search_trace"]["stop_reason"] == "budget"


async def test_think_stops_when_no_new_cards_come(models_dir):
    world = World()
    world.same_urls = True  # every query finds the same pages: the second round has nothing new to read
    world.reflect = _open_q2_until(99)
    state, message = await _run("/search topic", world, _settings(models_dir), mode="think")
    trace = message.additional_kwargs["search_trace"]
    assert trace["stop_reason"] in ("no_hits", "diminishing")
    assert len(world.fetched) == len(set(world.fetched))
    assert any(r["reason"].startswith("既に読んだ") for r in trace["rejected"])


async def test_diminishing_when_a_round_adds_no_card(models_dir):
    world = World()
    world.reflect = _open_q2_until(99)

    real = FakeLLM.chat

    async def no_cards_after_first(self, messages, tools=None, **kw):
        system = _system(messages)
        if "reader in a web research team" in system and not tools and "gamma" in json.dumps(messages):
            return ChatReply('{"cards":[]}')
        return await real(self, messages, tools=tools, **kw)

    FakeLLM.chat = no_cards_after_first
    try:
        world.no_hits = False
        # Round 2 hits have no snippet, so the snippet fallback gives no card either.
        orig = FakeSearchClient.search

        async def search(self, query, tool="web", fetch_pages=None):
            result = await orig(self, query, tool, fetch_pages)
            if "gamma" in query:
                result.hits = [SearchHit(h.title, h.url, "") for h in result.hits]
            return result

        FakeSearchClient.search = search
        try:
            state, message = await _run("/search topic", world, _settings(models_dir), mode="think")
        finally:
            FakeSearchClient.search = orig
    finally:
        FakeLLM.chat = real
    assert message.additional_kwargs["search_trace"]["stop_reason"] == "diminishing"


async def test_plan_failure_falls_back_to_one_intent(models_dir):
    for mode in ("fast", "think"):
        world = World()
        world.plan_fail = True
        state, message = await _run("/search ROG Ally X メモリ", world, _settings(models_dir), mode=mode)
        trace = message.additional_kwargs["search_trace"]
        assert len([i for i in trace["intents"] if i["round"] == 0]) == 1
        assert "規則で 1 本" in trace["roles"]["planner"]


async def test_lm_studio_down_plans_with_local_proxy(models_dir):
    world = World()
    lm = FakeLMStudio(world, up=False)
    state, message = await _run("/search offline", world, _settings(models_dir), lmstudio=lm, mode="think")
    assert "lmstudio:chat" not in world.events
    assert world.started[0] == "bonsai-2-27b-abliterated"  # planner = proxy leader, kept for synthesis
    assert world.started.count("bonsai-2-27b-abliterated") == 1
    assert world.synth == ["bonsai-2-27b-abliterated"]


async def test_proxy_start_failure_tries_next_candidate(models_dir):
    world = World()
    world.crash = {"bonsai-2-27b-abliterated"}
    state, message = await _run("/search topic", world, _settings(models_dir), mode="think")
    assert world.synth == ["bonsai-2-27b"]


async def test_contradictions_are_reported_with_both_cards(models_dir):
    world = World()

    def reflect(n, card_ids):
        return {"subquestions": [{"id": "q1", "status": "answered", "evidence_card_ids": card_ids[:1]},
                                 {"id": "q2", "status": "partial", "evidence_card_ids": card_ids[-1:]}],
                "contradictions": [{"subquestion_id": "q2", "card_ids": card_ids[:2], "summary": "日付が違う"}],
                "next_intents": [], "stop": True, "stop_reason": "diminishing"}

    world.reflect = reflect
    state, message = await _run("/search topic", world, _settings(models_dir), mode="think")
    assert "**食い違い**" in message.content and "日付が違う [1] [2]" in message.content
    assert "矛盾" in world.synth_inputs[-1]


# --- writing --------------------------------------------------------------------------------------------------------


async def test_fast_write_is_one_shot_without_search(models_dir):
    world = World()
    state, message = await _run("実在の事件を元に小説を書いて", world, _settings(models_dir), mode="fast")
    assert world.searches == [] and "tor" not in world.events
    assert [k for k, _ in world.writer_inputs] == ["draft"]
    assert message.content == "本文1。猫が歩いた。"
    assert state["artifact"]["draft"] == "本文1。猫が歩いた。"
    assert all("furry" not in s.lower() and "danbooru" not in s.lower() for _, s in world.calls)


async def test_think_write_outlines_drafts_and_revises_with_edits(models_dir):
    world = World()
    state, message = await _run("猫の短編小説を書いて", world, _settings(models_dir), mode="think")
    assert [k for k, _ in world.writer_inputs] == ["outline", "draft", "revise"]
    revise_input = world.writer_inputs[2][1]
    assert "本文1。猫が歩いた。" in revise_input and "ブリーフ" in revise_input  # the draft and the brief go in
    assert message.content == "本文1。猫が駆けた。"  # an edit was applied, not a rewrite
    assert state["artifact"]["status"] == "revised" and state["artifact"]["revision_notes"] == ["表現を整えた"]
    assert message.additional_kwargs["thinking"] == [{"stage": "本文", "text": "本文の思考"}]
    assert job_lock.holder is None


async def test_continue_starts_from_the_artifact_draft(models_dir):
    world = World()
    graph = _hitl_graph()
    config = _config(world, _settings(models_dir), mode="fast", thread="w1")
    await graph.ainvoke({"messages": [HumanMessage(content="猫の小説を書いて")]}, config)
    state = await graph.ainvoke({"messages": [HumanMessage(content="続きを書いて")]}, config)
    last_input = world.writer_inputs[-1][1]
    assert "これまでの本文の末尾" in last_input and "本文1。猫が歩いた。" in last_input
    assert state["artifact"]["draft"] == "本文1。猫が歩いた。\n\n本文2。猫が歩いた。"


async def test_think_write_with_facts_searches_first(models_dir):
    world = World()
    state, message = await _run("実在の事件を調べてから小説にして", world, _settings(models_dir), mode="think")
    assert world.searches and world.synth
    assert [k for k, _ in world.writer_inputs] == ["outline", "draft", "revise"]
    assert "参考資料" in world.writer_inputs[0][1] and "まとめた回答です" in world.writer_inputs[0][1]


async def test_chapters_interrupt_and_reject_stops(models_dir):
    world = World()
    world.chapters = 3
    graph = _hitl_graph()
    config = _config(world, _settings(models_dir), mode="think", thread="w2")
    state = await graph.ainvoke({"messages": [HumanMessage(content="長編小説を章立てで書いて")]}, config)
    card = _interrupt(state)
    assert card["action_requests"][0]["name"] == "continue_writing"
    assert job_lock.holder is None  # the lock is not held while waiting for the user
    state = await graph.ainvoke(Command(resume={"decisions": [{"type": "approve"}]}), config)
    assert _interrupt(state) is not None and world.drafts == 2
    state = await graph.ainvoke(Command(resume={"decisions": [{"type": "reject"}]}), config)
    assert _interrupt(state) is None and world.drafts == 2
    assert state["artifact"]["chapter_index"] == 2
    assert any("止めました" in m.content for m in state["messages"])
    chapters = [m for m in state["messages"] if m.id.endswith(("-ch1", "-ch2"))]
    assert [m.content for m in chapters] == ["本文1。猫が駆けた。", "本文2。猫が駆けた。"]


# --- code -----------------------------------------------------------------------------------------------------------


def _runs(world):
    return [argv for argv in world.docker if argv[1] == "run"]


async def test_fast_code_writes_files_and_never_runs(models_dir):
    world = World()
    settings = _settings(models_dir)
    state, message = await _run("1 から 3 を表示する Python スクリプトを書いて実行して", world, settings, mode="fast")
    assert world.docker == []
    run_dir = settings.code_dir / state["code"]["run_id"]
    assert (run_dir / "main.py").read_text(encoding="utf-8").startswith("for i in range")
    assert "速いモードでは実行しません" in message.content


async def test_think_code_waits_for_approval_then_runs_in_the_sandbox(models_dir):
    world = World()
    settings = _settings(models_dir)
    graph = _hitl_graph()
    config = _config(world, settings, mode="think", thread="c1")
    state = await graph.ainvoke({"messages": [HumanMessage(content="1 から 3 を表示するコードを書いて実行して")]}, config)
    card = _interrupt(state)
    assert card["action_requests"][0]["name"] == "run_code"
    assert card["action_requests"][0]["args"]["network"] == "none"
    assert _runs(world) == []  # nothing ran before the approval
    assert job_lock.holder is None
    state = await graph.ainvoke(Command(resume={"decisions": [{"type": "approve"}]}), config)
    runs = _runs(world)
    assert len(runs) == 1 and runs[0][-2:] == ["python", "main.py"]
    assert "--network" in runs[0] and runs[0][runs[0].index("--network") + 1] == "none"
    assert world.job_lock_during_run == [None]  # the sandbox does not hold the image tab's job lock
    assert "1\n2\n3" in state["messages"][-1].content and state["code"]["last_exit"] == 0


async def test_reject_does_not_run(models_dir):
    world = World()
    graph = _hitl_graph()
    config = _config(world, _settings(models_dir), mode="think", thread="c2")
    await graph.ainvoke({"messages": [HumanMessage(content="コードを書いて実行して")]}, config)
    state = await graph.ainvoke(Command(resume={"decisions": [{"type": "reject"}]}), config)
    assert _runs(world) == [] and "実行しませんでした" in state["messages"][-1].content


async def test_edit_changes_the_command_and_asks_again(models_dir):
    world = World()
    graph = _hitl_graph()
    config = _config(world, _settings(models_dir), mode="think", thread="c3")
    await graph.ainvoke({"messages": [HumanMessage(content="コードを書いて実行して")]}, config)
    edit = {"type": "edit", "edited_action": {"name": "run_code", "args": {"command": "python3 main.py"}}}
    state = await graph.ainvoke(Command(resume={"decisions": [edit]}), config)
    assert _interrupt(state)["action_requests"][0]["args"]["command"] == "python3 main.py"
    assert _runs(world) == []
    bad = {"type": "edit", "edited_action": {"name": "run_code", "args": {"command": "python main.py && rm -rf /"}}}
    state = await graph.ainvoke(Command(resume={"decisions": [bad]}), config)
    assert "反映できませんでした" in state["messages"][-1].content and _runs(world) == []
    state = await graph.ainvoke(Command(resume={"decisions": [{"type": "approve"}]}), config)
    assert _runs(world)[0][-2:] == ["python3", "main.py"]


async def test_failed_run_is_fixed_once_then_stops(models_dir):
    world = World()
    graph = _hitl_graph()
    config = _config(world, _settings(models_dir), mode="think", thread="c4",
                     docker=FakeDocker(world, exits=[1, 1, 1]))
    await graph.ainvoke({"messages": [HumanMessage(content="コードを書いて実行して")]}, config)
    state = await graph.ainvoke(Command(resume={"decisions": [{"type": "approve"}]}), config)
    assert _interrupt(state) is not None and len(world.code_inputs) == 2  # the fix asked again for approval
    assert "Traceback: boom" in world.code_inputs[1]
    state = await graph.ainvoke(Command(resume={"decisions": [{"type": "approve"}]}), config)
    assert _interrupt(state) is None and len(_runs(world)) == 2  # first run + one fix, then stop
    assert "2 回実行しましたが成功しませんでした" in state["messages"][-1].content


async def test_timeout_kills_the_container(models_dir):
    world = World()
    graph = _hitl_graph()
    config = _config(world, _settings(models_dir), mode="think", thread="c5",
                     docker=FakeDocker(world, timeout=True))
    await graph.ainvoke({"messages": [HumanMessage(content="コードを書いて実行して")]}, config)
    await graph.ainvoke(Command(resume={"decisions": [{"type": "approve"}]}), config)
    assert any(argv[1] == "kill" for argv in world.docker)


async def test_docker_missing_writes_files_and_says_why(models_dir):
    world = World()
    settings = _settings(models_dir)
    state, message = await _run("コードを書いて実行して", world, settings, mode="think",
                                docker=FakeDocker(world, up=False))
    assert _runs(world) == [] and "Docker Desktop が起動していません" in message.content
    assert (settings.code_dir / state["code"]["run_id"] / "main.py").is_file()
    assert _interrupt(state) is None


async def test_stopped_docker_desktop_is_started_only_for_the_approved_run(models_dir):
    world = World()
    graph = _hitl_graph()
    config = _config(world, _settings(models_dir), mode="think", thread="c7",
                     docker=FakeDocker(world, up=False, desktop=True))
    state = await graph.ainvoke({"messages": [HumanMessage(content="コードを書いて実行して")]}, config)
    assert "Docker Desktop: 停止中" in _interrupt(state)["action_requests"][0]["description"]
    assert not any(a[1:3] == ["desktop", "start"] for a in world.docker)  # nothing started before approval
    state = await graph.ainvoke(Command(resume={"decisions": [{"type": "approve"}]}), config)
    verbs = [" ".join(a[1:3]) for a in world.docker if a[1] in ("desktop", "run")]
    assert verbs[-3:] == ["desktop start", "run --rm", "desktop stop"]
    assert state["code"]["last_exit"] == 0


async def test_dependencies_get_network_only_for_the_setup_step(models_dir):
    world = World()
    world.code_replies = [CODE_REPLY.replace("COMMAND:", "FILE: requirements.txt\n```text\nrich\n```\nCOMMAND:")]
    graph = _hitl_graph()
    config = _config(world, _settings(models_dir), mode="think", thread="c6")
    state = await graph.ainvoke({"messages": [HumanMessage(content="rich を pip install してコードを書いて実行して")]},
                                config)
    assert _interrupt(state)["action_requests"][0]["args"]["network"] == "setup"
    await graph.ainvoke(Command(resume={"decisions": [{"type": "approve"}]}), config)
    setup, run = _runs(world)
    assert setup[setup.index("--network") + 1] == "bridge" and "pip" in setup
    assert run[run.index("--network") + 1] == "none"


# --- event loop --------------------------------------------------------------------------------------------------------


async def test_no_blocking_calls_in_event_loop(models_dir):
    # langgraph dev runs nodes under blockbuster and fails runs that block the loop.
    from blockbuster import blockbuster_ctx

    with blockbuster_ctx():
        world = World()
        state, message = await _run("/search topic", world, _settings(models_dir), mode="think")
        assert not state.get("error") and world.synth
        world = World()
        state, message = await _run("ROG Ally X の発売日はいつ？", world, _settings(models_dir))
        assert not state.get("error")
        world = World()
        state, message = await _run("猫の短編小説を書いて", world, _settings(models_dir), mode="think")
        assert not state.get("error")
        world = World()
        state, message = await _run("コードを書いて", world, _settings(models_dir), mode="fast")
        assert not state.get("error")
        world = World()
        graph = _hitl_graph()
        config = _config(world, _settings(models_dir), mode="think", thread="blk")
        await graph.ainvoke({"messages": [HumanMessage(content="コードを書いて実行して")]}, config)
        state = await graph.ainvoke(Command(resume={"decisions": [{"type": "approve"}]}), config)
        assert not state.get("error") and _runs(world)
