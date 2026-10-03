"""LangGraph agent (graph id ``agent``): chat input -> ComfyUI furry_ja workflow -> image in chat.

LangGraph never calls LM Studio. The ComfyUI workflow calls the LLM, ejects it,
and only then loads the checkpoint (design doc §4). This graph uploads media,
submits the API workflow, waits on ComfyUI, saves the image under outputs/ and
returns the image bytes as a chat content block.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import logging.handlers
import queue
import random
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, MessagesState, StateGraph

from furry_agent.comfy_client import ComfyClient, ComfyError
from furry_agent.config import Settings
from furry_agent.media import MediaError, Request, parse_request
from furry_agent.workflow import build_prompt, load_workflow

log = logging.getLogger("furry_agent")

DEFAULT_TEXT_FOR_IMAGES = "参照画像の内容をもとに、同じ主題で描いてください。"
_submit_lock: asyncio.Lock | None = None


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
    log.addHandler(handler)
    log.setLevel(logging.INFO)


class State(MessagesState):
    progress_id: str
    job: dict[str, Any]
    tags: dict[str, Any]
    error: str | None


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
    request = parse_request(human.content)
    if not request.text and request.images:
        request.text = DEFAULT_TEXT_FOR_IMAGES
    if not request.text:
        raise MediaError("日本語で描きたい内容を入力してください（画像は 0〜2 枚まで添付できます）。")
    return request


def _progress(state: State, text: str) -> AIMessage:
    return AIMessage(id=state["progress_id"], content=text)


def _fail(state: State, exc: Exception) -> dict:
    log.error("run failed: %s", exc)
    text = f"⚠️ 生成できませんでした: {exc}"
    message = AIMessage(id=state.get("progress_id") or f"error-{uuid.uuid4()}", content=text)
    return {"messages": [message], "error": str(exc)}


async def _fail_and_free(state: State, exc: Exception, client: ComfyClient) -> dict:
    """Report the error and release whatever ComfyUI loaded (e.g. the checkpoint) before failing."""
    try:
        await client.free()
    except Exception as free_exc:  # pragma: no cover - best effort
        log.warning("ComfyUI /free failed: %s", free_exc)
    return _fail(state, exc)


def _route(state: State) -> str:
    return END if state.get("error") else "next"


async def ingest(state: State, config: RunnableConfig) -> dict:
    await asyncio.to_thread(_setup_file_logging, _settings(config).logs_dir)
    progress_id = f"progress-{uuid.uuid4()}"
    try:
        request = _request(state)
    except MediaError as exc:
        return _fail({**state, "progress_id": progress_id}, exc)
    mode = "img2img" if request.images else "txt2img"
    log.info("request mode=%s refs=%d text=%s", mode, len(request.images), request.text)
    return {
        "progress_id": progress_id,
        "error": None,
        "tags": {},
        "job": {"mode": mode, "n_refs": len(request.images)},
        "messages": [AIMessage(
            id=progress_id,
            content=f"受け付けました（{mode}、参照画像 {len(request.images)} 枚）。ComfyUI の空きを確認しています…",
        )],
    }


async def submit(state: State, config: RunnableConfig) -> dict:
    global _submit_lock
    settings = _settings(config)
    client = _client(config, settings)
    try:
        request = _request(state)
        workflow = await asyncio.to_thread(load_workflow, settings.workflow_path)
        if _submit_lock is None:
            _submit_lock = asyncio.Lock()
        async with _submit_lock:
            # One generation at a time: never queue while ComfyUI is still busy.
            await client.wait_queue_idle(settings.timeout_s)
            # Drop ComfyUI's cached checkpoint so the 27B has the shared memory.
            await client.free()
            await asyncio.sleep(2.0)
            ckpt_name = settings.ckpt_name or workflow["ckpt"]["inputs"]["ckpt_name"]
            available = await client.checkpoints()
            if ckpt_name not in available:
                raise ComfyError(f"チェックポイント {ckpt_name} が ComfyUI に見つかりません")
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            ref_names = []
            for i, media in enumerate(request.images):
                ref_names.append(await client.upload_image(
                    media.data, f"ref_{stamp}_{uuid.uuid4().hex[:6]}_{i + 1}.{media.extension}", media.mime
                ))
            seed = random.randint(0, 2**32 - 1)
            prompt = build_prompt(workflow, request.text, seed, ref_names, ckpt_name)
            client_id = uuid.uuid4().hex
            prompt_id = await client.submit(prompt, client_id)
    except (ComfyError, MediaError, OSError, ValueError) as exc:
        return _fail(state, exc)
    except Exception as exc:  # httpx / websockets errors
        return _fail(state, ComfyError(f"ComfyUI に接続できません: {exc!r}"))

    deadline = time.time() + settings.timeout_s
    job = {**state["job"], "prompt_id": prompt_id, "client_id": client_id, "seed": seed,
           "ckpt_name": ckpt_name, "refs": ref_names, "deadline": deadline}
    log.info("submitted prompt_id=%s seed=%d mode=%s refs=%s", prompt_id, seed, job["mode"], ref_names)
    steps = "参照画像のタグ付け → " if ref_names else ""
    return {
        "job": job,
        "messages": [_progress(state, (
            f"ComfyUI に投入しました（{job['mode']}、seed {seed}）。\n\n"
            f"{steps}LM Studio の Qwen3.8 27B でタグを生成中です。モデルのロードを含め数分かかります…"
        ))],
    }


def _monotonic_deadline(job: dict) -> float:
    return time.monotonic() + max(1.0, job["deadline"] - time.time())


async def await_tags(state: State, config: RunnableConfig) -> dict:
    settings = _settings(config)
    client = _client(config, settings)
    job = state["job"]
    try:
        result = await client.wait(job["prompt_id"], job["client_id"], until_node="split",
                                   deadline=_monotonic_deadline(job))
    except Exception as exc:
        return await _fail_and_free(state, exc, client)
    split = result.outputs.get("split") or {}
    tags = {
        "positive": (split.get("positive") or [""])[0],
        "negative": (split.get("negative") or [""])[0],
        "split_mode": (split.get("split_mode") or ["?"])[0],
    }
    log.info("tags prompt_id=%s mode=%s positive=%s", job["prompt_id"], tags["split_mode"], tags["positive"])
    return {
        "tags": tags,
        # ckpt usually runs before split, so keep its unload check for the image phase.
        "job": {**job, "done": result.done, "gate": result.outputs.get("ckpt") or {}},
        "messages": [_progress(state, (
            "タグを生成しました。LM Studio のモデルを unload してから、yiffInHell で画像を生成しています…\n\n"
            f"**positive**: {tags['positive']}\n\n**negative**: {tags['negative']}"
        ))],
    }


def _save_outputs(outputs_dir: Path, job: dict, tags: dict, name: str, data: bytes) -> Path:
    outputs_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = outputs_dir / f"{stamp}_{job['prompt_id'][:8]}_{name}"
    path.write_bytes(data)
    meta = {k: job.get(k) for k in ("prompt_id", "seed", "mode", "ckpt_name", "refs")}
    path.with_suffix(".json").write_text(json.dumps({**meta, **tags}, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


async def await_image(state: State, config: RunnableConfig) -> dict:
    settings = _settings(config)
    client = _client(config, settings)
    job = state["job"]
    gate: dict = dict(job.get("gate") or {})

    def on_event(kind: str, data: dict) -> None:
        if kind == "executed" and data.get("node") == "ckpt":
            gate.update(data.get("output") or {})
            log.info("ckpt gate: LM Studio unloaded=%s forced_unload=%s",
                     gate.get("lmstudio_unloaded"), gate.get("forced_unload"))
        elif kind == "executing" and data.get("node") == "sampler":
            log.info("KSampler started prompt_id=%s; LM Studio unloaded at checkpoint load=%s",
                     job["prompt_id"], gate.get("lmstudio_unloaded"))

    try:
        result = await client.wait(job["prompt_id"], job["client_id"],
                                   deadline=_monotonic_deadline(job), on_event=on_event)
        outputs = result.outputs
        ckpt_ui = outputs.get("ckpt") or gate
        if ckpt_ui.get("lmstudio_unloaded") != [True]:
            raise ComfyError("チェックポイント読み込み前に LM Studio の unload を確認できませんでした")
        log.info("eject verified prompt_id=%s: LM Studio had no model loaded before checkpoint/KSampler",
                 job["prompt_id"])
        images = (outputs.get("save") or {}).get("images") or []
        if not images:
            raise ComfyError("Save Image の出力が見つかりません")
        blocks, saved = [], []
        for image in images:
            data = await client.view(image["filename"], image.get("subfolder", ""), image.get("type", "output"))
            path = await asyncio.to_thread(_save_outputs, settings.outputs_dir, job, state.get("tags", {}),
                                           image["filename"], data)
            saved.append((image, path))
            blocks.append({
                "type": "image",
                "mimeType": "image/png",
                "data": base64.b64encode(data).decode("ascii"),
                "metadata": {"name": image["filename"]},
            })
    except Exception as exc:
        return _fail(state, exc)
    finally:
        try:
            await client.free()  # release the checkpoint before the next LLM load
        except Exception as exc:  # pragma: no cover - best effort
            log.warning("ComfyUI /free failed: %s", exc)

    for image, path in saved:
        log.info("saved prompt_id=%s comfy=%s/%s local=%s", job["prompt_id"], image.get("subfolder"),
                 image["filename"], path)
    tags = state.get("tags", {})
    comfy_file = "/".join(p for p in (saved[0][0].get("subfolder"), saved[0][0]["filename"]) if p)
    text = (
        f"生成しました（{job['mode']}、seed {job['seed']}、{job['ckpt_name']}）。\n\n"
        f"**positive**: {tags.get('positive', '')}\n\n**negative**: {tags.get('negative', '')}\n\n"
        f"保存先: ComfyUI output/{comfy_file}、リポジトリ outputs/{saved[0][1].name}"
    )
    return {"messages": [AIMessage(id=state["progress_id"], content=[{"type": "text", "text": text}, *blocks])]}


builder = StateGraph(State)
builder.add_node("ingest", ingest)
builder.add_node("submit", submit)
builder.add_node("await_tags", await_tags)
builder.add_node("await_image", await_image)
builder.add_edge(START, "ingest")
builder.add_conditional_edges("ingest", _route, {"next": "submit", END: END})
builder.add_conditional_edges("submit", _route, {"next": "await_tags", END: END})
builder.add_conditional_edges("await_tags", _route, {"next": "await_image", END: END})
builder.add_edge("await_image", END)

graph = builder.compile()
graph.name = "furry_ja agent"
