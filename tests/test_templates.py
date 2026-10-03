import importlib.util
import json
from pathlib import Path

import pytest

from furry_agent.planner import select_workflow, template_roles
from furry_agent.templates import (
    LoraSpec,
    TemplateError,
    build_run_prompt,
    insert_loras,
    load_map,
    load_template,
    parse_loras,
    resolve_lora_names,
    unknown_node_types,
)
from furry_agent.workflow import FIXED_NODE_IDS, build_prompt, load_workflow

ROOT = Path(__file__).resolve().parent.parent
MAP = load_map("sdxl")
TEMPLATE_IDS = sorted(MAP["templates"])
KNOWN_TYPES = {
    "LMConnectLMStudioBackend", "PrimitiveStringMultiline", "LoadImage", "LMConnectVision", "StringConcatenate",
    "LMConnectPromptWithSystem", "LMConnectEjectLMStudioModel", "FurryJaSplitTags", "FurryJaCheckpointLoaderAfterEject",
    "CLIPTextEncode", "ImageScaleToTotalPixels", "EmptyLatentImage", "VAEEncode", "KSampler", "VAEDecode", "SaveImage",
    "IPAdapterUnifiedLoader", "IPAdapterAdvanced", "FurryJaImageAfter", "DWPreprocessor", "DiffControlNetLoader",
    "SetUnionControlNetType", "ControlNetApplyAdvanced", "PreviewImage", "ImageToMask", "SetLatentNoiseMask",
    "LoraLoader", "Canny", "DepthAnythingV2Preprocessor", "FurryJaReleaseEncoders",
}


def _plan(template_id, **kw):
    plan = {"template_id": template_id, "model_family": "sdxl", "positive": "x", "negative": "", "width": 832,
            "height": 1216, "seed": 5, "steps": 28, "cfg": 5.5, "denoise": None, "strengths": {},
            "pose_preprocessor": None, "needs_confirmation": False, "confirmation_reason": None, "notes": []}
    return {**plan, **kw}


def _images(template_id):
    return {role: f"furry_ja/{role}.png" for role in template_roles(template_id)}


def _ancestors(prompt, node_id, seen=None):
    seen = set() if seen is None else seen
    for value in prompt[node_id]["inputs"].values():
        if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and value[0] not in seen:
            seen.add(value[0])
            _ancestors(prompt, value[0], seen)
    return seen


def _links(prompt):
    for node_id, node in prompt.items():
        for name, value in node["inputs"].items():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[1], int):
                yield node_id, name, value


def test_catalog_matches_selector():
    assert len(TEMPLATE_IDS) == 24
    for template_id in TEMPLATE_IDS:
        assert select_workflow(template_roles(template_id)) == template_id
        assert sorted(template_roles(template_id)) == MAP["templates"][template_id]["roles"]


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_every_slot_exists_in_template(template_id):
    prompt, entry = load_template("sdxl", template_id)
    assert set(FIXED_NODE_IDS) - {"ref_image", "vision"} <= set(prompt)
    for name, path in entry["slots"].items():
        node_id = path.split(".")[0]
        assert node_id in prompt, f"{template_id}: slot {name} -> {node_id}"
        if path.count(".") == 2:
            assert path.split(".")[2] in prompt[node_id]["inputs"]
    for node_id, name, (src, _) in _links(prompt):
        assert src in prompt, f"{template_id}: {node_id}.{name} -> missing {src}"
    assert not unknown_node_types(prompt, KNOWN_TYPES)


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_heavy_nodes_only_after_eject(template_id):
    # AGENTS.md: checkpoint, IP-Adapter, ControlNet and preprocessors never load next to the 27B.
    prompt, _ = load_template("sdxl", template_id)
    for node_id, node in prompt.items():
        if node["class_type"] in {"FurryJaCheckpointLoaderAfterEject", "IPAdapterUnifiedLoader", "IPAdapterAdvanced",
                                  "DiffControlNetLoader", "DWPreprocessor", "KSampler"}:
            assert "eject" in _ancestors(prompt, node_id), f"{template_id}: {node_id}"
    assert {"eject", "split", "prompt_node", "ckpt"} <= _ancestors(prompt, "sampler")


