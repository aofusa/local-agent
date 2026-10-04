import asyncio
import time

import pytest

from furry_agent.job_lock import JobLock, JobLockBusy


async def test_other_tab_is_refused_after_timeout():
    lock = JobLock(poll_s=0.01)
    token = await lock.acquire("image", 1)
    started = time.monotonic()
    with pytest.raises(JobLockBusy, match="画像タブが実行中"):
        await lock.acquire("chat", 0.1)
    assert time.monotonic() - started < 1
    lock.release(token)
    assert await lock.acquire("chat", 0.1)


async def test_same_tab_waits_without_limit_when_asked():
    lock = JobLock(poll_s=0.01)
    first = await lock.acquire("image", 1)

    async def release_later():
        await asyncio.sleep(0.1)
        lock.release(first)

    asyncio.get_running_loop().create_task(release_later())
    second = await lock.acquire("image", 0.01, None)
    assert second != first and lock.holder == "image"


async def test_release_needs_the_token_and_lease_expires():
    lock = JobLock(lease_s=0.05, poll_s=0.01)
    token = await lock.acquire("chat", 1)
    assert not lock.release("chat-wrong")
    assert lock.holder == "chat"
    await asyncio.sleep(0.08)
    assert lock.holder is None  # a vanished holder does not block forever
    assert not lock.release(token)


async def test_renew_extends_the_lease():
    lock = JobLock(lease_s=0.1, poll_s=0.01)
    token = await lock.acquire("chat", 1)
    for _ in range(3):
        await asyncio.sleep(0.05)
        lock.renew(token)
    assert lock.holder == "chat"
