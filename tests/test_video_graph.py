"""The video graph (src/furry_agent/video_graph.py) against a fake ComfyUI: one start image in, the Wan 2.2 template
filled (per-expert LoRAs, size, length, seed), the order verified from the nodes' reports, the mp4 saved under
outputs/video and returned as a /media/video URL."""

import base64
import io
import json
import re
from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage
from PIL import Image

from furry_agent import video_graph
from furry_agent.comfy_client import WaitResult
from furry_agent.config import Settings
from furry_agent.job_lock import job_lock
from furry_agent.video_plan import DEFAULT_INSTRUCTION, VideoSettings

ROOT = Path(__file__).resolve().parent.parent
PROMPT_ID = "11111111-2222-3333-4444-555555555555"
DISTILL_HIGH = "wan2.2_i2v_A14b_high_noise_lora_rank64_lightx2v_4step_1022.safetensors"
DISTILL_LOW = "wan2.2_i2v_A14b_low_noise_lora_rank64_lightx2v_4step_1022.safetensors"
FURRY_HIGH = "Furry Enhancer Wan2.2 V3 High Noise I2V.safetensors"
FURRY_LOW = "Furry Enhancer Wan2.2 V3 Low Noise I2V .safetensors"
EXPERT_FILES = ["Wan2.2-I2V-A14B-HighNoise-Q4_K_M.gguf", "Wan2.2-I2V-A14B-LowNoise-Q4_K_M.gguf",
                "Wan2.2-I2V-A14B-HighNoise-Q3_K_M.gguf", "Wan2.2-I2V-A14B-LowNoise-Q3_K_M.gguf"]
NODES = {"LMConnectLMStudioBackend", "PrimitiveStringMultiline", "LoadImage", "LMConnectVision", "StringConcatenate",
         "LMConnectPromptWithSystem", "FurryJaEjectLLM", "FurryJaSplitTags", "FurryJaTextEncoderAfter", "CLIPTextEncode",
         "FurryJaReleaseAfter", "FurryJaVAELoaderAfter", "WanImageToVideo", "FurryJaVideoModelAfter", "ModelSamplingSD3",
         "KSamplerAdvanced", "VAEDecodeTiled", "CreateVideo", "SaveVideo", "LoraLoaderModelOnly", "UnetLoaderGGUF"}


