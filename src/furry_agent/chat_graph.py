"""LangGraph chat tab (graph id ``chat``): conversation, deep web search through Tor, writing and code.

    ingest      last human message -> chat / search / write / code / "use the image tab" (router.route, keyword
                rules) and the mode: configurable.mode fast | think | auto (modes.choose; none = fast)
    route       only for messages the rules could not place: Qwen3-1.7B-heretic picks CHAT / SEARCH / WRITE /
                CODE, rewrites a search query and says whether the request is deep (auto mode)
    chat        the Qwen3.8 27B (llama.cpp router) answers directly (think mode: thinking tokens on)
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
                contradictions and the stop reason; every llama-server is gone, the LLM router is unloaded, the shared
                job lock is released. A writing request that needed facts goes on to the writer instead.
    write_*     write_nodes: brief/outline -> draft -> revise (-> chapter confirmation)
    code_*      code_nodes: code_plan -> write_files -> confirm_run -> sandbox_exec -> observe
    controller  control_nodes (think, compound requests only): the 27B picks search / write / code / image or
                answers; each tool's pipeline returns to controller_record, then controller again or finish
                (docs/autonomous-controller-design.md)

The image graph (graph.py) is not changed by this module; both share job_lock so they never run together.
docs/chat-deep-search-creative-sandbox.md is the design.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from furry_agent import (claim_nodes, claim_verify as cv, code_nodes, control_nodes, modes,
                         search_agent as sa, write_nodes, writing)
from furry_agent.bonsai_select import Catalog, Selection, SelectionError, select_model
from furry_agent.bonsai_worker import Ledger, WorkerError, run_reader
from furry_agent.chat_common import (CONTROL_RECORD, RESET, ChatState, StageError, _ask, capped, _cleanup, _conf,
                                     _fail, _final, _held, _history, _is_think, _last_human, _leaders, _ledgers,
                                     _llm, _lock, _progress, _prompt, _settings, _text_of, controlled,
                                     end_or_record, log)
from furry_agent.chat_models import (LEADER_CTX, LEADER_LABEL, PORT_FILTER, PORT_ROUTE, PROXY_LABEL,
                                     _catalog, _ensure_tor, _free_mb, _leader, _leader_client, _run_model, _search_client,
                                     _server)
from furry_agent.config import env_int
from furry_agent.graph import _setup_file_logging  # the same logs/furry_agent.log as the image tab
from furry_agent.job_lock import JobLockBusy, job_lock
from furry_agent.llm_client import LLMError
from furry_agent.router import (CHAT, CODE, SEARCH, TO_IMAGE_TAB, WRITE, Route, compound_kinds, is_compound,
                                route as route_rules)
from furry_agent.search_client import SearchError
from furry_agent.tor_service import TorUnavailable

__all__ = ["graph", "ChatState", "WorkerError", "RESET"]

HITS_PER_INTENT = 4  # default of SEARCH_HITS_PER_INTENT
# max_tokens of each step (.env). The context window still caps every call (chat_common.capped).
CHAT_TOKENS = env_int("CHAT_TOKENS", 1536, 64)
CHAT_ANSWER_MIN = env_int("CHAT_ANSWER_MIN", 512, 16)
PLAN_TOKENS = env_int("SEARCH_PLAN_TOKENS", 400, 64)
PLAN_TOKENS_THINK = env_int("SEARCH_PLAN_TOKENS_THINK", 700, 64)
CARD_TOKENS = env_int("SEARCH_CARD_TOKENS", 700, 64)
CRITIQUE_TOKENS = env_int("SEARCH_CRITIQUE_TOKENS", 900, 64)
SYNTH_TOKENS = env_int("SEARCH_SYNTH_TOKENS", 1200, 64)
SYNTH_TOKENS_THINK = env_int("SEARCH_SYNTH_TOKENS_THINK", 1600, 64)
SYNTH_ANSWER_MIN = env_int("SEARCH_SYNTH_ANSWER_MIN", 800, 16)
ROUTER_KINDS = {"SEARCH": SEARCH, "WRITE": WRITE, "CODE": CODE, "CHAT": CHAT}  # the router never sends to the image tab


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
             "hits": [RESET], "cards": [RESET], "logs": [RESET], "thinking": [RESET],
             "evidence": [RESET], "claims": [], "claim_audit": [], "verify_error": None,
             "control": {}}
    human = _last_human(state)
    if human is None:
        return {**reset, "error": "no input", "messages": [AIMessage(id=progress_id, content="メッセージがありません。")]}
    text, media = _text_of(human)
    decision = route_rules(text, media, has_draft=has_draft, task=conf.get("task"))
    kind = decision.kind
    if kind == CHAT and not decision.explicit and settings.auto_route and sa.ambiguous_question(decision.text):
        kind = "route"
    compound = kind != TO_IMAGE_TAB and is_compound(decision)
    choice = modes.choose(conf.get("mode"), decision, has_draft=has_draft, draft_status=artifact.get("status", ""),
                          compound=compound)
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
    route_state = {**vars(decision), "kind": kind, "claim_verify": settings.claim_verify,
                   "claim_fail_open": settings.claim_fail_open}
    if compound and choice.mode == modes.THINK:
        # Several tools in turn (search, then write ...): the control loop picks them (autonomous-controller §4).
        log.info("chat control loop: kinds=%s", compound_kinds(decision.text))
        return {**reset, "route": route_state, "control": control_nodes.new_control(settings),
                "messages": [AIMessage(id=progress_id, content="依頼を道具の手順に分けています（自律モード）…",
                                       additional_kwargs={"chat_mode": info})]}
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
    if controlled(state):
        return "controller"
    kind = state["route"]["kind"]
    if kind == WRITE:
        return "plan" if state["route"].get("search_first") else "write_brief"
    return {SEARCH: "plan", "route": "route", CODE: "code_plan"}.get(kind, "chat")


async def route(state: ChatState, config: RunnableConfig) -> dict:
    """Qwen3-1.7B-heretic: chat, search, write or code? Also rewrites a search query and says "deep"."""
    settings = _settings(config)
    llm = _llm(config, settings)
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
        await asyncio.shield(_cleanup(token, llm))
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
    llm = _llm(config, settings)
    token = state.get("lock_token")
    try:
        token = await _lock(state, config, settings)
        messages = [{"role": "system", "content": await _prompt(settings, "system_chat.txt")},
                    *_history(state, settings.history_turns)]
        async with _held(token):
            reply, thoughts = await _ask(state, settings, llm, messages, base=CHAT_TOKENS, answer_min=CHAT_ANSWER_MIN,
                                         temperature=0.6, stage="回答")
        text = reply.content or "（空の応答でした）"
        log.info("chat answered mode=%s", state.get("mode"))
    except asyncio.CancelledError:
        await asyncio.shield(_cleanup(token, llm, unload=True))
        raise
    except JobLockBusy as exc:
        return _fail(state, exc, "lock")
    except (LLMError, OSError) as exc:
        await _cleanup(token, llm, unload=True)
        return _fail(state, f"LLM サーバ（llama.cpp）に接続できないか、時間切れです（{exc}）", "chat")
    # Plain chat keeps the 27B loaded (the router's idle sleep unloads it); the image workflow ejects it anyway.
    await _cleanup(token)
    return {"messages": [_final(state, text, thoughts=thoughts)], "lock_token": None}


# --- plan ---------------------------------------------------------------------------------------------------------


def _question(state: ChatState) -> str:
    return state["route"]["text"]


async def plan(state: ChatState, config: RunnableConfig) -> dict:
    settings = _settings(config)
    llm = _llm(config, settings)
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
        use_llm = settings.search_planner == "llm" and await llm.reachable()
        parsed, planner = None, None
        if use_llm:
            # The user's requirement: the Qwen3.8 27B does the first step (the plan) ...
            async with _held(token):
                parsed = await sa.ask_json(llm, planner_messages, schema,
                                           max_tokens=capped(settings, llm, PLAN_TOKENS_THINK if think else PLAN_TOKENS))
            planner = LEADER_LABEL
        if parsed is None:
            try:
                client, planner = await _leader(config, settings, token, "plan")
                parsed = await sa.ask_json(client, planner_messages, schema, max_tokens=PLAN_TOKENS_THINK if think else PLAN_TOKENS)
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
        resident = use_llm and bool(await llm.loaded())
        mode = "resident"
        if resident:
            try:
                fit = select_model("worker", catalog, rank, available, _free_mb(config), leader_resident=True,
                                   reserve_mb=settings.reserve_mb, max_width=len(intents),
                                   override=settings.model_override)
                if fit.width < min(len(intents), settings.fanout_width):
                    raise SelectionError("幅が足りません")
            except SelectionError as exc:
                log.info("readers do not fit next to the router's 27B (%s): unloading it", exc)
                await llm.unload_all()
                mode = "proxy"
        else:
            mode = "proxy"
        search.update({"intents": intents, "pending": [i["id"] for i in intents], "mode": mode,
                       "roles": {**search["roles"], "planner": planner,
                                 "leader": LEADER_LABEL if mode == "resident" else
                                 PROXY_LABEL}})
        _ledgers[token] = Ledger()
        # Query and intent texts are not logged (design doc §5.10); result URLs are, in search_client.
        log.info("search plan mode=%s chat_mode=%s planner=%s intents=%s subquestions=%d", mode, state.get("mode"),
                 planner, [i["tool"] for i in intents], len(search["subquestions"]))
    except asyncio.CancelledError:
        await asyncio.shield(_cleanup(token, llm, unload=True))
        raise
    except JobLockBusy as exc:
        return _fail(state, exc, "lock")
    except (StageError, SelectionError, TorUnavailable, SearchError, LLMError, WorkerError, OSError, ValueError) as exc:
        await _cleanup(token, llm, unload=True)
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
        return end_or_record(state)
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
            async with asyncio.timeout(settings.intent_timeout_s):
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
    llm = _llm(config, settings)
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
        await asyncio.shield(_cleanup(token, llm, unload=True))
        raise
    except (SelectionError, OSError) as exc:
        await _cleanup(token, llm, unload=True)
        return _fail(state, exc, "worker", _trace(state, search))
    readers = len(search["slots"])
    prefix = f"第 {search['round'] + 1} ラウンド: " if _is_think(state) else ""
    text = (f"{prefix}検索結果 {len(hits)} 件のページを {readers} 体の reader（{roles.get('reader', '')}）が読んでいます…"
            if readers else f"{prefix}検索結果がありませんでした。")
    return {"search": search, "messages": [_progress(state, text, _trace(state, search))]}


def _after_reading(state: ChatState) -> str:
    """Think mode scores the round (critique); fast mode, or SEARCH_CRITIQUE=0, skips the critic (§3.3)."""
    if state.get("error"):
        return end_or_record(state)
    return "judge" if _is_think(state) and _settings_critique(state) else claim_nodes.answer_entry(state)


def _settings_critique(state: ChatState) -> bool:
    return bool(state["search"].get("critique", True))


def _to_read(state: ChatState):
    if state.get("error"):
        return end_or_record(state)
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
        # SEARCH_TOTAL_TIMEOUT_S (0 = none) is an optional budget over every intent this reader handles; opening
        # pages may use 60 % of each share so the card extraction always gets its turn. Without it, only the idle
        # timeout of each model call (AGENT_IDLE_TIMEOUT_S) and the page fetch timeouts apply.
        jobs_n = len(payload["jobs"])
        budget = settings.search_total_timeout_s * jobs_n if settings.search_total_timeout_s > 0 else None
        async with asyncio.timeout(budget):
            await server.start(settings.idle_timeout_s)
            llm = server.client(settings.idle_timeout_s)

            async def ask_cards(llm_client, messages):
                return await sa.ask_json(llm_client, messages, sa.Cards, max_tokens=CARD_TOKENS, temperature=0.1)

            for job in payload["jobs"]:
                intent, hits = job["intent"], job["hits"]
                for h in hits:
                    client.allow(h["url"])
                entry = {"intent_id": intent["id"], "kind": "read", "opened": [], "error": None}
                try:
                    out = await run_reader(intent, hits, llm, client, ledger,
                                           await _prompt(settings, "system_bonsai_worker.txt"), question, ask_cards,
                                           browse_budget_s=settings.search_total_timeout_s * 0.6)  # 0 = none
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
        await asyncio.shield(_cleanup(token, _llm(config, settings), unload=True))
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


async def judge(state: ChatState, config: RunnableConfig) -> dict:
    """Think mode: show that the critic is working (a node's text appears when the node ends)."""
    search = state["search"]
    return {"messages": [_progress(state, f"第 {search['round'] + 1} ラウンドの結果から、足りない点を判定しています…",
                                   _trace(state, search))]}


async def critique(state: ChatState, config: RunnableConfig) -> dict:
    """Think mode: score the sub-questions and decide on another round (§3.3). It never writes the answer."""
    settings = _settings(config)
    llm = _llm(config, settings)
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
                    sa.Reflect, max_tokens=capped(settings, client, CRITIQUE_TOKENS), temperature=0.2)
            roles["critic"] = label
        except asyncio.CancelledError:
            await asyncio.shield(_cleanup(token, llm, unload=True))
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
        text = f"検索を終えました（停止理由: {sa.STOP_LABELS.get(reason, reason)}）。{roles.get('leader', '')} が回答をまとめています…"
    log.info("critique round=%d new_cards=%d pages=%d open=%d -> %s", search["round"], len(new_cards),
             search["pages_read"], len(search["open"]), "search" if go else reason)
    return {"search": search, "messages": [_progress(state, text, _trace(state, search))]}


