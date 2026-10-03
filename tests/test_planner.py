import itertools
import random

import pytest

from furry_agent.planner import (
    CONFIDENCE_THRESHOLD,
    REFERENCE_ROLES,
    build_plan,
    clamp_size,
    classify_intent,
    resolve_roles,
    select_workflow,
    template_roles,
    wants_previous_output,
)


def ref(i, role="auto", strength=None):
    return {
        "image_id": f"img_{i}", "role": role, "resolved_role": None, "filename_on_comfy": None,
        "sha256": f"{i:064x}", "width": 512, "height": 512, "strength": strength,
        "source": "attachment", "local_path": None, "mime": "image/png",
    }


def plan_for(text, refs):
    intent = classify_intent(text, refs)
    resolution = resolve_roles(refs, intent)
    return intent, resolution


# --- select_workflow ---------------------------------------------------------

@pytest.mark.parametrize("roles, expected", [
    ((), "t2i_basic"),
    (("base",), "i2i_basic"),
    (("style",), "style_t2i"),
    (("base", "style"), "style_i2i"),
    (("pose",), "pose_t2i"),
    (("base", "pose"), "pose_i2i"),
    (("character",), "character_t2i"),
    (("character", "pose"), "character_pose_t2i"),
    (("style", "pose", "character"), "character_pose_style_t2i"),
    (("base", "mask"), "inpaint_basic"),
    (("base", "mask", "style"), "style_inpaint"),
])
def test_select_workflow_table(roles, expected):
    assert select_workflow(roles) == expected
    assert select_workflow(list(reversed(roles))) == expected  # order independent
    assert template_roles(expected) == set(roles)


def test_every_role_set_has_a_unique_template():
    ids = set()
    for n in range(4):
        for refs in itertools.combinations(REFERENCE_ROLES, n):
            for extra in ((), ("base",), ("base", "mask")):
                ids.add(select_workflow(refs + extra))
    assert len(ids) == 24


@pytest.mark.parametrize("roles", [("auto",), ("mask",), ("nope",)])
def test_select_workflow_rejects_unresolved(roles):
    with pytest.raises(ValueError):
        select_workflow(roles)


# --- classify + resolve: WI §3.3 cases ----------------------------------------

def test_case1_style_only():
    _, res = plan_for("この絵の雰囲気で、夜の神戸港に立つキャラクター", [ref(1)])
    assert res.roles == {"img_1": "style"} and not res.needs_confirmation
    assert select_workflow(res.roles.values()) == "style_t2i"


def test_case2_pose_only():
    _, res = plan_for("このポーズで、白衣のケモノキャラ。背景はシンプル", [ref(1)])
    assert res.roles == {"img_1": "pose"} and not res.needs_confirmation


def test_case3_character_and_pose_in_text_order():
    _, res = plan_for("このキャラをこのポーズにして", [ref(1), ref(2)])
    assert res.roles == {"img_1": "character", "img_2": "pose"}
    assert not res.needs_confirmation


def test_case4_base_kept_and_style():
    _, res = plan_for("構図は維持して画風だけ寄せて", [ref(1), ref(2)])
    assert res.roles == {"img_1": "base", "img_2": "style"}
    assert select_workflow(res.roles.values()) == "style_i2i"


def test_case5_three_references_by_letter():
    _, res = plan_for("AのキャラをBのポーズ、Cの画風で", [ref(1), ref(2), ref(3)])
    assert res.roles == {"img_1": "character", "img_2": "pose", "img_3": "style"}
    assert select_workflow(res.roles.values()) == "character_pose_style_t2i"


def test_case6_vague_asks_for_confirmation():
    _, res = plan_for("いい感じに合わせて", [ref(1), ref(2)])
    assert res.needs_confirmation and res.reason
    # The proposal is complete so approving it can run.
    assert all(r != "auto" for r in res.proposal.values())


def test_ordinals_in_any_order():
    _, res = plan_for("2枚目の画風で、1枚目のキャラを描いて", [ref(1), ref(2)])
    assert res.roles == {"img_1": "character", "img_2": "style"}


def test_image_number_and_fullwidth_digits():
    _, res = plan_for("画像２のポーズで画像１の子を", [ref(1), ref(2)])
    assert res.roles["img_2"] == "pose"


def test_explicit_roles_win_and_skip_taken_roles():
    refs = [ref(1), ref(2, "pose")]
    _, res = plan_for("このキャラをこのポーズにして", refs)
    assert res.roles == {"img_1": "character", "img_2": "pose"}


def test_all_explicit_needs_no_text_understanding():
    # NFR-3: explicit roles continue regardless of the instruction.
    refs = [ref(1, "character"), ref(2, "pose"), ref(3, "style")]
    _, res = plan_for("いい感じに合わせて", refs)
    assert not res.needs_confirmation
    assert select_workflow(res.roles.values()) == "character_pose_style_t2i"


