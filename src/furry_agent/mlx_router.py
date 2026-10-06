"""A model router for macOS that prefers MLX: the llama.cpp router's API in front of one child server at a time.

    python -m furry_agent.mlx_router --preset tools/llm/models.ini --port 8080

The preset is the one scripts/setup-llm writes (llama-server --models-preset format). A section with
``engine = mlx`` runs ``mlx_lm.server`` on its MLX folder (Apple silicon, Metal through MLX); any other section runs
``llama-server`` (the PrismML fork, Metal) with the section's keys as options. Clients see the same API as
llama-server in router mode, so ComfyUI's eject node, the chat tab and /coder/turn need no change:

    GET  /v1/models, /models     every section with status.value loaded | loading | unloaded
    POST /models/load            {"model": id}
    POST /models/unload          {"model": id}   stops the child process: its memory is free again
    POST /v1/chat/completions    loads the section named by "model" (stopping any other: one model at a time)
    GET  /health

For an MLX child the router also does what llama-server's ``--reasoning-format deepseek`` and ``reasoning = off``
do: thinking is off unless the request turns it on (chat_template_kwargs.enable_thinking), and ``<think>`` text is
moved to ``reasoning_content`` so it never ends up in the answer. Loopback only, like the llama.cpp router.
"""

from __future__ import annotations

import argparse
import asyncio
import configparser
import contextlib
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
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

log = logging.getLogger("furry_agent.mlx_router")
HOST = "127.0.0.1"
CHILD_BASE_PORT = 18300
# llama-server keys that the MLX server takes under another name (the rest of the section is not passed to MLX).
MLX_SAMPLING = {"temp": "temperature", "top-p": "top_p", "top-k": "top_k", "min-p": "min_p",
                "repeat-penalty": "repetition_penalty", "presence-penalty": "presence_penalty"}
# Router-only keys (never passed to llama-server as options).
ROUTER_KEYS = {"engine", "sleep-idle-seconds"}


@dataclass
class Section:
    id: str
    settings: dict[str, str]

    @property
    def engine(self) -> str:
        return self.settings.get("engine", "llamacpp").strip().lower()

    @property
    def thinking_default(self) -> bool:
        return self.settings.get("reasoning", "off").strip().lower() not in ("off", "false", "0")


def read_preset(path: Path) -> list[Section]:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str  # keep the option names as written
    # The keys before the first section ("version = 1") are the preset's own, not a model's.
    parser.read_string("[__preset__]\n" + Path(path).read_text(encoding="utf-8"))
    return [Section(name, {k: v.strip() for k, v in parser[name].items()}) for name in parser.sections()
            if name != "__preset__"]


def llama_args(section: Section, exe: str, port: int, api_key: str) -> list[str]:
    args = [exe, "--host", HOST, "--port", str(port), "--api-key", api_key, "--alias", section.id]
    for key, value in section.settings.items():
        if key in ROUTER_KEYS:
            continue
        if key == "model":
            args += ["-m", value]
        elif value.lower() == "true":
            args.append(f"--{key}")
        elif value.lower() == "false":
            continue
        else:
            args += [f"--{key}", value]
    return args


def mlx_args(section: Section, python: str, port: int) -> list[str]:
    # mlx_lm.server: --model, --host, --port; sampling defaults come with each request (see sampling()).
    return [python, "-m", "mlx_lm", "server", "--model", section.settings["model"], "--host", HOST, "--port", str(port)]


def sampling(section: Section) -> dict:
    """The section's sampling as OpenAI request fields (an MLX child gets them on every request it does not set)."""
    out = {}
    for key, name in MLX_SAMPLING.items():
        if key in section.settings:
            with contextlib.suppress(ValueError):
                out[name] = float(section.settings[key]) if key != "top-k" else int(section.settings[key])
    return out


def free_port(start: int) -> int:
    for port in range(start, start + 200):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            if sock.connect_ex((HOST, port)) != 0:
                return port
    raise RuntimeError("空きポートがありません")


class ThinkSplitter:
    """Moves ``<think>...</think>`` from streamed content to reasoning_content (an MLX child returns it inline)."""

    def __init__(self, thinking_open: bool = False):
        self.inside = thinking_open
        self.buffer = ""

    def feed(self, text: str) -> tuple[str, str]:
        self.buffer += text
        answer, thought = "", ""
        while self.buffer:
            tag = "</think>" if self.inside else "<think>"
            index = self.buffer.find(tag)
            if index < 0:
                # Keep a partial tag at the end for the next chunk.
                keep = next((n for n in range(len(tag) - 1, 0, -1) if self.buffer.endswith(tag[:n])), 0)
                chunk, self.buffer = self.buffer[:len(self.buffer) - keep], self.buffer[len(self.buffer) - keep:]
                if self.inside:
                    thought += chunk
                else:
                    answer += chunk
                break
            if self.inside:
                thought += self.buffer[:index]
            else:
                answer += self.buffer[:index]
            self.buffer = self.buffer[index + len(tag):]
            self.inside = not self.inside
        return answer, thought