def _png(size=(832, 1216), color=(200, 80, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _block(png):
    return {"type": "image", "mimeType": "image/png", "data": base64.b64encode(png).decode(), "metadata": {"name": "a.png"}}


class FakeComfy:
    def __init__(self, *, nodes=NODES, experts=EXPERT_FILES, loras=(DISTILL_HIGH, DISTILL_LOW, FURRY_HIGH, FURRY_LOW),
                 gate_ok=True, kept=None, split_mode="json"):
        self.nodes, self.experts, self.lora_files = set(nodes), list(experts), list(loras)
        self.gate_ok, self.kept, self.split_mode = gate_ok, kept or {}, split_mode
        self.submitted, self.uploads, self.freed, self.interrupted = [], [], 0, []
        self.video = b"\x00\x00\x00\x18ftypmp42 fake video"

    async def wait_queue_idle(self, timeout_s=None):
        return None

    async def free(self):
        self.freed += 1

    async def node_types(self):
        return set(self.nodes)

    async def checkpoints(self, node_class, field):
        return {("FurryJaVideoModelAfter", "unet_name"): self.experts,
                ("FurryJaTextEncoderAfter", "clip_name"): ["umt5_xxl_fp8_e4m3fn_scaled.safetensors"],
                ("FurryJaVAELoaderAfter", "vae_name"): ["wan_2.1_vae.safetensors"]}.get((node_class, field), [])

    async def loras(self):
        return list(self.lora_files)

    async def input_exists(self, name):
        return name in self.uploads

    async def upload_image(self, data, filename, mime, subfolder="furry_ja"):
        self.uploads.append(f"{subfolder}/{filename}")
        return f"{subfolder}/{filename}"

    async def submit(self, prompt, client_id):
        self.submitted.append(prompt)
        return PROMPT_ID

    async def wait(self, prompt_id, client_id, until_node=None, deadline=None, on_event=None, idle_s=None):
        split = {"positive": ["An anthropomorphic white wolf woman wags her tail. Static camera."],
                 "negative": ["static, frozen"], "split_mode": [self.split_mode]}
        if until_node == "split":
            return WaitResult(done=False, outputs={"split": split})
        outputs = {"split": split}
        for node in ("te", "unet_high", "unet_low"):
            outputs[node] = {"llm_unloaded": [self.gate_ok], "forced_unload": []}
        for node in ("release_te", "release_high", "release_low"):
            outputs[node] = {"stage": [node], "alive_before": [], "diffusion_before": self.kept.get(node, []),
                             "alive_after": [], "free_vram_gb": [8.0], "free_ram_gb": [6.0]}
        if on_event:
            for node, output in outputs.items():
                on_event("executed", {"node": node, "output": output, "prompt_id": prompt_id})
        outputs["save"] = {"images": [{"filename": "wan22_00001_.mp4", "subfolder": "furry_ja/video", "type": "output"}],
                           "animated": [True]}
        return WaitResult(done=True, outputs=outputs)

    async def view(self, filename, subfolder="", folder_type="output"):
        return self.video

    async def interrupt(self, prompt_id):
        self.interrupted.append(prompt_id)


@pytest.fixture
def settings(tmp_path):
    return Settings(comfyui_url="http://127.0.0.1:8188", workflows_dir=ROOT / "workflows",
                    outputs_dir=tmp_path / "outputs", logs_dir=tmp_path / "logs", timeout_s=600)


@pytest.fixture(autouse=True)
def fast_sleep_and_fresh_cache(monkeypatch):
    async def no_sleep(_):
        return None
    monkeypatch.setattr(video_graph.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(video_graph, "_uploaded", {})
    for name in ("DEFAULT_VIDEO_MODEL", "HOST_MODELS_DISABLE"):
        monkeypatch.delenv(name, raising=False)


async def _run(content, fake, settings, **configurable):
    config = {"configurable": {"comfy_client": fake, "settings": settings, "video_settings": VideoSettings(),
                               **configurable}}
    return await video_graph.graph.ainvoke({"messages": [HumanMessage(content=content)]}, config)


def _last(state):
    return state["messages"][-1]


def _text(message):
    content = message.content
    return content if isinstance(content, str) else "".join(b.get("text", "") for b in content if b.get("type") == "text")


async def test_image_to_video(settings):
    fake = FakeComfy()
    state = await _run([{"type": "text", "text": "しっぽを振りながら笑う"}, _block(_png())], fake, settings)
    message = _last(state)
    video = [b for b in message.content if b.get("type") == "video"][0]
    assert re.fullmatch(r"/media/video/[0-9a-f]{32}\.mp4", video["url"])
    assert video["mimeType"] == "video/mp4"
    assert video["metadata"]["width"] == 448 and video["metadata"]["height"] == 656
    assert video["metadata"]["frames"] == 49 and video["metadata"]["name"].startswith("video_")
    assert message.additional_kwargs["model_info"] == {"kind": "video", "id": "wan22-i2v-a14b-q3",
                                                       "label": "Wan 2.2 I2V A14B（Q3_K_M・省メモリ）"}
    saved = settings.outputs_dir / "video" / video["url"].rsplit("/", 1)[1]
    assert saved.read_bytes() == fake.video
    meta = json.loads(saved.with_suffix(".json").read_text(encoding="utf-8"))
    assert meta["prompt_id"] == PROMPT_ID and meta["positive"].startswith("An anthropomorphic")
    assert "outputs/video/" in _text(message) and "⚠️" not in _text(message)
    assert fake.freed >= 2  # before queueing and after the run
    assert job_lock.holder is None  # released once queued, like the image tab


async def test_template_is_filled_for_the_run(settings):
    fake = FakeComfy()
    await _run([{"type": "text", "text": "耳を動かす"}, _block(_png())], fake, settings)
    prompt = fake.submitted[0]
    assert prompt["user_prompt"]["inputs"]["value"] == "耳を動かす"
    assert prompt["ref_image"]["inputs"]["image"] == fake.uploads[0]
    assert (prompt["i2v"]["inputs"]["width"], prompt["i2v"]["inputs"]["height"], prompt["i2v"]["inputs"]["length"]) \
        == (448, 656, 49)
    assert prompt["sampler_high"]["inputs"]["noise_seed"] == prompt["sampler_low"]["inputs"]["noise_seed"]
    assert prompt["unet_high"]["inputs"]["unet_name"] == "Wan2.2-I2V-A14B-HighNoise-Q3_K_M.gguf"
    # The distill LoRA, then the kemono LoRA, after each expert.
    assert [prompt[f"lora_high_{i}"]["inputs"]["lora_name"] for i in (1, 2)] == [DISTILL_HIGH, FURRY_HIGH]
    assert [prompt[f"lora_low_{i}"]["inputs"]["lora_name"] for i in (1, 2)] == [DISTILL_LOW, FURRY_LOW]
    assert prompt["shift_high"]["inputs"]["model"] == ["lora_high_2", 0]
    assert prompt["decode"]["inputs"]["tile_size"] == 256 and prompt["save"]["inputs"]["format.codec.encoding.crf"] == 20.0
    assert prompt["llm_backend"]["inputs"]["read_timeout_seconds"] == 600


async def test_model_and_length_from_the_request(settings):
    fake = FakeComfy()
    await _run([_block(_png((1216, 832)))], fake, settings, video_model="wan22-i2v-a14b-q4", video_seconds=5)
    prompt = fake.submitted[0]
    assert prompt["unet_low"]["inputs"]["unet_name"] == "Wan2.2-I2V-A14B-LowNoise-Q4_K_M.gguf"
    assert prompt["i2v"]["inputs"]["length"] == 81
    assert (prompt["i2v"]["inputs"]["width"], prompt["i2v"]["inputs"]["height"]) == (656, 448)


async def test_empty_instruction_uses_subtle_motion(settings):
    fake = FakeComfy()
    await _run([_block(_png())], fake, settings)
    assert fake.submitted[0]["user_prompt"]["inputs"]["value"] == DEFAULT_INSTRUCTION


@pytest.mark.parametrize("content, message", [
    ([{"type": "text", "text": "動かして"}], "1 枚添付"),
    ([_block(_png()), _block(_png(color=(1, 2, 3)))], "1 枚だけ"),
])
async def test_needs_exactly_one_image(settings, content, message):
    fake = FakeComfy()
    state = await _run(content, fake, settings)
    assert message in _text(_last(state)) and "［入力］" in _text(_last(state))
    assert fake.submitted == []


async def test_bad_length_is_refused(settings):
    state = await _run([_block(_png())], FakeComfy(), settings, video_seconds="long")
    assert "秒数" in _text(_last(state))


async def test_unknown_model_is_refused_not_replaced(settings):
    fake = FakeComfy()
    state = await _run([_block(_png())], fake, settings, video_model="wan22")
    assert "動画モデル" in _text(_last(state)) and fake.submitted == []


async def test_missing_comfyui_gguf(settings):
    fake = FakeComfy(nodes=NODES - {"UnetLoaderGGUF"})
    state = await _run([_block(_png())], fake, settings)
    assert "UnetLoaderGGUF" in _text(_last(state)) and "setup-video-models" in _text(_last(state))
    assert job_lock.holder is None


async def test_missing_expert_file(settings):
    fake = FakeComfy(experts=EXPERT_FILES[:2])  # only the Q4_K_M files: the default Q3_K_M is missing
    state = await _run([_block(_png())], fake, settings)
    assert "Wan2.2-I2V-A14B-HighNoise-Q3_K_M.gguf" in _text(_last(state))


async def test_missing_kemono_lora(settings):
    fake = FakeComfy(loras=(DISTILL_HIGH, DISTILL_LOW, FURRY_HIGH))
    state = await _run([_block(_png())], fake, settings)
    assert "Furry Enhancer Wan2.2 V3 Low Noise I2V" in _text(_last(state)) and fake.submitted == []


async def test_loader_without_the_router_check_fails(settings):
    fake = FakeComfy(gate_ok=False)
    state = await _run([_block(_png())], fake, settings)
    assert "unload を確認できませんでした" in _text(_last(state))


async def test_a_model_kept_by_a_cache_is_reported(settings):
    fake = FakeComfy(kept={"release_high": ["WAN22"]})
    state = await _run([_block(_png())], fake, settings)
    assert "release_high" in _text(_last(state)) and "--cache-none" in _text(_last(state))
    assert any(b.get("type") == "video" for b in _last(state).content)


async def test_broken_json_still_makes_a_video(settings):
    state = await _run([_block(_png())], FakeComfy(split_mode="fallback"), settings)
    assert "JSON ではなかった" in _text(_last(state))
    assert any(b.get("type") == "video" for b in _last(state).content)


async def test_refetch(settings):
    fake = FakeComfy()
    state = await _run(f"再取得 {PROMPT_ID}", fake, settings)
    video = [b for b in _last(state).content if b.get("type") == "video"][0]
    assert video["url"].startswith("/media/video/") and fake.submitted == []
