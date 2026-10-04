"""LangGraph chat tab (graph id ``chat``): conversation, and a Grok-style web search through Tor.

    ingest      last human message -> chat / search / "use the image tab" (router.route, keyword rules)
    route       only for questions the rules sent to chat: Qwen3-1.7B-heretic decides search or not and
                rewrites the query
    chat        the LM Studio Qwen3.8 27B answers directly
    plan        the LM Studio Qwen3.8 27B writes 1-3 search intents (web / news / browse). Then it is unloaded
                when the readers do not fit next to it, and the Ternary-Bonsai-2-27B abliterated (PTQ1_0,
                PrismML llama-server) becomes the leader for critique and synthesis ("proxy" mode)
    search      one per intent (Send): plain Python, DuckDuckGo through Tor, no model
    filter      Bonsai-4B keeps the relevant results; the reader model and the number of readers are chosen
    read        one per reader (Send): a Ternary-Bonsai-8B llama-server opens pages with a tool call and
                extracts fact cards per URL; quotes are checked against the fetched text; the process is killed
    critique    the leader lists missing intents; at most one more search round (back to search)
    synthesize  the leader writes one answer from the cards with [n] citations; every llama-server is gone,
                LM Studio is unloaded, the shared job lock is released

The image graph (graph.py) is not changed by this module; both share job_lock so they never run together.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated, Any

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.types import Send

from furry_agent import bonsai_worker, search_agent as sa
from furry_agent.bonsai_select import (Catalog, Rank, Selection, SelectionError, available_models, free_memory_mb,
                                       select_model)
from furry_agent.bonsai_worker import Ledger, LlamaServer, WorkerError, free_port, run_reader
from furry_agent.comfy_client import ComfyClient
from furry_agent.config import ChatSettings
from furry_agent.graph import _setup_file_logging  # the same logs/furry_agent.log as the image tab
from furry_agent.job_lock import JobLockBusy, job_lock
from furry_agent.llm_client import LLMError, LMStudio, OpenAICompatClient, strip_thinking
from furry_agent.router import CHAT, SEARCH, TO_IMAGE_TAB, route as route_rules
from furry_agent.search_client import SearchError, TorSearchClient
from furry_agent.tor_service import TorUnavailable, ensure_tor

log = logging.getLogger("furry_agent.chat")

LEADER_LABEL = "Qwen3.8 27B abliterated（LM Studio）"
RESET = "__reset__"
HITS_PER_INTENT = 4
PORT_ROUTE, PORT_FILTER, PORT_LEADER = 7, 8, 9  # offsets from BONSAI_BASE_PORT; readers use 0..2
LARGE_BOOT_S = 300.0
_ledgers: dict[str, Ledger] = {}
_leaders: dict[str, tuple[LlamaServer, Selection]] = {}


def _append(old: list | None, new: list | None) -> list:
    """Parallel nodes append; [RESET] (from ingest) clears the run's data."""
    if new and new[0] == RESET:
        return []
    return [*(old or []), *(new or [])]


class ChatState(MessagesState):
    progress_id: str
    error: str | None
    route: dict[str, Any]
    lock_token: str | None
    search: dict[str, Any]
    hits: Annotated[list[dict], _append]
    cards: Annotated[list[dict], _append]
    logs: Annotated[list[dict], _append]


class StageError(RuntimeError):
    def __init__(self, stage: str, message: str):
        super().__init__(message)
        self.stage = stage


# --- dependencies (overridable through config["configurable"] for tests) ----------------------------------------


def _conf(config: RunnableConfig | None) -> dict:
    return (config or {}).get("configurable", {})


def _settings(config: RunnableConfig | None) -> ChatSettings:
    return _conf(config).get("chat_settings") or ChatSettings.from_env()


def _lmstudio(config: RunnableConfig | None, settings: ChatSettings) -> LMStudio:
    return _conf(config).get("lmstudio") or LMStudio(settings.lmstudio_url, settings.lmstudio_model,
                                                     settings.chat_timeout_s)


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


def _comfy(config: RunnableConfig | None, settings: ChatSettings) -> ComfyClient | None:
    conf = _conf(config)
    if "comfy_client" in conf:
        return conf["comfy_client"]
    return ComfyClient(settings.comfyui_url, 30)


