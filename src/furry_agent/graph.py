"""LangGraph agent (graph id ``agent``): chat input -> registered ComfyUI template -> image in chat.

LangGraph never calls the LLM router. The ComfyUI workflow calls the LLM, ejects it, and only then loads
the checkpoint, IP-Adapter and ControlNet (design doc §4). This graph:

    ingest    read the message, normalize up to 4 reference images (or the previous output) into references;
              the model family (sdxl / flux) comes from COMFY_MODEL_FAMILY
    plan      rule-based roles -> family role check -> template id -> clamped parameters, and a summary (WI §4.6)
    confirm   LangGraph interrupt when the roles are ambiguous (agent-chat-ui HITL card)
    submit    upload (deduplicated by sha256), inject through the node map, validate node types, /prompt
    await_tags / await_image   wait on ComfyUI, verify the LLM unload, save under outputs/, return the image

Reference image bytes never enter the graph state: only sha256, role, size and the ComfyUI filename (NFR-1).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import logging.handlers
import os
import queue
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.types import interrupt
from PIL import Image

from furry_agent.comfy_client import ComfyClient, ComfyError
from furry_agent.config import Settings
from furry_agent.families import FLUX, LABELS, FamilyError, check_roles, looks_like_tag_list
from furry_agent.job_lock import JobLockBusy, job_lock
from furry_agent.media import IMAGE_ROLES, MAX_IMAGES, Media, MediaError, Request, parse_request
from furry_agent.planner import (
    GenerationPlan,
    Intent,
    ReferenceImage,
    build_plan,
    classify_intent,
    resolve_roles,
    select_workflow,
    wants_previous_output,
)
from furry_agent.templates import (
    LoraSpec,
    TemplateError,
    build_run_prompt,
    load_map,
    load_template,
    model_slots,
    parse_loras,
    resolve_lora_names,
    slot_value,
    unknown_node_types,
)

log = logging.getLogger("furry_agent")

DEFAULT_TEXT_FOR_IMAGES = "参照画像の内容をもとに、同じ主題で描いてください。"
CONFIRM_ACTION = "generate_image"
_REFETCH = re.compile(r"再取得\s*([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")
# sha256 -> ComfyUI input filename, so the same image is uploaded once (WI §4.6 upload_images).
_uploaded: dict[str, str] = {}


def _setup_file_logging(logs_dir: Path) -> None:
    """Log to logs/furry_agent.log through a queue so async nodes never block on file IO."""
    if any(getattr(h, "_furry_agent", False) for h in log.handlers):
        return
    logs_dir.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(logs_dir / "furry_agent.log", encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    q: queue.Queue = queue.Queue()
    handler = logging.handlers.QueueHandler(q)
    handler._furry_agent = True  # type: ignore[attr-defined]
    listener = logging.handlers.QueueListener(q, file_handler)
    listener.start()
    log.addHandler(handler)  # furry_agent.comfy propagates here
    log.setLevel(logging.INFO)


class State(MessagesState):
    progress_id: str
    job: dict[str, Any]
    tags: dict[str, Any]
    error: str | None
    references: list[ReferenceImage]
    plan: GenerationPlan | None
    proposal: dict[str, Any] | None
    comfy_prompt_id: str | None
    outputs: list[str]


class StageError(RuntimeError):
    """An error with the stage it happened in (WI §1.3: which step failed)."""

    def __init__(self, stage: str, exc: Exception | str):
        super().__init__(str(exc))
        self.stage = stage


def _settings(config: RunnableConfig | None) -> Settings:
    configurable = (config or {}).get("configurable", {})
    return configurable.get("settings") or Settings.from_env()


def _client(config: RunnableConfig | None, settings: Settings) -> ComfyClient:
    configurable = (config or {}).get("configurable", {})
    return configurable.get("comfy_client") or ComfyClient(settings.comfyui_url, settings.timeout_s)


def _last_human(state: State) -> HumanMessage | None:
    for message in reversed(state["messages"]):
        if isinstance(message, HumanMessage) or getattr(message, "type", None) == "human":
            return message
    return None


def _request(state: State) -> Request:
    human = _last_human(state)
    if human is None:
        raise MediaError("ユーザーのメッセージが見つかりません")
    return parse_request(human.content)


def _progress(state: State, text: str) -> AIMessage:
    return AIMessage(id=state["progress_id"], content=text)


def _fail(state: State, exc: Exception, stage: str | None = None) -> dict:
    stage = getattr(exc, "stage", None) or stage
    log.error("run failed stage=%s: %s", stage, exc)
    prefix = f"［{stage}］" if stage else ""
    text = f"⚠️ 生成できませんでした{prefix}: {exc}"
    message = AIMessage(id=state.get("progress_id") or f"error-{uuid.uuid4()}", content=text)
    return {"messages": [message], "error": str(exc)}


async def _release(client: ComfyClient) -> None:
    try:
        await client.free()
    except Exception as exc:  # pragma: no cover - best effort
        log.warning("ComfyUI /free failed: %s", exc)


async def _cancel(client: ComfyClient, prompt_id: str) -> None:
    """The run was cancelled from the UI: stop the ComfyUI prompt too (FR-12)."""
    try:
        await client.interrupt(prompt_id)
        await client.free()
        log.info("cancelled prompt_id=%s (ComfyUI interrupted)", prompt_id)
    except Exception as exc:  # pragma: no cover - best effort
        log.warning("ComfyUI interrupt failed for %s: %s", prompt_id, exc)


def _route(state: State) -> str:
    return END if state.get("error") else "next"


# --- ingest ------------------------------------------------------------------------------------------


def _reference(image_id: str, media: Media, source: str = "attachment", local_path: str | None = None) -> ReferenceImage:
    return ReferenceImage(
        image_id=image_id, role=media.role, resolved_role=None, filename_on_comfy=None, sha256=media.sha256,
        width=media.width, height=media.height, strength=media.strength, source=source,
        local_path=local_path, mime=media.mime,
    )


def _configurable_roles(config: RunnableConfig | None, images: list[Media]) -> None:
    """Fallback channel when a UI cannot keep block metadata: configurable.references = [{index, role, strength}]."""
    for item in ((config or {}).get("configurable", {}).get("references") or []):
        index = int(item.get("index", 0))
        if 1 <= index <= len(images) and images[index - 1].role == "auto":
            role = str(item.get("role") or "auto")
            if role not in IMAGE_ROLES:
                raise MediaError(f"画像の役割 {role!r} は使えません")
            images[index - 1].role = role
            if item.get("strength") is not None:
                images[index - 1].strength = float(item["strength"])


def _previous_output(state: State) -> Media | None:
    for path in reversed(state.get("outputs") or []):
        file = Path(path)
        if file.exists():
            data = file.read_bytes()
            with Image.open(file) as image:
                width, height = image.size
            return Media(mime="image/png", data=data, name=file.name, role="base", width=width, height=height)
    return None


async def ingest(state: State, config: RunnableConfig) -> dict:
    await asyncio.to_thread(_setup_file_logging, _settings(config).logs_dir)
    progress_id = f"progress-{uuid.uuid4()}"
    reset = {"progress_id": progress_id, "error": None, "tags": {}, "plan": None, "proposal": None,
             "comfy_prompt_id": None, "references": []}
    settings = _settings(config)
    try:
        request = _request(state)
        _configurable_roles(config, request.images)
        refetch = _REFETCH.search(request.text)
        if refetch and not request.images:
            prompt_id = refetch.group(1)
            log.info("refetch prompt_id=%s", prompt_id)
            return {**reset, "comfy_prompt_id": prompt_id,
                    "job": {"mode": "refetch", "prompt_id": prompt_id, "client_id": uuid.uuid4().hex,
                            "seed": None, "ckpt_name": None, "refs": [], "template_id": "?"},
                    "messages": [AIMessage(id=progress_id, content=f"prompt_id {prompt_id} の結果を ComfyUI から取得しています…")]}
        references = [_reference(f"img_{i}", m) for i, m in enumerate(request.images, start=1)]
        if wants_previous_output(request.text, bool(request.images)) and not any(m.role == "base" for m in request.images):
            previous = await asyncio.to_thread(_previous_output, state)
            if previous is not None:
                if len(references) >= MAX_IMAGES:
                    raise MediaError(f"前回の生成画像を含めると {MAX_IMAGES} 枚を超えます")
                references.append(_reference("prev", previous, "previous_output", str(state["outputs"][-1])))
        if not request.text and not references:
            raise MediaError(f"日本語で描きたい内容を入力してください（画像は 0〜{MAX_IMAGES} 枚まで添付できます）。")
    except (MediaError, ValueError) as exc:
        return _fail({**state, "progress_id": progress_id}, exc, "入力")
    log.info("request family=%s refs=%s text=%s", settings.model_family,
             [(r["image_id"], r["role"], r["sha256"][:12]) for r in references], request.text)
    return {
        **reset,
        "references": references,
        "job": {"text": request.text or DEFAULT_TEXT_FOR_IMAGES, "family": settings.model_family},
        "messages": [AIMessage(id=progress_id, content=f"受け付けました（参照画像 {len(references)} 枚）。役割とテンプレートを決めています…")],
    }


def _route_ingest(state: State) -> str:
    if state.get("error"):
        return END
    return "refetch" if state["job"].get("mode") == "refetch" else "next"


# --- plan / confirm --------------------------------------------------------------------------------


def _image_label(ref: ReferenceImage) -> str:
    if ref["source"] == "previous_output":
        return "前回の生成画像"
    return f"画像{ref['image_id'].split('_')[-1]}"


def _summary(plan: GenerationPlan, references: list[ReferenceImage], roles: dict[str, str],
             ignored: dict[str, str], loras: list[str]) -> str:
    family = plan["model_family"]
    lines = [f"**モデル**: {LABELS.get(family, family)}", f"**テンプレート**: {plan['template_id']}（{family}）"]
    for ref in references:
        role = roles.get(ref["image_id"])
        if role is None:
            lines.append(f"- {_image_label(ref)}: 不使用（{ignored.get(ref['image_id'], '')}）")
            continue
        detail = ""
        if role in plan["strengths"]:
            detail = f" 強度 {plan['strengths'][role]:.2f}"
        if role == "pose":
            detail += f"（{plan['pose_preprocessor']}）"
        if role == "base" and plan["denoise"] is not None:
            detail = f" denoise {plan['denoise']:.2f}"
        lines.append(f"- {_image_label(ref)}: {role}{detail}")
    size = "参照画像に合わせる" if "base" in roles.values() else f"{plan['width']}×{plan['height']}"
    sampler = f" / {plan['sampler_name']} {plan['scheduler']}" if plan.get("sampler_name") else ""
    lines.append(f"- サイズ {size} / seed {plan['seed']} / steps {plan['steps']} / cfg {plan['cfg']}{sampler}")
    lines.append(f"- LoRA: {', '.join(loras) if loras else 'なし'}")
    lines += [f"- ※ {note}" for note in plan["notes"]]
    return "\n".join(lines)


def _family(state: State, settings: Settings) -> str:
    return (state.get("job") or {}).get("family") or settings.model_family


def _make_plan(state: State, settings: Settings, proposal_roles: dict[str, str] | None = None) -> dict:
    """Pure planning step shared by plan and confirm. Returns a state update (no messages)."""
    references = [dict(r) for r in state["references"]]
    if proposal_roles:
        for ref in references:
            ref["role"] = proposal_roles.get(ref["image_id"], ref["role"])
    family = _family(state, settings)
    family_map = load_map(family, settings.workflows_dir)  # TemplateError for an unregistered family
    intent: Intent = classify_intent(state["job"]["text"], references)
    resolution = resolve_roles(references, intent)
    # Unsupported references are refused before asking anything (Chroma: only a base image).
    checked, notes, denoise = check_roles(family_map, resolution.proposal if resolution.needs_confirmation
                                          else resolution.roles)
    if not resolution.needs_confirmation:
        resolution.roles = checked
    for ref in references:
        ref["resolved_role"] = resolution.roles.get(ref["image_id"])
    template_id = select_workflow(resolution.roles.values()) if not resolution.needs_confirmation else None
    if template_id:
        load_template(family, template_id, settings.workflows_dir)  # TemplateError when missing
    plan = build_plan(template_id or "?", family, references, resolution.roles, intent,
                      defaults=settings.plan_defaults(family, family_map.get("defaults")))
    if denoise is not None and plan["denoise"] is not None and intent.denoise is None:
        plan["denoise"] = denoise
    plan["notes"] += notes
    plan["needs_confirmation"] = resolution.needs_confirmation
    plan["confirmation_reason"] = resolution.reason
    return {"references": references, "plan": plan, "resolution": resolution}


async def plan(state: State, config: RunnableConfig) -> dict:
    settings = _settings(config)
    try:
        loras = [s.name for s in parse_loras(settings.loras_for(_family(state, settings)))]
        result = await asyncio.to_thread(_make_plan, state, settings)
    except (ValueError, TemplateError, FamilyError) as exc:
        return _fail(state, exc, "ワークフロー選択")
    resolution, plan_ = result["resolution"], result["plan"]
    if plan_["needs_confirmation"]:
        proposal = {
            "roles": resolution.proposal,
            "reason": resolution.reason,
            "labels": {r["image_id"]: _image_label(r) for r in result["references"]},
        }
        log.info("needs confirmation: %s proposal=%s", resolution.reason, resolution.proposal)
        return {"references": result["references"], "plan": plan_, "proposal": proposal,
                "messages": [_progress(state, f"画像の役割を確認させてください: {resolution.reason}")]}
    log.info("plan template=%s roles=%s seed=%d", plan_["template_id"], resolution.roles, plan_["seed"])
    summary = _summary(plan_, result["references"], resolution.roles, resolution.ignored, loras)
    return {"references": result["references"], "plan": plan_, "proposal": None,
            "job": {**state["job"], "summary": summary},
            "messages": [_progress(state, f"次の内容で生成します。\n\n{summary}\n\nComfyUI の空きを確認しています…")]}


def _route_plan(state: State) -> str:
    if state.get("error"):
        return END
    return "confirm" if state.get("proposal") else "submit"


def _decision_roles(decision: dict, proposal: dict) -> dict[str, str] | None:
    """Map the agent-chat-ui HITL decision to roles; None means the user cancelled."""
    kind = decision.get("type")
    if kind in ("reject", "ignore"):
        return None
    if kind == "edit":
        args = (decision.get("edited_action") or {}).get("args") or {}
        roles = {}
        for image_id in proposal["roles"]:
            value = str(args.get(image_id, proposal["roles"][image_id])).strip().lower()
            if value not in IMAGE_ROLES or value == "auto":
                raise ValueError(f"{proposal['labels'].get(image_id, image_id)} の役割 {value!r} は使えません"
                                 f"（{' / '.join(r for r in IMAGE_ROLES if r != 'auto')}）")
            roles[image_id] = value
        return roles
    return dict(proposal["roles"])


async def confirm(state: State, config: RunnableConfig) -> dict:
    proposal = state["proposal"]
    description = (
        f"{proposal['reason']}。\n提案した役割で実行するなら承認、変える場合は各画像の値を "
        "style / pose / character / base / mask に書き換えて送信、やめる場合は却下してください。\n"
        + "\n".join(f"- {image_id} = {proposal['labels'][image_id]}" for image_id in proposal["roles"])
    )
    response = interrupt({
        "action_requests": [{"name": CONFIRM_ACTION, "args": dict(proposal["roles"]), "description": description}],
        "review_configs": [{"action_name": CONFIRM_ACTION, "allowed_decisions": ["approve", "edit", "reject"]}],
    })
    decisions = response.get("decisions") if isinstance(response, dict) else response
    decision = (decisions or [{"type": "approve"}])[0] if isinstance(decisions, list) else {"type": "approve"}
    settings = _settings(config)
    try:
        roles = _decision_roles(decision, proposal)
        if roles is None:
            log.info("user cancelled at confirmation")
            return {"error": "cancelled", "proposal": None,
                    "messages": [_progress(state, "中止しました。役割を指定して送り直してください。")]}
        explicit_state = {**state, "references": [{**r, "role": roles[r["image_id"]]} for r in state["references"]]}
        result = await asyncio.to_thread(_make_plan, explicit_state, settings)
        if result["plan"]["needs_confirmation"]:
            raise ValueError(result["plan"]["confirmation_reason"])
    except (ValueError, TemplateError) as exc:
        return _fail(state, exc, "役割推定")
    resolution, plan_ = result["resolution"], result["plan"]
    loras = [s.name for s in parse_loras(settings.loras_for(_family(state, settings)))]
    summary = _summary(plan_, result["references"], resolution.roles, resolution.ignored, loras)
    log.info("confirmed template=%s roles=%s", plan_["template_id"], resolution.roles)
    return {"references": result["references"], "plan": plan_, "proposal": None,
            "job": {**state["job"], "summary": summary},
            "messages": [_progress(state, f"次の内容で生成します。\n\n{summary}\n\nComfyUI の空きを確認しています…")]}


# --- submit ------------------------------------------------------------------------------------------


def _reference_bytes(state: State, ref: ReferenceImage) -> Media:
    if ref["source"] == "previous_output":
        path = Path(ref["local_path"] or "")
        return Media(mime="image/png", data=path.read_bytes(), name=path.name)
    index = int(ref["image_id"].split("_")[1]) - 1
    return _request(state).images[index]


async def _upload(client: ComfyClient, media: Media, sha256: str) -> str:
    known = _uploaded.get(sha256)
    if known and await client.input_exists(known):
        return known
    name = await client.upload_image(media.data, f"ref_{sha256[:20]}.{media.extension}", media.mime)
    _uploaded[sha256] = name
    return name


async def _check_nodes(client: ComfyClient, prompt: dict, family_map: dict) -> None:
    missing = unknown_node_types(prompt, await client.node_types())
    if missing:
        hint = family_map.get("node_setup_hint") or "scripts\\setup-comfyui-refs.ps1 を実行して ComfyUI を再起動してください"
        raise TemplateError(f"この環境の ComfyUI に無いノードがあります: {', '.join(missing)}。{hint}")


async def _check_models(client: ComfyClient, settings: Settings, family: str, template: dict, entry: dict,
                        family_map: dict) -> str:
    """Every model file of the template must exist in ComfyUI (WI §5.7: name the missing files, no fallback)."""
    values = {slot: slot_value(template, path) for slot, path in model_slots(entry).items()}
    values.update({k: v for k, v in settings.model_overrides(family).items() if k in values})
    values["ckpt_name"] = settings.ckpt_for(family) or values["ckpt_name"]
    if family != FLUX and "chroma" in values["ckpt_name"].lower():
        raise TemplateError(f"{values['ckpt_name']} は Chroma1-HD のモデルです。.env の COMFY_MODEL_FAMILY=flux "
                            "も設定して LangGraph を再起動してください")
    missing = []
    for slot, path in model_slots(entry).items():
        node_id, _, field = path.split(".", 2)
        if values[slot] not in await client.checkpoints(template[node_id]["class_type"], field):
            missing.append(values[slot])
    if missing:
        if family_map.get("model_setup_hint"):
            raise ComfyError(f"{family_map.get('label', family)} のモデルファイルが ComfyUI に見つかりません: "
                             f"{', '.join(missing)}。{family_map['model_setup_hint']}")
        raise ComfyError(f"チェックポイント {', '.join(missing)} が ComfyUI に見つかりません")
    return values["ckpt_name"]


async def submit(state: State, config: RunnableConfig) -> dict:
    settings = _settings(config)
    client = _client(config, settings)
    plan_ = state["plan"]
    family = plan_["model_family"]
    stage = "キュー待ち"
    try:
        loras: list[LoraSpec] = parse_loras(settings.loras_for(family))
        # Shared with the chat tab (job_lock.py): the other job is waited for while it works (its holder renews
        # the lease; a holder that died frees the lock when the lease runs out). JOB_LOCK_TIMEOUT_S, when set,
        # bounds the wait for a chat run.
        lock_limit = os.environ.get("JOB_LOCK_TIMEOUT_S", "").strip()
        token = await job_lock.acquire("image", float(lock_limit) if lock_limit else None, None)
        try:
            # One generation at a time: never queue while ComfyUI is still busy (waited for while ComfyUI answers;
            # that run has its own idle timeout).
            await client.wait_queue_idle()
            # Drop ComfyUI's cached models so the 27B has the shared memory.
            await client.free()
            await asyncio.sleep(2.0)
            stage = "ワークフロー注入"
            template, entry = await asyncio.to_thread(load_template, family, plan_["template_id"],
                                                      settings.workflows_dir)
            family_map = await asyncio.to_thread(load_map, family, settings.workflows_dir)
            await _check_nodes(client, template, family_map)
            ckpt_name = await _check_models(client, settings, family, template, entry, family_map)
            if loras:
                loras = resolve_lora_names(loras, await client.loras())
            stage = "アップロード"
            images, references = {}, []
            for ref in state["references"]:
                ref = dict(ref)
                if ref["resolved_role"]:
                    media = await asyncio.to_thread(_reference_bytes, state, ref)
                    ref["filename_on_comfy"] = await _upload(client, media, ref["sha256"])
                    images[ref["resolved_role"]] = ref["filename_on_comfy"]
                references.append(ref)
            stage = "ワークフロー注入"
            prompt = await asyncio.to_thread(build_run_prompt, family, plan_, images, state["job"]["text"],
                                             ckpt_name, loras, settings.workflows_dir,
                                             settings.model_overrides(family),
                                             llm_read_timeout_s=settings.timeout_s)
            await _check_nodes(client, prompt, family_map)
            stage = "キュー投入"
            client_id = uuid.uuid4().hex
            prompt_id = await client.submit(prompt, client_id)
        finally:
            job_lock.release(token)
    except JobLockBusy as exc:
        return _fail(state, exc, stage)
    except (ComfyError, MediaError, TemplateError, OSError, ValueError) as exc:
        return _fail(state, exc, stage)
    except Exception as exc:  # httpx / websockets errors
        return _fail(state, ComfyError(f"ローカルの ComfyUI（{settings.comfyui_url}）に接続できません: {exc!r}"), stage)

    job = {**state["job"], "mode": plan_["template_id"], "template_id": plan_["template_id"], "prompt_id": prompt_id,
           "client_id": client_id, "seed": plan_["seed"], "ckpt_name": ckpt_name, "family": family,
           "loras": [f"{s.name}:{s.strength_model}" for s in loras],
           "refs": [(r["image_id"], r["resolved_role"], r["filename_on_comfy"]) for r in references]}
    log.info("submitted prompt_id=%s family=%s template=%s seed=%d steps=%s cfg=%s refs=%s loras=%s", prompt_id,
             family, plan_["template_id"], plan_["seed"], plan_["steps"], plan_["cfg"], job["refs"], job["loras"])
    vision = "参照画像の役割別タグ付け → " if images else ""
    making = "英語の説明文" if family == FLUX else "タグ"
    return {
        "job": job,
        "references": references,
        "comfy_prompt_id": prompt_id,
        "messages": [_progress(state, (
            f"ComfyUI に投入しました（prompt_id {prompt_id}）。\n\n{job.get('summary', '')}\n\n"
            f"{vision}Qwen3.8 27B（llama.cpp）で{making}を生成中です。モデルのロードを含め数分かかります…"
        ))],
    }


# --- wait ----------------------------------------------------------------------------------------------


def _timeout_hint(exc: Exception, job: dict) -> Exception:
    if "タイムアウト" in str(exc):
        return StageError("タイムアウト", f"{exc}。prompt_id {job['prompt_id']}。完了後に「再取得 {job['prompt_id']}」と送ると結果を取得できます")
    return exc


async def await_tags(state: State, config: RunnableConfig) -> dict:
    settings = _settings(config)
    client = _client(config, settings)
    job = state["job"]
    try:
        # Idle timeout (AGENT_IDLE_TIMEOUT_S): every event of the prompt restarts it.
        result = await client.wait(job["prompt_id"], job["client_id"], until_node="split", idle_s=settings.timeout_s)
    except asyncio.CancelledError:
        await asyncio.shield(_cancel(client, job["prompt_id"]))
        raise
    except Exception as exc:
        await _release(client)
        return _fail(state, _timeout_hint(exc, job), "生成")
    split = result.outputs.get("split") or {}
    tags = {
        "positive": (split.get("positive") or [""])[0],
        "negative": (split.get("negative") or [""])[0],
        "split_mode": (split.get("split_mode") or ["?"])[0],
    }
    log.info("tags prompt_id=%s mode=%s positive=%s", job["prompt_id"], tags["split_mode"], tags["positive"])
    warnings = list(job.get("warnings") or [])
    chroma = job.get("family") == FLUX
    if chroma and looks_like_tag_list(tags["positive"]):
        warnings.append("LLM が説明文ではなくタグ列を返しました。Chroma1-HD では品質が落ちることがあります")
    if chroma and tags["split_mode"] == "fallback":
        warnings.append("LLM の出力が JSON ではなかったため、生の出力を positive にしています")
    made = "英語の説明文を生成しました" if chroma else "タグを生成しました"
    return {
        "tags": tags,
        # ckpt usually runs before split, so keep its unload check for the image phase.
        "job": {**job, "done": result.done, "gate": result.outputs.get("ckpt") or {}, "warnings": warnings},
        "messages": [_progress(state, (
            f"{made}。LLM サーバのモデルを unload してから画像を生成しています…\n\n"
            f"**positive**: {tags['positive']}\n\n**negative**: {tags['negative']}"
        ))],
    }


def _save_outputs(outputs_dir: Path, job: dict, tags: dict, plan_: dict | None, name: str, data: bytes) -> Path:
    outputs_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = outputs_dir / f"{stamp}_{job['prompt_id'][:8]}_{name}"
    path.write_bytes(data)
    meta = {k: job.get(k) for k in ("prompt_id", "seed", "family", "template_id", "ckpt_name", "refs", "loras")}
    if plan_:
        meta.update({k: plan_.get(k) for k in ("strengths", "denoise", "pose_preprocessor", "width", "height",
                                               "steps", "cfg", "sampler_name", "scheduler")})
    path.with_suffix(".json").write_text(json.dumps({**meta, **tags}, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _image_block(data: bytes, name: str) -> dict:
    return {"type": "image", "mimeType": "image/png", "data": base64.b64encode(data).decode("ascii"),
            "metadata": {"name": name}}


async def await_image(state: State, config: RunnableConfig) -> dict:
    settings = _settings(config)
    client = _client(config, settings)
    job = state["job"]
    gate: dict = dict(job.get("gate") or {})

    def on_event(kind: str, data: dict) -> None:
        if kind == "executed" and data.get("node") == "ckpt":
            gate.update(data.get("output") or {})
            log.info("ckpt gate: LLM router unloaded=%s forced_unload=%s",
                     gate.get("llm_unloaded"), gate.get("forced_unload"))
        elif kind == "executing" and data.get("node") == "sampler":
            log.info("KSampler started prompt_id=%s; LLM router unloaded at checkpoint load=%s",
                     job["prompt_id"], gate.get("llm_unloaded"))

    saved, blocks = [], []
    try:
        result = await client.wait(job["prompt_id"], job["client_id"], on_event=on_event,
                                   idle_s=settings.timeout_s)
        outputs = result.outputs
        ckpt_ui = outputs.get("ckpt") or gate
        if ckpt_ui.get("llm_unloaded") != [True]:
            raise ComfyError("チェックポイント読み込み前に LLM サーバ（llama.cpp）の unload を確認できませんでした")
        log.info("eject verified prompt_id=%s: the LLM router had no model loaded before checkpoint/KSampler",
                 job["prompt_id"])
        images = (outputs.get("save") or {}).get("images") or []
        if not images:
            raise ComfyError("Save Image の出力が見つかりません")
        for image in images:
            data = await client.view(image["filename"], image.get("subfolder", ""), image.get("type", "output"))
            path = await asyncio.to_thread(_save_outputs, settings.outputs_dir, job, state.get("tags", {}),
                                           state.get("plan"), image["filename"], data)
            saved.append((image, path))
            blocks.append(_image_block(data, image["filename"]))
        # Pose templates also return the extracted skeleton/depth so a failed extraction is visible.
        for image in (outputs.get("pose_preview") or {}).get("images") or []:
            data = await client.view(image["filename"], image.get("subfolder", ""), image.get("type", "temp"))
            blocks.append(_image_block(data, f"pose_{image['filename']}"))
    except asyncio.CancelledError:
        await asyncio.shield(_cancel(client, job["prompt_id"]))
        raise
    except Exception as exc:
        await _release(client)
        return _fail(state, _timeout_hint(exc, job), "生成")
    await _release(client)  # release the checkpoint before the next LLM load

    for image, path in saved:
        log.info("saved prompt_id=%s comfy=%s/%s local=%s", job["prompt_id"], image.get("subfolder"),
                 image["filename"], path)
    tags = state.get("tags", {})
    comfy_file = "/".join(p for p in (saved[0][0].get("subfolder"), saved[0][0]["filename"]) if p)
    pose_note = "\n\n2 枚目はポーズ参照から抽出した ControlNet 入力です。" if len(blocks) > len(saved) else ""
    warnings = "".join(f"\n\n⚠️ {w}" for w in job.get("warnings") or [])
    text = (
        f"生成しました（{job.get('template_id', '?')}、seed {job.get('seed')}、{job.get('ckpt_name')}）。{warnings}\n\n"
        f"{job.get('summary', '')}\n\n"
        f"**positive**: {tags.get('positive', '')}\n\n**negative**: {tags.get('negative', '')}\n\n"
        f"保存先: ComfyUI output/{comfy_file}、リポジトリ outputs/{saved[0][1].name}\n\n"
        f"この画像を続けて直すときは「さっきの画像を…」と送ってください。{pose_note}"
    )
    return {
        "outputs": [*(state.get("outputs") or []), *(str(p) for _, p in saved)][-20:],
        "messages": [AIMessage(id=state["progress_id"], content=[{"type": "text", "text": text}, *blocks])],
    }


builder = StateGraph(State)
builder.add_node("ingest", ingest)
builder.add_node("plan", plan)
builder.add_node("confirm", confirm)
builder.add_node("submit", submit)
builder.add_node("await_tags", await_tags)
builder.add_node("await_image", await_image)
builder.add_edge(START, "ingest")
builder.add_conditional_edges("ingest", _route_ingest, {"next": "plan", "refetch": "await_image", END: END})
builder.add_conditional_edges("plan", _route_plan, {"confirm": "confirm", "submit": "submit", END: END})
builder.add_conditional_edges("confirm", _route, {"next": "submit", END: END})
builder.add_conditional_edges("submit", _route, {"next": "await_tags", END: END})
builder.add_conditional_edges("await_tags", _route, {"next": "await_image", END: END})
builder.add_edge("await_image", END)

graph = builder.compile()
graph.name = "furry_ja agent"
