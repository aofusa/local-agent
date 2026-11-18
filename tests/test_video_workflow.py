"""The Wan 2.2 image-to-video template (docs/image-to-video-design.md §5): fixed node ids, one large model at a
time (every loader behind the previous stage), slots, and the per-expert LoRA insertion."""

import importlib.util
import json
from pathlib import Path

import pytest

from furry_agent.templates import (
    LoraSpec,
    TemplateError,
    build_video_prompt,
    insert_model_loras,
    load_map,
    load_template,
)

ROOT = Path(__file__).resolve().parent.parent
MAP = load_map("wan22")
FIXED_IDS = {
    "llm_backend", "llm_backend_vision", "user_prompt", "ref_image", "vision", "prompt_join", "prompt_node", "eject",
    "split", "te", "positive", "negative", "release_te", "vae", "i2v", "unet_high", "shift_high", "sampler_high",
    "release_high", "unet_low", "shift_low", "sampler_low", "release_low", "decode", "video", "save",
}
KNOWN_TYPES = {
    "LMConnectLMStudioBackend", "PrimitiveStringMultiline", "LoadImage", "LMConnectVision", "StringConcatenate",
    "LMConnectPromptWithSystem", "FurryJaEjectLLM", "FurryJaSplitTags", "FurryJaTextEncoderAfter", "CLIPTextEncode",
    "FurryJaReleaseAfter", "FurryJaVAELoaderAfter", "WanImageToVideo", "FurryJaVideoModelAfter", "ModelSamplingSD3",
    "KSamplerAdvanced", "VAEDecodeTiled", "CreateVideo", "SaveVideo",
}
DISTILL_HIGH = LoraSpec("wan2.2_i2v_A14b_high_noise_lora_rank64_lightx2v_4step_1022.safetensors", 1.0, 1.0)
DISTILL_LOW = LoraSpec("wan2.2_i2v_A14b_low_noise_lora_rank64_lightx2v_4step_1022.safetensors", 1.0, 1.0)
FURRY_HIGH = LoraSpec("Furry Enhancer Wan2.2 V3 High Noise I2V.safetensors", 0.8, 0.8)
FURRY_LOW = LoraSpec("Furry Enhancer Wan2.2 V3 Low Noise I2V .safetensors", 1.0, 1.0)


def _template():
    prompt, _ = load_template("wan22", "i2v")
    return prompt


def _ancestors(prompt, node_id, seen=None):
    seen = set() if seen is None else seen
    for value in prompt[node_id]["inputs"].values():
        if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and value[0] not in seen:
            seen.add(value[0])
            _ancestors(prompt, value[0], seen)
    return seen


def _values(**kw):
    values = {"prompt": "しっぽを振る", "start_image": "furry_ja/start.png", "seed": 7,
              "high_name": "high.gguf", "low_name": "low.gguf", "width": 448, "height": 656, "length": 49,
              "steps": 4, "split_step": 2, "cfg": 1.0, "shift": 5.0, "sampler_name": "euler", "scheduler": "simple",
              "fps": 16.0, "crf": 20.0, "vae_tile": 256, "vae_overlap": 64, "vae_temporal": 16,
              "vae_temporal_overlap": 4, "te_device": "default"}
    return {**values, **kw}