_prompts: dict[Path, str] = {}


async def _prompt(settings: ChatSettings, name: str) -> str:
    """Prompt file text, read in a thread once (langgraph dev fails runs that block the event loop)."""
    path = Path(settings.prompts_dir) / name
    if path not in _prompts:
        _prompts[path] = (await asyncio.to_thread(path.read_text, encoding="utf-8")).strip()
    return _prompts[path]


def _catalog(settings: ChatSettings) -> tuple[Catalog, Rank, dict[str, Path]]:
    catalog = Catalog.load(settings.catalog_path)
    return catalog, Rank.load(settings.rank_path), available_models(catalog, settings.models_dir)


# --- helpers ---------------------------------------------------------------------------------------------------


def _text_of(message) -> tuple[str, bool]:
    content = message.content
    if isinstance(content, str):
        return content, False
    texts, media = [], False
    for block in content or []:
        if isinstance(block, dict):
            if block.get("type") == "text":
                texts.append(block.get("text") or "")
            elif block.get("type") in ("image", "image_url", "file", "video", "audio", "input_image"):
                media = True
        elif isinstance(block, str):
            texts.append(block)
    return "\n".join(texts).strip(), media


def _last_human(state: ChatState):
    for message in reversed(state["messages"]):
        if isinstance(message, HumanMessage) or getattr(message, "type", None) == "human":
            return message
    return None


def _history(state: ChatState, turns: int) -> list[dict]:
    """Text-only human/ai turns of this thread (progress text and trace data are not sent back)."""
    out = []
    for message in state["messages"]:
        kind = getattr(message, "type", None)
        if kind not in ("human", "ai"):
            continue
        text, _ = _text_of(message)
        if text:
            out.append({"role": "user" if kind == "human" else "assistant", "content": text[:4000]})
    return out[-turns * 2:]


def _progress(state: ChatState, text: str, trace: dict | None = None) -> AIMessage:
    return AIMessage(id=state["progress_id"], content=text,
                     additional_kwargs={"search_trace": trace} if trace else {})


def _fail(state: ChatState, exc: Exception | str, stage: str | None = None, trace: dict | None = None) -> dict:
    stage = getattr(exc, "stage", None) or stage
    log.error("chat failed stage=%s: %s", stage, exc)
    prefix = f"［{stage}］" if stage else ""
    message = AIMessage(id=state.get("progress_id") or f"error-{uuid.uuid4()}",
                        content=f"⚠️ 応答できませんでした{prefix}: {exc}",
                        additional_kwargs={"search_trace": trace} if trace else {})
    return {"messages": [message], "error": str(exc), "lock_token": None}


async def _cleanup(token: str | None, lmstudio: LMStudio | None = None, unload: bool = False) -> None:
    """Kill every llama-server (the proxy leader included), optionally unload LM Studio, release the lock."""
    try:
        leader = _leaders.pop(token, None) if token else None
        if leader is not None:
            await leader[0].stop()
        left = await bonsai_worker.kill_all()
        if left:
            log.info("stopped llama-server pids=%s", left)
        if unload and lmstudio is not None:
            try:
                await lmstudio.unload_all()
            except Exception as exc:  # LM Studio down: nothing is loaded there anyway
                log.warning("LM Studio unload failed: %s", exc)
    finally:
        if bonsai_worker.live_pids():
            log.error("llama-server still alive after cleanup: %s", bonsai_worker.live_pids())
        if token:
            _ledgers.pop(token, None)
            job_lock.release(token)


async def _image_tab_busy(config: RunnableConfig | None, settings: ChatSettings) -> bool:
    comfy = _comfy(config, settings)
    if comfy is None:
        return False
    try:
        return await comfy.queue_busy()
    except Exception:  # ComfyUI not running: the image tab cannot be busy
        return False


