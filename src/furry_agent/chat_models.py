"""Starting and stopping the chat tab's local models (PrismML llama-server) and the other outside dependencies.

Shared by the search nodes (chat_graph) and the claim checks (claim_nodes). Every dependency can be replaced through ``config["configurable"]`` for tests (server_factory,
search_factory, free_memory, ensure_tor).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig

from furry_agent.bonsai_select import Catalog, Rank, Selection, available_models, free_memory_mb, select_model
from furry_agent.bonsai_worker import LlamaServer, WorkerError, free_port
from furry_agent.chat_common import ChatState, _conf, _leaders, _llm, leader_label, log
from furry_agent.config import ChatSettings, env_int
from furry_agent.llm_client import OpenAICompatClient
from furry_agent.search_client import TorSearchClient
from furry_agent.tor_service import ensure_tor

LEADER_LABEL = "Qwen3.8 27B abliterated（llama.cpp）"
PROXY_LABEL = "Ternary-Bonsai-2-27B abliterated（代理、llama.cpp）"
PORT_ROUTE, PORT_FILTER, PORT_LEADER = 7, 8, 9  # offsets from BONSAI_BASE_PORT; readers use 0..2
# The proxy leader's llama-server context (_server: large models get at least this much).
LEADER_CTX = env_int("BONSAI_LEADER_CTX", 8192, 1024, 262144)


def _server(config: RunnableConfig | None, settings: ChatSettings, selection: Selection, port: int) -> LlamaServer:
    factory = _conf(config).get("server_factory")
    if factory:
        return factory(selection, port)
    ctx = max(settings.ctx, LEADER_CTX) if selection.model.large else settings.ctx
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
            await server.start(settings.idle_timeout_s)
            return await fn(server.client(max_tokens_timeout or settings.idle_timeout_s), selection), selection
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
        return server.client(settings.idle_timeout_s), selection.model.label
    catalog, rank, available = await asyncio.to_thread(_catalog, settings)
    failed: list[str] = []
    errors = []
    for _ in range(2):
        selection = select_model(task, catalog, rank, available, _free_mb(config), leader_resident=False,
                                 reserve_mb=settings.reserve_mb, exclude=tuple(failed))
        server = _server(config, settings, selection, await asyncio.to_thread(free_port, settings.base_port + PORT_LEADER))
        try:
            await server.start(settings.idle_timeout_s)
        except WorkerError as exc:
            await server.stop()
            failed.append(selection.model.id)
            errors.append(str(exc))
            continue
        _leaders[token] = (server, selection)
        log.info("proxy leader %s on port %s (boot %.1fs)", selection.model.id, server.port, server.boot_s)
        return server.client(settings.idle_timeout_s), selection.model.label
    raise WorkerError("代理リーダーを起動できません（次点も失敗）: " + " / ".join(errors))


async def _leader_client(config, settings: ChatSettings, state: ChatState, task: str):
    if (state.get("search") or {}).get("mode") == "resident":
        return _llm(config, settings), leader_label(settings)
    return await _leader(config, settings, state["lock_token"], task)
