"""Read and enforce the llama.cpp router's loaded-model state (llama-server --models-preset, scripts/start-llm.ps1).

Used by the `eject` node and the checkpoint gate so that the checkpoint is only loaded once the router holds no
LLM in memory (AGENTS.md: the 27B and the checkpoint never co-reside). The router lists its models at GET /models
(``status.value``: unloaded / loading / loaded / ...) and stops a model's child process on POST /models/unload;
the stop is asynchronous, so the state is polled until nothing is resident.
"""

from __future__ import annotations

import json
import time
import urllib.request
from urllib.parse import urlparse

# Status values whose child process still holds memory.
RESIDENT = ("loaded", "loading", "unloading", "sleeping")


def native_base(base_url: str) -> str:
    """http://127.0.0.1:8080/v1 -> http://127.0.0.1:8080 (the router's model API is not under /v1)."""
    parsed = urlparse(base_url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _request(url: str, payload: dict | None = None, timeout: float = 15.0) -> dict:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
    return json.loads(body) if body else {}


def resident_models(models_payload: dict) -> list[str]:
    """Ids of the models whose child process exists."""
    found = []
    for entry in models_payload.get("data") or models_payload.get("models") or []:
        status = entry.get("status") or {}
        value = status.get("value") if isinstance(status, dict) else status
        if value in RESIDENT and entry.get("id"):
            found.append(entry["id"])
    return found


def ensure_unloaded(base_url: str, wait_s: float = 60.0, fetch=_request, sleep=time.sleep,
                    clock=time.monotonic) -> dict:
    """Unload every model the router still holds and wait until none remain.

    Returns a report dict. Raises ``RuntimeError`` if a model is still resident after ``wait_s`` seconds, so the
    checkpoint is never loaded next to the LLM.
    """
    base = native_base(base_url)
    unloaded: list[str] = []
    deadline = clock() + wait_s
    while True:
        resident = resident_models(fetch(f"{base}/models"))
        if not resident:
            return {"llm_loaded": [], "forced_unload": unloaded, "verified_unloaded": True}
        if clock() > deadline:
            break
        for model in resident:
            if model not in unloaded:
                fetch(f"{base}/models/unload", {"model": model})
                unloaded.append(model)
        sleep(1.0)
    raise RuntimeError(f"the LLM router still has loaded models before checkpoint load: {resident}")
