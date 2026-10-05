"""Connect to the local Tor SOCKS proxy, starting tor.exe (scripts/setup-tor.ps1) when it is not running.

The same command line as scripts/start-tor.ps1: tools/tor/torrc, data in tools/tor/data, log in logs/tor.log.
Started only when TOR_AUTOSTART=1 (default) and TOR_EXE exists; the process keeps running after the search
(Tor is the one long-lived helper; Bonsai is not).
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
import time
from pathlib import Path

from furry_agent.config import REPO_ROOT, ChatSettings, env_float
from furry_agent.search_client import tor_listening

log = logging.getLogger("furry_agent.tor")
_start_lock = asyncio.Lock()


class TorUnavailable(RuntimeError):
    pass


def tor_command(exe: str, logs_dir: Path) -> list[str]:
    return [exe, "-f", str(REPO_ROOT / "tools" / "tor" / "torrc"),
            "--DataDirectory", str(REPO_ROOT / "tools" / "tor" / "data"),
            "--Log", f"notice file {Path(logs_dir) / 'tor.log'}"]


def _spawn(exe: str, logs_dir: Path) -> subprocess.Popen:
    logs_dir.mkdir(parents=True, exist_ok=True)
    (REPO_ROOT / "tools" / "tor" / "data").mkdir(parents=True, exist_ok=True)
    (logs_dir / "tor.log").write_text("", encoding="utf-8")
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    process = subprocess.Popen(tor_command(exe, logs_dir), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, creationflags=flags)
    (REPO_ROOT / "tools" / "tor" / "tor.pid").write_text(str(process.pid), encoding="ascii")
    return process


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


BOOTSTRAP_TIMEOUT_S = env_float("TOR_BOOTSTRAP_TIMEOUT_S", 90.0, 5.0)


async def ensure_tor(settings: ChatSettings, timeout_s: float | None = None) -> str:
    """Return a short status; raise TorUnavailable when the SOCKS port cannot be reached."""
    if await asyncio.to_thread(tor_listening, settings.tor_socks_url):
        return "running"
    exe_ok = bool(settings.tor_exe) and await asyncio.to_thread(Path(settings.tor_exe).is_file)
    if not settings.tor_autostart or not exe_ok:
        raise TorUnavailable("Tor が 127.0.0.1:9050 で待ち受けていません。scripts\\start-tor.ps1 で起動してください"
                             "（未導入なら scripts\\setup-tor.ps1）")
    async with _start_lock:
        if await asyncio.to_thread(tor_listening, settings.tor_socks_url):
            return "running"
        log_file = Path(settings.logs_dir) / "tor.log"
        # File work and the process start run in a thread (langgraph dev fails runs that block the loop).
        process = await asyncio.to_thread(_spawn, settings.tor_exe, Path(settings.logs_dir))
        log.info("started tor pid=%s", process.pid)
        deadline = time.monotonic() + (BOOTSTRAP_TIMEOUT_S if timeout_s is None else timeout_s)
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise TorUnavailable(f"Tor が終了しました（{log_file}）")
            text = await asyncio.to_thread(_read, log_file)
            if "Bootstrapped 100%" in text:
                return "started"
            await asyncio.sleep(1.0)
        raise TorUnavailable(f"Tor の接続が {timeout_s:.0f} 秒以内に完了しませんでした（{log_file}）")
