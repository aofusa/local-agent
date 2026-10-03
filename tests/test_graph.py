import base64
import io
import json
from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage
from PIL import Image

from furry_agent import graph as graph_module
from furry_agent.comfy_client import ComfyError, WaitResult
from furry_agent.config import Settings

ROOT = Path(__file__).resolve().parent.parent


def _png(color=(200, 80, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buf, format="PNG")
    return buf.getvalue()


class FakeComfy:
    def __init__(self, fail_at=None, gate_ok=True):
        self.calls = []
        self.uploads = []
        self.submitted = None
        self.fail_at = fail_at
        self.gate_ok = gate_ok
        self.image = _png()

    async def wait_queue_idle(self, timeout_s=None):
        self.calls.append("wait_queue_idle")

    async def free(self):
        self.calls.append("free")

    async def checkpoints(self):
        return ["yiffInHell_yihVANTABLACK.safetensors"]

    async def upload_image(self, data, filename, mime, subfolder="furry_ja"):
        self.uploads.append((filename, mime, data))
        return f"{subfolder}/{filename}"

    async def submit(self, prompt, client_id):
        if self.fail_at == "submit":
            raise ComfyError("/prompt rejected (400)")
        self.calls.append("submit")
        self.submitted = prompt
        return "prompt-123"

    async def wait(self, prompt_id, client_id, until_node=None, deadline=None, on_event=None):
        if until_node == "split":
            self.calls.append("wait_split")
            return WaitResult(done=False, outputs={"split": {
                "positive": ["masterpiece, 1girl, anthro, wolf, white fur, kimono, sunset, beach"],
                "negative": ["worst quality"], "split_mode": ["json"]}})
        self.calls.append("wait_done")
        if on_event:
            on_event("executed", {"node": "ckpt", "output": {"lmstudio_unloaded": [self.gate_ok]}})
            on_event("executing", {"node": "sampler"})
        return WaitResult(done=True, outputs={
            "ckpt": {"lmstudio_unloaded": [self.gate_ok], "forced_unload": []},
            "save": {"images": [{"filename": "furry_ja_00001_.png", "subfolder": "furry_ja", "type": "output"}]},
        })

    async def view(self, filename, subfolder="", folder_type="output"):
        self.calls.append(f"view:{subfolder}/{filename}")
        return self.image


@pytest.fixture
def settings(tmp_path):
    return Settings(
        comfyui_url="http://127.0.0.1:8188",
        ckpt_name=None,
        workflow_path=ROOT / "workflows" / "furry_ja_api.json",
        outputs_dir=tmp_path / "outputs",
        logs_dir=tmp_path / "logs",
        timeout_s=600,
    )


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    async def no_sleep(_):
        return None
    monkeypatch.setattr(graph_module.asyncio, "sleep", no_sleep)


async def _run(content, fake, settings):
    config = {"configurable": {"comfy_client": fake, "settings": settings}}
    return await graph_module.graph.ainvoke({"messages": [HumanMessage(content=content)]}, config)


async def test_text_only_returns_image_and_saves(settings):
    fake = FakeComfy()
    state = await _run("夕焼けの海辺に立つ白い狼獣人の女性、和服", fake, settings)

    assert fake.calls[:2] == ["wait_queue_idle", "free"]
    assert fake.calls[-1] == "free"  # released after the run
    prompt = fake.submitted
    assert prompt["user_prompt"]["inputs"]["value"] == "夕焼けの海辺に立つ白い狼獣人の女性、和服"
    assert prompt["latent"]["class_type"] == "EmptyLatentImage"
    assert "ref_image" not in prompt

    messages = state["messages"]
    assert len(messages) == 2  # human + one AI message (progress replaced in place)
    final = messages[-1]
    image_blocks = [b for b in final.content if b.get("type") == "image"]
    assert len(image_blocks) == 1
    assert image_blocks[0]["mimeType"] == "image/png"
    assert base64.b64decode(image_blocks[0]["data"]) == fake.image
    text = final.content[0]["text"]
    assert "wolf" in text and "furry_ja/furry_ja_00001_.png" in text

    saved = list(settings.outputs_dir.glob("*.png"))
    assert len(saved) == 1 and saved[0].read_bytes() == fake.image
    meta = json.loads(saved[0].with_suffix(".json").read_text(encoding="utf-8"))
    assert meta["prompt_id"] == "prompt-123" and meta["split_mode"] == "json"
    log_text = (settings.logs_dir / "furry_agent.log")
    assert log_text.exists()


async def test_reference_image_is_uploaded_and_img2img(settings):
    fake = FakeComfy()
    png = _png((10, 20, 30))
    content = [
        {"type": "text", "text": "この子を和服で"},
        {"type": "image", "mimeType": "image/png", "data": base64.b64encode(png).decode(), "metadata": {"name": "r.png"}},
    ]
    await _run(content, fake, settings)
    assert len(fake.uploads) == 1 and fake.uploads[0][2] == png
    prompt = fake.submitted
    assert prompt["ref_image"]["inputs"]["image"].startswith("furry_ja/ref_")
    assert prompt["latent"]["class_type"] == "VAEEncode"
    assert prompt["sampler"]["inputs"]["denoise"] == 0.45


async def test_image_without_text_gets_default_instruction(settings):
    fake = FakeComfy()
    block = {"type": "image", "mimeType": "image/png", "data": base64.b64encode(_png()).decode()}
    await _run([block], fake, settings)
    assert fake.submitted["user_prompt"]["inputs"]["value"] == graph_module.DEFAULT_TEXT_FOR_IMAGES


async def test_empty_message_is_rejected_without_submitting(settings):
    fake = FakeComfy()
    state = await _run("   ", fake, settings)
    assert fake.submitted is None
    assert "日本語" in state["messages"][-1].content
    assert state["error"]


async def test_video_is_rejected(settings):
    fake = FakeComfy()
    block = {"type": "file", "mimeType": "video/mp4", "data": base64.b64encode(b"x").decode()}
    state = await _run([{"type": "text", "text": "動画から"}, block], fake, settings)
    assert fake.submitted is None
    assert "VideoHelperSuite" in state["messages"][-1].content


async def test_submit_error_is_reported(settings):
    fake = FakeComfy(fail_at="submit")
    state = await _run("テスト", fake, settings)
    assert "生成できませんでした" in state["messages"][-1].content
    assert len(state["messages"]) == 2


async def test_missing_unload_verification_fails(settings):
    fake = FakeComfy(gate_ok=False)
    state = await _run("テスト", fake, settings)
    assert "unload" in state["messages"][-1].content
    assert not list(settings.outputs_dir.glob("*.png"))
    assert fake.calls[-1] == "free"


async def test_unknown_checkpoint(settings):
    fake = FakeComfy()
    settings = Settings(**{**settings.__dict__, "ckpt_name": "missing.safetensors"})
    state = await _run("テスト", fake, settings)
    assert "missing.safetensors" in state["messages"][-1].content
    assert fake.submitted is None
