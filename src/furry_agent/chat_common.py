"""State and helpers shared by the chat tab's nodes (chat_graph, write_nodes, code_nodes).

Dependencies can be replaced through ``config["configurable"]`` for tests (chat_settings, llm, server_factory,
search_factory, free_memory, ensure_tor, comfy_client, sandbox_runner).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from pathlib import Path
from typing import Annotated, Any

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import MessagesState

from furry_agent import bonsai_worker, claim_verify as cv
from furry_agent.bonsai_worker import Ledger, LlamaServer
from furry_agent.bonsai_select import Selection
from furry_agent.comfy_client import ComfyClient
from furry_agent.config import ChatSettings, env_float, env_int
from furry_agent.job_lock import JobLockBusy, job_lock
from furry_agent.llm_client import LlamaRouter
from furry_agent.modes import THINK

log = logging.getLogger("furry_agent.chat")

RESET = "__reset__"
RENEW_EVERY_S = env_float("JOB_LOCK_RENEW_S", 60.0, 1.0)
HISTORY_CHARS = env_int("CHAT_HISTORY_CHARS", 4000, 100)  # one earlier turn sent back to the model
# The least room left for thinking before think mode turns it on (the rest of max_tokens is shared with the
# answer; when the thoughts use everything, _ask answers again without thinking).
THINK_RESERVE = env_int("CHAT_THINK_RESERVE", 256, 0)
_ledgers: dict[str, Ledger] = {}
_leaders: dict[str, tuple[LlamaServer, Selection]] = {}


def _append(old: list | None, new: list | None) -> list:
    """Parallel nodes append; [RESET] (from ingest) clears the run's data."""
    if new and new[0] == RESET:
        return [*new[1:]]
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
    # fast | think (resolved from configurable.mode; auto picks one), and how it was chosen
    mode: str
    mode_info: dict[str, Any]
    # Thinking tokens of this run (shown apart from the answer, never in it)
    thinking: Annotated[list[dict], _append]
    # The thread's writing artifact: kept across turns so "続き" continues the draft (§4.1)
    artifact: dict[str, Any]
    # The code branch of this run (§5.2)
    code: dict[str, Any]
    # Claim verification (docs/claim-verification-design.md §5.4): the cards every claim is checked against
    # (the search cards, numbered once), the claims and their verdicts, the audit of the
    # final text, and why verification failed ("" when it did not).
    evidence: Annotated[list[dict], _append]
    claims: list[dict]
    claim_audit: list[dict]
    verify_error: str | None
    # The control loop (docs/autonomous-controller-design.md §6): active, steps, trace, decision, answer ...
    control: dict[str, Any]


CONTROL_RECORD = "controller_record"


def controlled(state) -> bool:
    """This run is the control loop: the end of a tool goes back to controller_record instead of END."""
    return bool((state.get("control") or {}).get("active"))


def end_or_record(state) -> str:
    """END for the single-tool paths; controller_record when the control loop called the tool (§5.2)."""
    return CONTROL_RECORD if controlled(state) else "__end__"


class StageError(RuntimeError):
    def __init__(self, stage: str, message: str):
        super().__init__(message)
        self.stage = stage


def _conf(config: RunnableConfig | None) -> dict:
    return (config or {}).get("configurable", {})


def _settings(config: RunnableConfig | None) -> ChatSettings:
    return _conf(config).get("chat_settings") or ChatSettings.from_env()


def _llm(config: RunnableConfig | None, settings: ChatSettings) -> LlamaRouter:
    return _conf(config).get("llm") or LlamaRouter(settings.llm_url, settings.llm_model,
                                                     settings.idle_timeout_s)


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
    """Text-only human/ai turns of this thread (progress text, trace and thinking data are not sent back)."""
    out = []
    progress = state.get("progress_id")
    for message in state["messages"]:
        kind = getattr(message, "type", None)
        if kind not in ("human", "ai") or (progress and getattr(message, "id", None) == progress):
            continue  # this run's own progress text is not a turn of the conversation
        text, _ = _text_of(message)
        if text:
            out.append({"role": "user" if kind == "human" else "assistant", "content": text[:HISTORY_CHARS]})
    return out[-turns * 2:]


def _is_think(state: ChatState) -> bool:
    return state.get("mode") == THINK


def _kwargs(state: ChatState, trace: dict | None = None, *, thinking: bool = False, task: dict | None = None) -> dict:
    """additional_kwargs of a chat message: the search trace, the run's mode, its thinking and task steps."""
    out: dict[str, Any] = {}
    if trace:
        out["search_trace"] = trace
    if state.get("mode_info"):
        out["chat_mode"] = state["mode_info"]
    if task:
        out["task_trace"] = task
    claims = claim_trace(state)
    if claims:
        out["claim_trace"] = claims
    if thinking and _is_think(state):
        thoughts = [t for t in state.get("thinking") or [] if t.get("text")]
        if thoughts:
            out["thinking"] = thoughts
    return out