def _after_critique(state: ChatState):
    if state.get("error"):
        return end_or_record(state)
    if state["search"].get("pending"):
        return _to_search(state)
    return claim_nodes.answer_entry(state)


def _research_text(answer: str, refs: list[dict]) -> str:
    return answer + ("\n\n出典:\n" + "\n".join(f"[{r['n']}] {r['title']} {r['url']}" for r in refs) if refs else "")


async def synthesize(state: ChatState, config: RunnableConfig) -> dict:
    """The answer from the search cards. With claim verification it is written
    from the supported and partial claims only and then audited (claim_audit keeps the leader and the lock);
    otherwise every model is freed here."""
    settings = _settings(config)
    llm = _llm(config, settings)
    search = dict(state["search"])
    token = state.get("lock_token")
    job_lock.renew(token)
    cards = state.get("cards") or []
    hits = state.get("hits") or []
    logs = state.get("logs") or []
    refs = sa.references(cards, hits)
    evidence = claim_nodes.evidence_of(state, settings)
    roles = dict(search.get("roles") or {})
    think = _is_think(state)
    claims = state.get("claims") or []
    verified = bool(state["route"].get("claim_verify") and (search.get("claim") or {}).get("started")
                    and not state.get("verify_error"))
    search["pages_read"] = _pages_read(logs)
    if not search.get("stop_reason"):
        # Fast mode: one round by design; think mode reaches here through the critic, which set the reason.
        search["stop_reason"] = "no_hits" if not hits else "budget"
    thoughts: list[dict] = []
    then_write = bool(state["route"].get("search_first"))
    update: dict[str, Any] = {}
    try:
        if not refs:
            # Nothing came back: do not wake up a leader (design doc §5.4 step 6).
            errors = "; ".join(f"{e['error']}" for e in logs if e.get("error"))
            text = "検索結果がありません。Tor 出口が拒否された可能性があります。"
            if errors:
                text += f"\n\n（{errors[:400]}）"
            search["stop_reason"] = "no_hits"
            await _cleanup(token, llm, unload=True)
            if then_write:
                artifact = {**(state.get("artifact") or {}), "research": ""}
                return {"lock_token": None, "search": search, "artifact": artifact,
                        "messages": [_progress(state, text + "\n\n資料なしで書きます…", _trace(state, search))]}
            return {"lock_token": None, "messages": [_progress(state, text, _trace(state, search))]}
        block = cv.verified_block(claims, evidence) if verified else ""
        if verified and not block:
            # Not one claim survived the check: no answer is written from unchecked cards (§4.1).
            answer, label = "出典カードで確かめられた主張がありませんでした。", roles.get("critic") or ""
        else:
            client, label = await _leader_client(config, settings, state, "synthesize")
            # Thinking tokens only on the router's 27B (the proxy leader runs with --reasoning off, 8192 context).
            resident = search.get("mode") == "resident"
            user = sa.leader_input(search["question"], cards, refs, search if think else None)
            if block:
                user += f"\n\n検証済みの主張（これ以外の事実は書かない。[n] はそのまま使う）:\n{block}"
            async with _held(token):
                reply, thoughts = await _ask(
                    state, settings, client,
                    [{"role": "system", "content": await _prompt(settings, "system_search.txt")},
                     {"role": "user", "content": user}],
                    base=SYNTH_TOKENS_THINK if think else SYNTH_TOKENS, answer_min=SYNTH_ANSWER_MIN, temperature=0.4, stage="統合", thinking=resident,
                    context=None if resident else LEADER_CTX)
            answer = reply.content or "（統合モデルの応答が空でした）"
            roles["synthesizer"] = label
            if state.get("verify_error"):  # CLAIM_VERIFY_FAIL_OPEN=1: the unaudited synthesis, marked (§5.3)
                answer = "（突き合わせ失敗。以下は主張の監査を通していない統合です）\n\n" + answer
    except asyncio.CancelledError:
        await asyncio.shield(_cleanup(token, llm, unload=True))
        raise
    except (LLMError, WorkerError, SelectionError, OSError) as exc:
        await _cleanup(token, llm, unload=True)
        return _fail(state, exc, "chat", _trace(state, search))
    search["roles"] = roles
    search["answer"] = answer
    search["thoughts"] = thoughts
    update["search"] = search
    log.info("search answered leader=%s cards=%d verified=%s", label, len(evidence), verified)
    if verified and block:
        # The leader and the lock stay for the audit (claim_audit frees them).
        view = {**state, **update}
        return {**update, "messages": [_progress(view, "回答の各文を出典と照合しています（監査）…",
                                                 _trace(view, search))]}
    # Every llama-server is killed and the 27B unloaded so the image tab gets the memory back (design doc §5.8).
    await _cleanup(token, llm, unload=True)
    update["lock_token"] = None
    return {**update, **await finish_answer({**state, **update}, config)}