def rewrite_chunk(chunk: dict, splitter: ThinkSplitter, show_thinking: bool) -> dict:
    for choice in chunk.get("choices") or []:
        for part in ("delta", "message"):
            message = choice.get(part)
            if not isinstance(message, dict):
                continue
            if message.get("reasoning") and not message.get("reasoning_content"):
                message["reasoning_content"] = message.pop("reasoning")
            content = message.get("content")
            if isinstance(content, str) and content:
                answer, thought = splitter.feed(content)
                message["content"] = answer
                if thought and show_thinking:
                    message["reasoning_content"] = (message.get("reasoning_content") or "") + thought
    return chunk


@dataclass
class Child:
    section: Section
    process: subprocess.Popen
    port: int
    api_key: str = ""
    ready: bool = False
    started: float = field(default_factory=time.monotonic)

    @property
    def url(self) -> str:
        return f"http://{HOST}:{self.port}"


class Router:
    def __init__(self, sections: list[Section], *, llama_server: str, mlx_python: str, logs_dir: Path,
                 load_timeout_s: float = 1200.0):
        self.sections = {s.id: s for s in sections}
        self.llama_server, self.mlx_python, self.logs_dir = llama_server, mlx_python, logs_dir
        self.child: Child | None = None
        self.lock = asyncio.Lock()
        self.load_timeout_s = load_timeout_s
        self.last_request = time.monotonic()

    def status(self, model_id: str) -> str:
        if self.child and self.child.section.id == model_id and self.child.process.poll() is None:
            return "loaded" if self.child.ready else "loading"
        return "unloaded"

    def models(self) -> dict:
        return {"object": "list", "data": [
            {"id": s.id, "object": "model", "owned_by": s.engine, "status": {"value": self.status(s.id)}}
            for s in self.sections.values()]}

    async def stop(self) -> list[str]:
        child, self.child = self.child, None
        if child is None:
            return []
        child.process.terminate()
        try:
            await asyncio.to_thread(child.process.wait, 20)
        except subprocess.TimeoutExpired:
            child.process.kill()
            await asyncio.to_thread(child.process.wait, 10)
        log.info("unloaded %s", child.section.id)
        return [child.section.id]

    async def ensure(self, model_id: str) -> Child:
        section = self.sections.get(model_id)
        if section is None:
            raise KeyError(model_id)
        async with self.lock:
            if self.child and self.child.section.id == model_id and self.child.process.poll() is None:
                if self.child.ready:
                    return self.child
            else:
                await self.stop()  # one model at a time (llama-server --models-max 1)
                self.child = await asyncio.to_thread(self._spawn, section)
            await self._wait_ready(self.child)
            return self.child

    def _spawn(self, section: Section) -> Child:
        port = free_port(CHILD_BASE_PORT)
        api_key = secrets.token_hex(16)
        if section.engine == "mlx":
            argv = mlx_args(section, self.mlx_python, port)
            api_key = ""
        else:
            argv = llama_args(section, self.llama_server, port, api_key)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        log_file = open(self.logs_dir / f"llm-router-{section.id}.log", "ab")  # noqa: SIM115 - owned by the child
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log_file, stderr=subprocess.STDOUT)
        log.info("loading %s (%s) pid=%s port=%s", section.id, section.engine, process.pid, port)
        return Child(section, process, port, api_key)

    async def _wait_ready(self, child: Child) -> None:
        deadline = time.monotonic() + self.load_timeout_s
        path = "/health" if child.section.engine != "mlx" else "/v1/models"
        async with httpx.AsyncClient(timeout=5, trust_env=False) as http:
            while time.monotonic() < deadline:
                if child.process.poll() is not None:
                    self.child = None
                    raise RuntimeError(f"{child.section.id} のサーバが終了しました（logs/llm-router-{child.section.id}.log）")
                with contextlib.suppress(httpx.HTTPError):
                    r = await http.get(child.url + path, headers=self._headers(child))
                    if r.status_code == 200:
                        child.ready = True
                        log.info("loaded %s in %.1fs", child.section.id, time.monotonic() - child.started)
                        return
                await asyncio.sleep(0.5)
        await self.stop()
        raise RuntimeError(f"{child.section.id} を {self.load_timeout_s:.0f} 秒以内に読み込めませんでした")

    @staticmethod
    def _headers(child: Child) -> dict:
        return {"Authorization": f"Bearer {child.api_key}"} if child.api_key else {}

    def prepare(self, child: Child, body: dict) -> tuple[dict, bool]:
        """The request as the child expects it; returns (body, thinking requested)."""
        body = dict(body)
        kwargs = dict(body.get("chat_template_kwargs") or {})
        thinking = bool(kwargs.get("enable_thinking", child.section.thinking_default))
        if child.section.engine == "mlx":
            kwargs["enable_thinking"] = thinking
            body["chat_template_kwargs"] = kwargs
            for name, value in sampling(child.section).items():
                body.setdefault(name, value)
            body["model"] = child.section.settings["model"]
        return body, thinking

    async def idle_sleeper(self) -> None:
        """Unload after sleep-idle-seconds without a request (llama-server's --sleep-idle-seconds)."""
        while True:
            await asyncio.sleep(10)
            child = self.child
            if not child:
                continue
            limit = float(child.section.settings.get("sleep-idle-seconds", "0") or 0)
            if limit > 0 and time.monotonic() - self.last_request > limit and not self.lock.locked():
                await self.stop()