def claim_trace(state) -> dict | None:
    """The progress table of claim verification (UI: claim_trace). None when verification did not run."""
    claims = state.get("claims") or []
    audit = state.get("claim_audit") or []
    error = state.get("verify_error")
    if not claims and not audit and not error:
        return None
    evidence = [c for c in state.get("evidence") or [] if c != RESET and c.get("evidence_id")]
    dropped = [a for a in audit if a.get("status") in cv.DROP]
    return {"claims": cv.table(claims, evidence), "error": error or "",
            "audit": {"checked": len(audit), "dropped": [{"text": a["text"][:120], "status": a["status"],
                                                          "note": a.get("note", "")} for a in dropped]},
            "evidence": [{"n": c["n"], "locator": c["locator"], "source_type": c.get("source_type", "web")}
                         for c in evidence][:30]}



def _progress(state: ChatState, text: str, trace: dict | None = None, *, task: dict | None = None) -> AIMessage:
    return AIMessage(id=state["progress_id"], content=text, additional_kwargs=_kwargs(state, trace, task=task))


def _final(state: ChatState, text: str, trace: dict | None = None, *, task: dict | None = None,
           thoughts: list[dict] | None = None, message_id: str | None = None) -> AIMessage:
    """The answer message: thinking goes to additional_kwargs.thinking (think mode only), never into content."""
    view = {**state, "thinking": [*(state.get("thinking") or []), *(thoughts or [])]}
    return AIMessage(id=message_id or state["progress_id"], content=text,
                     additional_kwargs=_kwargs(view, trace, thinking=True, task=task))


def _usage(reply) -> str:
    """Tokens of one reply for the log (completion, of which thinking) and the time it took."""
    usage = (getattr(reply, "raw", None) or {}).get("usage") or {}
    thinking = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
    return (f"tokens={usage.get('completion_tokens', '?')} thinking_tokens={thinking if thinking is not None else '?'} "
            f"seconds={getattr(reply, 'seconds', 0.0):.1f}")


def _thought(stage: str, reply) -> list[dict]:
    text = (getattr(reply, "reasoning", "") or "").strip()
    return [{"stage": stage, "text": text[-12000:]}] if text else []


def _fail(state: ChatState, exc: Exception | str, stage: str | None = None, trace: dict | None = None) -> dict:
    stage = getattr(exc, "stage", None) or stage
    log.error("chat failed stage=%s: %s", stage, exc)
    prefix = f"［{stage}］" if stage else ""
    message = AIMessage(id=state.get("progress_id") or f"error-{uuid.uuid4()}",
                        content=f"⚠️ 応答できませんでした{prefix}: {exc}",
                        additional_kwargs=_kwargs(state, trace))
    return {"messages": [message], "error": str(exc), "lock_token": None}


async def _cleanup(token: str | None, llm: LlamaRouter | None = None, unload: bool = False) -> None:
    """Kill every llama-server (the proxy leader included), optionally unload the LLM router, release the lock."""
    try:
        leader = _leaders.pop(token, None) if token else None
        if leader is not None:
            await leader[0].stop()
        left = await bonsai_worker.kill_all()
        if left:
            log.info("stopped llama-server pids=%s", left)
        if unload and llm is not None:
            try:
                await llm.unload_all()
            except Exception as exc:  # router down: nothing is loaded there anyway
                log.warning("LLM router unload failed: %s", exc)
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
    if job_lock.holds(state.get("lock_token")):
        job_lock.renew(state["lock_token"])
        return state["lock_token"]
    # The other tab's job is waited for while it works: its holder renews the lease, and a holder that died frees
    # the lock when the lease runs out. JOB_LOCK_TIMEOUT_S, when set, bounds the wait instead.
    limit = settings.job_lock_timeout_s
    started = time.monotonic()
    token = await job_lock.acquire("chat", limit, limit)
    # The image tab releases the lock after queueing; ComfyUI may still be generating (LLM + checkpoint). While
    # ComfyUI answers and its queue is busy, the chat run waits (the image run has its own idle timeout).
    waited = False
    while await _image_tab_busy(config, settings):
        if limit is not None and time.monotonic() - started >= limit:
            job_lock.release(token)
            raise JobLockBusy("image")
        if not waited:
            log.info("chat waits for the image tab's ComfyUI run")
            waited = True
        job_lock.renew(token)
        await asyncio.sleep(2.0)
    return token


@contextlib.asynccontextmanager
async def _held(token: str | None):
    """Renew the job lock's lease while a long model call runs (a long call outlives the 15-minute lease)."""
    async def beat():
        while True:
            await asyncio.sleep(RENEW_EVERY_S)
            job_lock.renew(token)

    task = asyncio.get_running_loop().create_task(beat()) if token else None
    try:
        yield
    finally:
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


