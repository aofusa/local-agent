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

from furry_agent.config import REPO_ROOT, ChatSettings
from furry_agent.search_client import tor_listening

log = logging.getLogger("furry_agent.tor")
_start_lock = asyncio.Lock()


class TorUnavailable(RuntimeError):
    pass


def tor_command(exe: str, logs_dir: Path) -> list[str]:
    return [exe, "-f", str(REPO_ROOT / "tools" / "tor" / "torrc"),
            "--DataDirectory", str(REPO_ROOT / "tools" / "tor" / "data"),
            "--Log", f"notice file {Path(logs_dir) / 'tor.log'}"]


async def ensure_tor(settings: ChatSettings, timeout_s: float = 90.0) -> str:
    """Return a short status; raise TorUnavailable when the SOCKS port cannot be reached."""
    if await asyncio.to_thread(tor_listening, settings.tor_socks_url):
        return "running"
    if not settings.tor_autostart or not settings.tor_exe or not Path(settings.tor_exe).is_file():
        raise TorUnavailable("Tor が 127.0.0.1:9050 で待ち受けていません。scripts\\start-tor.ps1 で起動してください"
                             "（未導入なら scripts\\setup-tor.ps1）")
    async with _start_lock:
        if await asyncio.to_thread(tor_listening, settings.tor_socks_url):
            return "running"
        logs_dir = Path(settings.logs_dir)
        logs_dir.mkdir(parents=True, exist_ok=True)
        (REPO_ROOT / "tools" / "tor" / "data").mkdir(parents=True, exist_ok=True)
        log_file = logs_dir / "tor.log"
        log_file.write_text("", encoding="utf-8")
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        process = subprocess.Popen(tor_command(settings.tor_exe, logs_dir), stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
        (REPO_ROOT / "tools" / "tor" / "tor.pid").write_text(str(process.pid), encoding="ascii")
        log.info("started tor pid=%s", process.pid)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise TorUnavailable(f"Tor が終了しました（{log_file}）")
            text = log_file.read_text(encoding="utf-8", errors="replace") if log_file.exists() else ""
            if "Bootstrapped 100%" in text:
                return "started"
            await asyncio.sleep(1.0)
        raise TorUnavailable(f"Tor の接続が {timeout_s:.0f} 秒以内に完了しませんでした（{log_file}）")