async def _lock(state: ChatState, config: RunnableConfig | None, settings: ChatSettings) -> str:
    """The run's job lock (taken once, by the first node that loads a model)."""
    if state.get("lock_token"):
        job_lock.renew(state["lock_token"])
        return state["lock_token"]
    token = await job_lock.acquire("chat", settings.job_lock_timeout_s, settings.job_lock_timeout_s)
    # The image tab releases the lock after queueing; ComfyUI may still be generating (LLM + checkpoint).
    if await _image_tab_busy(config, settings):
        job_lock.release(token)
        raise JobLockBusy("image")
    return token


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
            "round": intent.get("round", 0),
            "hits": [{"title": h.get("title", ""), "url": h["url"]} for h in mine][:5],
            "opened": opened,
            "cards": sum(1 for c in cards if c.get("intent_id") == intent["id"]),
            "error": log_entry.get("error"),
            "status": "done" if log_entry else "running",
        })
    return {"mode": search.get("mode"), "roles": search.get("roles") or {}, "width": search.get("width"),
            "intents": intents, **extra}


# --- ingest / route / chat ----------------------------------------------------------------------------------------


async def ingest(state: ChatState, config: RunnableConfig) -> dict:
    settings = _settings(config)
    await asyncio.to_thread(_setup_file_logging, settings.logs_dir)
    progress_id = f"progress-{uuid.uuid4()}"
    reset = {"progress_id": progress_id, "error": None, "lock_token": None, "search": {},
             "hits": [RESET], "cards": [RESET], "logs": [RESET]}
    human = _last_human(state)
    if human is None:
        return {**reset, "error": "no input", "messages": [AIMessage(id=progress_id, content="メッセージがありません。")]}
    text, media = _text_of(human)
    decision = route_rules(text, media)
    kind = decision.kind
    if kind == CHAT and settings.auto_route and sa.ambiguous_question(decision.text):
        kind = "route"
    log.info("chat route=%s reason=%s", kind, decision.reason)
    if kind == TO_IMAGE_TAB:
        return {**reset, "route": {**vars(decision)}, "error": "image_tab",
                "messages": [AIMessage(id=progress_id, content=(
                    f"{decision.reason}。画面上部の「画像」タブに切り替えて送ってください。"
                    "このタブでは会話と Web 検索だけを行います。"))]}
    if not decision.text:
        return {**reset, "error": "empty", "messages": [AIMessage(id=progress_id, content="内容を入力してください。")]}
    note = {"search": "検索します（Tor 経由）…", "route": "検索が必要か判断しています…"}.get(kind, "考えています…")
    return {**reset, "route": {**vars(decision), "kind": kind}, "messages": [AIMessage(id=progress_id, content=note)]}


def _after_ingest(state: ChatState) -> str:
    if state.get("error"):
        return END
    return {SEARCH: "plan", "route": "route"}.get(state["route"]["kind"], "chat")


async def route(state: ChatState, config: RunnableConfig) -> dict:
    """Qwen3-1.7B-heretic: does this question need the web? Also rewrites it as a query."""
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
    kind = SEARCH if decision and decision.search else CHAT
    log.info("router %s -> %s query=%s", label, kind, bool(decision and decision.query))
    return {"lock_token": token,
            "route": {**state["route"], "kind": kind, "router": label,
                      "router_query": (decision.query if decision else "")},
            "messages": [_progress(state, "検索します（Tor 経由）…" if kind == SEARCH else "考えています…")]}


def _after_route(state: ChatState) -> str:
    if state.get("error"):
        return END
    return "plan" if state["route"]["kind"] == SEARCH else "chat"


async def chat(state: ChatState, config: RunnableConfig) -> dict:
    settings = _settings(config)
    lmstudio = _lmstudio(config, settings)
    token = state.get("lock_token")
    try:
        token = await _lock(state, config, settings)
        messages = [{"role": "system", "content": await _prompt(settings, "system_chat.txt")},
                    *_history(state, settings.history_turns)]
        reply = await lmstudio.chat(messages, max_tokens=1536, temperature=0.6, timeout_s=settings.chat_timeout_s)
        text = strip_thinking(reply.content) or "（空の応答でした）"
        log.info("chat answered in %.1fs", reply.seconds)
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
    return {"messages": [AIMessage(id=state["progress_id"], content=text)], "lock_token": None}


# --- plan ---------------------------------------------------------------------------------------------------------


