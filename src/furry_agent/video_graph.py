"""LangGraph graph ``video`` (docs/image-to-video-design.md §6): one start image and a Japanese instruction -> a short
video from Wan 2.2 I2V in ComfyUI -> the video in chat, served by LangGraph under /media/video.

LangGraph never calls the LLM router: the ComfyUI workflow writes the English motion prompt, ejects the 27B, then loads
umT5, the high-noise and the low-noise expert one at a time with release nodes in between. This graph:

    ingest        exactly one image (the start frame) and the instruction (empty = subtle motion); the video model is
                  the catalog entry named by configurable.video_model, else DEFAULT_VIDEO_MODEL (config/host_models.json)
    plan          size (the image's aspect ratio within VIDEO_MAX_PIXELS), length (configurable.video_seconds, else
                  VIDEO_SECONDS, at most VIDEO_MAX_SECONDS), seed, and a summary
    submit        job lock, ComfyUI /free, nodes / files / LoRAs checked, start image uploaded, template filled, /prompt
    await_prompt  until `split` has run: the motion prompt
    await_video   until SaveVideo has run: the loaders' router checks and the release reports verified, the mp4 copied
                  to outputs/video/<uuid>.mp4 and returned as a /media/video URL

Like the image tab, the job lock is held until the run is queued; while ComfyUI works, the chat tab and /coder/turn
wait for its queue to empty, and the image tab waits for it before it queues.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, MessagesState, StateGraph

from furry_agent.comfy_client import ComfyClient, ComfyError
from furry_agent.config import Settings
from furry_agent.graph import _setup_file_logging  # the same logs/furry_agent.log as the other tabs
from furry_agent.job_lock import JobLockBusy, job_lock
from furry_agent.media import Media, MediaError, parse_request
from furry_agent.media_api import download_name, videos_dir
from furry_agent.model_catalog import (
    EXPERTS,
    VIDEO_LOADERS,
    CatalogError,
    HostCatalog,
    ModelChoiceError,
    load_catalog,
    resolve_video,
    video_files,
    video_nodes,
)
from furry_agent.templates import TemplateError, build_video_prompt, load_map, resolve_lora_names, unknown_node_types
from furry_agent.video_plan import (
    DEFAULT_INSTRUCTION,
    VideoRequestError,
    VideoSettings,
    plan_video,
    requested_seconds,
)

log = logging.getLogger("furry_agent")

TEMPLATE_ID = "i2v"
LOADER_NODES = ("te", "unet_high", "unet_low")
RELEASE_NODES = ("release_te", "release_high", "release_low")
_REFETCH = re.compile(r"再取得\s*([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")
# sha256 -> ComfyUI input filename, so the same start image is uploaded once.
_uploaded: dict[str, str] = {}


class State(MessagesState):
    progress_id: str
    job: dict[str, Any]
    prompt: dict[str, Any]
    error: str | None
    outputs: list[str]


def _settings(config: RunnableConfig | None) -> Settings:
    configurable = (config or {}).get("configurable", {})
    return configurable.get("settings") or Settings.from_env()


def _video_settings(config: RunnableConfig | None) -> VideoSettings:
    return (config or {}).get("configurable", {}).get("video_settings") or VideoSettings.from_env()


def _host_catalog(config: RunnableConfig | None) -> HostCatalog:
    return (config or {}).get("configurable", {}).get("host_catalog") or load_catalog()


def _client(config: RunnableConfig | None, settings: Settings) -> ComfyClient:
    configurable = (config or {}).get("configurable", {})
    return configurable.get("comfy_client") or ComfyClient(settings.comfyui_url, settings.timeout_s)


def model_info(job: dict) -> dict:
    """additional_kwargs of the reply: which video model made it (the UI shows the label under the message)."""
    if not job.get("video_model"):
        return {}
    return {"model_info": {"kind": "video", "id": job["video_model"], "label": job.get("video_label", "")}}


def _last_human(state: State) -> HumanMessage | None:
    for message in reversed(state["messages"]):
        if isinstance(message, HumanMessage) or getattr(message, "type", None) == "human":
            return message
    return None


def _start_image(state: State) -> Media:
    human = _last_human(state)
    if human is None:
        raise MediaError("ユーザーのメッセージが見つかりません")
    images = parse_request(human.content).images
    if len(images) != 1:
        raise MediaError("開始画像が見つかりません")
    return images[0]


def _progress(state: State, text: str) -> AIMessage:
    return AIMessage(id=state["progress_id"], content=text)


def _fail(state: State, exc: Exception, stage: str | None = None) -> dict:
    log.error("video run failed stage=%s: %s", stage, exc)
    prefix = f"［{stage}］" if stage else ""
    message = AIMessage(id=state.get("progress_id") or f"error-{uuid.uuid4()}",
                        content=f"⚠️ 動画を作れませんでした{prefix}: {exc}")
    return {"messages": [message], "error": str(exc)}


async def _release(client: ComfyClient) -> None:
    try:
        await client.free()
    except Exception as exc:  # pragma: no cover - best effort
        log.warning("ComfyUI /free failed: %s", exc)


async def _cancel(client: ComfyClient, prompt_id: str) -> None:
    """The run was cancelled from the UI: stop the ComfyUI prompt too."""
    try:
        await client.interrupt(prompt_id)
        await client.free()
        log.info("cancelled video prompt_id=%s (ComfyUI interrupted)", prompt_id)
    except Exception as exc:  # pragma: no cover - best effort
        log.warning("ComfyUI interrupt failed for %s: %s", prompt_id, exc)


def _timeout_hint(exc: Exception, job: dict) -> Exception:
    if "タイムアウト" in str(exc):
        return ComfyError(f"{exc}。prompt_id {job['prompt_id']}。完了後に「再取得 {job['prompt_id']}」と送ると結果を取得できます")
    return exc


def _route(state: State) -> str:
    return END if state.get("error") else "next"


# --- ingest / plan ----------------------------------------------------------------------------------------------


async def ingest(state: State, config: RunnableConfig) -> dict:
    settings = _settings(config)
    await asyncio.to_thread(_setup_file_logging, settings.logs_dir)
    configurable = (config or {}).get("configurable", {})
    progress_id = f"progress-{uuid.uuid4()}"
    reset = {"progress_id": progress_id, "error": None, "prompt": {}, "job": {}}
    failed = {**state, "progress_id": progress_id}
    try:
        human = _last_human(state)
        if human is None:
            raise MediaError("ユーザーのメッセージが見つかりません")
        request = parse_request(human.content)
        refetch = _REFETCH.search(request.text)
        if refetch and not request.images:
            prompt_id = refetch.group(1)
            log.info("video refetch prompt_id=%s", prompt_id)
            return {**reset, "job": {"mode": "refetch", "prompt_id": prompt_id, "client_id": uuid.uuid4().hex},
                    "messages": [AIMessage(id=progress_id, content=f"prompt_id {prompt_id} の動画を ComfyUI から取得しています…")]}
        if not request.images:
            raise MediaError("動かしたい画像を 1 枚添付してください（画像タブで作った画像は「この画像を動画にする」で渡せます）。")
        if len(request.images) > 1:
            raise MediaError(f"動画タブで添付できる画像は 1 枚だけです（{len(request.images)} 枚添付されています）。"
                             "開始画像にする 1 枚だけを付けて送り直してください")
        seconds = requested_seconds(configurable.get("video_seconds"))
    except (MediaError, VideoRequestError) as exc:
        return _fail(failed, exc, "入力")
    try:
        model = await asyncio.to_thread(resolve_video, configurable.get("video_model"), _host_catalog(config))
    except (ModelChoiceError, CatalogError) as exc:
        return _fail(failed, exc, "モデル選択")
    image = request.images[0]
    # The instruction itself stays out of the log (its length only).
    log.info("video request model=%s image=%s %dx%d seconds=%s instruction_chars=%d", model.id, image.sha256[:12],
             image.width, image.height, seconds, len(request.text))
    return {
        **reset,
        "job": {"text": request.text or DEFAULT_INSTRUCTION, "video_model": model.id, "video_label": model.label,
                "family": model.family, "image_sha": image.sha256, "image_size": [image.width, image.height],
                "seconds_requested": seconds},
        "messages": [AIMessage(id=progress_id, content=f"受け付けました（{model.label}）。大きさと長さを決めています…")],
    }


def _route_ingest(state: State) -> str:
    if state.get("error"):
        return END
    return "refetch" if state["job"].get("mode") == "refetch" else "next"


async def plan(state: State, config: RunnableConfig) -> dict:
    settings = _settings(config)
    job = state["job"]
    try:
        model = _host_catalog(config).video[job["video_model"]]
        family_map = await asyncio.to_thread(load_map, model.family, settings.workflows_dir)
        params = {**(family_map.get("defaults") or {}), **model.params}
        run = plan_video(params, tuple(job["image_size"]), _video_settings(config), job.get("seconds_requested"))
    except (TemplateError, VideoRequestError, KeyError, ValueError) as exc:
        return _fail(state, exc, "計画")
    files = video_files(model, family_map)
    loras = {expert: [f"{s.name}:{s.strength_model:g}" for s in model.loras_for(expert)] for expert in EXPERTS}
    summary = (
        f"動画モデル: {model.label}（High {files['high_name']} / Low {files['low_name']}）\n\n"
        f"大きさ: {run['width']}×{run['height']}、{run['frames']} フレーム（約 {run['seconds']:g} 秒、{run['fps']:g} fps）、"
        f"seed {run['seed']}\n\n"
        f"サンプリング: {run['steps']} ステップ（High {run['split_step']} / Low {run['steps'] - run['split_step']}）、"
        f"cfg {run['cfg']:g}、shift {run['shift']:g}、{run['sampler_name']} / {run['scheduler']}\n\n"
        f"LoRA: High {', '.join(loras['high']) or 'なし'} / Low {', '.join(loras['low']) or 'なし'}"
    )
    notes = "".join(f"\n\n{note}" for note in run["notes"])
    return {
        "job": {**job, "plan": run, "files": files, "models": {**(family_map.get("models") or {}), **model.models},
                "loras": loras, "summary": summary},
        "messages": [_progress(state, f"{summary}{notes}\n\nComfyUI に投入します…")],
    }


# --- submit -----------------------------------------------------------------------------------------------------


async def _upload(client: ComfyClient, media: Media) -> str:
    known = _uploaded.get(media.sha256)
    if known and await client.input_exists(known):
        return known
    name = await client.upload_image(media.data, f"start_{media.sha256[:20]}.{media.extension}", media.mime)
    _uploaded[media.sha256] = name
    return name


async def _check_files(client: ComfyClient, job: dict, family_map: dict) -> None:
    """Every model file of the run must exist in ComfyUI: name the missing ones, never fall back."""
    missing = []
    for slot, name in job["files"].items():
        node_class, field = VIDEO_LOADERS[slot]
        if name not in await client.checkpoints(node_class, field):
            missing.append(name)
    if missing:
        raise ComfyError(f"{family_map.get('label', job['family'])} のモデルファイルが ComfyUI に見つかりません: "
                         f"{', '.join(missing)}。{family_map.get('model_setup_hint', '')}")


async def submit(state: State, config: RunnableConfig) -> dict:
    settings = _settings(config)
    client = _client(config, settings)
    job = state["job"]
    run = job["plan"]
    stage = "キュー待ち"
    try:
        model = _host_catalog(config).video[job["video_model"]]
        lock_limit = os.environ.get("JOB_LOCK_TIMEOUT_S", "").strip()
        token = await job_lock.acquire("video", float(lock_limit) if lock_limit else None, None)
        try:
            # One generation at a time: never queue while ComfyUI is still busy, and drop its cached models so the
            # 27B (the first model of the workflow) has the shared memory.
            await client.wait_queue_idle()
            await client.free()
            await asyncio.sleep(2.0)
            stage = "ノード"
            family_map = await asyncio.to_thread(load_map, job["family"], settings.workflows_dir)
            known = await client.node_types()
            missing = sorted(video_nodes(model) - known)
            if missing:
                raise TemplateError(f"この環境の ComfyUI に無いノードがあります: {', '.join(missing)}。"
                                    f"{family_map.get('node_setup_hint', '')}")
            stage = "モデル"
            await _check_files(client, job, family_map)
            available = await client.loras()
            loras = {expert: resolve_lora_names(model.loras_for(expert), available) for expert in EXPERTS}
            stage = "アップロード"
            image_name = await _upload(client, await asyncio.to_thread(_start_image, state))
            stage = "ワークフロー注入"
            files = job["files"]
            values = {
                "prompt": job["text"], "start_image": image_name, "seed": run["seed"],
                "high_name": files["high_name"], "low_name": files["low_name"], "clip_name": files.get("clip_name"),
                "vae_name": files.get("vae_name"), "weight_dtype": job["models"].get("weight_dtype"),
                "te_device": run["te_device"], "width": run["width"], "height": run["height"],
                "length": run["frames"], "steps": run["steps"], "split_step": run["split_step"], "cfg": run["cfg"],
                "sampler_name": run["sampler_name"], "scheduler": run["scheduler"], "shift": run["shift"],
                "prompt_prefix": run["prompt_prefix"], "negative": run["negative"], "vae_tile": run["vae_tile"],
                "vae_overlap": run["vae_overlap"], "vae_temporal": run["vae_temporal"],
                "vae_temporal_overlap": run["vae_temporal_overlap"], "fps": run["fps"], "crf": run["crf"],
            }
            prompt = await asyncio.to_thread(build_video_prompt, job["family"], TEMPLATE_ID, values, loras["high"],
                                             loras["low"], settings.workflows_dir, settings.timeout_s)
            unknown = unknown_node_types(prompt, known)
            if unknown:
                raise TemplateError(f"この環境の ComfyUI に無いノードがあります: {', '.join(unknown)}。"
                                    f"{family_map.get('node_setup_hint', '')}")
            stage = "キュー投入"
            client_id = uuid.uuid4().hex
            prompt_id = await client.submit(prompt, client_id)
        finally:
            job_lock.release(token)
    except JobLockBusy as exc:
        return _fail(state, exc, stage)
    except (ComfyError, MediaError, TemplateError, OSError, ValueError, KeyError) as exc:
        return _fail(state, exc, stage)
    except Exception as exc:  # httpx / websockets errors
        return _fail(state, ComfyError(f"ローカルの ComfyUI（{settings.comfyui_url}）に接続できません: {exc!r}"), stage)
    applied = {expert: [f"{s.name}:{s.strength_model:g}" for s in loras[expert]] for expert in EXPERTS}
    log.info("video submitted prompt_id=%s model=%s size=%dx%d frames=%d fps=%g seed=%d loras=%s", prompt_id,
             job["video_model"], run["width"], run["height"], run["frames"], run["fps"], run["seed"], applied)
    return {
        "job": {**job, "prompt_id": prompt_id, "client_id": client_id, "loras": applied},
        "messages": [_progress(state, (
            f"ComfyUI に投入しました（prompt_id {prompt_id}）。\n\n{job['summary']}\n\n"
            "LLM（llama.cpp）が開始画像を見て、動きの説明を書いています。モデルのロードを含め数分かかります…"
        ))],
    }


# --- wait -------------------------------------------------------------------------------------------------------


async def await_prompt(state: State, config: RunnableConfig) -> dict:
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
        return _fail(state, _timeout_hint(exc, job), "動きの説明")
    split = result.outputs.get("split") or {}
    prompt = {"positive": (split.get("positive") or [""])[0], "negative": (split.get("negative") or [""])[0],
              "split_mode": (split.get("split_mode") or ["?"])[0]}
    log.info("video prompt prompt_id=%s mode=%s positive=%s", job["prompt_id"], prompt["split_mode"], prompt["positive"])
    warnings = []
    if prompt["split_mode"] == "fallback":
        warnings.append("LLM の出力が JSON ではなかったため、生の出力を positive にしています")
    return {
        "prompt": prompt,
        "job": {**job, "warnings": warnings},
        "messages": [_progress(state, (
            "動きの説明を作りました。LLM サーバのモデルを unload してから、umT5 → High → Low の順に 1 つずつ読み込んで"
            "動画を作っています（この端末では十数分以上かかります）…\n\n"
            f"**positive**: {prompt['positive']}\n\n**negative**: {prompt['negative']}"
        ))],
    }


def _save_video(folder: Path, job: dict, prompt: dict, comfy_file: str, data: bytes) -> Path:
    """The repository's copy under a random name (served by /media/video) and its conditions next to it."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{uuid.uuid4().hex}.mp4"
    path.write_bytes(data)
    meta = {k: job.get(k) for k in ("prompt_id", "video_model", "family", "loras", "files")}
    meta.update({"created": datetime.now().isoformat(timespec="seconds"), "comfy_file": comfy_file,
                 **{k: v for k, v in (job.get("plan") or {}).items() if k != "notes"}, **prompt})
    path.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _check_order(outputs: dict, reports: dict) -> list[str]:
    """Verify the one-model-at-a-time order from the nodes' UI outputs: every loader saw the LLM router empty (an
    error), and no diffusion model was still alive at a release (a warning: it was offloaded, not freed)."""
    for node in LOADER_NODES:
        if (outputs.get(node) or reports.get(node) or {}).get("llm_unloaded") != [True]:
            raise ComfyError(f"{node} の読み込み前に LLM サーバ（llama.cpp）の unload を確認できませんでした")
    warnings = []
    for node in RELEASE_NODES:
        kept = (outputs.get(node) or reports.get(node) or {}).get("diffusion_before") or []
        if kept:
            warnings.append(f"{node} の時点で拡散モデル（{', '.join(kept)}）が残っていました。ComfyUI を --cache-none で"
                            "起動してください（scripts の start-comfyui は付けています）")
    return warnings


