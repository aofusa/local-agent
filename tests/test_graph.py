import asyncio
import base64
import io
import json
from dataclasses import replace
from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from PIL import Image

from furry_agent import graph as graph_module
from furry_agent.comfy_client import ComfyError, WaitResult
from furry_agent.config import Settings

ROOT = Path(__file__).resolve().parent.parent
NODE_TYPES = {
    "LMConnectLMStudioBackend", "PrimitiveStringMultiline", "LoadImage", "LMConnectVision", "StringConcatenate",
    "LMConnectPromptWithSystem", "LMConnectEjectLMStudioModel", "FurryJaSplitTags", "FurryJaCheckpointLoaderAfterEject",
    "CLIPTextEncode", "ImageScaleToTotalPixels", "EmptyLatentImage", "VAEEncode", "KSampler", "VAEDecode", "SaveImage",
    "IPAdapterUnifiedLoader", "IPAdapterAdvanced", "FurryJaImageAfter", "DWPreprocessor", "DiffControlNetLoader",
    "SetUnionControlNetType", "ControlNetApplyAdvanced", "PreviewImage", "ImageToMask", "SetLatentNoiseMask",
    "LoraLoader", "Canny", "DepthAnythingV2Preprocessor",
}


def _png(color=(200, 80, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buf, format="PNG")
    return buf.getvalue()


def _block(png, **meta):
    return {"type": "image", "mimeType": "image/png", "data": base64.b64encode(png).decode(), "metadata": meta}


class FakeComfy:
    def __init__(self, fail_at=None, gate_ok=True, node_types=NODE_TYPES):
        self.calls = []
        self.uploads = []
        self.inputs = set()
        self.submitted = None
        self.fail_at = fail_at
        self.gate_ok = gate_ok
        self.types = node_types
        self.image = _png()
        self.preview = _png((0, 0, 0))

    async def wait_queue_idle(self, timeout_s=None):
        self.calls.append("wait_queue_idle")

    async def free(self):
        self.calls.append("free")

    async def checkpoints(self):
        return ["yiffInHell_yihVANTABLACK.safetensors"]

    async def loras(self):
        return ["KemonoStyleAV1.safetensors", "CiviFur-30.safetensors"]

    async def node_types(self):
        return set(self.types)

    async def input_exists(self, name):
        return name in self.inputs

    async def interrupt(self, prompt_id):
        self.calls.append(f"interrupt:{prompt_id}")

    async def upload_image(self, data, filename, mime, subfolder="furry_ja"):
        self.uploads.append((filename, mime, data))
        self.inputs.add(f"{subfolder}/{filename}")
        return f"{subfolder}/{filename}"

    async def submit(self, prompt, client_id):
        if self.fail_at == "submit":
            raise ComfyError("キュー投入を ComfyUI が拒否しました（400）")
        self.calls.append("submit")
        self.submitted = prompt
        return "0b7e2f4a-1111-2222-3333-444455556666"

    async def wait(self, prompt_id, client_id, until_node=None, deadline=None, on_event=None):
        if self.fail_at == "cancel":
            raise asyncio.CancelledError
        if until_node == "split":
            self.calls.append("wait_split")
            if self.fail_at == "tags":
                raise ComfyError("FurryJaSplitTags (split): [LM Connect Error] HTTP Error 400")
            if self.fail_at == "timeout":
                raise ComfyError("ComfyUI の完了待ちがタイムアウトしました（10 分）")
            return WaitResult(done=False, outputs={"ckpt": {"lmstudio_unloaded": [self.gate_ok]}, "split": {
                "positive": ["masterpiece, 1girl, anthro, wolf, white fur, kimono, sunset, beach"],
                "negative": ["worst quality"], "split_mode": ["json"]}})
        self.calls.append("wait_done")
        if on_event:
            on_event("executing", {"node": "sampler"})
        outputs = {"save": {"images": [{"filename": "furry_ja_00001_.png", "subfolder": "furry_ja", "type": "output"}]}}
        if self.submitted and "pose_preview" in self.submitted:
            outputs["pose_preview"] = {"images": [{"filename": "p_00001_.png", "subfolder": "", "type": "temp"}]}
        if until_node is None and self.fail_at == "refetch":
            outputs["ckpt"] = {"lmstudio_unloaded": [True]}
        return WaitResult(done=True, outputs=outputs)

    async def view(self, filename, subfolder="", folder_type="output"):
        self.calls.append(f"view:{subfolder}/{filename}")
        return self.preview if folder_type == "temp" else self.image


@pytest.fixture
def settings(tmp_path):
    return Settings(
        comfyui_url="http://127.0.0.1:8188",
        ckpt_name=None,
        workflows_dir=ROOT / "workflows",
        outputs_dir=tmp_path / "outputs",
        logs_dir=tmp_path / "logs",
        timeout_s=600,
    )


@pytest.fixture(autouse=True)
def fast_sleep_and_fresh_cache(monkeypatch):
    async def no_sleep(_):
        return None
    monkeypatch.setattr(graph_module.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(graph_module, "_uploaded", {})


def _config(fake, settings, **extra):
    return {"configurable": {"comfy_client": fake, "settings": settings, **extra}}


async def _run(content, fake, settings, **extra):
    return await graph_module.graph.ainvoke({"messages": [HumanMessage(content=content)]}, _config(fake, settings, **extra))


def _final_images(state):
    return [b for b in state["messages"][-1].content if b.get("type") == "image"]


async def test_text_only_returns_image_and_saves(settings):
    fake = FakeComfy()
    state = await _run("夕焼けの海辺に立つ白い狼獣人の女性、和服", fake, settings)

    assert fake.calls[:2] == ["wait_queue_idle", "free"]
    assert fake.calls[-1] == "free"  # released after the run
    prompt = fake.submitted
    assert prompt["user_prompt"]["inputs"]["value"] == "夕焼けの海辺に立つ白い狼獣人の女性、和服"
    assert prompt["latent"]["class_type"] == "EmptyLatentImage"
    assert "ref_image" not in prompt and "lora_1" not in prompt

    messages = state["messages"]
    assert len(messages) == 2  # human + one AI message (progress replaced in place)
    images = _final_images(state)
    assert len(images) == 1 and images[0]["mimeType"] == "image/png"
    assert base64.b64decode(images[0]["data"]) == fake.image
    text = messages[-1].content[0]["text"]
    assert "t2i_basic" in text and "wolf" in text and "furry_ja/furry_ja_00001_.png" in text
    assert f"seed {state['plan']['seed']}" in text

    saved = list(settings.outputs_dir.glob("*.png"))
    assert len(saved) == 1 and saved[0].read_bytes() == fake.image
    meta = json.loads(saved[0].with_suffix(".json").read_text(encoding="utf-8"))
    assert meta["template_id"] == "t2i_basic" and meta["split_mode"] == "json"
    assert state["outputs"] == [str(saved[0])]
    assert (settings.logs_dir / "furry_agent.log").exists()


async def test_ksampler_log_reports_gate_seen_before_split(settings, caplog):
    caplog.set_level("INFO", logger="furry_agent")
    await _run("テスト", FakeComfy(), settings)
    assert "LM Studio unloaded at checkpoint load=[True]" in caplog.text


async def test_single_image_is_i2i_basic_and_deduplicated(settings):
    fake = FakeComfy()
    png = _png((10, 20, 30))
    content = [{"type": "text", "text": "この子を和服で"}, _block(png, name="r.png")]
    state = await _run(content, fake, settings)
    assert len(fake.uploads) == 1 and fake.uploads[0][2] == png
    assert not any(png == bytes(v) for v in json.dumps(state["references"]).encode().split())  # no bytes in state
    prompt = fake.submitted
    assert prompt["ref_image"]["inputs"]["image"].startswith("furry_ja/ref_")
    assert prompt["latent"]["class_type"] == "VAEEncode"
    assert prompt["sampler"]["inputs"]["denoise"] == 0.45
    assert state["references"][0]["resolved_role"] == "base"
    assert state["references"][0]["filename_on_comfy"] == prompt["ref_image"]["inputs"]["image"]

    await _run(content, fake, settings)  # same image again: no second upload
    assert len(fake.uploads) == 1


async def test_image_without_text_gets_default_instruction(settings):
    fake = FakeComfy()
    await _run([_block(_png())], fake, settings)
    assert fake.submitted["user_prompt"]["inputs"]["value"] == graph_module.DEFAULT_TEXT_FOR_IMAGES


async def test_three_roles_from_metadata(settings):
    fake = FakeComfy()
    content = [
        {"type": "text", "text": "夜の港で"},
        _block(_png((1, 1, 1)), role="character", strength=0.9),
        _block(_png((2, 2, 2)), role="pose"),
        _block(_png((3, 3, 3)), role="style", strength=0.3),
    ]
    state = await _run(content, fake, settings)
    prompt = fake.submitted
    assert state["plan"]["template_id"] == "character_pose_style_t2i"
    assert prompt["ipa_character"]["inputs"]["weight"] == 0.9
    assert prompt["ipa_style"]["inputs"]["weight"] == 0.3
    assert prompt["pose_apply"]["inputs"]["strength"] == 0.8
    assert len(fake.uploads) == 3
    images = _final_images(state)
    assert len(images) == 2 and base64.b64decode(images[1]["data"]) == fake.preview
    assert len(list(settings.outputs_dir.glob("*.png"))) == 1  # only the result is saved
    text = state["messages"][-1].content[0]["text"]
    assert "character 強度 0.90" in text and "pose 強度 0.80（openpose）" in text


async def test_roles_from_text(settings):
    fake = FakeComfy()
    content = [{"type": "text", "text": "AのキャラをBのポーズ、線画優先で"}, _block(_png((1, 1, 1))), _block(_png((2, 2, 2)))]
    state = await _run(content, fake, settings)
    assert state["plan"]["template_id"] == "character_pose_t2i"
    assert fake.submitted["pose_preprocess"]["class_type"] == "Canny"


async def test_configurable_references_fallback(settings):
    fake = FakeComfy()
    content = [{"type": "text", "text": "夜の港"}, _block(_png())]
    state = await _run(content, fake, settings, references=[{"index": 1, "role": "style", "strength": 0.7}])
    assert state["plan"]["template_id"] == "style_t2i"
    assert fake.submitted["ipa_style"]["inputs"]["weight"] == 0.7


async def test_five_images_rejected(settings):
    fake = FakeComfy()
    state = await _run([{"type": "text", "text": "x"}] + [_block(_png())] * 5, fake, settings)
    assert fake.submitted is None and "4 枚まで" in state["messages"][-1].content
    assert "［入力］" in state["messages"][-1].content


# --- confirmation interrupt -------------------------------------------------------------------------


@pytest.fixture
def hitl_graph():
    return graph_module.builder.compile(checkpointer=InMemorySaver())


async def _start_ambiguous(hitl_graph, fake, settings, thread="t1"):
    config = _config(fake, settings, thread_id=thread)
    content = [{"type": "text", "text": "いい感じに合わせて"}, _block(_png((1, 1, 1))), _block(_png((2, 2, 2)))]
    result = await hitl_graph.ainvoke({"messages": [HumanMessage(content=content)]}, config)
    return config, result


async def test_ambiguous_interrupts_before_generating(hitl_graph, settings):
    fake = FakeComfy()
    config, result = await _start_ambiguous(hitl_graph, fake, settings)
    assert fake.submitted is None and not fake.uploads
    request = result["__interrupt__"][0].value
    assert request["review_configs"][0]["allowed_decisions"] == ["approve", "edit", "reject"]
    assert request["action_requests"][0]["args"] == {"img_1": "character", "img_2": "pose"}

    state = await hitl_graph.ainvoke(Command(resume={"decisions": [{"type": "approve"}]}), config)
    assert state["plan"]["template_id"] == "character_pose_t2i"
    assert fake.submitted is not None and _final_images(state)


async def test_edit_changes_roles(hitl_graph, settings):
    fake = FakeComfy()
    config, _ = await _start_ambiguous(hitl_graph, fake, settings)
    edit = {"type": "edit", "edited_action": {"name": "generate_image", "args": {"img_1": "base", "img_2": "style"}}}
    state = await hitl_graph.ainvoke(Command(resume={"decisions": [edit]}), config)
    assert state["plan"]["template_id"] == "style_i2i"


async def test_reject_and_bad_edit_do_not_generate(hitl_graph, settings):
    fake = FakeComfy()
    config, _ = await _start_ambiguous(hitl_graph, fake, settings, "t2")
    state = await hitl_graph.ainvoke(Command(resume={"decisions": [{"type": "reject", "message": "no"}]}), config)
    assert fake.submitted is None and "中止" in state["messages"][-1].content

    config, _ = await _start_ambiguous(hitl_graph, fake, settings, "t3")
    edit = {"type": "edit", "edited_action": {"name": "generate_image", "args": {"img_1": "face", "img_2": "pose"}}}
    state = await hitl_graph.ainvoke(Command(resume={"decisions": [edit]}), config)
    assert fake.submitted is None and "face" in state["messages"][-1].content


async def test_previous_output_is_reused_as_base(hitl_graph, settings):
    fake = FakeComfy()
    config = _config(fake, settings, thread_id="reuse")
    await hitl_graph.ainvoke({"messages": [HumanMessage(content="白い狼")]}, config)
    assert not fake.uploads
    state = await hitl_graph.ainvoke({"messages": [HumanMessage(content="さっきの画像を夜にして")]}, config)
    assert state["plan"]["template_id"] == "i2i_basic"
    assert len(fake.uploads) == 1 and fake.uploads[0][2] == fake.image
    assert state["references"][0]["source"] == "previous_output"


# --- LoRA -------------------------------------------------------------------------------------------


async def test_loras_from_settings(settings):
    fake = FakeComfy()
    state = await _run("白い狼", fake, replace(settings, loras="kemonostyleav1:0.7, CiviFur-30"))
    prompt = fake.submitted
    assert prompt["lora_1"]["inputs"]["lora_name"] == "KemonoStyleAV1.safetensors"
    assert prompt["lora_1"]["inputs"]["strength_model"] == 0.7
    assert prompt["lora_2"]["inputs"]["model"] == ["lora_1", 0]
    assert prompt["sampler"]["inputs"]["model"] == ["lora_2", 0]
    assert "LoRA: kemonostyleav1, CiviFur-30" in state["messages"][-1].content[0]["text"]


async def test_missing_lora_fails_before_submit(settings):
    fake = FakeComfy()
    state = await _run("白い狼", fake, replace(settings, loras="nothere"))
    assert fake.submitted is None and "nothere" in state["messages"][-1].content


# --- errors -----------------------------------------------------------------------------------------


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


async def test_submit_error_is_reported_with_stage(settings):
    fake = FakeComfy(fail_at="submit")
    state = await _run("テスト", fake, settings)
    assert "生成できませんでした［キュー投入］" in state["messages"][-1].content
    assert len(state["messages"]) == 2


async def test_missing_nodes_point_to_setup_script(settings):
    fake = FakeComfy(node_types=NODE_TYPES - {"IPAdapterAdvanced"})
    state = await _run([{"type": "text", "text": "夜"}, _block(_png(), role="style")], fake, settings)
    assert fake.submitted is None
    content = state["messages"][-1].content
    assert "IPAdapterAdvanced" in content and "setup-comfyui-refs.ps1" in content


async def test_unknown_family(settings):
    fake = FakeComfy()
    state = await _run("夜", fake, replace(settings, model_family="flux"))
    assert fake.submitted is None and "flux" in state["messages"][-1].content


async def test_missing_unload_verification_fails(settings):
    fake = FakeComfy(gate_ok=False)
    state = await _run("テスト", fake, settings)
    assert "unload" in state["messages"][-1].content
    assert not list(settings.outputs_dir.glob("*.png"))
    assert fake.calls[-1] == "free"


async def test_unknown_checkpoint(settings):
    fake = FakeComfy()
    state = await _run("テスト", fake, replace(settings, ckpt_name="missing.safetensors"))
    assert "missing.safetensors" in state["messages"][-1].content
    assert fake.submitted is None


async def test_failed_run_releases_comfyui_memory(settings):
    fake = FakeComfy(fail_at="tags")
    state = await _run("テスト", fake, settings)
    assert "LM Connect Error" in state["messages"][-1].content
    assert fake.calls[-1] == "free"


async def test_timeout_returns_prompt_id_and_refetch_works(settings):
    fake = FakeComfy(fail_at="timeout")
    state = await _run("テスト", fake, settings)
    content = state["messages"][-1].content
    assert "［タイムアウト］" in content and "再取得 0b7e2f4a-1111-2222-3333-444455556666" in content

    fake = FakeComfy(fail_at="refetch")
    state = await _run("再取得 0b7e2f4a-1111-2222-3333-444455556666", fake, settings)
    assert fake.submitted is None and _final_images(state)


async def test_cancel_interrupts_comfyui(settings):
    fake = FakeComfy(fail_at="cancel")
    state = {"messages": [], "progress_id": "p", "job": {"prompt_id": "pid", "client_id": "c", "deadline": 1e12}}
    with pytest.raises(asyncio.CancelledError):
        await graph_module.await_tags(state, _config(fake, settings))
    assert fake.calls[:2] == ["interrupt:pid", "free"]


async def test_no_image_bytes_in_log(settings):
    fake = FakeComfy()
    png = _png((9, 9, 9))
    await _run([{"type": "text", "text": "直して"}, _block(png)], fake, settings)
    for handler in graph_module.log.handlers:
        if hasattr(handler, "flush"):
            handler.flush()
    await asyncio.sleep(0)
    log_text = (settings.logs_dir / "furry_agent.log").read_text(encoding="utf-8") if (settings.logs_dir / "furry_agent.log").exists() else ""
    assert base64.b64encode(png).decode()[:40] not in log_text


async def test_no_blocking_calls_in_event_loop(settings):
    # langgraph dev runs nodes under blockbuster and fails runs that block the loop.
    from blockbuster import blockbuster_ctx

    fake = FakeComfy()
    graph_module.log.handlers.clear()
    with blockbuster_ctx():
        state = await _run([{"type": "text", "text": "このキャラをこのポーズで"}, _block(_png((1, 1, 1))),
                            _block(_png((2, 2, 2)))], fake, settings)
    assert not state.get("error")
