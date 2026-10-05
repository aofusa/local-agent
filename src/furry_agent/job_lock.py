"""One job at a time across the image tab and the chat tab (design doc §5.8).

The image tab holds the lock while it submits to ComfyUI; the chat tab holds it from its first LM Studio call
until the last search worker is gone. A run spans several graph nodes, so the lock is token based (any task may
release it) with a lease that frees a holder that disappeared (a cancelled run whose cleanup never ran).
Waiting is bounded by JOB_LOCK_TIMEOUT_S when the holder is the other tab; there is no queue.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass

TAB_LABELS = {"image": "画像タブ", "chat": "チャットタブ", "coder": "CUI（cirka）"}


class JobLockBusy(RuntimeError):
    """The other tab holds the lock longer than the wait limit."""

    def __init__(self, holder: str):
        super().__init__(f"{TAB_LABELS.get(holder, holder)}が実行中です。終わってから送り直してください")
        self.holder = holder


@dataclass
class _Holder:
    token: str
    tab: str
    expires: float


class JobLock:
    def __init__(self, lease_s: float = 900.0, poll_s: float = 0.2):
        self.lease_s = lease_s
        self.poll_s = poll_s
        self._holder: _Holder | None = None

    @property
    def holder(self) -> str | None:
        self._expire()
        return self._holder.tab if self._holder else None

    def _expire(self) -> None:
        if self._holder and time.monotonic() > self._holder.expires:
            self._holder = None

    def try_acquire(self, tab: str) -> str | None:
        self._expire()
        if self._holder is not None:
            return None
        token = f"{tab}-{uuid.uuid4().hex}"
        self._holder = _Holder(token, tab, time.monotonic() + self.lease_s)
        return token

    async def acquire(self, tab: str, timeout_s: float, same_tab_timeout_s: float | None = None) -> str:
        """Wait for the lock. ``timeout_s`` applies while the other tab holds it; ``same_tab_timeout_s``
        (None = no limit) while the same tab does (the image tab keeps its old behaviour of waiting)."""
        started = time.monotonic()
        while True:
            token = self.try_acquire(tab)
            if token:
                return token
            holder = self.holder
            limit = same_tab_timeout_s if holder == tab else timeout_s
            if holder is not None and limit is not None and time.monotonic() - started >= limit:
                raise JobLockBusy(holder)
            await asyncio.sleep(self.poll_s)

    def holds(self, token: str | None) -> bool:
        self._expire()
        return bool(token) and self._holder is not None and self._holder.token == token

    def renew(self, token: str | None) -> None:
        if token and self._holder and self._holder.token == token:
            self._holder.expires = time.monotonic() + self.lease_s

    def release(self, token: str | None) -> bool:
        if token and self._holder and self._holder.token == token:
            self._holder = None
            return True
        return False


# Shared by graph.py (image) and chat_graph.py (chat); both graphs run in the same LangGraph server process.
job_lock = JobLock()
