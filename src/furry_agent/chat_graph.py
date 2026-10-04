"""LangGraph chat tab (graph id ``chat``): conversation, deep web search through Tor, writing and code.

    ingest      last human message -> chat / search / write / code / "use the image tab" (router.route, keyword
                rules) and the mode: configurable.mode fast | think | auto (modes.choose; none = fast)
    route       only for messages the rules could not place: Qwen3-1.7B-heretic picks CHAT / SEARCH / WRITE /
                CODE, rewrites a search query and says whether the request is deep (auto mode)
    chat        the LM Studio Qwen3.8 27B answers directly (think mode: thinking tokens on)
    plan        fast: 1 intent. think: the goal, 2-5 sub-questions and 1-3 intents. The 27B is unloaded when the
                readers do not fit next to it, and the Ternary-Bonsai-2-27B abliterated (PTQ1_0, PrismML
                llama-server) becomes the leader for critique and synthesis ("proxy" mode)
    search      one per intent (Send): plain Python, DuckDuckGo through Tor, no model
    filter      Bonsai-4B keeps the relevant results; already read URLs are dropped; readers are chosen
    read        one per reader (Send): a Ternary-Bonsai-8B llama-server opens pages and extracts fact cards
    judge       think only: progress text while the critic works
    critique    think only: the leader scores every sub-question (answered / partial / open), lists
                contradictions and writes the next intents; search_agent.next_round decides another round
                (up to 4 rounds, 12 pages, SEARCH_WALL_CLOCK_S) or the stop reason
    synthesize  the leader writes one answer from the cards with [n] citations, the open sub-questions, the
                contradictions and the stop reason; every llama-server is gone, LM Studio is unloaded, the shared
                job lock is released. A writing request that needed facts goes on to the writer instead.
    write_*     write_nodes: brief/outline -> draft -> revise (-> chapter confirmation)
    code_*      code_nodes: code_plan -> write_files -> confirm_run -> sandbox_exec -> observe

The image graph (graph.py) is not changed by this module; both share job_lock so they never run together.
docs/chat-deep-search-creative-sandbox.md is the design.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from furry_agent import code_nodes, modes, search_agent as sa, write_nodes, writing
from furry_agent.bonsai_select import (Catalog, Rank, Selection, SelectionError, available_models, free_memory_mb,
                                       select_model)
from furry_agent.bonsai_worker import Ledger, LlamaServer, WorkerError, free_port, run_reader
from furry_agent.chat_common import (RESET, ChatState, StageError, _ask, capped, _cleanup, _conf, _fail, _final, _held,
                                     _history, _is_think, _last_human, _leaders, _ledgers, _lmstudio, _lock,
                                     _progress, _prompt, _settings, _text_of, log)
from furry_agent.config import ChatSettings
from furry_agent.graph import _setup_file_logging  # the same logs/furry_agent.log as the image tab
from furry_agent.job_lock import JobLockBusy, job_lock
from furry_agent.llm_client import LLMError, OpenAICompatClient
from furry_agent.router import CHAT, CODE, SEARCH, TO_IMAGE_TAB, WRITE, Route, route as route_rules
from furry_agent.search_client import SearchError, TorSearchClient
from furry_agent.tor_service import TorUnavailable, ensure_tor

__all__ = ["graph", "ChatState", "WorkerError", "RESET"]

LEADER_LABEL = "Qwen3.8 27B abliterated（LM Studio）"
HITS_PER_INTENT = 4  # default of SEARCH_HITS_PER_INTENT
PORT_ROUTE, PORT_FILTER, PORT_LEADER = 7, 8, 9  # offsets from BONSAI_BASE_PORT; readers use 0..2
LARGE_BOOT_S = 300.0
LEADER_CTX = 8192  # the proxy leader's llama-server context (_server: large models get at least 8192)
ROUTER_KINDS = {"SEARCH": SEARCH, "WRITE": WRITE, "CODE": CODE, "CHAT": CHAT}  # the router never sends to the image tab


# --- dependencies (overridable through config["configurable"] for tests) ----------------------------------------


def _server(config: RunnableConfig | None, settings: ChatSettings, selection: Selection, port: int) -> LlamaServer:
    factory = _conf(config).get("server_factory")
    if factory:
        return factory(selection, port)
    ctx = max(settings.ctx, 8192) if selection.model.large else settings.ctx
    return LlamaServer(settings.llama_server, selection.path, port, ctx, selection.ngl, selection.model.label,
                       settings.logs_dir)


def _search_client(config: RunnableConfig | None, settings: ChatSettings, tag: str):
    factory = _conf(config).get("search_factory")
    if factory:
        return factory(tag)
    return TorSearchClient(settings.tor_socks_url, settings.search_timeout_s, settings.search_max_results,
                           0, isolation=tag)


def _free_mb(config: RunnableConfig | None) -> int:
    fn = _conf(config).get("free_memory")
    return int(fn()) if fn else free_memory_mb()


async def _ensure_tor(config: RunnableConfig | None, settings: ChatSettings) -> str:
    fn = _conf(config).get("ensure_tor")
    return await fn(settings) if fn else await ensure_tor(settings)


def _catalog(settings: ChatSettings) -> tuple[Catalog, Rank, dict[str, Path]]:
    catalog = Catalog.load(settings.catalog_path)
    return catalog, Rank.load(settings.rank_path), available_models(catalog, settings.models_dir)


async def _run_model(config, settings: ChatSettings, task: str, port: int,
                     fn: Callable[[OpenAICompatClient, Selection], Awaitable[Any]], *, leader_resident: bool,
                     max_tokens_timeout: float | None = None) -> tuple[Any, Selection]:
    """Start the best model for ``task``, run ``fn`` on it and kill it. A failed start tries the next candidate
    once; a second failure stops (design doc §5.6 step 5)."""
    catalog, rank, available = await asyncio.to_thread(_catalog, settings)
    failed: list[str] = []
    errors = []
    for _ in range(2):
        selection = select_model(task, catalog, rank, available, _free_mb(config), leader_resident=leader_resident,
                                 reserve_mb=settings.reserve_mb, exclude=tuple(failed))
        server = _server(config, settings, selection, await asyncio.to_thread(free_port, settings.base_port + port))
        try:
            await server.start(LARGE_BOOT_S if selection.model.large else settings.worker_timeout_s)
            return await fn(server.client(max_tokens_timeout or settings.worker_timeout_s), selection), selection
        except WorkerError as exc:
            failed.append(selection.model.id)
            errors.append(str(exc))
            log.warning("%s model %s failed to start: %s", task, selection.model.id, exc)
        finally:
            await server.stop()
    raise WorkerError(f"{task} のモデルを起動できません（次点も失敗）: " + " / ".join(errors))


async def _leader(config, settings: ChatSettings, token: str, task: str) -> tuple[OpenAICompatClient, str]:
    """The proxy leader (Ternary-Bonsai-2-27B abliterated): started once per run and kept for critique and
    synthesis, killed by _cleanup."""
    if token in _leaders and _leaders[token][0].alive:
        server, selection = _leaders[token]
        return server.client(settings.chat_timeout_s), selection.model.label
    catalog, rank, available = await asyncio.to_thread(_catalog, settings)
    failed: list[str] = []
    errors = []
    for _ in range(2):
        selection = select_model(task, catalog, rank, available, _free_mb(config), leader_resident=False,
                                 reserve_mb=settings.reserve_mb, exclude=tuple(failed))
        server = _server(config, settings, selection, await asyncio.to_thread(free_port, settings.base_port + PORT_LEADER))
        try:
            await server.start(LARGE_BOOT_S)
        except WorkerError as exc:
            await server.stop()
            failed.append(selection.model.id)
            errors.append(str(exc))
            continue
        _leaders[token] = (server, selection)
        log.info("proxy leader %s on port %s (boot %.1fs)", selection.model.id, server.port, server.boot_s)
        return server.client(settings.chat_timeout_s), selection.model.label
    raise WorkerError("代理リーダーを起動できません（次点も失敗）: " + " / ".join(errors))


# --- trace -----------------------------------------------------------------------------------------------------------


def _pages_read(logs: list[dict]) -> int:
    return len({u for entry in logs if entry.get("kind") == "read" for u in entry.get("opened") or []})


def _trace(state: ChatState, search: dict, hits: list[dict] | None = None, cards: list[dict] | None = None,
           logs: list[dict] | None = None, **extra) -> dict:
    hits = state.get("hits") or [] if hits is None else hits
    cards = state.get("cards") or [] if cards is None else cards
    logs = state.get("logs") or [] if logs is None else logs
    intents = []
    for intent in search.get("intents") or []:
        mine = [h for h in hits if h.get("intent_id") == intent["id"]]
        log_entry = next((entry for entry in logs if entry.get("intent_id") == intent["id"]
                          and entry.get("kind") == "search"), {})
        opened = [u for entry in logs if entry.get("intent_id") == intent["id"] for u in entry.get("opened") or []]
        intents.append({
            "id": intent["id"], "tool": intent["tool"], "q": intent["q"], "why": intent.get("why", ""),
            "round": intent.get("round", 0), "subquestion_id": intent.get("subquestion_id", ""),
            "hits": [{"title": h.get("title", ""), "url": h["url"]} for h in mine][:5],
            "opened": opened,
            "cards": sum(1 for c in cards if c.get("intent_id") == intent["id"]),
            "error": log_entry.get("error"),
            "status": "done" if log_entry else "running",
        })
    started = search.get("started")
    deep = {
        "chat_mode": state.get("mode"), "round": search.get("round", 0), "max_rounds": search.get("max_rounds"),
        "goal": search.get("goal", ""), "subquestions": search.get("subquestions") or [],
        "contradictions": search.get("contradictions") or [], "stop_reason": search.get("stop_reason"),
        "pages_read": _pages_read(logs), "max_pages": search.get("max_pages"),
        "elapsed_s": round(time.time() - started, 1) if started else None,
        "adopted": [{"id": c.get("id", ""), "url": c["url"], "domain": c.get("domain", ""),
                     "round": c.get("round", 0), "subquestion_ids": c.get("subquestion_ids") or [],
                     "verified": bool(c.get("quote_verified")), "stance": c.get("stance", "")}
                    for c in cards if c.get("quote_verified")][:30],
        "rejected": (search.get("rejected") or [])[:30]
        + [{"url": c["url"], "reason": "引用を本文で確認できない（未確認として扱う）", "round": c.get("round", 0)}
           for c in cards if not c.get("quote_verified")][:30],
    }
    return {"mode": search.get("mode"), "roles": search.get("roles") or {}, "width": search.get("width"),
            "intents": intents, **deep, **extra}


# --- ingest / route / chat ----------------------------------------------------------------------------------------


def _mode_info(choice: modes.ModeChoice) -> dict:
    return {"mode": choice.mode, "requested": choice.requested, "reason": choice.reason, "label": choice.label}


_NOTES = {
    (SEARCH, modes.FAST): "検索します（Tor 経由）…",
    (SEARCH, modes.THINK): "意図を分解しています（思考モード）…",
    ("route", modes.FAST): "どう答えるか判断しています…",
    ("route", modes.THINK): "どう答えるか判断しています…",
    (WRITE, modes.FAST): "本文を書いています…",
    (WRITE, modes.THINK): "アウトラインを作っています…",
    (CODE, modes.FAST): "コードを書いています…",
    (CODE, modes.THINK): "コードを書いています（承認後にコンテナで実行します）…",
    (CHAT, modes.THINK): "考えています（思考モード）…",
}


async def ingest(state: ChatState, config: RunnableConfig) -> dict:
    settings = _settings(config)
    conf = _conf(config)
    await asyncio.to_thread(_setup_file_logging, settings.logs_dir)
    progress_id = f"progress-{uuid.uuid4()}"
    artifact = state.get("artifact") or {}
    has_draft = bool(artifact.get("draft"))
    reset = {"progress_id": progress_id, "error": None, "lock_token": None, "search": {}, "code": {},
             "hits": [RESET], "cards": [RESET], "logs": [RESET], "thinking": [RESET]}
    human = _last_human(state)
    if human is None:
        return {**reset, "error": "no input", "messages": [AIMessage(id=progress_id, content="メッセージがありません。")]}
    text, media = _text_of(human)
    decision = route_rules(text, media, has_draft=has_draft, task=conf.get("task"))
    kind = decision.kind
    if kind == CHAT and not decision.explicit and settings.auto_route and sa.ambiguous_question(decision.text):
        kind = "route"
    choice = modes.choose(conf.get("mode"), decision, has_draft=has_draft, draft_status=artifact.get("status", ""))
    info = _mode_info(choice)
    reset.update({"mode": choice.mode, "mode_info": info})
    log.info("chat route=%s reason=%s mode=%s requested=%s", kind, decision.reason, choice.mode, choice.requested)
    if kind == TO_IMAGE_TAB:
        content = (f"{decision.reason}。画面上部の「画像」タブに切り替えて送ってください。"
                   "このタブでは会話、Web 検索、文章、コードを扱います。")
        if has_draft and "画像" in text or has_draft and "絵" in text:
            excerpt = writing.scene_excerpt(artifact["draft"])
            content += f"\n\n画像タブに貼る描写（いまの本文の最後の場面）:\n\n> {excerpt.replace(chr(10), chr(10) + '> ')}"
        return {**reset, "route": {**vars(decision)}, "error": "image_tab",
                "messages": [AIMessage(id=progress_id, content=content, additional_kwargs={"chat_mode": info})]}
    if not decision.text:
        return {**reset, "error": "empty", "messages": [AIMessage(id=progress_id, content="内容を入力してください。")]}
    route_state = {**vars(decision), "kind": kind}
    if kind == WRITE and decision.needs_search and not decision.continuation:
        # A writing task on real facts: think mode searches first; fast mode writes without searching (§7).
        route_state["search_first"] = choice.mode == modes.THINK
        route_state["search_skipped"] = choice.mode == modes.FAST
    note = _NOTES.get((kind, choice.mode), "考えています…")
    if route_state.get("search_first"):
        note = "資料を調べてから書きます。意図を分解しています（思考モード）…"
    return {**reset, "route": route_state,
            "messages": [AIMessage(id=progress_id, content=note, additional_kwargs={"chat_mode": info})]}


def _after_ingest(state: ChatState) -> str:
    if state.get("error"):
        return END
    kind = state["route"]["kind"]
    if kind == WRITE:
        return "plan" if state["route"].get("search_first") else "write_brief"
    return {SEARCH: "plan", "route": "route", CODE: "code_plan"}.get(kind, "chat")


async def route(state: ChatState, config: RunnableConfig) -> dict:
    """Qwen3-1.7B-heretic: chat, search, write or code? Also rewrites a search query and says "deep"."""
    settings = _settings(config)
    lmstudio = _lmstudio(config, settings)
    try:
        token = await _lock(state, config, settings)
    except JobLockBusy as exc:
        return _fail(state, exc, "lock")
    decision, label = None, None
    try:
        async def ask(client, selection):
            return await sa.ask_json(client, [{"role": "system", "content": await _prompt(settings, "system_search_route.txt")},
                                              {"role": "user", "content": state["route"]["text"]}],
                                     sa.RouteDecision, max_tokens=80, temperature=0.0)

        decision, selection = await _run_model(config, settings, "route", PORT_ROUTE, ask, leader_resident=False)
        label = selection.model.label
    except asyncio.CancelledError:
        await asyncio.shield(_cleanup(token, lmstudio))
        raise
    except (SelectionError, WorkerError, OSError) as exc:
        log.info("router model unavailable, plain chat: %s", exc)
    # A failed router falls back to chat; it never sends the message to the image tab (§7).
    kind = ROUTER_KINDS.get(decision.kind, CHAT) if decision else CHAT
    log.info("router %s -> %s query=%s deep=%s", label, kind, bool(decision and decision.query),
             bool(decision and decision.deep))
    route_state = {**state["route"], "kind": kind, "router": label,
                   "router_query": (decision.query if decision and kind == SEARCH else "")}
    update: dict[str, Any] = {}
    info = state.get("mode_info") or {}
    if info.get("requested") == modes.AUTO:
        fields = {k: route_state[k] for k in ("text", "urls", "reason", "needs_search", "long", "continuation")
                  if k in route_state}
        choice = modes.choose(modes.AUTO, Route(kind=kind, **fields),
                              router_deep=bool(decision and decision.deep))
        info = _mode_info(choice)
        update = {"mode": choice.mode, "mode_info": info}
    mode = update.get("mode", state.get("mode"))
    view = {**state, **update}
    return {"lock_token": token, "route": route_state, **update,
            "messages": [_progress(view, _NOTES.get((kind, mode), "考えています…"))]}


def _after_route(state: ChatState) -> str:
    if state.get("error"):
        return END
    return {SEARCH: "plan", WRITE: "write_brief", CODE: "code_plan"}.get(state["route"]["kind"], "chat")


async def chat(state: ChatState, config: RunnableConfig) -> dict:
    settings = _settings(config)
    lmstudio = _lmstudio(config, settings)
    token = state.get("lock_token")
    try:
        token = await _lock(state, config, settings)
        messages = [{"role": "system", "content": await _prompt(settings, "system_chat.txt")},
                    *_history(state, settings.history_turns)]
        async with _held(token):
            reply, thoughts = await _ask(state, settings, lmstudio, messages, base=1536, answer_min=512,
                                         temperature=0.6, stage="回答")
        text = reply.content or "（空の応答でした）"
        log.info("chat answered mode=%s", state.get("mode"))
    except asyncio.CancelledError:
        await asyncio.shield(_cleanup(token, lmstudio, unload=True))
        raise
    except JobLockBusy as exc:
        return _fail(state, exc, "lock")
    except (LLMError, OSError) as exc:
        await _cleanup(token, lmstudio, unload=True)
        return _fail(state, f"LM Studio に接続できないか、時間切れです（{exc}）", "chat")
    # Plain chat keeps the 27B loaded (LM Studio's JIT TTL unloads it); the image workflow ejects it anyway.
    await _cleanup(token)
    return {"messages": [_final(state, text, thoughts=thoughts)], "lock_token": None}


# --- plan ---------------------------------------------------------------------------------------------------------


def _question(state: ChatState) -> str:
    return state["route"]["text"]


async def plan(state: ChatState, config: RunnableConfig) -> dict:
    settings = _settings(config)
    lmstudio = _lmstudio(config, settings)
    question = _question(state)
    urls = state["route"].get("urls") or []
    token = state.get("lock_token")
    think = _is_think(state)
    stage = "lock"
    search: dict[str, Any] = {
        "question": question, "started": time.time(), "round": 0,
        "max_rounds": settings.search_max_rounds if think else 1,
        "max_pages": settings.search_max_pages, "max_wall_clock_s": settings.search_wall_clock_s,
        "pages_read": 0, "wall_clock_s": 0.0, "new_cards_last_round": 0, "stop_reason": None,
        "goal": "", "subquestions": [], "covered": [], "open": [], "contradictions": [], "rejected": [],
        "critique": settings.search_critique,
        "roles": {"router": state["route"].get("router")}}
    try:
        token = await _lock(state, config, settings)
        stage = "search"
        log.info("tor %s", await _ensure_tor(config, settings))
        if not settings.llama_server or not await asyncio.to_thread(Path(settings.llama_server).is_file):
            raise StageError("search", "PrismML 版 llama.cpp がありません。scripts\\setup-llamacpp.ps1 を実行してください")
        catalog, rank, available = await asyncio.to_thread(_catalog, settings)
        if not available:
            raise StageError("search", "検索用モデルがありません。scripts\\setup-search-models.ps1 を実行してください")

        stage = "plan"
        prompt = "system_search_plan_deep.txt" if think else "system_search_plan.txt"
        schema = sa.DeepPlan if think else sa.Plan
        planner_messages = [{"role": "system", "content": await _prompt(settings, prompt)},
                            {"role": "user", "content": question}]
        use_lmstudio = settings.search_planner == "lmstudio" and await lmstudio.reachable()
        parsed, planner = None, None
        if use_lmstudio:
            # The user's requirement: the Qwen3.8 27B does the first step (the plan) ...
            async with _held(token):
                parsed = await sa.ask_json(lmstudio, planner_messages, schema,
                                           max_tokens=capped(settings, lmstudio, 700 if think else 400))
            planner = LEADER_LABEL
        if parsed is None:
            try:
                client, planner = await _leader(config, settings, token, "plan")
                parsed = await sa.ask_json(client, planner_messages, schema, max_tokens=700 if think else 400)
            except (WorkerError, SelectionError) as exc:
                log.warning("local planner unavailable: %s", exc)
        router_query = state["route"].get("router_query", "")
        if think:
            goal, subs, intents, fallback = sa.deep_plan(parsed, question, urls, settings.fanout_width, router_query)
            search.update({"goal": goal, "subquestions": subs, "open": [s["id"] for s in subs]})
        else:
            # Fast: one intent, one round, no critic (§3.4).
            intents, fallback = sa.plan_intents(parsed, question, urls, 1, router_query)
        if fallback:
            planner = f"{planner or '計画モデルなし'} → 規則で 1 本"
        for intent in intents:
            intent["round"] = 0

        # ... and is unloaded when the readers do not fit next to it (measured: 0.4 GB free with the IQ3_M 27B).
        stage = "worker"
        resident = use_lmstudio and bool(await lmstudio.loaded())
        mode = "resident"
        if resident:
            try:
                fit = select_model("worker", catalog, rank, available, _free_mb(config), leader_resident=True,
                                   reserve_mb=settings.reserve_mb, max_width=len(intents),
                                   override=settings.model_override)
                if fit.width < min(len(intents), settings.fanout_width):
                    raise SelectionError("幅が足りません")
            except SelectionError as exc:
                log.info("readers do not fit next to the LM Studio 27B (%s): unloading it", exc)
                await lmstudio.unload_all()
                mode = "proxy"
        else:
            mode = "proxy"
        search.update({"intents": intents, "pending": [i["id"] for i in intents], "mode": mode,
                       "roles": {**search["roles"], "planner": planner,
                                 "leader": LEADER_LABEL if mode == "resident" else
                                 "Ternary-Bonsai-2-27B abliterated（代理、llama.cpp）"}})
        _ledgers[token] = Ledger()
        # Query and intent texts are not logged (design doc §5.10); result URLs are, in search_client.
        log.info("search plan mode=%s chat_mode=%s planner=%s intents=%s subquestions=%d", mode, state.get("mode"),
                 planner, [i["tool"] for i in intents], len(search["subquestions"]))
    except asyncio.CancelledError:
        await asyncio.shield(_cleanup(token, lmstudio, unload=True))
        raise
    except JobLockBusy as exc:
        return _fail(state, exc, "lock")
    except (StageError, SelectionError, TorUnavailable, SearchError, LLMError, WorkerError, OSError, ValueError) as exc:
        await _cleanup(token, lmstudio, unload=True)
        return _fail(state, exc, getattr(exc, "stage", stage))
    lines = "\n".join(f"- {i['tool']}: `{i['q']}`（{i.get('why') or '―'}）" for i in intents)
    leader_note = ("27B は載せたまま統合します" if mode == "resident" else
                   "27B は unload し、批評と統合は Ternary-Bonsai-2-27B abliterated が代理で行います")
    if think:
        subs = "\n".join(f"- {s['id']}: {s['question']}" for s in search["subquestions"])
        text = (f"目的: {search['goal']}\n\n下位問い:\n{subs}\n\n第 1 ラウンドを検索しています（計画: {planner}）。\n"
                f"{lines}\n\n{leader_note}…")
    else:
        text = f"検索意図を {len(intents)} 本に分けました（計画: {planner}）。\n{lines}\n\nTor 経由で検索しています。{leader_note}…"
    return {"lock_token": token, "search": search,
            "messages": [_progress(state, text, _trace({**state, "hits": [], "cards": [], "logs": []}, search, [], [], []))]}


def _to_search(state: ChatState):
    if state.get("error"):
        return END
    search = state["search"]
    pending = [i for i in search["intents"] if i["id"] in search["pending"]]
    if not pending:
        return "synthesize"
    return [Send("search", {"intent": i, "lock_token": state["lock_token"]}) for i in pending]


# --- search / filter / read ----------------------------------------------------------------------------------------


async def search(payload: dict, config: RunnableConfig) -> dict:
    """One intent: plain Python through Tor (no model). Never raises."""
    settings = _settings(config)
    intent = payload["intent"]
    job_lock.renew(payload.get("lock_token"))
    client = _search_client(config, settings, f"{(payload.get('lock_token') or '')[:8]}-s{intent['id']}")
    entry: dict = {"intent_id": intent["id"], "kind": "search", "error": None}
    hits: list[dict] = []
    try:
        if intent["tool"] == "browse":
            client.allow(intent["q"])
            hits = [{"intent_id": intent["id"], "title": intent["q"], "url": intent["q"], "snippet": ""}]
        else:
            async with asyncio.timeout(settings.search_timeout_s * 3):
                result = await client.search(intent["q"], intent["tool"], fetch_pages=0)
            hits = [{"intent_id": intent["id"], "title": h.title, "url": h.url, "snippet": h.snippet}
                    for h in result.hits]
            entry.update({"provider": result.provider, "error": None if hits else (result.error or "0 件")})
    except asyncio.CancelledError:
        raise
    except (TimeoutError, SearchError, OSError) as exc:
        entry["error"] = f"検索に失敗しました: {exc!r}"[:300]
    entry["count"] = len(hits)
    log.info("search intent=%s tool=%s hits=%d error=%s", intent["id"], intent["tool"], len(hits), entry["error"])
    return {"hits": hits, "logs": [entry]}


async def filter_hits(state: ChatState, config: RunnableConfig) -> dict:
    """Bonsai-4B drops irrelevant results; then choose the reader model and how many readers run."""
    settings = _settings(config)
    lmstudio = _lmstudio(config, settings)
    token = state.get("lock_token")
    job_lock.renew(token)
    search = dict(state["search"])
    pending = set(search["pending"])
    # A URL found in an earlier round is never read again (§3.2).
    seen: set[str] = {h["url"] for h in state.get("hits") or [] if h["intent_id"] not in pending}
    hits, rejected = [], list(search.get("rejected") or [])
    for h in state.get("hits") or []:
        if h["intent_id"] not in pending:
            continue
        if h["url"] in seen:
            rejected.append({"url": h["url"], "title": h.get("title", ""), "reason": "既に読んだか重複した URL",
                             "round": search["round"]})
            continue
        seen.add(h["url"])
        hits.append(h)
    search["round_hits"] = len(hits)
    roles = dict(search.get("roles") or {})
    try:
        if settings.search_filter and len(hits) > 2 and any(i["tool"] != "browse" for i in search["intents"]):
            async def judge(client, selection):
                return await sa.ask_json(client, [
                    {"role": "system", "content": await _prompt(settings, "system_search_filter.txt")},
                    {"role": "user", "content": sa.filter_input(search.get("goal") or search["question"], hits)}],
                    sa.Relevance, max_tokens=80, temperature=0.0)

            try:
                relevance, selection = await _run_model(config, settings, "filter", PORT_FILTER, judge,
                                                        leader_resident=search["mode"] == "resident")
                before = hits
                hits = sa.apply_filter(hits, relevance)
                kept = {h["url"] for h in hits}
                rejected += [{"url": h["url"], "title": h.get("title", ""), "reason": "フィルタで関連が低いと判定",
                              "round": search["round"]} for h in before if h["url"] not in kept]
                roles["filter"] = selection.model.label
                log.info("filter %s kept %d/%d", selection.model.id, len(hits), len(before))
            except (SelectionError, WorkerError) as exc:
                log.info("filter skipped: %s", exc)
        groups: dict[int, list[dict]] = {}
        for h in hits:
            groups.setdefault(h["intent_id"], []).append(h)
        jobs = [{"intent": i, "hits": groups[i["id"]][:settings.hits_per_intent]}
                for i in search["intents"] if i["id"] in groups and i["id"] in pending]
        slots: list[list[dict]] = []
        if jobs:
            catalog, rank, available = await asyncio.to_thread(_catalog, settings)
            leader_up = search["mode"] == "resident" or (token in _leaders)
            selection = select_model("worker", catalog, rank, available, _free_mb(config), leader_resident=leader_up,
                                     reserve_mb=settings.reserve_mb, max_width=min(len(jobs), settings.fanout_width),
                                     override=settings.model_override)
            # Fewer readers than intents: a reader handles several intents one after another. The width never
            # grows with the rounds (§3.4: more rounds, not more parallel readers).
            slots = [jobs[n::selection.width] for n in range(selection.width)]
            search.update({"reader_id": selection.model.id, "reader_path": str(selection.path),
                           "reader_ngl": selection.ngl, "reader_mem": selection.mem_mb, "width": selection.width})
            label = f"{selection.model.label} × {selection.width}"
            # The first round's readers name the role; an extra round is appended.
            roles["reader"] = label if search["round"] == 0 else f"{roles.get('reader') or ''}、追加 × {selection.width}"
        search["slots"] = slots
        search["roles"] = roles
        search["rejected"] = rejected[-60:]
    except asyncio.CancelledError:
        await asyncio.shield(_cleanup(token, lmstudio, unload=True))
        raise
    except (SelectionError, OSError) as exc:
        await _cleanup(token, lmstudio, unload=True)
        return _fail(state, exc, "worker", _trace(state, search))
    readers = len(search["slots"])
    prefix = f"第 {search['round'] + 1} ラウンド: " if _is_think(state) else ""
    text = (f"{prefix}検索結果 {len(hits)} 件のページを {readers} 体の reader（{roles.get('reader', '')}）が読んでいます…"
            if readers else f"{prefix}検索結果がありませんでした。")
    return {"search": search, "messages": [_progress(state, text, _trace(state, search))]}


def _after_reading(state: ChatState) -> str:
    """Think mode scores the round (critique); fast mode, or SEARCH_CRITIQUE=0, skips the critic (§3.3)."""
    if state.get("error"):
        return END
    return "judge" if _is_think(state) and _settings_critique(state) else "synthesize"


def _settings_critique(state: ChatState) -> bool:
    return bool(state["search"].get("critique", True))


def _to_read(state: ChatState):
    if state.get("error"):
        return END
    slots = state["search"].get("slots") or []
    if not slots:
        return _after_reading(state)
    return [Send("read", {"slot": n, "jobs": jobs, "search": state["search"], "lock_token": state["lock_token"]})
            for n, jobs in enumerate(slots)]


def _card_fields(cards: list[dict], intent: dict) -> list[dict]:
    """Card id ("<intent>.<n>"), round, sub-question, stance and quote check (§3.1)."""
    out = []
    for n, card in enumerate(cards, start=1):
        sub = intent.get("subquestion_id")
        out.append({**card, "intent_id": intent["id"], "id": f"{intent['id']}.{n}", "round": intent.get("round", 0),
                    "subquestion_ids": [sub] if sub else [],
                    "stance": "contradicts" if card.get("conflicts") else ("background" if card.get("fallback")
                                                                           else "supports"),
                    "quote_verified": any(c.get("quote_ok") for c in card["claims"])})
    return out


async def read(payload: dict, config: RunnableConfig) -> dict:
    """One reader process for one or more intents. Never raises; the process is always killed."""
    settings = _settings(config)
    search, token = payload["search"], payload.get("lock_token")
    job_lock.renew(token)
    catalog = await asyncio.to_thread(Catalog.load, settings.catalog_path)
    selection = Selection("worker", catalog.models[search["reader_id"]], 1, search["reader_mem"],
                          Path(search["reader_path"]), search["reader_ngl"])
    server = _server(config, settings, selection, settings.base_port + payload["slot"])
    ledger = _ledgers.setdefault(token or "", Ledger())
    client = _search_client(config, settings, f"{(token or '')[:8]}-r{payload['slot']}")
    cards: list[dict] = []
    logs: list[dict] = []
    started = time.monotonic()
    question = search.get("goal") or search["question"]
    try:
        # The budget covers every intent this reader handles; opening pages may use 60 % of each share so the
        # card extraction always gets its turn.
        jobs_n = len(payload["jobs"])
        async with asyncio.timeout(settings.search_total_timeout_s * jobs_n):
            await server.start(LARGE_BOOT_S if selection.model.large else settings.worker_timeout_s)
            llm = server.client(settings.worker_timeout_s)

            async def ask_cards(llm_client, messages):
                return await sa.ask_json(llm_client, messages, sa.Cards, max_tokens=700, temperature=0.1)

            for job in payload["jobs"]:
                intent, hits = job["intent"], job["hits"]
                for h in hits:
                    client.allow(h["url"])
                entry = {"intent_id": intent["id"], "kind": "read", "opened": [], "error": None}
                try:
                    out = await run_reader(intent, hits, llm, client, ledger,
                                           await _prompt(settings, "system_bonsai_worker.txt"), question, ask_cards,
                                           browse_budget_s=settings.search_total_timeout_s * 0.6)
                    allowed = {h["url"] for h in hits}
                    found = sa.card_dicts(out["cards"], allowed) or sa.snippet_cards(hits)
                    sources = {h["url"]: f"{h.get('title', '')} {h.get('snippet', '')}" for h in hits}
                    for page in out["pages"]:
                        sources[page["url"]] = sources.get(page["url"], "") + " " + page["title"] + " " + page["text"]
                    cards += _card_fields(sa.verify_cards(found, sources), intent)
                    entry.update({"opened": out["opened"], "tool_call": out["tool_call"]})
                except (LLMError, SearchError) as exc:
                    entry["error"] = str(exc)[:300]
                    cards += _card_fields(sa.verify_cards(sa.snippet_cards(hits), {}), intent)
                logs.append(entry)
    except asyncio.CancelledError:
        # The run was stopped from the UI: every branch is cancelled, so free everything here.
        await asyncio.shield(_cleanup(token, _lmstudio(config, settings), unload=True))
        raise
    except (TimeoutError, WorkerError, LLMError, OSError) as exc:
        logs.append({"intent_id": payload["jobs"][0]["intent"]["id"], "kind": "read", "error": str(exc)[:300] or "時間切れ"})
        for job in payload["jobs"]:
            if not any(c["intent_id"] == job["intent"]["id"] for c in cards):
                cards += _card_fields(sa.verify_cards(sa.snippet_cards(job["hits"]), {}), job["intent"])
    finally:
        await asyncio.shield(server.stop())
    log.info("reader slot=%s model=%s cards=%d seconds=%.1f", payload["slot"], selection.model.id, len(cards),
             time.monotonic() - started)
    return {"cards": cards, "logs": logs}


# --- judge / critique / synthesize -----------------------------------------------------------------------------------


async def _leader_client(config, settings: ChatSettings, state: ChatState, task: str):
    if state["search"]["mode"] == "resident":
        return _lmstudio(config, settings), LEADER_LABEL
    return await _leader(config, settings, state["lock_token"], task)


async def judge(state: ChatState, config: RunnableConfig) -> dict:
    """Think mode: show that the critic is working (a node's text appears when the node ends)."""
    search = state["search"]
    return {"messages": [_progress(state, f"第 {search['round'] + 1} ラウンドの結果から、足りない点を判定しています…",
                                   _trace(state, search))]}


async def critique(state: ChatState, config: RunnableConfig) -> dict:
    """Think mode: score the sub-questions and decide on another round (§3.3). It never writes the answer."""
    settings = _settings(config)
    lmstudio = _lmstudio(config, settings)
    token = state.get("lock_token")
    job_lock.renew(token)
    search = dict(state["search"])
    cards = state.get("cards") or []
    logs = state.get("logs") or []
    roles = dict(search.get("roles") or {})
    current = search["round"]
    new_cards = [c for c in cards if c.get("round", 0) == current]
    search.update({"round": current + 1, "new_cards_last_round": len(new_cards), "pages_read": _pages_read(logs),
                   "wall_clock_s": round(time.time() - search["started"], 1)})
    reflect = None
    if search.get("round_hits", 0) > 0 and cards:
        try:
            client, label = await _leader_client(config, settings, state, "critique")
            refs = sa.references(cards, state.get("hits") or [], limit=20)
            async with _held(token):
                reflect = await sa.ask_json(client, [
                    {"role": "system", "content": await _prompt(settings, "system_search_critique.txt")},
                    {"role": "user", "content": sa.reflect_input(search, cards, refs)}],
                    sa.Reflect, max_tokens=capped(settings, client, 900), temperature=0.2)
            roles["critic"] = label
        except asyncio.CancelledError:
            await asyncio.shield(_cleanup(token, lmstudio, unload=True))
            raise
        except (WorkerError, SelectionError, LLMError) as exc:
            log.info("critique failed: %s", exc)
    next_id = max(i["id"] for i in search["intents"]) + 1
    search.update(sa.apply_reflect(search, reflect, cards, next_id, settings.fanout_width))
    if reflect is None and search.get("round_hits", 0) > 0 and cards:
        search["critic_reason"] = "error"
    go, reason = sa.next_round(search)
    rounds = list(search.get("rounds") or [])
    rounds.append({"round": current + 1, "intents": [i["id"] for i in search["intents"] if i.get("round") == current],
                   "hits": search.get("round_hits", 0), "new_cards": len(new_cards),
                   "pages_read": search["pages_read"], "decision": "search" if go else reason})
    search["rounds"] = rounds
    search["roles"] = roles
    if go:
        added = [{**i, "round": current + 1} for i in search["next_intents"]]
        search.update({"intents": [*search["intents"], *added], "pending": [i["id"] for i in added],
                       "stop_reason": None})
        text = (f"第 {current + 2} ラウンドを検索しています（未回答の下位問い {len(search['open'])} 件）:\n"
                + "\n".join(f"- {i['tool']}: `{i['q']}`（{i.get('subquestion_id')}）" for i in added))
    else:
        search.update({"pending": [], "stop_reason": reason})
        text = f"{sa.STOP_LABELS.get(reason, reason)}ため検索を終え、{roles.get('leader', '')} が回答をまとめています…"
    log.info("critique round=%d new_cards=%d pages=%d open=%d -> %s", search["round"], len(new_cards),
             search["pages_read"], len(search["open"]), "search" if go else reason)
    return {"search": search, "messages": [_progress(state, text, _trace(state, search))]}


def _after_critique(state: ChatState):
    if state.get("error"):
        return END
    if state["search"].get("pending"):
        return _to_search(state)
    return "synthesize"


def _research_text(answer: str, refs: list[dict]) -> str:
    return answer + ("\n\n出典:\n" + "\n".join(f"[{r['n']}] {r['title']} {r['url']}" for r in refs) if refs else "")


async def synthesize(state: ChatState, config: RunnableConfig) -> dict:
    settings = _settings(config)
    lmstudio = _lmstudio(config, settings)
    search = dict(state["search"])
    token = state.get("lock_token")
    job_lock.renew(token)
    cards = state.get("cards") or []
    hits = state.get("hits") or []
    logs = state.get("logs") or []
    refs = sa.references(cards, hits)
    roles = dict(search.get("roles") or {})
    think = _is_think(state)
    search["pages_read"] = _pages_read(logs)
    if not search.get("stop_reason"):
        # Fast mode: one round by design; think mode reaches here through the critic, which set the reason.
        search["stop_reason"] = "no_hits" if not hits else "budget"
    thoughts: list[dict] = []
    then_write = bool(state["route"].get("search_first"))
    try:
        if not refs:
            # Nothing came back: do not wake up a leader (design doc §5.4 step 6).
            errors = "; ".join(f"{e['error']}" for e in logs if e.get("error"))
            text = "検索結果がありません。Tor 出口が拒否された可能性があります。"
            if errors:
                text += f"\n\n（{errors[:400]}）"
            search["stop_reason"] = "no_hits"
            await _cleanup(token, lmstudio, unload=True)
            if then_write:
                artifact = {**(state.get("artifact") or {}), "research": ""}
                return {"lock_token": None, "search": search, "artifact": artifact,
                        "messages": [_progress(state, text + "\n\n資料なしで書きます…", _trace(state, search))]}
            return {"lock_token": None, "messages": [_progress(state, text, _trace(state, search))]}
        client, label = await _leader_client(config, settings, state, "synthesize")
        # Thinking tokens only on the LM Studio 27B (the proxy leader runs with --reasoning off, 8192 context).
        resident = search["mode"] == "resident"
        async with _held(token):
            reply, thoughts = await _ask(
                state, settings, client,
                [{"role": "system", "content": await _prompt(settings, "system_search.txt")},
                 {"role": "user", "content": sa.leader_input(search["question"], cards, refs, search if think else None)}],
                base=1600 if think else 1200, answer_min=800, temperature=0.4, stage="統合", thinking=resident,
                context=None if resident else LEADER_CTX)
        answer = reply.content or "（統合モデルの応答が空でした）"
        roles["synthesizer"] = label
    except asyncio.CancelledError:
        await asyncio.shield(_cleanup(token, lmstudio, unload=True))
        raise
    except (LLMError, WorkerError, SelectionError, OSError) as exc:
        await _cleanup(token, lmstudio, unload=True)
        return _fail(state, exc, "chat", _trace(state, search))
    # Every llama-server is killed and the 27B unloaded so the image tab gets the memory back (design doc §5.8).
    await _cleanup(token, lmstudio, unload=True)
    search["roles"] = roles
    seconds = round(time.time() - search.get("started", time.time()), 1)
    search["wall_clock_s"] = seconds
    confirmed = sum(1 for c in cards for cl in c["claims"] if cl.get("quote_ok"))
    trace = _trace(state, search, seconds=seconds, cards_total=len(cards), claims_confirmed=confirmed)
    log.info("search answered leader=%s refs=%d rounds=%s stop=%s seconds=%.1f", label, len(refs),
             search.get("round"), search.get("stop_reason"), seconds)
    if then_write:
        artifact = {**(state.get("artifact") or {}), "research": _research_text(answer, refs)}
        return {"lock_token": None, "search": search, "artifact": artifact, "thinking": thoughts,
                "messages": [_progress(state, "調べた内容をもとにアウトラインを作っています…", trace)]}
    content = sa.format_answer(answer, refs, search if think else None, cards)
    return {"lock_token": None, "search": search,
            "messages": [_final(state, content, trace, thoughts=thoughts)]}


def _after_synthesize(state: ChatState) -> str:
    if state.get("error") or not state["route"].get("search_first"):
        return END
    return "write_brief"


builder = StateGraph(ChatState)
builder.add_node("ingest", ingest)
builder.add_node("route", route)
builder.add_node("chat", chat)
builder.add_node("plan", plan)
builder.add_node("search", search)
builder.add_node("filter", filter_hits)
builder.add_node("read", read)
builder.add_node("judge", judge)
builder.add_node("critique", critique)
builder.add_node("synthesize", synthesize)
write_nodes.add_nodes(builder)
code_nodes.add_nodes(builder)
builder.add_edge(START, "ingest")
builder.add_conditional_edges("ingest", _after_ingest, ["chat", "route", "plan", "write_brief", "code_plan", END])
builder.add_conditional_edges("route", _after_route, ["chat", "plan", "write_brief", "code_plan", END])
builder.add_edge("chat", END)
builder.add_conditional_edges("plan", _to_search, ["search", "synthesize", END])
builder.add_edge("search", "filter")
builder.add_conditional_edges("filter", _to_read, ["read", "judge", "synthesize", END])
builder.add_conditional_edges("read", _after_reading, ["judge", "synthesize", END])
builder.add_edge("judge", "critique")
builder.add_conditional_edges("critique", _after_critique, ["search", "synthesize", END])
builder.add_conditional_edges("synthesize", _after_synthesize, ["write_brief", END])

graph = builder.compile()
graph.name = "chat agent"