async def plan(state: ChatState, config: RunnableConfig) -> dict:
    settings = _settings(config)
    lmstudio = _lmstudio(config, settings)
    question = state["route"]["text"]
    urls = state["route"].get("urls") or []
    token = state.get("lock_token")
    stage = "lock"
    search: dict[str, Any] = {"question": question, "started": time.time(), "round": 0,
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
        planner_messages = [{"role": "system", "content": await _prompt(settings, "system_search_plan.txt")},
                            {"role": "user", "content": question}]
        use_lmstudio = settings.search_planner == "lmstudio" and await lmstudio.reachable()
        parsed, planner = None, None
        if use_lmstudio:
            # The user's requirement: the Qwen3.8 27B does the first step (the plan) ...
            parsed = await sa.ask_json(lmstudio, planner_messages, sa.Plan, max_tokens=400)
            planner = LEADER_LABEL
        if parsed is None:
            try:
                client, planner = await _leader(config, settings, token, "plan")
                parsed = await sa.ask_json(client, planner_messages, sa.Plan, max_tokens=400)
            except (WorkerError, SelectionError) as exc:
                log.warning("local planner unavailable: %s", exc)
        intents, fallback = sa.plan_intents(parsed, question, urls, settings.fanout_width,
                                            state["route"].get("router_query", ""))
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
        log.info("search plan mode=%s planner=%s intents=%s", mode, planner, [i["tool"] for i in intents])
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
    return {"lock_token": token, "search": search,
            "messages": [_progress(state, f"検索意図を {len(intents)} 本に分けました（計画: {planner}）。\n{lines}\n\n"
                                          f"Tor 経由で検索しています。{leader_note}…",
                                   _trace({**state, "hits": [], "cards": [], "logs": []}, search, [], [], []))]}


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
    seen: set[str] = {h["url"] for h in state.get("hits") or [] if h["intent_id"] not in pending}
    hits = []
    for h in state.get("hits") or []:
        if h["intent_id"] in pending and h["url"] not in seen:
            seen.add(h["url"])
            hits.append(h)
    roles = dict(search.get("roles") or {})
    try:
        if settings.search_filter and len(hits) > 2 and any(i["tool"] != "browse" for i in search["intents"]):
            async def judge(client, selection):
                return await sa.ask_json(client, [
                    {"role": "system", "content": await _prompt(settings, "system_search_filter.txt")},
                    {"role": "user", "content": sa.filter_input(search["question"], hits)}],
                    sa.Relevance, max_tokens=80, temperature=0.0)

            try:
                relevance, selection = await _run_model(config, settings, "filter", PORT_FILTER, judge,
                                                        leader_resident=search["mode"] == "resident")
                before = len(hits)
                hits = sa.apply_filter(hits, relevance)
                roles["filter"] = selection.model.label
                log.info("filter %s kept %d/%d", selection.model.id, len(hits), before)
            except (SelectionError, WorkerError) as exc:
                log.info("filter skipped: %s", exc)
        groups: dict[int, list[dict]] = {}
        for h in hits:
            groups.setdefault(h["intent_id"], []).append(h)
        jobs = [{"intent": i, "hits": groups[i["id"]][:HITS_PER_INTENT]}
                for i in search["intents"] if i["id"] in groups]
        slots: list[list[dict]] = []
        if jobs:
            catalog, rank, available = await asyncio.to_thread(_catalog, settings)
            leader_up = search["mode"] == "resident" or (token in _leaders)
            selection = select_model("worker", catalog, rank, available, _free_mb(config), leader_resident=leader_up,
                                     reserve_mb=settings.reserve_mb, max_width=min(len(jobs), settings.fanout_width),
                                     override=settings.model_override)
            # Fewer readers than intents: a reader handles several intents one after another.
            slots = [jobs[n::selection.width] for n in range(selection.width)]
            search.update({"reader_id": selection.model.id, "reader_path": str(selection.path),
                           "reader_ngl": selection.ngl, "reader_mem": selection.mem_mb, "width": selection.width})
            label = f"{selection.model.label} × {selection.width}"
            # The first round's readers name the role; an extra round is appended.
            roles["reader"] = label if search["round"] == 0 else f"{roles.get('reader') or ''}、追加 × {selection.width}"
        search["slots"] = slots
        search["roles"] = roles
    except asyncio.CancelledError:
        await asyncio.shield(_cleanup(token, lmstudio, unload=True))
        raise
    except (SelectionError, OSError) as exc:
        await _cleanup(token, lmstudio, unload=True)
        return _fail(state, exc, "worker", _trace(state, search))
    readers = len(search["slots"])
    text = (f"検索結果 {len(hits)} 件を {readers} 体の reader（{roles.get('reader', '')}）が読んでいます…"
            if readers else "検索結果がありませんでした。")
    return {"search": search, "messages": [_progress(state, text, _trace(state, search))]}


def _to_read(state: ChatState):
    if state.get("error"):
        return END
    slots = state["search"].get("slots") or []
    if not slots:
        return "critique"
    return [Send("read", {"slot": n, "jobs": jobs, "search": state["search"], "lock_token": state["lock_token"]})
            for n, jobs in enumerate(slots)]


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
                                           await _prompt(settings, "system_bonsai_worker.txt"), search["question"], ask_cards,
                                           browse_budget_s=settings.search_total_timeout_s * 0.6)
                    allowed = {h["url"] for h in hits}
                    found = sa.card_dicts(out["cards"], allowed) or sa.snippet_cards(hits)
                    sources = {h["url"]: f"{h.get('title', '')} {h.get('snippet', '')}" for h in hits}
                    for page in out["pages"]:
                        sources[page["url"]] = sources.get(page["url"], "") + " " + page["title"] + " " + page["text"]
                    for card in sa.verify_cards(found, sources):
                        cards.append({**card, "intent_id": intent["id"]})
                    entry.update({"opened": out["opened"], "tool_call": out["tool_call"]})
                except (LLMError, SearchError) as exc:
                    entry["error"] = str(exc)[:300]
                    cards += [{**c, "intent_id": intent["id"]} for c in sa.verify_cards(sa.snippet_cards(hits), {})]
                logs.append(entry)
    except asyncio.CancelledError:
        # The run was stopped from the UI: every branch is cancelled, so free everything here.
        await asyncio.shield(_cleanup(token, _lmstudio(config, settings), unload=True))
        raise
    except (TimeoutError, WorkerError, LLMError, OSError) as exc:
        logs.append({"intent_id": payload["jobs"][0]["intent"]["id"], "kind": "read", "error": str(exc)[:300] or "時間切れ"})
        for job in payload["jobs"]:
            if not any(c["intent_id"] == job["intent"]["id"] for c in cards):
                cards += [{**c, "intent_id": job["intent"]["id"]} for c in sa.verify_cards(sa.snippet_cards(job["hits"]), {})]
    finally:
        await asyncio.shield(server.stop())
    log.info("reader slot=%s model=%s cards=%d seconds=%.1f", payload["slot"], selection.model.id, len(cards),
             time.monotonic() - started)
    return {"cards": cards, "logs": logs}