async def finish_answer(state: ChatState, config: RunnableConfig) -> dict:
    """claim_drop and the end of synthesize: delete the sentences the audit rejected, add the sources and send
    the final message (or hand the research to the writer). No model is called here."""
    settings = _settings(config)
    search = dict(state["search"])
    evidence = [c for c in state.get("evidence") or [] if c != RESET and c.get("evidence_id")]
    cards = state.get("cards") or []
    answer = search.get("answer") or ""
    error = state.get("verify_error")
    audit = state.get("claim_audit") or []
    claims = state.get("claims") or []
    deleted: list[str] = []
    if error and not answer:
        # CLAIM_VERIFY_FAIL_OPEN=0: no unaudited answer, the excerpts only (§5.3).
        answer = f"{claim_nodes.FAIL_TEXT.get(error, claim_nodes.FAIL_TEXT['json'])}。\n\n{cv.excerpts_text(evidence)}"
    elif audit:
        drop = {a["sentence_index"] for a in audit if a["status"] in cv.DROP}
        answer, deleted = cv.drop_sentences(answer, drop)
        if not answer.strip():
            answer = "監査の結果、出典で支持される文が残りませんでした。抜粋:\n\n" + cv.excerpts_text(evidence)
    if deleted:
        log.info("claim_drop deleted %d sentences", len(deleted))
    if any(c.get("status") == "contradicted" for c in [*claims, *audit]):
        answer += "\n\n一部の候補は出典と矛盾したため本文から除いた。"
    seconds = round(time.time() - search.get("started", time.time()), 1)
    search["wall_clock_s"] = seconds
    thoughts = search.pop("thoughts", None) or []
    view = {**state, "search": search}
    refs = sa.references(cards, state.get("hits") or [])
    confirmed = sum(1 for c in cards for cl in c["claims"] if cl.get("quote_ok"))
    trace = _trace(view, search, seconds=seconds, cards_total=len(cards), claims_confirmed=confirmed)
    log.info("search answered refs=%d rounds=%s stop=%s seconds=%.1f", len(refs), search.get("round"),
             search.get("stop_reason"), seconds)
    if state["route"].get("search_first"):
        artifact = {**(state.get("artifact") or {}), "research": _research_text(answer, refs)}
        return {"lock_token": None, "search": search, "artifact": artifact, "thinking": thoughts,
                "messages": [_progress(view, "調べた内容をもとにアウトラインを作っています…", trace)]}
    content = sa.format_answer(answer, refs, search if _is_think(state) else None, cards)
    return {"lock_token": None, "search": search, "messages": [_final(view, content, trace, thoughts=thoughts)]}


