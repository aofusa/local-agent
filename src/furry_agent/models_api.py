"""``GET /models`` (docs/host-model-selection-design.md §6): the models a client may pick, with what the host can use.

Mounted with /coder/turn on the LangGraph server (langgraph.json ``http.app``), so the Web UI and cirka ask the same
host they already talk to. Every entry of config/host_models.json is listed; one that cannot be used now keeps its
place with ``available: false`` and the reason (the router has no preset section for it, ComfyUI lacks its file,
HOST_MODELS_DISABLE, the server on another host does not answer or lacks the model ...). Clients send the ``id`` and show the ``label``; nothing here exposes a path.
"""

from __future__ import annotations

import asyncio
import logging
import os
from urllib.parse import urlparse

from starlette.requests import Request
from starlette.responses import JSONResponse

from furry_agent.comfy_client import ComfyClient
from furry_agent.llm_client import LlamaRouter, RemoteLLM, remote_error
from furry_agent.model_catalog import (CatalogError, HostCatalog, InferenceModel, default_ids, image_unavailable,
                                       inference_unavailable, load_catalog)
from furry_agent.templates import TemplateError, load_map

log = logging.getLogger("furry_agent.models")
PROBE_S = 4.0
# Which loader input lists the files of a family's slots.
LOADERS = {
    "sdxl": {"ckpt_name": ("FurryJaCheckpointLoaderAfterEject", "ckpt_name")},
    "dit": {"ckpt_name": ("FurryJaDiffusionLoaderAfterEject", "unet_name"),
            "clip_name": ("FurryJaDiffusionLoaderAfterEject", "clip_name"),
            "vae_name": ("FurryJaDiffusionLoaderAfterEject", "vae_name")},
}


async def _router_models(url: str) -> list[str] | None:
    try:
        return await asyncio.wait_for(LlamaRouter(url, "", PROBE_S).model_ids(PROBE_S), PROBE_S + 1)
    except Exception as exc:  # the router is down: the GGUF files decide
        log.info("models: LLM router not reachable (%s)", type(exc).__name__)
        return None


async def _remote_models(model: InferenceModel) -> list[str] | str | None:
    """The model list of an entry's server on another host, or why it could not be read (None: not asked, its URL
    or model is not set)."""
    if model.endpoint is None or model.endpoint.missing():
        return None
    url, _, key = model.endpoint.resolved()
    try:
        return await asyncio.wait_for(RemoteLLM(url, "", PROBE_S, kind=model.endpoint.kind, api_key=key)
                                      .model_ids(PROBE_S), PROBE_S + 1)
    except Exception as exc:
        log.info("models: %s not reachable (%s)", model.id, type(exc).__name__)
        return remote_error(exc)


def _remote_info(model: InferenceModel) -> dict | None:
    """What the UI shows of a model on another host: its kind, host and model name (never the API key)."""
    if model.endpoint is None:
        return None
    url, name, _ = model.endpoint.resolved()
    return {"kind": model.endpoint.kind, "host": urlparse(url).netloc if url else "", "model": name}


async def _comfy_files(comfy: ComfyClient, families: set[str]) -> tuple[dict | None, list[str] | None]:
    try:
        info = await asyncio.wait_for(comfy.object_info(max_age_s=30.0), PROBE_S * 3)
    except Exception as exc:  # ComfyUI is down: files are checked again when a run queues
        log.info("models: ComfyUI not reachable (%s)", type(exc).__name__)
        return None, None

    def choices(node: str, field: str) -> list[str]:
        try:
            return list(info[node]["input"]["required"][field][0])
        except (KeyError, IndexError, TypeError):
            return []

    out = {}
    for family in families:
        loaders = LOADERS["sdxl" if family == "sdxl" else "dit"]
        out[family] = {slot: choices(node, field) for slot, (node, field) in loaders.items()}
    loras = choices("LoraLoader", "lora_name")
    return out, loras


async def describe(catalog: HostCatalog, *, llm_url: str, comfy: ComfyClient) -> dict:
    families = {m.family for m in catalog.image.values()}
    maps = {}
    for family in families:
        try:
            maps[family] = await asyncio.to_thread(load_map, family)
        except TemplateError:
            continue
    remotes = [m for m in catalog.inference.values() if m.remote]
    router, (files, loras), *answers = await asyncio.gather(
        _router_models(llm_url), _comfy_files(comfy, families), *(_remote_models(m) for m in remotes))
    remote = {m.id: answer for m, answer in zip(remotes, answers)}
    no_inference = await asyncio.to_thread(inference_unavailable, catalog, router, remote)
    no_image = image_unavailable(catalog, maps, files, loras)
    return {
        "defaults": default_ids(catalog),
        "inference": [{"id": m.id, "label": m.label, "available": m.id not in no_inference,
                       "reason": no_inference.get(m.id, ""), "context": m.context,
                       "thinking": m.thinks, "vision": m.vision,
                       **({"remote": _remote_info(m)} if m.remote else {})} for m in catalog.inference.values()],
        "image": [{"id": m.id, "label": m.label, "family": m.family,
                   "family_label": (maps.get(m.family) or {}).get("label", m.family), "ckpt_name": m.ckpt,
                   "prompt_style": m.prompt_style, "loras": m.lora_text, "params": m.params,
                   "available": m.id not in no_image, "reason": no_image.get(m.id, "")}
                  for m in catalog.image.values()],
    }


async def models_endpoint(request: Request):
    state = request.app.state
    try:
        catalog = getattr(state, "host_catalog", None) or await asyncio.to_thread(load_catalog)
    except CatalogError as exc:
        return JSONResponse({"error": str(exc), "code": "catalog"}, status_code=500)
    llm_url = os.environ.get("LLM_URL", "").strip() or "http://127.0.0.1:8080/v1"
    comfy = getattr(state, "comfy_client", None) or ComfyClient(os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188"), 30)
    body = await describe(catalog, llm_url=getattr(state, "llm_url", None) or llm_url, comfy=comfy)
    return JSONResponse(body, headers={"Cache-Control": "no-store"})