@pytest.mark.parametrize("template_id", ["style_t2i", "character_pose_style_t2i", "pose_i2i"])
def test_role_vision_feeds_prompt_node(template_id):
    prompt, _ = load_template("sdxl", template_id)
    upstream = _ancestors(prompt, "prompt_node")
    for role in template_roles(template_id) - {"base", "mask"}:
        assert f"vision_{role}" in upstream
    if "base" in template_roles(template_id):
        assert "vision" in upstream


def test_style_and_character_use_ipadapter_pose_uses_controlnet():
    prompt, _ = load_template("sdxl", "character_pose_style_t2i")
    assert prompt["ipa_style"]["inputs"]["weight_type"] == "style transfer"
    assert prompt["ipa_style"]["inputs"]["image"] == ["style_image", 0]
    assert prompt["ipa_character"]["inputs"]["image"] == ["character_image", 0]
    assert prompt["release"]["inputs"] == {"model": ["ipa_style", 0], "positive": ["pose_apply", 0],
                                           "negative": ["pose_apply", 1]}
    assert [prompt["sampler"]["inputs"][k] for k in ("model", "positive", "negative")] == [
        ["release", 0], ["release", 1], ["release", 2]]
    assert prompt["pose_apply"]["inputs"]["image"] == ["pose_preprocess", 0]
    assert prompt["latent"]["class_type"] == "EmptyLatentImage"
    # The pose image's pixels only reach ControlNet (no identity from the pose reference);
    # its text tags reach the LLM like every other section.
    pixel_users = {n for n, _, v in _links(prompt) if v == ["pose_image", 0]}
    assert pixel_users == {"vision_pose", "pose_gate"}
    assert prompt["latent"]["inputs"] == {"width": 832, "height": 1216, "batch_size": 1}


def test_basic_templates_equal_phase1_prompts():
    # WI §8.4: templating must not change what text-only / single-image runs submit.
    workflow = load_workflow(ROOT / "workflows" / "furry_ja_api.json")
    t2i = build_run_prompt("sdxl", _plan("t2i_basic"), {}, "夕焼けの狼", ckpt_name="a.safetensors")
    assert t2i == build_prompt(workflow, "夕焼けの狼", 5, None, "a.safetensors")
    i2i = build_run_prompt("sdxl", _plan("i2i_basic", denoise=0.45), {"base": "furry_ja/b.png"}, "和服で")
    assert i2i == build_prompt(workflow, "和服で", 5, ["furry_ja/b.png"])


def test_injects_values_into_slots():
    plan = _plan("character_pose_style_t2i", width=1024, height=768,
                 strengths={"character": 0.9, "pose": 1.1, "style": 0.4}, pose_preprocessor="depth")
    prompt = build_run_prompt("sdxl", plan, _images("character_pose_style_t2i"), "指示")
    assert prompt["user_prompt"]["inputs"]["value"] == "指示"
    assert prompt["sampler"]["inputs"]["seed"] == 5
    assert (prompt["latent"]["inputs"]["width"], prompt["latent"]["inputs"]["height"]) == (1024, 768)
    assert prompt["ipa_character"]["inputs"]["weight"] == 0.45  # 0.9 x character scale 0.5
    assert prompt["ipa_style"]["inputs"]["weight"] == 0.4      # style scale 1.0
    assert prompt["pose_apply"]["inputs"]["strength"] == 1.1
    assert prompt["pose_image"]["inputs"]["image"] == "furry_ja/pose.png"
    assert prompt["pose_preprocess"]["class_type"] == "DepthAnythingV2Preprocessor"
    assert prompt["pose_preprocess"]["inputs"]["image"] == ["pose_gate", 0]
    assert prompt["pose_union"]["inputs"]["type"] == "depth"


def test_inpaint_uses_mask():
    prompt = build_run_prompt("sdxl", _plan("inpaint_basic", denoise=0.7), _images("inpaint_basic"), "直して")
    assert prompt["mask_image"]["inputs"]["image"] == "furry_ja/mask.png"
    assert prompt["sampler"]["inputs"]["latent_image"] == ["latent_mask", 0]
    assert prompt["sampler"]["inputs"]["denoise"] == 0.7