def _after_synthesize(state: ChatState) -> str:
    if state.get("error"):
        return end_or_record(state)
    if claim_nodes.audit_needed(state) and state.get("lock_token"):
        return "claim_audit"
    if controlled(state):
        return CONTROL_RECORD  # the controller decides what follows a search; never the writer directly (§9)
    return "write_brief" if state["route"].get("search_first") else END


def _after_drop(state: ChatState) -> str:
    if state.get("error") or controlled(state) or not state["route"].get("search_first"):
        return end_or_record(state)
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
builder.add_node("claim_extract", claim_nodes.claim_extract)
builder.add_node("claim_verify", claim_nodes.claim_verify)
builder.add_node("claim_audit", claim_nodes.claim_audit)
builder.add_node("claim_drop", claim_nodes.claim_drop)
write_nodes.add_nodes(builder)
code_nodes.add_nodes(builder)
control_nodes.add_nodes(builder)
ANSWER = ["claim_extract", "synthesize", CONTROL_RECORD, END]
builder.add_edge(START, "ingest")
builder.add_conditional_edges("ingest", _after_ingest,
                              ["chat", "route", "plan", "write_brief", "code_plan", "controller", END])
builder.add_conditional_edges("route", _after_route, ["chat", "plan", "write_brief", "code_plan", END])
builder.add_edge("chat", END)
builder.add_conditional_edges("plan", _to_search, ["search", "synthesize", CONTROL_RECORD, END])
builder.add_edge("search", "filter")
builder.add_conditional_edges("filter", _to_read, ["read", "judge", *ANSWER])
builder.add_conditional_edges("read", _after_reading, ["judge", *ANSWER])
builder.add_edge("judge", "critique")
builder.add_conditional_edges("critique", _after_critique, ["search", *ANSWER])
# Claim verification (docs/claim-verification-design.md §5.1)
builder.add_conditional_edges("claim_extract", claim_nodes._after_extract, ["claim_verify", "synthesize", "claim_drop"])
builder.add_conditional_edges("claim_verify", claim_nodes._after_verify, ["synthesize", "claim_drop"])
builder.add_conditional_edges("synthesize", _after_synthesize, ["claim_audit", "write_brief", CONTROL_RECORD, END])
builder.add_edge("claim_audit", "claim_drop")
builder.add_conditional_edges("claim_drop", _after_drop, ["write_brief", CONTROL_RECORD, END])

graph = builder.compile()
graph.name = "chat agent"