async def await_video(state: State, config: RunnableConfig) -> dict:
    settings = _settings(config)
    client = _client(config, settings)
    job = state["job"]
    reports: dict[str, dict] = {}

    def on_event(kind: str, data: dict) -> None:
        node = data.get("node")
        if kind == "executed" and node in LOADER_NODES + RELEASE_NODES:
            output = data.get("output") or {}
            reports[node] = output
            if node in RELEASE_NODES:
                log.info("video %s prompt_id=%s alive_before=%s diffusion_before=%s free_vram=%s free_ram=%s", node,
                         job["prompt_id"], output.get("alive_before"), output.get("diffusion_before"),
                         output.get("free_vram_gb"), output.get("free_ram_gb"))
            else:
                log.info("video %s loaded prompt_id=%s; LLM router unloaded=%s", node, job["prompt_id"],
                         output.get("llm_unloaded"))
        elif kind == "executing" and node in ("sampler_high", "sampler_low"):
            log.info("video %s started prompt_id=%s", node, job["prompt_id"])

    folder = videos_dir(settings.outputs_dir)
    try:
        result = await client.wait(job["prompt_id"], job["client_id"], on_event=on_event, idle_s=settings.timeout_s)
        outputs = result.outputs
        warnings = list(job.get("warnings") or [])
        if job.get("mode") != "refetch":
            warnings += _check_order(outputs, reports)
        saved = (outputs.get("save") or {}).get("images") or []
        if not saved:
            raise ComfyError("SaveVideo の出力（mp4）が見つかりません")
        item = saved[0]
        data = await client.view(item["filename"], item.get("subfolder", ""), item.get("type", "output"))
        path = await asyncio.to_thread(_save_video, folder, job, state.get("prompt") or {}, item["filename"], data)
    except asyncio.CancelledError:
        await asyncio.shield(_cancel(client, job["prompt_id"]))
        raise
    except Exception as exc:
        await _release(client)
        return _fail(state, _timeout_hint(exc, job), "生成")
    await _release(client)  # drop whatever ComfyUI still holds before the next 27B load

    run = job.get("plan") or {}
    prompt = state.get("prompt") or {}
    comfy_file = "/".join(p for p in (item.get("subfolder"), item["filename"]) if p)
    log.info("video saved prompt_id=%s comfy=%s local=%s bytes=%d", job["prompt_id"], comfy_file, path, len(data))
    warning_text = "".join(f"\n\n⚠️ {w}" for w in warnings)
    if run:
        head = (f"動画を作りました（{job.get('video_label')}、{run['width']}×{run['height']}、{run['frames']} フレーム・"
                f"約 {run['seconds']:g} 秒、seed {run['seed']}）。")
    else:
        head = f"動画を取得しました（prompt_id {job['prompt_id']}）。"
    text = (f"{head}{warning_text}\n\n{job.get('summary', '')}\n\n"
            f"**positive**: {prompt.get('positive', '')}\n\n**negative**: {prompt.get('negative', '')}\n\n"
            f"保存先: ComfyUI output/{comfy_file}、リポジトリ outputs/video/{path.name}")
    metadata = {"name": download_name(path)}
    if run:
        metadata.update(width=run["width"], height=run["height"], frames=run["frames"], fps=run["fps"],
                        seconds=run["seconds"])
    block = {"type": "video", "mimeType": "video/mp4", "url": f"/media/video/{path.name}", "metadata": metadata}
    return {
        "outputs": [*(state.get("outputs") or []), str(path)][-20:],
        "messages": [AIMessage(id=state["progress_id"], content=[{"type": "text", "text": text.strip()}, block],
                               additional_kwargs=model_info(job))],
    }


builder = StateGraph(State)
builder.add_node("ingest", ingest)
builder.add_node("plan", plan)
builder.add_node("submit", submit)
builder.add_node("await_prompt", await_prompt)
builder.add_node("await_video", await_video)
builder.add_edge(START, "ingest")
builder.add_conditional_edges("ingest", _route_ingest, {"next": "plan", "refetch": "await_video", END: END})
builder.add_conditional_edges("plan", _route, {"next": "submit", END: END})
builder.add_conditional_edges("submit", _route, {"next": "await_prompt", END: END})
builder.add_conditional_edges("await_prompt", _route, {"next": "await_video", END: END})
builder.add_edge("await_video", END)

graph = builder.compile()
graph.name = "furry_ja video"
