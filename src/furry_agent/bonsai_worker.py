"""Search readers: start a PrismML llama-server, let the model open result pages, kill the process.

Lifecycle (design doc §5.5): a worker is started for one search branch on 127.0.0.1:<18181+n>, and stopped on
completion, failure, timeout or cancellation. Stopping kills the process tree by PID (no unload API) and then
checks the port; anything still listening there is killed again. Every live process is in a registry so the
chat graph can verify that none is left before it releases the job lock.

Searching itself is plain Python (search_client); a reader model only chooses which result pages to open
(an ``open_page`` tool call, at most two rounds) and extracts fact cards for the question. The tool runs here,
in the orchestrator, which owns the Tor socket and the URL policy. A URL already opened by another reader in
the same run is not fetched again.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from furry_agent.llm_client import OpenAICompatClient
from furry_agent.search_agent import focus_text

log = logging.getLogger("furry_agent.bonsai")

HOST = "127.0.0.1"
MAX_TOOL_ROUNDS = 2
PAGE_TIMEOUT_S = 20.0
PAGE_CHARS = 1200
OPEN_PAGE_TOOL = {
    "type": "function",
    "function": {
        "name": "open_page",
        "description": "Open one of the search results and return its text.",
        "parameters": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "a URL from the result list"}},
            "required": ["url"],
        },
    },
}


class WorkerError(RuntimeError):
    pass


_live: dict[int, "LlamaServer"] = {}


def live_pids() -> list[int]:
    return [pid for pid, server in _live.items() if server.alive]


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex((HOST, port)) == 0


def pids_on_port(port: int) -> list[int]:
    """PIDs listening on 127.0.0.1:<port> (Windows netstat; empty elsewhere)."""
    if sys.platform != "win32":
        return []
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True, timeout=10,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    pids = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[3].upper() == "LISTENING" and parts[1].endswith(f":{port}"):
            pids.add(int(parts[4]))
    return sorted(pids)


def kill_pid(pid: int) -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=15,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    else:  # pragma: no cover - the target machine is Windows
        try:
            os.kill(pid, 9)
        except OSError:
            pass


@dataclass
class LlamaServer:
    exe: str
    model_path: Path
    port: int
    ctx: int = 4096
    ngl: int = 99
    label: str = ""
    logs_dir: Path | None = None
    process: subprocess.Popen | None = field(default=None, repr=False)
    boot_s: float = 0.0
    # Per-process key: the server listens on loopback with CORS open, so a web page in a local browser could
    # otherwise reach it while a search runs. /health stays public.
    api_key: str = field(default_factory=lambda: secrets.token_urlsafe(24), repr=False)

    @property
    def base_url(self) -> str:
        return f"http://{HOST}:{self.port}/v1"

    @property
    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def command(self) -> list[str]:
        cmd = [self.exe, "-m", str(self.model_path), "--host", HOST, "--port", str(self.port),
               "-c", str(self.ctx), "-np", "1", "-ngl", str(self.ngl), "--no-webui", "--jinja",
               "--reasoning", "off", "--cache-ram", "0", "--api-key", self.api_key]
        if self.ngl > 0:
            cmd += ["-fa", "on"]  # PTQ1_0 needs flash attention for its Hadamard transform
        if self.logs_dir:
            cmd += ["--log-file", str(Path(self.logs_dir) / f"llama-server-{self.port}.log")]
        return cmd

    def client(self, timeout_s: float = 120.0) -> OpenAICompatClient:
        return OpenAICompatClient(self.base_url, timeout_s=timeout_s, api_key=self.api_key)

    async def start(self, timeout_s: float = 120.0) -> float:
        # File and socket checks run in a thread: langgraph dev fails runs that block the event loop.
        if not self.exe or not await asyncio.to_thread(Path(self.exe).is_file):
            raise WorkerError("PrismML 版 llama-server がありません（scripts\\setup-llamacpp.ps1 を実行してください）")
        if await asyncio.to_thread(port_in_use, self.port):
            raise WorkerError(f"ポート {self.port} は使用中です")
        if self.logs_dir:
            await asyncio.to_thread(Path(self.logs_dir).mkdir, parents=True, exist_ok=True)
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        started = time.monotonic()
        self.process = await asyncio.to_thread(
            subprocess.Popen, self.command(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, creationflags=flags)
        _live[self.process.pid] = self
        log.info("llama-server start pid=%s port=%s model=%s ngl=%s", self.process.pid, self.port, self.label, self.ngl)
        try:
            async with httpx.AsyncClient(timeout=3.0, trust_env=False) as http:
                while True:
                    if not self.alive:
                        raise WorkerError(f"{self.label} の llama-server が起動直後に終了しました（logs\\llama-server-{self.port}.log）")
                    try:
                        if (await http.get(f"http://{HOST}:{self.port}/health")).status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    if time.monotonic() - started > timeout_s:
                        raise WorkerError(f"{self.label} の起動が {timeout_s:.0f} 秒以内に終わりませんでした")
                    await asyncio.sleep(0.5)
        except BaseException:
            await asyncio.shield(self.stop())
            raise
        self.boot_s = time.monotonic() - started
        log.info("llama-server ready pid=%s port=%s boot_s=%.1f", self.process.pid, self.port, self.boot_s)
        return self.boot_s

    async def stop(self) -> None:
        process = self.process
        if process is not None:
            if process.poll() is None:
                await asyncio.to_thread(kill_pid, process.pid)
                try:
                    await asyncio.to_thread(process.wait, 10)
                except subprocess.TimeoutExpired:
                    process.kill()
            _live.pop(process.pid, None)
            log.info("llama-server stopped pid=%s port=%s", process.pid, self.port)
        # The port must be free afterwards; kill whatever still listens there (design doc §5.5).
        for _ in range(3):
            if not await asyncio.to_thread(port_in_use, self.port):
                return
            for pid in await asyncio.to_thread(pids_on_port, self.port):
                log.warning("killing leftover listener pid=%s on port %s", pid, self.port)
                await asyncio.to_thread(kill_pid, pid)
            await asyncio.sleep(0.5)


async def kill_all() -> list[int]:
    """Stop every llama-server this process started. Returns the PIDs that were still alive."""
    alive = [server for server in list(_live.values())]
    pids = [s.process.pid for s in alive if s.process is not None and s.alive]
    await asyncio.gather(*(s.stop() for s in alive), return_exceptions=True)
    return pids


def free_port(start: int, taken: set[int] | None = None) -> int:
    port = start
    taken = taken or set()
    while port in taken or port_in_use(port):
        port += 1
    return port


# --- the reader conversation ----------------------------------------------------------------------------------


class Ledger:
    """Queries and URLs already used in this search run (shared by the parallel branches)."""

    def __init__(self) -> None:
        self._seen: set[str] = set()

    @staticmethod
    def key(value: str) -> str:
        return " ".join(value.lower().split()).rstrip("/")

    def seen(self, value: str) -> bool:
        return self.key(value) in self._seen

    def claim(self, value: str) -> bool:
        """True when the value is new and is now reserved for the caller."""
        key = self.key(value)
        if not key or key in self._seen:
            return False
        self._seen.add(key)
        return True


def fence(text: str) -> str:
    """Fetched text is data, not instructions: keep it inside a fence (design doc §5.10)."""
    return "```text\n" + (text or "").replace("```", "'''") + "\n```"


def hits_block(hits: list[dict]) -> str:
    body = "\n\n".join(f"[{i}] {h.get('title', '')}\nURL: {h['url']}\n抜粋: {h.get('snippet', '')}"
                       for i, h in enumerate(hits, start=1))
    return fence(body or "（結果 0 件）")


def requested_url(reply, hits: list[dict]) -> tuple[str | None, bool]:
    """The URL the model asked to open: a real tool call, else a listed URL or [n] in the text."""
    urls = [h["url"] for h in hits]
    for call in reply.tool_calls:
        if call.name == "open_page":
            url = str(call.arguments.get("url", "")).strip()
            if url in urls:
                return url, True
            number = url.strip("[]")
            if number.isdigit() and 1 <= int(number) <= len(urls):
                return urls[int(number) - 1], True
    content = reply.content or ""
    for url in urls:
        if url in content:
            return url, True
    return None, False


async def _fetch(search, url: str):
    try:
        return await asyncio.wait_for(search.fetch(url), PAGE_TIMEOUT_S)
    except (TimeoutError, asyncio.TimeoutError):
        log.info("page fetch over %.0fs: %s", PAGE_TIMEOUT_S, url)
        return None


async def run_reader(intent: dict, hits: list[dict], client: OpenAICompatClient, search, ledger: Ledger,
                     system_prompt: str, question: str, ask_cards, browse_budget_s: float = 75.0) -> dict:
    """One Grok-style reader for one search intent.

    ``ask_cards(client, messages)`` returns validated fact cards (schema check and one retry, see
    search_agent.ask_json). Opening pages stops after ``browse_budget_s`` so the card extraction always runs.
    Pages are cut to the lines relevant to the question (search_agent.focus_text) before the model sees them.
    Returns {"pages": [...], "cards": [...], "tool_call": bool, "opened": [...]}.
    """
    deadline = time.monotonic() + browse_budget_s
    focus = f"{question} {intent.get('q', '')} {intent.get('why', '')}"
    out: dict = {"pages": [], "cards": [], "tool_call": False, "opened": []}
    messages = [{"role": "system", "content": system_prompt},
                {"role": "user", "content": (
                    f"質問: {question}\n調べる意図: {intent.get('why') or intent.get('q', '')}\n\n"
                    f"検索結果:\n{hits_block(hits)}\n\n"
                    "質問に答えるのに最も役立つ結果を open_page で開いてください（最大 2 ページ）。")}]
    for round_no in range(1, MAX_TOOL_ROUNDS + 1):
        if time.monotonic() > deadline:
            break
        reply = await client.chat(messages, tools=[OPEN_PAGE_TOOL], tool_choice="auto", max_tokens=96,
                                  temperature=0.1)
        url, from_model = requested_url(reply, hits)
        out["tool_call"] = out["tool_call"] or from_model
        if url is None and round_no == 1:
            # No usable tool call: the orchestrator opens the top result that nobody opened yet.
            url = next((h["url"] for h in hits if not ledger.seen(h["url"])), None)
        if url is None or not ledger.claim(url):
            break
        page = await _fetch(search, url)
        out["opened"].append(url)
        focused = focus_text(page.text, focus, PAGE_CHARS) if page else ""
        if page:
            out["pages"].append({"url": url, "title": page.title, "text": page.text[:3000]})
        call_id = f"call_{intent.get('id', 0)}_{round_no}"
        text = f"{page.title}\n{focused}" if page else "（このページは取得できませんでした）"
        messages.append({"role": "assistant", "content": "", "tool_calls": [{
            "id": call_id, "type": "function",
            "function": {"name": "open_page", "arguments": json.dumps({"url": url})}}]})
        messages.append({"role": "tool", "tool_call_id": call_id, "content": fence(text)})
        if round_no < MAX_TOOL_ROUNDS:
            messages.append({"role": "user", "content": (
                "もう 1 ページ開く必要があれば open_page を呼び、十分なら「十分」とだけ答えてください。")})
    messages.append({"role": "user", "content": (
        "開いたページと検索結果の抜粋から、質問に関係する事実・数値・日付・反証だけを URL ごとに抜き出してください。"
        "全文の要約はしないこと。")})
    out["cards"] = await ask_cards(client, messages)
    return out