def test_generated_files_are_up_to_date():
    spec = importlib.util.spec_from_file_location("build_workflows", ROOT / "scripts" / "build_workflows.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    mapping, prompts = module.video_map("wan22")
    assert mapping == MAP
    for template_id, prompt in prompts.items():
        path = ROOT / "workflows" / mapping["templates"][template_id]["file"]
        assert prompt == json.loads(path.read_text(encoding="utf-8")), template_id


def test_fixed_node_ids_and_types():
    prompt = _template()
    assert set(prompt) == FIXED_IDS
    assert {node["class_type"] for node in prompt.values()} <= KNOWN_TYPES
    assert MAP["kind"] == "video" and MAP["supported_roles"] == ["base"]


def test_large_models_load_one_at_a_time():
    prompt = _template()
    # Every loader waits for the LLM eject; the order is te -> release_te -> vae -> high -> release_high -> low.
    for loader in ("te", "vae", "unet_high", "unet_low"):
        assert "eject" in _ancestors(prompt, loader), loader
    assert {"positive", "negative", "release_te"} <= _ancestors(prompt, "vae")
    assert {"release_te", "i2v"} <= _ancestors(prompt, "unet_high")
    assert {"sampler_high", "release_high", "unet_high"} <= _ancestors(prompt, "unet_low")
    assert {"sampler_low", "release_low"} <= _ancestors(prompt, "decode")
    # The release nodes see their stage's outputs, and the text encoder is never an input after release_te.
    assert prompt["release_te"]["inputs"]["a"] == ["positive", 0]
    assert prompt["release_te"]["inputs"]["b"] == ["negative", 0]
    assert prompt["release_high"]["inputs"]["a"] == ["sampler_high", 0]
    assert prompt["sampler_low"]["inputs"]["latent_image"] == ["release_high", 0]
    assert "te" not in {v[0] for v in prompt["i2v"]["inputs"].values() if isinstance(v, list)}


def test_two_stage_sampling_splits_the_steps():
    prompt = _template()
    high, low = prompt["sampler_high"]["inputs"], prompt["sampler_low"]["inputs"]
    assert (high["add_noise"], high["return_with_leftover_noise"]) == ("enable", "enable")
    assert (low["add_noise"], low["return_with_leftover_noise"]) == ("disable", "disable")
    assert high["start_at_step"] == 0 and high["end_at_step"] == low["start_at_step"] == 2
    assert high["steps"] == low["steps"] == 4 and high["cfg"] == low["cfg"] == 1.0
    assert prompt["shift_high"]["inputs"]["shift"] == prompt["shift_low"]["inputs"]["shift"] == 5.0


def test_llm_part_matches_the_image_contract():
    prompt = _template()
    assert prompt["vision"]["inputs"]["image_1"] == ["ref_image", 0]
    assert prompt["prompt_node"]["inputs"]["prompt"] == ["prompt_join", 0]
    assert prompt["eject"]["inputs"]["passthrough"] == ["prompt_node", 0]
    assert prompt["split"]["inputs"]["text"] == ["eject", 0]
    assert prompt["split"]["inputs"]["prompt_style"] == "prose"
    assert prompt["i2v"]["inputs"]["start_image"] == ["ref_image", 0]
    system = prompt["prompt_node"]["inputs"]["system_prompt"]
    assert '{"positive":"...","negative":"..."}' in system and "kemono" in system


def test_save_writes_mp4_h264():
    save = _template()["save"]["inputs"]
    assert save["format"] == "mp4" and save["format.codec"] == "h264"
    assert save["format.codec.encoding"] == "re-encode" and save["format.codec.encoding.crf"] == 20.0


def test_every_slot_exists_in_template():
    prompt = _template()
    for slot, paths in MAP["templates"]["i2v"]["slots"].items():
        for path in paths if isinstance(paths, list) else [paths]:
            node_id, section, field = path.split(".", 2)
            assert section == "inputs" and field in prompt[node_id]["inputs"], (slot, path)


def test_build_video_prompt_injects_values_into_both_experts():
    prompt = build_video_prompt("wan22", "i2v", _values(seed=123, steps=6, split_step=3, crf=23.0))
    for sampler in ("sampler_high", "sampler_low"):
        assert prompt[sampler]["inputs"]["noise_seed"] == 123
        assert prompt[sampler]["inputs"]["steps"] == 6
    assert prompt["sampler_high"]["inputs"]["end_at_step"] == prompt["sampler_low"]["inputs"]["start_at_step"] == 3
    assert prompt["unet_high"]["inputs"]["unet_name"] == "high.gguf"
    assert prompt["unet_low"]["inputs"]["unet_name"] == "low.gguf"
    assert (prompt["i2v"]["inputs"]["width"], prompt["i2v"]["inputs"]["height"]) == (448, 656)
    assert prompt["i2v"]["inputs"]["length"] == 49
    assert prompt["ref_image"]["inputs"]["image"] == "furry_ja/start.png"
    assert prompt["user_prompt"]["inputs"]["value"] == "しっぽを振る"
    assert prompt["save"]["inputs"]["format.codec.encoding.crf"] == 23.0


def test_build_video_prompt_sets_the_llm_read_timeout():
    prompt = build_video_prompt("wan22", "i2v", _values(), llm_read_timeout_s=1200)
    for node_id in ("llm_backend", "llm_backend_vision"):
        assert prompt[node_id]["inputs"]["read_timeout_seconds"] == 1200


def test_missing_required_slot():
    with pytest.raises(TemplateError, match="start_image"):
        build_video_prompt("wan22", "i2v", _values(start_image=None))


def test_loras_follow_each_expert_in_order():
    prompt = build_video_prompt("wan22", "i2v", _values(), [DISTILL_HIGH, FURRY_HIGH], [DISTILL_LOW, FURRY_LOW])
    assert prompt["lora_high_1"]["class_type"] == "LoraLoaderModelOnly"
    assert prompt["lora_high_1"]["inputs"]["model"] == ["unet_high", 0]
    assert prompt["lora_high_1"]["inputs"]["lora_name"] == DISTILL_HIGH.name
    assert prompt["lora_high_2"]["inputs"]["model"] == ["lora_high_1", 0]
    assert prompt["lora_high_2"]["inputs"]["strength_model"] == 0.8
    assert prompt["shift_high"]["inputs"]["model"] == ["lora_high_2", 0]
    assert prompt["lora_low_2"]["inputs"]["lora_name"] == FURRY_LOW.name
    assert prompt["shift_low"]["inputs"]["model"] == ["lora_low_2", 0]
    # A LoRA loads and unloads with its expert: the low-noise chain still waits for release_high.
    assert "release_high" in _ancestors(prompt, "lora_low_1")
    assert "release_high" not in _ancestors(prompt, "lora_high_1")


def test_no_loras_leave_the_template_unchanged():
    prompt = build_video_prompt("wan22", "i2v", _values())
    assert not any(node_id.startswith("lora_") for node_id in prompt)
    assert prompt["shift_high"]["inputs"]["model"] == ["unet_high", 0]


def test_lora_only_on_one_expert():
    prompt = build_video_prompt("wan22", "i2v", _values(), [], [FURRY_LOW])
    assert "lora_high_1" not in prompt and prompt["lora_low_1"]["inputs"]["lora_name"] == FURRY_LOW.name


def test_insert_model_loras_repoints_every_consumer():
    prompt = {"m": {"class_type": "X", "inputs": {}}, "a": {"class_type": "Y", "inputs": {"model": ["m", 0]}},
              "b": {"class_type": "Y", "inputs": {"model": ["m", 0], "other": ["m", 1]}}}
    insert_model_loras(prompt, [LoraSpec("x.safetensors", 0.5, 0.5)], "m", "lora_m")
    assert prompt["a"]["inputs"]["model"] == prompt["b"]["inputs"]["model"] == ["lora_m_1", 0]
    assert prompt["b"]["inputs"]["other"] == ["m", 1]
