"""GET /models (docs/host-model-selection-design.md §6) with a fake router and ComfyUI."""

import httpx
import pytest

from furry_agent import coder_gate, models_api
from furry_agent.comfy_client import ComfyError


class Comfy:
    def __init__(self, info=None, down=False):
        self.info, self.down = info or {}, down

    async def object_info(self, max_age_s=300.0):
        if self.down:
            raise ComfyError("down")
        return self.info


def _choices(*names):
    return [list(names)]


INFO = {
    "FurryJaCheckpointLoaderAfterEject": {"input": {"required": {"ckpt_name": _choices(
        "yiffInHell_yihVANTABLACK.safetensors", "rekemono_v100.safetensors")}}},
    "FurryJaDiffusionLoaderAfterEject": {"input": {"required": {
        "unet_name": _choices("wulverKrea2_v05_fp8.safetensors", "chroma_v10HD.safetensors"),
        "clip_name": _choices("qwen3vl_4b_fp8_scaled.safetensors", "t5xxl_fp8_e4m3fn.safetensors"),
        "vae_name": _choices("qwen_image_vae.safetensors", "ae.safetensors")}}},
    "LoraLoader": {"input": {"required": {"lora_name": _choices("novabeast xl v1 rank64 pony.safetensors")}}},
}


@pytest.fixture(autouse=True)
def env(monkeypatch):
    for name in ("DEFAULT_INFERENCE_MODEL", "DEFAULT_IMAGE_MODEL", "HOST_MODELS_DISABLE"):
        monkeypatch.delenv(name, raising=False)


async def _get(comfy, router):
    async def fake_router(url):
        return router
    app = coder_gate.app
    app.state.comfy_client = comfy
    original = models_api._router_models
    models_api._router_models = fake_router
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://host") as client:
            response = await client.get("/models")
    finally:
        models_api._router_models = original
        del app.state.comfy_client
    return response.status_code, response.json()


async def test_lists_every_model_with_availability():
    status, body = await _get(Comfy(INFO), ["qwen3.8-27b-abliterated"])
    assert status == 200
    assert body["defaults"] == {"inference": "qwen3.8-27b-abliterated", "image": "yiffinhell-vantablack"}
    inference = {m["id"]: m for m in body["inference"]}
    assert inference["qwen3.8-27b-abliterated"]["available"] is True
    assert inference["bonsai-2-27b-abliterated"]["available"] is False
    assert "プリセット" in inference["bonsai-2-27b-abliterated"]["reason"]
    assert inference["bonsai-2-27b-abliterated"]["context"] == 8192
    image = {m["id"]: m for m in body["image"]}
    assert len(image) == 8
    assert image["yiffinhell-vantablack"]["available"] and image["rekemono"]["available"]
    assert image["wulver"]["available"] and image["wulver"]["family_label"] == "Krea 2"
    assert image["chroma-hd"]["available"]
    assert not image["indigofurrymix-anima"]["available"]  # no qwen_3_06b_base / unet in this ComfyUI
    assert "yiffInHell_yihMETLLICTETR.safetensors" in image["yiffinhell-metallictetra"]["reason"]
    # Labels and ids only: no file system path leaks.
    assert ":\\" not in str(body) and "/Users/" not in str(body)


async def test_hosts_down_still_lists_everything(monkeypatch, tmp_path):
    monkeypatch.setenv("BONSAI_MODELS_DIR", str(tmp_path))
    status, body = await _get(Comfy(down=True), None)
    assert status == 200 and len(body["image"]) == 8
    assert all(m["available"] for m in body["image"])  # files are checked again when a run queues
    assert {m["id"] for m in body["inference"] if not m["available"]} >= {"bonsai-2-27b-abliterated"}


async def test_disabled_ids_are_listed_but_unavailable(monkeypatch):
    monkeypatch.setenv("HOST_MODELS_DISABLE", "wulver")
    status, body = await _get(Comfy(INFO), ["qwen3.8-27b-abliterated", "bonsai-2-27b-abliterated"])
    wulver = next(m for m in body["image"] if m["id"] == "wulver")
    assert wulver["available"] is False and "HOST_MODELS_DISABLE" in wulver["reason"]
    assert all(m["available"] for m in body["inference"] if "remote" not in m)