def test_missing_required_slot():
    with pytest.raises(TemplateError, match="必須スロット"):
        build_run_prompt("sdxl", _plan("style_t2i"), {}, "x")


def test_unknown_family_and_template():
    with pytest.raises(TemplateError, match="flux"):
        load_template("flux", "t2i_basic")
    with pytest.raises(TemplateError):
        load_template("sdxl", "nope_t2i")


def test_parse_loras():
    assert parse_loras("") == []
    specs = parse_loras("a.safetensors:0.8, b ; c:0.5:0.2\n")
    assert specs == [LoraSpec("a.safetensors", 0.8, 0.8), LoraSpec("b", 1.0, 1.0), LoraSpec("c", 0.5, 0.2)]
    for bad in ("a:x", "a:1:2:3", "a:9"):
        with pytest.raises(TemplateError):
            parse_loras(bad)


def test_resolve_lora_names():
    available = ["KemonoStyleAV1.safetensors", "sub\\CiviFur-30.safetensors"]
    specs = resolve_lora_names(parse_loras("kemonostyleav1:0.7, CiviFur-30"), available)
    assert [s.name for s in specs] == available
    with pytest.raises(TemplateError, match="missing"):
        resolve_lora_names(parse_loras("missing"), available)


@pytest.mark.parametrize("template_id", ["t2i_basic", "character_pose_style_t2i", "pose_i2i"])
def test_lora_chain_after_checkpoint(template_id):
    loras = [LoraSpec("a.safetensors", 0.8, 0.8), LoraSpec("b.safetensors", 0.5, 0.3)]
    prompt = build_run_prompt("sdxl", _plan(template_id), _images(template_id), "x", loras=loras)
    assert prompt["lora_1"]["inputs"]["model"] == ["ckpt", 0]
    assert prompt["lora_2"]["inputs"]["model"] == ["lora_1", 0]
    assert prompt["lora_2"]["inputs"]["strength_clip"] == 0.3
    refs = [(n, name) for n, name, v in _links(prompt) if v[0] == "ckpt" and v[1] in (0, 1) and not n.startswith("lora_")]
    assert refs == []
    assert prompt["positive"]["inputs"]["clip"] == ["lora_2", 1]
    assert "lora_2" in _ancestors(prompt, "sampler")
    assert "eject" in _ancestors(prompt, "lora_1")


def test_no_loras_leaves_prompt_unchanged():
    prompt, _ = load_template("sdxl", "style_t2i")
    assert insert_loras(json.loads(json.dumps(prompt)), []) == prompt


def test_generated_files_are_up_to_date():
    spec = importlib.util.spec_from_file_location("build_workflows", ROOT / "scripts" / "build_workflows.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    mapping, prompts = module.node_map()
    assert mapping == json.loads((ROOT / "workflows" / "maps" / "sdxl.json").read_text(encoding="utf-8"))
    for template_id, prompt in prompts.items():
        path = ROOT / "workflows" / mapping["templates"][template_id]["file"]
        assert prompt == json.loads(path.read_text(encoding="utf-8")), template_id


@pytest.mark.parametrize("template_id", [t for t in TEMPLATE_IDS if template_roles(t) & {"character", "style", "pose"}])
def test_encoders_released_after_all_conditioning(template_id):
    # The release node must run after every text/CLIP-Vision encode, i.e. they are all its ancestors.
    prompt, _ = load_template("sdxl", template_id)
    upstream = _ancestors(prompt, "release")
    encoders = {n for n, node in prompt.items() if node["class_type"] in ("CLIPTextEncode", "IPAdapterAdvanced")}
    assert encoders <= upstream
    assert prompt["sampler"]["inputs"]["model"] == ["release", 0]


def test_basic_templates_have_no_release_node():
    for template_id in ("t2i_basic", "i2i_basic", "inpaint_basic"):
        assert "release" not in load_template("sdxl", template_id)[0]
