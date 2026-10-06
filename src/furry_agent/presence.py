"""The presence turn for vrc-pilot (vrc-pilot docs/04 "推論と local-agent の改修"): ``POST /presence/turn``.

One stateless completion: vrc-pilot sends its world-state summary, persona and policy; the model returns at most
two ActionCommands and a one-line belief. There are no tools: a tool call from the model is dropped and the turn
is a ``noop``. Nothing here touches the image graph (graph.py), the chat/search graph (chat_graph.py), ComfyUI,
Tor or the sandbox.

    request  {"world": {...}, "persona": {...}, "policy": {"allow_move": false, "allow_say": true, "max_actions": 2},
              "model": "<optional config/host_models.json inference id>"}
    response {"actions": [ActionCommand, ...], "belief": "..."}
    422      the model's actions break the contract or the policy (a say over 144 characters is never cut here:
             the model gets one chance to rewrite it, then the turn is refused)
    504      no reply within PRESENCE_TIMEOUT_S (4 s)

The turn does not take ``job_lock``: a 4 s budget cannot wait behind an image generation, and the presence model
is a separate (usually remote) inference model. Pick it with ``PRESENCE_MODEL`` (an inference id; the host's
default model otherwise). The system prompt is prompts/presence_system.txt, a copy of vrc-pilot's
docs/prompts/planner_system.txt (that file is the source).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from furry_agent.config import REPO_ROOT, ChatSettings, env_float
from furry_agent.llm_client import LLMError, parse_json_object
from furry_agent.model_catalog import CatalogError, ModelChoiceError, resolve_inference

log = logging.getLogger("furry_agent.presence")

PROMPT_PATH = REPO_ROOT / "prompts" / "presence_system.txt"
TIMEOUT_S = env_float("PRESENCE_TIMEOUT_S", 4.0, 0.5)
MAX_TOKENS = 300
KINDS = ("noop", "say", "look", "move", "stop", "emote_key")
MOVE_KINDS = ("move", "look")
REWRITE_JSON = "説明を付けず、JSON オブジェクトだけで書き直せ。"
REWRITE_SHORT = "say.text が 144 文字を超えた。意味を保って 144 文字以内で書き直し、同じ形の JSON だけを返せ。"


class TurnRefused(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def system_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


# -- contract (vrc-pilot schemas/action_command.schema.json, checked without a schema library) ------------------

def _num(v: Any, lo: float, hi: float) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and lo <= v <= hi


def _int(v: Any, lo: int, hi: int) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi


def action_errors(a: Any) -> list[str]:
    if not isinstance(a, dict):
        return ["action is not an object"]
    out = []
    allowed = {"action_id", "ts", "kind", "say", "look", "move", "reason", "addressed_to"}
    out += [f"unknown key {k}" for k in a if k not in allowed]
    for k in ("action_id", "ts"):
        if not isinstance(a.get(k), str):
            out.append(f"{k} must be a string")
    kind = a.get("kind")
    if kind not in KINDS:
        out.append(f"kind {kind!r} is not one of {KINDS}")
    say = a.get("say")
    if say is not None:
        if not isinstance(say, dict):
            out.append("say must be an object")
        else:
            text = say.get("text")
            if text is not None and not isinstance(text, str):
                out.append("say.text must be a string")
            elif isinstance(text, str) and len(text) > 144:
                out.append(f"say.text is {len(text)} characters (max 144)")
            for b in ("immediate", "notification"):
                if b in say and not isinstance(say[b], bool):
                    out.append(f"say.{b} must be a boolean")
    look = a.get("look")
    if look is not None:
        if not isinstance(look, dict):
            out.append("look must be an object")
        else:
            for k in ("yaw", "pitch"):
                if k in look and not _num(look[k], -1, 1):
                    out.append(f"look.{k} must be within -1..1")
            if "duration_ms" in look and not _int(look["duration_ms"], 0, 2000):
                out.append("look.duration_ms must be an integer 0..2000")
    move = a.get("move")
    if move is not None:
        if not isinstance(move, dict):
            out.append("move must be an object")
        else:
            for k in ("forward", "strafe"):
                if k in move and not _num(move[k], -1, 1):
                    out.append(f"move.{k} must be within -1..1")
            if "duration_ms" in move and not _int(move["duration_ms"], 0, 1500):
                out.append("move.duration_ms must be an integer 0..1500")
            if "run" in move and not isinstance(move["run"], bool):
                out.append("move.run must be a boolean")
    if "reason" in a and (not isinstance(a["reason"], str) or len(a["reason"]) > 240):
        out.append("reason must be a string of at most 240 characters")
    if "addressed_to" in a and a["addressed_to"] is not None and not isinstance(a["addressed_to"], str):
        out.append("addressed_to must be a string or null")
    return out


def complete_action(raw: Any) -> Any:
    """Add the server-owned keys (action_id, ts) and accept the flattened ``{"kind":"say","text":…}``."""
    if not isinstance(raw, dict):
        return raw
    a = dict(raw)
    a.setdefault("action_id", f"act-{uuid.uuid4().hex[:12]}")
    a.setdefault("ts", _now())
    if a.get("kind") == "say" and "say" not in a and "text" in a:
        a["say"] = {"text": a.pop("text")}
    if a.get("kind") == "say" and isinstance(a.get("say"), dict):
        a["say"] = {"immediate": True, "notification": False, **a["say"]}
    for kind in MOVE_KINDS:
        if a.get("kind") == kind and kind not in a:
            body = {k: a.pop(k) for k in ("forward", "strafe", "duration_ms", "run", "yaw", "pitch") if k in a}
            a[kind] = body
    return a


def check_turn(obj: dict, policy: dict) -> tuple[list[dict], str]:
    """(actions, belief) or :class:`TurnRefused` with every problem found."""
    raw = obj.get("actions")
    if raw is None:
        raw = [{"kind": "noop"}]
    if not isinstance(raw, list):
        raise TurnRefused(["actions must be a list"])
    max_actions = int(policy.get("max_actions", 2))
    if len(raw) > max_actions:
        raise TurnRefused([f"{len(raw)} actions (max {max_actions})"])
    actions = [complete_action(a) for a in raw]
    problems = []
    for i, a in enumerate(actions):
        problems += [f"actions[{i}]: {e}" for e in action_errors(a)]
        kind = a.get("kind") if isinstance(a, dict) else None
        if kind in MOVE_KINDS and not policy.get("allow_move", False):
            problems.append(f"actions[{i}]: {kind} while policy.allow_move is false")
        if kind == "say" and not policy.get("allow_say", True):
            problems.append(f"actions[{i}]: say while policy.allow_say is false")
    if sum(1 for a in actions if isinstance(a, dict) and a.get("kind") == "say") > 1:
        problems.append("more than one say in a turn")
    if problems:
        raise TurnRefused(problems)
    if not actions:
        actions = [complete_action({"kind": "noop"})]
    belief = obj.get("belief")
    return actions, belief if isinstance(belief, str) else ""


# -- the model -----------------------------------------------------------------------------------------------------

def make_presence_llm(model_id: str | None, timeout_s: float):
    """The client of the presence model (an entry of config/host_models.json, usually on another host)."""
    from furry_agent.chat_common import make_llm, with_inference

    settings = ChatSettings.from_env()
    model = resolve_inference(model_id or os.environ.get("PRESENCE_MODEL", "").strip() or None)
    return make_llm(with_inference(settings, model), timeout_s), model.id


async def run_turn(body: dict, llm, timeout_s: float = TIMEOUT_S) -> dict:
    """The turn itself; ``llm`` is anything with the OpenAICompatClient ``chat`` signature."""
    world, persona, policy = body.get("world"), body.get("persona") or {}, body.get("policy") or {}
    if not isinstance(world, dict) or not isinstance(persona, dict) or not isinstance(policy, dict):
        raise ValueError("world, persona and policy must be objects")
    deadline = time.monotonic() + timeout_s
    messages = [{"role": "system", "content": system_prompt()},
                {"role": "user", "content": json.dumps({"world": world, "persona": persona, "policy": policy},
                                                       ensure_ascii=False)}]

    async def ask(msgs: list[dict]):
        left = deadline - time.monotonic()
        if left <= 0.05:
            raise asyncio.TimeoutError
        return await asyncio.wait_for(llm.chat(msgs, max_tokens=MAX_TOKENS, temperature=0.3, json_mode=True,
                                               timeout_s=left, thinking=False), left)

    reply = await ask(messages)
    if reply.tool_calls:  # presence turns have no tools
        log.info("presence: model returned tool calls; noop")
        return {"actions": [complete_action({"kind": "noop"})], "belief": "", "dropped": "tool_calls"}
    obj = parse_json_object(reply.content)
    if obj is None:
        messages += [{"role": "assistant", "content": reply.content}, {"role": "user", "content": REWRITE_JSON}]
        reply = await ask(messages)
        obj = parse_json_object(reply.content)
        if obj is None:
            raise TurnRefused(["the reply is not a JSON object"])
    try:
        actions, belief = check_turn(obj, policy)
    except TurnRefused as exc:
        if not any("max 144" in e for e in exc.errors):
            raise
        messages += [{"role": "assistant", "content": reply.content}, {"role": "user", "content": REWRITE_SHORT}]
        reply = await ask(messages)
        obj = parse_json_object(reply.content)
        if obj is None:
            raise
        actions, belief = check_turn(obj, policy)
    return {"actions": actions, "belief": belief}


# -- HTTP ----------------------------------------------------------------------------------------------------------

LLM_FACTORY: Callable[[str | None, float], tuple[Any, str]] = make_presence_llm


async def turn_endpoint(request: Request):
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return JSONResponse({"error": "body must be JSON"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "body must be an object"}, status_code=400)
    try:
        llm, model_id = LLM_FACTORY(body.get("model"), TIMEOUT_S)
    except (ModelChoiceError, CatalogError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    started = time.monotonic()
    try:
        out = await run_turn(body, llm, TIMEOUT_S)
    except ValueError as exc:
        if isinstance(exc, TurnRefused):
            log.info("presence: refused %s", exc.errors)
            return JSONResponse({"error": "contract", "errors": exc.errors}, status_code=422)
        return JSONResponse({"error": str(exc)}, status_code=400)
    except asyncio.TimeoutError:
        return JSONResponse({"error": "timeout", "actions": [complete_action({"kind": "noop"})]}, status_code=504)
    except LLMError as exc:
        return JSONResponse({"error": f"model: {exc}"}, status_code=502)
    out["model"] = model_id
    out["ms"] = round((time.monotonic() - started) * 1000)
    return JSONResponse(out)


async def health_endpoint(request: Request):
    try:
        _, model_id = LLM_FACTORY(None, TIMEOUT_S)
    except (ModelChoiceError, CatalogError) as exc:
        return JSONResponse({"status": "error", "error": str(exc)}, status_code=503)
    return JSONResponse({"status": "ok", "model": model_id, "timeout_s": TIMEOUT_S,
                         "prompt": PROMPT_PATH.exists()})


routes = [Route("/presence/turn", turn_endpoint, methods=["POST"]),
          Route("/presence/health", health_endpoint, methods=["GET"])]
