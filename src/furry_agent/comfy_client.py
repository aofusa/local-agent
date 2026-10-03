"""Minimal async client for the ComfyUI HTTP API (design doc §5).

POST /upload/image -> POST /prompt -> WebSocket /ws -> GET /history/{id} -> GET /view.
ComfyUI listens on 127.0.0.1 only; this runs on the same machine as LangGraph.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx
import websockets

log = logging.getLogger("furry_agent.comfy")


class ComfyError(RuntimeError):
    """ComfyUI rejected the prompt or the run failed."""


@dataclass
class WaitResult:
    done: bool
    outputs: dict[str, dict] = field(default_factory=dict)
    events: list[str] = field(default_factory=list)


def _queue_busy(queue: dict) -> bool:
    return bool(queue.get("queue_running")) or bool(queue.get("queue_pending"))


def _history_error(entry: dict) -> str | None:
    status = entry.get("status") or {}
    if status.get("status_str") != "error":
        return None
    for kind, data in status.get("messages") or []:
        if kind == "execution_error":
            return f"{data.get('node_type')} ({data.get('node_id')}): {data.get('exception_message', '').strip()}"
    return "ComfyUI reported an execution error"


class ComfyClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8188", timeout_s: float = 600.0):
        self.base_url = base_url.rstrip("/")
        self.ws_url = self.base_url.replace("http://", "ws://").replace("https://", "wss://") + "/ws"
        self.timeout_s = timeout_s

    def _http(self, timeout: float = 60.0) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base_url, timeout=timeout, trust_env=False)

    async def system_stats(self) -> dict:
        async with self._http(10) as http:
            r = await http.get("/system_stats")
            r.raise_for_status()
            return r.json()

    async def checkpoints(self, node_class: str = "FurryJaCheckpointLoaderAfterEject") -> list[str]:
        async with self._http(30) as http:
            r = await http.get(f"/object_info/{node_class}")
            r.raise_for_status()
            return list(r.json()[node_class]["input"]["required"]["ckpt_name"][0])

    async def queue_busy(self) -> bool:
        async with self._http(10) as http:
            r = await http.get("/queue")
            r.raise_for_status()
            return _queue_busy(r.json())

    async def wait_queue_idle(self, timeout_s: float | None = None, poll_s: float = 2.0) -> None:
        deadline = time.monotonic() + (timeout_s or self.timeout_s)
        while await self.queue_busy():
            if time.monotonic() > deadline:
                raise ComfyError("ComfyUI のキューが空きませんでした（前の生成が終わっていません）")
            await asyncio.sleep(poll_s)

    async def free(self) -> None:
        """Unload ComfyUI models and drop its cache so the next LLM load has the memory."""
        async with self._http(10) as http:
            r = await http.post("/free", json={"unload_models": True, "free_memory": True})
            r.raise_for_status()

    async def upload_image(self, data: bytes, filename: str, mime: str, subfolder: str = "furry_ja") -> str:
        files = {"image": (filename, data, mime)}
        form = {"type": "input", "subfolder": subfolder, "overwrite": "true"}
        async with self._http(60) as http:
            r = await http.post("/upload/image", files=files, data=form)
            r.raise_for_status()
            body = r.json()
        name = body["name"]
        return f"{body['subfolder']}/{name}" if body.get("subfolder") else name

    async def submit(self, prompt: dict, client_id: str) -> str:
        async with self._http(60) as http:
            r = await http.post("/prompt", json={"prompt": prompt, "client_id": client_id})
        if r.status_code != 200:
            raise ComfyError(f"/prompt rejected ({r.status_code}): {r.text[:2000]}")
        body = r.json()
        if body.get("node_errors"):
            raise ComfyError(f"/prompt node_errors: {json.dumps(body['node_errors'])[:2000]}")
        return body["prompt_id"]

    async def history(self, prompt_id: str) -> dict | None:
        async with self._http(30) as http:
            r = await http.get(f"/history/{prompt_id}")
            r.raise_for_status()
            return r.json().get(prompt_id)

    async def view(self, filename: str, subfolder: str = "", folder_type: str = "output") -> bytes:
        params = {"filename": filename, "subfolder": subfolder, "type": folder_type}
        async with self._http(60) as http:
            r = await http.get("/view", params=params)
            r.raise_for_status()
            return r.content

    async def wait(
        self,
        prompt_id: str,
        client_id: str,
        until_node: str | None = None,
        deadline: float | None = None,
        on_event: Callable[[str, dict], None] | None = None,
    ) -> WaitResult:
        """Wait on /ws until ``until_node`` has executed or the prompt finished.

        Raises ComfyError on execution errors or when the deadline (monotonic) passes.
        History is polled as a fallback so events missed between connections are not lost.
        """
        deadline = deadline or (time.monotonic() + self.timeout_s)
        result = WaitResult(done=False)

        async def check_history() -> bool:
            entry = await self.history(prompt_id)
            if not entry:
                return False
            error = _history_error(entry)
            if error:
                raise ComfyError(error)
            result.outputs.update(entry.get("outputs") or {})
            result.done = bool((entry.get("status") or {}).get("completed", True))
            return True

        async with websockets.connect(
            f"{self.ws_url}?clientId={client_id}", max_size=None, proxy=None, open_timeout=10
        ) as ws:
            if await check_history():
                return result
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ComfyError("ComfyUI の完了待ちがタイムアウトしました（10 分）")
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=min(5.0, remaining))
                except TimeoutError:
                    if await check_history():
                        return result
                    continue
                if isinstance(raw, bytes):
                    continue  # preview frames
                msg = json.loads(raw)
                kind, data = msg.get("type"), msg.get("data") or {}
                if data.get("prompt_id") not in (None, prompt_id):
                    continue
                if kind not in ("status", "progress_state"):
                    if on_event:
                        on_event(kind, data)
                if kind == "executed":
                    node = data.get("node")
                    result.outputs[node] = data.get("output") or {}
                    result.events.append(f"executed:{node}")
                    if until_node and node == until_node:
                        return result
                elif kind == "executing":
                    node = data.get("node")
                    if node is None and data.get("prompt_id") == prompt_id:
                        await check_history()
                        result.done = True
                        return result
                    result.events.append(f"executing:{node}")
                elif kind == "execution_success":
                    await check_history()
                    result.done = True
                    return result
                elif kind == "execution_error":
                    raise ComfyError(
                        f"{data.get('node_type')} ({data.get('node_id')}): {str(data.get('exception_message', '')).strip()}"
                    )
                elif kind == "execution_interrupted":
                    raise ComfyError("ComfyUI の実行が中断されました")
