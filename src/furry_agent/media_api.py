"""``GET /media/video/<name>.mp4`` (docs/image-to-video-design.md §6.4): the videos the video tab made, served by
the LangGraph server so a browser on another host can play them (ComfyUI stays on the loopback).

Mounted with /coder/turn and /models (langgraph.json ``http.app``). Only names of the form <32 hex>.mp4 under
outputs/video/ are served: the name is never a path. FileResponse answers Range requests, so the player can seek.
``?download=1`` asks the browser to save the file (browsers ignore a cross-origin ``download`` attribute).
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse

from furry_agent.config import Settings

VIDEO_NAME = re.compile(r"[0-9a-f]{32}\.mp4")


def videos_dir(outputs_dir: Path | None = None) -> Path:
    return (outputs_dir or Settings.from_env().outputs_dir) / "video"


def download_name(path: Path) -> str:
    """The file name a browser saves: when it was made, not the random name it is served under."""
    stamp = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y%m%d-%H%M%S")
    return f"video_{stamp}.mp4"


async def video_endpoint(request: Request):
    name = request.path_params["name"]
    folder = getattr(request.app.state, "videos_dir", None) or videos_dir()
    path = Path(folder) / name
    if not VIDEO_NAME.fullmatch(name) or not path.is_file():
        return JSONResponse({"error": "動画が見つかりません", "code": "not_found"}, status_code=404)
    # Each name is written once, so the browser may keep it.
    headers = {"Cache-Control": "private, max-age=86400"}
    if request.query_params.get("download"):
        return FileResponse(path, media_type="video/mp4", headers=headers, filename=download_name(path))
    return FileResponse(path, media_type="video/mp4", headers=headers)