# --- critique / synthesize -----------------------------------------------------------------------------------------


async def _leader_client(config, settings: ChatSettings, state: ChatState, task: str):
    if state["search"]["mode"] == "resident":
        return _lmstudio(config, settings), LEADER_LABEL
    return await _leader(config, settings, state["lock_token"], task)


async def critique(state: ChatState, config: RunnableConfig) -> dict:
    """The leader lists what is missing; one more search round at most. It never writes the answer."""
    settings = _settings(config)
    lmstudio = _lmstudio(config, settings)
    token = state.get("lock_token")
    job_lock.renew(token)
    search = dict(state["search"])
    cards = state.get("cards") or []
    gaps: list[dict] = []
    roles = dict(search.get("roles") or {})
    if settings.search_critique and search["round"] == 0 and cards:
        try:
            client, label = await _leader_client(config, settings, state, "critique")
            refs = sa.references(cards, state.get("hits") or [])
            result = await sa.ask_json(client, [
                {"role": "system", "content": await _prompt(settings, "system_search_critique.txt")},
                {"role": "user", "content": sa.critique_input(search["question"], search["intents"], cards, refs)}],
                sa.Critique, max_tokens=300, temperature=0.2)
            next_id = max(i["id"] for i in search["intents"]) + 1
            gaps = sa.gap_intents(result, search["intents"], next_id, settings.fanout_width)
            roles["critic"] = label
        except asyncio.CancelledError:
            await asyncio.shield(_cleanup(token, lmstudio, unload=True))
            raise
        except (WorkerError, SelectionError, LLMError) as exc:
            log.info("critique skipped: %s", exc)
    for gap in gaps:
        gap["round"] = 1
    search.update({"roles": roles, "round": search["round"] + (1 if gaps else 0),
                   "intents": [*search["intents"], *gaps], "pending": [g["id"] for g in gaps]})
    log.info("critique gaps=%s", [g["tool"] for g in gaps])
    if gaps:
        text = "足りない点を追加で検索しています（1 回だけ）:\n" + "\n".join(f"- {g['tool']}: `{g['q']}`" for g in gaps)
    else:
        text = f"{roles.get('leader', '')} が回答をまとめています…"
    return {"search": search, "messages": [_progress(state, text, _trace(state, search))]}