def test_duplicate_roles_keep_first():
    refs = [ref(1, "style"), ref(2, "style")]
    _, res = plan_for("この画風で", refs)
    assert res.roles == {"img_1": "style"} and "img_2" in res.ignored


def test_style_and_character_on_same_image_is_a_conflict():
    _, res = plan_for("1枚目のキャラと画風で、2枚目のポーズ", [ref(1), ref(2)])
    assert res.needs_confirmation and "画風とキャラクター" in res.reason


def test_mask_without_base_needs_confirmation():
    _, res = plan_for("ここを直して", [ref(1, "mask")])
    assert res.needs_confirmation


def test_fewer_keywords_than_images_is_low_confidence():
    intent, res = plan_for("このポーズで描いて", [ref(1), ref(2)])
    assert intent.role_guesses["img_1"] == ("pose", 0.5)
    assert intent.role_guesses["img_1"][1] < CONFIDENCE_THRESHOLD
    assert res.needs_confirmation


# --- §4.11 compatibility -------------------------------------------------------

@pytest.mark.parametrize("text", ["この子を和服で", "背景を夜にして", "直して", "笑顔にして", ""])
def test_single_image_defaults_to_base(text):
    _, res = plan_for(text, [ref(1)])
    assert res.roles == {"img_1": "base"} and not res.needs_confirmation
    assert select_workflow(res.roles.values()) == "i2i_basic"


def test_single_image_character_for_new_scene():
    _, res = plan_for("このキャラで海辺のシーンを描いて", [ref(1)])
    assert res.roles == {"img_1": "character"}


def test_keep_composition_is_base():
    _, res = plan_for("構図はそのままで色を変えて", [ref(1)])
    assert res.roles == {"img_1": "base"}


# --- parameters ------------------------------------------------------------------

def test_parameters_from_text_are_removed_from_instruction():
    intent = classify_intent("夜の港 seed 1234 1000x700 線画優先", [])
    assert intent.seed == 1234 and (intent.width, intent.height) == (1000, 700)
    assert intent.pose_preprocessor == "canny"
    assert "1234" not in intent.text and "1000" not in intent.text and "夜の港" in intent.text


def test_landscape_and_depth_keywords():
    intent = classify_intent("横長で奥行き優先", [])
    assert (intent.width, intent.height) == (1216, 832) and intent.pose_preprocessor == "depth"


def test_clamp_size():
    assert clamp_size(100) == 512 and clamp_size(5000) == 1536 and clamp_size(1000) == 1024
    assert all(clamp_size(v) % 64 == 0 for v in range(300, 2000, 37))


def test_build_plan_defaults_and_random_seed():
    refs = [ref(1), ref(2)]
    intent, res = plan_for("このキャラをこのポーズにして", refs)
    plan = build_plan("character_pose_t2i", "sdxl", refs, res.roles, intent, rng=random.Random(1))
    assert plan["strengths"] == {"pose": 0.8, "character": 0.85}
    assert plan["denoise"] is None and plan["pose_preprocessor"] == "openpose"
    assert (plan["width"], plan["height"], plan["steps"], plan["cfg"]) == (832, 1216, 28, 5.5)
    assert 0 <= plan["seed"] < 2**32 and not plan["notes"]


def test_build_plan_clamps_and_notes():
    refs = [ref(1, "base", strength=0.99), ref(2, "style", strength=3.0)]
    intent = classify_intent("seed 7 3000x100", refs)
    res = resolve_roles(refs, intent)
    plan = build_plan("style_i2i", "sdxl", refs, res.roles, intent)
    assert plan["seed"] == 7
    assert plan["strengths"]["style"] == 1.0
    assert plan["denoise"] == 0.85
    assert (plan["width"], plan["height"]) == (1536, 512)
    assert len(plan["notes"]) == 3


@pytest.mark.parametrize("template, roles, expected", [
    ("i2i_basic", {"a": "base"}, 0.45),
    ("style_i2i", {"a": "base", "b": "style"}, 0.45),
    ("pose_i2i", {"a": "base", "b": "pose"}, 0.65),
    ("inpaint_basic", {"a": "base", "b": "mask"}, 0.75),
])
def test_default_denoise(template, roles, expected):
    refs = [ref(1), ref(2)]
    refs[0]["image_id"], refs[1]["image_id"] = "a", "b"
    plan = build_plan(template, "sdxl", refs, roles, classify_intent("", refs))
    assert plan["denoise"] == expected


def test_strength_hint_from_text():
    refs = [ref(1, "style")]
    intent = classify_intent("画風を強めに", refs)
    plan = build_plan("style_t2i", "sdxl", refs, {"img_1": "style"}, intent)
    assert plan["strengths"]["style"] == 0.8


def test_previous_output_words():
    assert wants_previous_output("さっきの画像を夜にして", has_attachments=False)
    assert wants_previous_output("これを和服にして", has_attachments=False)
    assert not wants_previous_output("この画像の画風で", has_attachments=True)
    assert not wants_previous_output("夜の港", has_attachments=False)
