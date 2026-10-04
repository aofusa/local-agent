import asyncio
import sys
from pathlib import Path

import pytest

from furry_agent import bonsai_worker
from furry_agent.bonsai_worker import Ledger, LlamaServer, WorkerError, free_port, port_in_use, run_reader
from furry_agent.llm_client import ChatReply, ToolCall

FAKE = Path(__file__).with_name("fake_llama_server.py")


class FakeServer(LlamaServer):
    """LlamaServer with the real process handling, but a tiny Python HTTP server as the executable."""

    crash: bool = False

    def command(self):
        return [sys.executable, str(FAKE), "--port", str(self.port)] + (["--crash"] if self.crash else [])


def _server(**kw) -> FakeServer:
    server = FakeServer(sys.executable, Path("model.gguf"), free_port(18500), label="fake", **kw)
    return server


def test_command_line_is_loopback_vulkan_and_thinking_off():
    cmd = LlamaServer("llama-server.exe", Path("m.gguf"), 18181, 4096, 99, "x").command()
    assert cmd[cmd.index("--host") + 1] == "127.0.0.1"
    assert cmd[cmd.index("--reasoning") + 1] == "off"
    assert cmd[cmd.index("-ngl") + 1] == "99" and "-fa" in cmd
    assert cmd[cmd.index("--cache-ram") + 1] == "0"
    assert "-fa" not in LlamaServer("x", Path("m"), 1, ngl=0).command()


async def test_start_and_stop_leave_no_pid_and_free_port():
    server = _server()
    await server.start(20)
    pid = server.process.pid
    assert pid in bonsai_worker.live_pids() and port_in_use(server.port)
    await server.stop()
    assert not server.alive and pid not in bonsai_worker.live_pids()
    assert not port_in_use(server.port)


async def test_crash_on_start_raises_and_leaves_nothing():
    server = _server()
    server.crash = True
    with pytest.raises(WorkerError, match="終了しました"):
        await server.start(20)
    assert bonsai_worker.live_pids() == []


async def test_cancel_during_start_kills_the_process():
    server = _server()
    task = asyncio.ensure_future(server.start(20))
    for _ in range(100):
        if server.process is not None:
            break
        await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not server.alive and bonsai_worker.live_pids() == []


async def test_kill_all_stops_every_server():
    servers = [_server(), None]
    await servers[0].start(20)
    servers[1] = FakeServer(sys.executable, Path("m"), free_port(servers[0].port + 1), label="fake")
    await servers[1].start(20)
    pids = await bonsai_worker.kill_all()
    assert len(pids) == 2 and bonsai_worker.live_pids() == []


async def test_missing_executable_is_reported():
    with pytest.raises(WorkerError, match="setup-llamacpp"):
        await LlamaServer("", Path("m"), free_port(18600)).start(1)


# --- the reader loop -------------------------------------------------------------------------------------------


class Page:
    def __init__(self, url):
        self.url, self.title, self.text = url, "T", f"本文 {url}"


class FakeSearch:
    def __init__(self):
        self.fetched = []

    async def fetch(self, url):
        self.fetched.append(url)
        return Page(url)


class ToolLoopLLM:
    """Keeps asking to open pages: the reader must stop after MAX_TOOL_ROUNDS."""

    def __init__(self, urls):
        self.urls = list(urls)
        self.tool_calls = 0

    async def chat(self, messages, tools=None, **kw):
        if tools:
            self.tool_calls += 1
            url = self.urls[min(self.tool_calls - 1, len(self.urls) - 1)]
            return ChatReply("", [ToolCall("open_page", {"url": url})])
        return ChatReply("{}")


HITS = [{"url": f"https://s{i}.example/", "title": f"t{i}", "snippet": "s"} for i in range(3)]


async def _cards(client, messages):
    return None


async def test_reader_stops_after_two_tool_rounds():
    llm, search = ToolLoopLLM([h["url"] for h in HITS]), FakeSearch()
    out = await run_reader({"id": 0, "q": "q"}, HITS, llm, search, Ledger(), "sys", "質問", _cards)
    assert llm.tool_calls == 2 and search.fetched == [HITS[0]["url"], HITS[1]["url"]]
    assert out["tool_call"] and len(out["pages"]) == 2


async def test_reader_does_not_refetch_and_ignores_unlisted_urls():
    ledger = Ledger()
    ledger.claim(HITS[0]["url"])  # another reader opened it already
    llm, search = ToolLoopLLM([HITS[0]["url"]]), FakeSearch()
    await run_reader({"id": 0, "q": "q"}, HITS, llm, search, ledger, "sys", "質問", _cards)
    assert search.fetched == []
    llm, search = ToolLoopLLM(["https://not-listed.example/"]), FakeSearch()
    out = await run_reader({"id": 1, "q": "q"}, HITS, llm, search, Ledger(), "sys", "質問", _cards)
    # No usable tool call: the orchestrator opens the top result itself, once.
    assert search.fetched == [HITS[0]["url"]] and not out["tool_call"]


async def test_reader_accepts_result_number():
    class NumberLLM(ToolLoopLLM):
        async def chat(self, messages, tools=None, **kw):
            if tools and not self.tool_calls:
                self.tool_calls += 1
                return ChatReply("", [ToolCall("open_page", {"url": "[3]"})])
            return ChatReply("十分")

    search = FakeSearch()
    await run_reader({"id": 0, "q": "q"}, HITS, NumberLLM([]), search, Ledger(), "sys", "質問", _cards)
    assert search.fetched == [HITS[2]["url"]]