def _after_critique(state: ChatState):
    if state.get("error"):
        return END
    if state["search"].get("pending"):
        return _to_search(state)
    return "synthesize"


async def synthesize(state: ChatState, config: RunnableConfig) -> dict:
    settings = _settings(config)
    lmstudio = _lmstudio(config, settings)
    search = dict(state["search"])
    token = state.get("lock_token")
    job_lock.renew(token)
    cards = state.get("cards") or []
    hits = state.get("hits") or []
    refs = sa.references(cards, hits)
    roles = dict(search.get("roles") or {})
    try:
        if not refs:
            # Nothing came back: do not wake up a leader (design doc §5.4 step 6).
            errors = "; ".join(f"{e['error']}" for e in state.get("logs") or [] if e.get("error"))
            text = "検索結果がありません。Tor 出口が拒否された可能性があります。"
            if errors:
                text += f"\n\n（{errors[:400]}）"
            await _cleanup(token, lmstudio, unload=True)
            return {"lock_token": None, "messages": [_progress(state, text, _trace(state, search))]}
        client, label = await _leader_client(config, settings, state, "synthesize")
        reply = await client.chat([{"role": "system", "content": await _prompt(settings, "system_search.txt")},
                                   {"role": "user", "content": sa.leader_input(search["question"], cards, refs)}],
                                  max_tokens=1200, temperature=0.4, timeout_s=settings.chat_timeout_s)
        answer = strip_thinking(reply.content) or "（統合モデルの応答が空でした）"
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
    confirmed = sum(1 for c in cards for cl in c["claims"] if cl.get("quote_ok"))
    trace = _trace(state, search, seconds=seconds, cards_total=len(cards), claims_confirmed=confirmed)
    log.info("search answered leader=%s refs=%d seconds=%.1f", label, len(refs), seconds)
    return {"lock_token": None,
            "messages": [AIMessage(id=state["progress_id"], content=sa.format_answer(answer, refs),
                                   additional_kwargs={"search_trace": trace})]}


builder = StateGraph(ChatState)
builder.add_node("ingest", ingest)
builder.add_node("route", route)
builder.add_node("chat", chat)
builder.add_node("plan", plan)
builder.add_node("search", search)
builder.add_node("filter", filter_hits)
builder.add_node("read", read)
builder.add_node("critique", critique)
builder.add_node("synthesize", synthesize)
builder.add_edge(START, "ingest")
builder.add_conditional_edges("ingest", _after_ingest, {"chat": "chat", "route": "route", "plan": "plan", END: END})
builder.add_conditional_edges("route", _after_route, {"chat": "chat", "plan": "plan", END: END})
builder.add_edge("chat", END)
builder.add_conditional_edges("plan", _to_search, ["search", "synthesize", END])
builder.add_edge("search", "filter")
builder.add_conditional_edges("filter", _to_read, ["read", "critique", END])
builder.add_edge("read", "critique")
builder.add_conditional_edges("critique", _after_critique, ["search", "synthesize", END])
builder.add_edge("synthesize", END)

graph = builder.compile()
graph.name = "chat search agent"
