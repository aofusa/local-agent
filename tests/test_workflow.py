import json
from pathlib import Path

import pytest

from furry_agent.workflow import FIXED_NODE_IDS, build_prompt, load_workflow

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def workflow():
    return load_workflow(ROOT / "workflows" / "furry_ja_api.json")


def _ancestors(prompt, node_id, seen=None):
    seen = set() if seen is None else seen
    for value in prompt[node_id]["inputs"].values():
        if isinstance(value, list) and value[0] not in seen:
            seen.add(value[0])
            _ancestors(prompt, value[0], seen)
    return seen


def _assert_links_resolve(prompt):
    for node_id, node in prompt.items():
        for name, value in node["inputs"].items():
            if isinstance(value, list):
                assert value[0] in prompt, f"{node_id}.{name} -> missing {value[0]}"


def test_fixed_ids_and_contract(workflow):
    assert set(FIXED_NODE_IDS) <= set(workflow)
    backend = workflow["llm_backend"]["inputs"]
    assert backend["base_url"] == "http://127.0.0.1:8080/v1"
    # LM Connect's auto-eject speaks LM Studio's API; the eject node unloads the router's model instead.
    assert backend["auto_eject_after_run"] is False
    assert workflow["eject"]["class_type"] == "FurryJaEjectLLM"
    assert workflow["eject"]["inputs"]["base_url"] == backend["base_url"]
    assert workflow["ckpt"]["inputs"]["llm_base_url"] == backend["base_url"]
    assert backend["disable_thinking"] is True
    sampler = workflow["sampler"]["inputs"]
    assert (sampler["steps"], sampler["cfg"], sampler["sampler_name"], sampler["scheduler"]) == (28, 5.5, "euler_ancestral", "normal")
    latent = workflow["latent"]["inputs"]
    assert (latent["width"], latent["height"], latent["batch_size"]) == (832, 1216, 1)
    assert workflow["vision"]["inputs"]["max_image_dimension"] == 768


def test_system_prompt_embedded_from_file(workflow):
    text = (ROOT / "prompts" / "system_furry_tags.txt").read_text(encoding="utf-8").strip()
    assert workflow["prompt_node"]["inputs"]["system_prompt"] == text


def test_checkpoint_and_sampler_only_after_eject(workflow):
    # The checkpoint loader and KSampler must depend on eject, so the 27B is
    # unloaded before the checkpoint is loaded (AGENTS.md: unload order).
    for prompt in (build_prompt(workflow, "テスト", 1), build_prompt(workflow, "テスト", 1, ["a.png"])):
        assert "eject" in _ancestors(prompt, "ckpt")
        assert {"eject", "split", "prompt_node", "ckpt"} <= _ancestors(prompt, "sampler")
        assert "prompt_node" in _ancestors(prompt, "eject")


def test_text_only(workflow):
    prompt = build_prompt(workflow, "白い狼の獣人", 42)
    assert prompt["user_prompt"]["inputs"]["value"] == "白い狼の獣人"
    assert prompt["sampler"]["inputs"]["seed"] == 42
    assert prompt["sampler"]["inputs"]["denoise"] == 1.0
    assert prompt["latent"]["class_type"] == "EmptyLatentImage"
    assert prompt["prompt_node"]["inputs"]["prompt"] == ["user_prompt", 0]
    for removed in ("ref_image", "ref_image_2", "vision", "prompt_join", "ref_scale"):
        assert removed not in prompt
    _assert_links_resolve(prompt)
    # input workflow is untouched
    assert "ref_image" in workflow


def test_one_reference_is_img2img(workflow):
    prompt = build_prompt(workflow, "同じ構図で", 7, ["furry_ja/ref_1.png"], ckpt_name="other.safetensors")
    assert prompt["ref_image"]["inputs"]["image"] == "furry_ja/ref_1.png"
    assert "ref_image_2" not in prompt and "image_2" not in prompt["vision"]["inputs"]
    assert prompt["latent"]["class_type"] == "VAEEncode"
    assert prompt["latent"]["inputs"] == {"pixels": ["ref_scale", 0], "vae": ["ckpt", 2]}
    assert prompt["sampler"]["inputs"]["denoise"] == 0.45
    assert prompt["prompt_node"]["inputs"]["prompt"] == ["prompt_join", 0]
    assert prompt["ckpt"]["inputs"]["ckpt_name"] == "other.safetensors"
    _assert_links_resolve(prompt)


def test_two_references_feed_vision(workflow):
    prompt = build_prompt(workflow, "二人", 7, ["a.png", "b.png"])
    assert prompt["ref_image_2"]["inputs"]["image"] == "b.png"
    assert prompt["vision"]["inputs"]["image_2"] == ["ref_image_2", 0]
    _assert_links_resolve(prompt)


def test_too_many_references(workflow):
    with pytest.raises(ValueError):
        build_prompt(workflow, "x", 1, ["a", "b", "c"])


def test_ui_workflow_is_consistent():
    ui = json.loads((ROOT / "workflows" / "furry_ja.json").read_text(encoding="utf-8"))
    nodes = {n["id"]: n for n in ui["nodes"]}
    titles = {n["properties"].get("furry_ja_id") for n in ui["nodes"]}
    assert set(FIXED_NODE_IDS) <= titles
    for link_id, src, src_slot, dst, dst_slot, _type in ui["links"]:
        assert link_id in nodes[src]["outputs"][src_slot]["links"]
        assert nodes[dst]["inputs"][dst_slot]["link"] == link_id


def test_build_workflows_env_override(tmp_path, monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("build_workflows", ROOT / "scripts" / "build_workflows.py")
    monkeypatch.setenv("LLM_MODEL", "some-model@q4_k_m")
    monkeypatch.setenv("CKPT_NAME", "other.safetensors")  # no longer read: the run injects its model's file
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    api = module.api_workflow()
    assert api["llm_backend"]["inputs"]["model"] == "some-model@q4_k_m"
    assert api["llm_backend_vision"]["inputs"]["model"] == "some-model@q4_k_m"
    assert api["ckpt"]["inputs"]["ckpt_name"] == "yiffInHell_yihVANTABLACK.safetensors"
