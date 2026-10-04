import json
from pathlib import Path

import pytest

from furry_agent.bonsai_select import (Catalog, Rank, SelectionError, available_models, free_memory_mb, select_model,
                                       task_order)

ROOT = Path(__file__).resolve().parent.parent
CATALOG = Catalog.load(ROOT / "config" / "search_models.json")
ALL = {m: Path(f"/models/{s.file}") for m, s in CATALOG.models.items()}


def test_catalog_has_the_eight_models_and_user_roles():
    assert set(CATALOG.models) == {"bonsai-8b", "ternary-8b", "bonsai-4b", "bonsai-2-27b", "bonsai-2-27b-abliterated",
                                   "qwen3.5-4b-heretic", "qwen3-1.7b-heretic", "qwen3-0.6b-heretic"}
    assert CATALOG.tasks["synthesize"][0] == "bonsai-2-27b-abliterated"
    assert CATALOG.tasks["critique"][0] == "bonsai-2-27b-abliterated"
    assert CATALOG.tasks["worker"][:2] == ["ternary-8b", "qwen3.5-4b-heretic"]
    assert CATALOG.tasks["route"][0] == "qwen3-1.7b-heretic"
    assert CATALOG.tasks["filter"][0] == "bonsai-4b"
    # 1-bit Bonsai-8B and Qwen3-0.6B are only fallbacks.
    assert CATALOG.tasks["worker"].index("bonsai-8b") > CATALOG.tasks["worker"].index("qwen3.5-4b-heretic")
    assert CATALOG.tasks["route"][-1] == "qwen3-0.6b-heretic"
    assert CATALOG.models["bonsai-2-27b-abliterated"].file == "Ternary-Bonsai-2-27B-PTQ1_0-abliterated.gguf"


def test_large_model_never_chosen_while_leader_resident():
    rank = Rank()
    sel = select_model("synthesize", CATALOG, rank, ALL, 20000, leader_resident=True, reserve_mb=0)
    assert not sel.model.large
    assert "LM Studio の 27B" in sel.skipped["bonsai-2-27b-abliterated"]
    sel = select_model("synthesize", CATALOG, rank, ALL, 20000, leader_resident=False, reserve_mb=0)
    assert sel.model.id == "bonsai-2-27b-abliterated"


def test_comfy_resident_also_excludes_large():
    sel = select_model("synthesize", CATALOG, Rank(), ALL, 20000, leader_resident=False, comfy_resident=True,
                       reserve_mb=0)
    assert not sel.model.large


def test_width_is_bounded_by_memory_and_falls_back_to_smaller_model():
    mem = CATALOG.models["ternary-8b"].mem_mb
    sel = select_model("worker", CATALOG, Rank(), ALL, 3072 + 2 * mem + 10, leader_resident=False, max_width=3)
    assert sel.model.id == "ternary-8b" and sel.width == 2
    # Not even one ternary-8b fits: the next candidate that fits is used.
    small = CATALOG.models["qwen3-1.7b-heretic"].mem_mb
    sel = select_model("worker", CATALOG, Rank(), ALL, 3072 + small + 1, leader_resident=False, max_width=3)
    assert sel.model.id == "qwen3-1.7b-heretic" and sel.width == 1
    with pytest.raises(SelectionError, match="メモリ不足"):
        select_model("worker", CATALOG, Rank(), ALL, 3000, leader_resident=False)


def test_missing_files_and_failed_probe_are_skipped(tmp_path):
    available = {k: v for k, v in ALL.items() if k != "ternary-8b"}
    rank_file = tmp_path / "rank.json"
    rank_file.write_text(json.dumps({"models": {"qwen3.5-4b-heretic": {"tasks": {"worker": False}},
                                                "bonsai-8b": {"tasks": {"worker": True}, "mem_mb": 1500}}}),
                         encoding="utf-8")
    sel = select_model("worker", CATALOG, Rank.load(rank_file), available, 30000, leader_resident=False)
    assert sel.model.id == "bonsai-8b" and sel.mem_mb == 1500
    assert sel.skipped["ternary-8b"] == "重みファイルがありません"
    assert sel.skipped["qwen3.5-4b-heretic"] == "検証に通っていません"


def test_rank_order_overrides_catalog_order(tmp_path):
    rank_file = tmp_path / "rank.json"
    rank_file.write_text(json.dumps({"order": {"worker": ["qwen3.5-4b-heretic", "ternary-8b"]},
                                     "models": {m: {"tasks": {"worker": True}} for m in CATALOG.models}}),
                         encoding="utf-8")
    rank = Rank.load(rank_file)
    assert task_order(CATALOG, rank, "worker")[:3] == ["qwen3.5-4b-heretic", "ternary-8b", "bonsai-8b"]
    assert select_model("worker", CATALOG, rank, ALL, 30000, leader_resident=False).model.id == "qwen3.5-4b-heretic"


def test_override_is_refused_instead_of_silently_switching():
    with pytest.raises(SelectionError, match="メモリ不足"):
        select_model("worker", CATALOG, Rank(), ALL, 3500, leader_resident=False, override="ternary-8b")
    with pytest.raises(SelectionError, match="config/search_models.json"):
        select_model("worker", CATALOG, Rank(), ALL, 30000, leader_resident=False, override="nope")


def test_exclude_after_failed_start():
    sel = select_model("synthesize", CATALOG, Rank(), ALL, 30000, leader_resident=False,
                       exclude=("bonsai-2-27b-abliterated",))
    assert sel.model.id == "bonsai-2-27b"


def test_available_models_checks_size(tmp_path):
    spec = CATALOG.models["bonsai-4b"]
    (tmp_path / spec.file).write_bytes(b"x" * 10)  # partial download
    assert available_models(CATALOG, tmp_path) == {}


def test_free_memory_is_positive():
    assert free_memory_mb() > 0


def test_unprobed_models_are_allowed_failed_ones_are_not(tmp_path):
    rank_file = tmp_path / "rank.json"
    rank_file.write_text(json.dumps({"models": {"ternary-8b": {"tasks": {"worker": False}}, "bonsai-8b": {"tool_ok": False}}}),
                         encoding="utf-8")
    rank = Rank.load(rank_file)
    assert not rank.passed("ternary-8b", "worker") and not rank.passed("bonsai-8b", "worker")
    assert rank.passed("qwen3.5-4b-heretic", "worker")