def make_app(router: Router) -> Starlette:
    async def models(request: Request):
        return JSONResponse(router.models())

    async def health(request: Request):
        return JSONResponse({"status": "ok"})

    async def load(request: Request):
        model = (await request.json()).get("model", "")
        try:
            await router.ensure(model)
        except KeyError:
            return JSONResponse({"error": f"unknown model {model}"}, status_code=404)
        except RuntimeError as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)
        return JSONResponse({"success": True})

    async def unload(request: Request):
        model = (await request.json()).get("model", "")
        if router.child and router.child.section.id == model:
            await router.stop()
        return JSONResponse({"success": True})

    async def completions(request: Request):
        body = await request.json()
        router.last_request = time.monotonic()
        model = body.get("model") or next(iter(router.sections), "")
        try:
            child = await router.ensure(model)
        except KeyError:
            return JSONResponse({"error": {"message": f"model {model!r} is not in the preset"}}, status_code=404)
        except RuntimeError as exc:
            return JSONResponse({"error": {"message": str(exc)}}, status_code=500)
        body, thinking = router.prepare(child, body)
        path = request.url.path
        mlx = child.section.engine == "mlx"
        client = httpx.AsyncClient(timeout=httpx.Timeout(10, read=None), trust_env=False)
        upstream = await client.send(client.build_request("POST", child.url + path, json=body,
                                                          headers=router._headers(child)), stream=True)
        if not body.get("stream"):
            data = await upstream.aread()
            await upstream.aclose()
            await client.aclose()
            router.last_request = time.monotonic()
            if mlx and upstream.status_code == 200:
                payload = rewrite_chunk(json.loads(data), ThinkSplitter(), thinking)
                return JSONResponse(payload)
            return Response(data, status_code=upstream.status_code, media_type="application/json")

        async def relay():
            splitter = ThinkSplitter()
            try:
                async for line in upstream.aiter_lines():
                    router.last_request = time.monotonic()
                    if mlx and line.startswith("data: ") and line[6:].strip() not in ("", "[DONE]"):
                        with contextlib.suppress(json.JSONDecodeError):
                            line = "data: " + json.dumps(rewrite_chunk(json.loads(line[6:]), splitter, thinking),
                                                         ensure_ascii=False)
                    yield (line + "\n").encode("utf-8")
            finally:
                await upstream.aclose()
                await client.aclose()

        return StreamingResponse(relay(), status_code=upstream.status_code, media_type="text/event-stream")

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        sleeper = asyncio.get_running_loop().create_task(router.idle_sleeper())
        try:
            yield
        finally:
            sleeper.cancel()
            await router.stop()

    app = Starlette(routes=[
        Route("/v1/models", models, methods=["GET"]), Route("/models", models, methods=["GET"]),
        Route("/health", health, methods=["GET"]), Route("/v1/health", health, methods=["GET"]),
        Route("/models/load", load, methods=["POST"]), Route("/models/unload", unload, methods=["POST"]),
        Route("/v1/chat/completions", completions, methods=["POST"]),
        Route("/v1/completions", completions, methods=["POST"]),
    ], lifespan=lifespan)
    return app


def main(argv: list[str] | None = None) -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--preset", required=True)
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--llama-server", default=os.environ.get("LLM_SERVER", ""))
    parser.add_argument("--mlx-python", default=os.environ.get("MLX_PYTHON", sys.executable))
    parser.add_argument("--logs", default=os.environ.get("LOGS_DIR", "logs"))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    router = Router(read_preset(Path(args.preset)), llama_server=args.llama_server, mlx_python=args.mlx_python,
                    logs_dir=Path(args.logs))
    log.info("router on %s:%s: %s", HOST, args.port, ", ".join(f"{s.id} ({s.engine})" for s in router.sections.values()))
    uvicorn.run(make_app(router), host=HOST, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