def prompt_tokens(messages: list[dict]) -> int:
    """A cautious token estimate: ~1 token per Japanese character, ~1 per 3.5 ASCII characters."""
    total = 0
    for message in messages:
        text = str(message.get("content") or "")
        ascii_ = sum(1 for ch in text if ord(ch) < 128)
        total += (len(text) - ascii_) + ascii_ // 3 + 8
    return total


# Measured generation speed per server (tokens/s), updated from every reply of a long enough call (logged only).
_speeds: dict[str, float] = {}


def _speed_key(client) -> str:
    return getattr(client, "base_url", "") or type(client).__name__


def record_speed(client, reply) -> None:
    usage = (getattr(reply, "raw", None) or {}).get("usage") or {}
    tokens, seconds = usage.get("completion_tokens") or 0, getattr(reply, "seconds", 0.0) or 0.0
    if tokens >= 64 and seconds > 1:
        # The first call includes loading the model, so this errs on the slow side.
        key = _speed_key(client)
        measured = tokens / seconds
        _speeds[key] = measured if key not in _speeds else 0.5 * _speeds[key] + 0.5 * measured


def capped(settings: ChatSettings, client, max_tokens: int) -> int:
    """The max_tokens of a call. Model calls have an idle timeout (AGENT_IDLE_TIMEOUT_S), not a total one, so a
    slow server is not given fewer tokens; only the context window limits a reply (see _ask)."""
    return max_tokens


def fit_messages(messages: list[dict], budget: int) -> list[dict]:
    """Drop the oldest turns (never the system prompt or the last message) until the prompt fits ``budget``
    tokens; a last message that is still too long keeps its end."""
    out = list(messages)
    while prompt_tokens(out) > budget and len(out) > 2:
        del out[1]
    if prompt_tokens(out) > budget and out:
        last = dict(out[-1])
        over = prompt_tokens(out) - budget
        text = str(last.get("content") or "")
        last["content"] = text[min(len(text), over + 16):]
        out[-1] = last
    return out


async def _ask(state: ChatState, settings: ChatSettings, client, messages: list[dict], *, base: int,
               answer_min: int, temperature: float, stage: str, thinking: bool = True,
               context: int | None = None) -> tuple[Any, list[dict]]:
    """One free-text model call that fits the model's context window.

    The router loads the 27B with LLM_CONTEXT tokens (4096 from scripts/setup-llm.ps1: more does not fit
    this machine's memory), and thinking tokens count against max_tokens. Thinking is used only in think mode
    and only when the window still leaves ``answer_min`` tokens plus a thinking budget; when the thoughts used
    everything and no answer came, the call is made once more without thinking (the thoughts are kept).
    """
    window = context or settings.llm_ctx
    want_think = thinking and _is_think(state)
    messages = fit_messages(messages, window - answer_min - (THINK_RESERVE if want_think else 0) - 48)
    room = capped(settings, client, max(256, window - prompt_tokens(messages) - 48))
    think = want_think and room >= answer_min + THINK_RESERVE
    max_tokens = min(room, base + settings.think_tokens) if think else min(room, base)
    reply = await client.chat(messages, max_tokens=max_tokens, temperature=temperature,
                              timeout_s=settings.idle_timeout_s, thinking=think)
    record_speed(client, reply)
    log.info("%s: thinking=%s max_tokens=%d %s", stage, think, max_tokens, _usage(reply))
    thoughts = _thought(stage, reply)
    if think and not (reply.content or "").strip():
        log.info("%s: thinking used the whole budget; answering again without thinking", stage)
        reply = await client.chat(messages, max_tokens=min(room, base), temperature=temperature,
                                  timeout_s=settings.idle_timeout_s, thinking=False)
        log.info("%s: retry %s", stage, _usage(reply))
    return reply, thoughts


def _hitl(name: str, args: dict, description: str) -> dict:
    """The agent-chat-ui HITL card, the same contract as the image tab's role confirmation."""
    return {"action_requests": [{"name": name, "args": args, "description": description}],
            "review_configs": [{"action_name": name, "allowed_decisions": ["approve", "edit", "reject"]}]}


def _decision(response) -> dict:
    """The first decision of a HITL resume value; anything unreadable counts as reject (never runs code)."""
    decisions = response.get("decisions") if isinstance(response, dict) else response
    if isinstance(decisions, list) and decisions and isinstance(decisions[0], dict):
        return decisions[0]
    return {"type": "reject"}


def _edited_args(decision: dict) -> dict:
    edited = decision.get("edited_action") or {}
    args = edited.get("args") if isinstance(edited, dict) else None
    return args if isinstance(args, dict) else {}
