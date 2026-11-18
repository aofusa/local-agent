"""GET /media/video/<uuid>.mp4 (src/furry_agent/media_api.py): only the video tab's files, by name, with Range."""

import httpx
import pytest

from furry_agent import coder_gate

NAME = "0123456789abcdef0123456789abcdef.mp4"


@pytest.fixture
def videos(tmp_path):
    (tmp_path / NAME).write_bytes(b"\x00\x00\x00\x18ftypmp42" + bytes(range(256)) * 4)
    (tmp_path / "secret.txt").write_text("no")
    coder_gate.app.state.videos_dir = tmp_path
    yield tmp_path
    del coder_gate.app.state.videos_dir


async def _get(path, headers=None):
    transport = httpx.ASGITransport(app=coder_gate.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://host") as client:
        return await client.get(path, headers=headers or {})


async def test_serves_a_video(videos):
    response = await _get(f"/media/video/{NAME}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "video/mp4"
    assert response.content == (videos / NAME).read_bytes()
    assert "content-disposition" not in response.headers


async def test_answers_range_requests(videos):
    response = await _get(f"/media/video/{NAME}", {"Range": "bytes=0-15"})
    assert response.status_code == 206
    assert response.headers["content-range"].startswith("bytes 0-15/")
    assert len(response.content) == 16


async def test_download_asks_the_browser_to_save(videos):
    response = await _get(f"/media/video/{NAME}?download=1")
    assert response.status_code == 200
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment") and "video_" in disposition and ".mp4" in disposition


@pytest.mark.parametrize("name", ["secret.txt", "0123456789ABCDEF0123456789ABCDEF.mp4", "abc.mp4",
                                  "..%2Fsecret.txt", "%2E%2E%2Fsecret.txt", "fedcba9876543210fedcba9876543210.mp4"])
async def test_anything_else_is_not_found(videos, name):
    response = await _get(f"/media/video/{name}")
    assert response.status_code == 404
