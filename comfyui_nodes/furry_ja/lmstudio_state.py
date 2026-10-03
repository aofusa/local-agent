"""Read and enforce LM Studio's loaded-model state through its native REST API.

Used by the checkpoint gate so that the checkpoint is only loaded once LM Studio
holds no LLM in memory (AGENTS.md: the 27B and the checkpoint never co-reside).
"""

from __future__ import annotations

import json
import time
import urllib.request
from urllib.parse import urlparse


def native_base(base_url: str) -> str:
    parsed = urlparse(base_url)
    return f"{parsed.scheme}://{parsed.netloc}/api/v1"


def _request(url: str, payload: dict | None = None, timeout: float = 15.0) -> dict:
    data = None
    headers = {"Authorization": "Bearer lm-studio"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
    return json.loads(body) if body else {}


def loaded_instances(models_payload: dict) -> list[tuple[str, str]]:
    """Return ``(model_key, instance_id)`` for every loaded LLM/VLM (embeddings are ignored)."""
    entries = models_payload.get("models") or models_payload.get("data") or []
    found = []
    for entry in entries:
        if entry.get("type") not in (None, "llm", "vlm"):
            continue
        key = entry.get("key") or entry.get("id") or "?"
        for inst in entry.get("loaded_instances") or []:
            inst_id = inst.get("id") if isinstance(inst, dict) else inst
            if inst_id:
                found.append((key, inst_id))
    return found


def ensure_unloaded(base_url: str, retries: int = 3, fetch=_request) -> dict:
    """Unload any LLM still resident in LM Studio and verify that none remain.

    Returns a report dict. Raises ``RuntimeError`` if a model is still loaded
    after the retries, so the checkpoint is never loaded next to the LLM.
    """
    base = native_base(base_url)
    unloaded: list[str] = []
    for attempt in range(retries + 1):
        instances = loaded_instances(fetch(f"{base}/models"))
        if not instances:
            return {"lmstudio_loaded": [], "forced_unload": unloaded, "verified_unloaded": True}
        if attempt == retries:
            break
        for _key, inst_id in instances:
            fetch(f"{base}/models/unload", {"instance_id": inst_id})
            unloaded.append(inst_id)
        time.sleep(1.0)
    raise RuntimeError(
        f"LM Studio still has loaded models before checkpoint load: {[i for _, i in instances]}"
    )
