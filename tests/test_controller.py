"""The chat tab's control loop (docs/autonomous-controller-design.md §13), with the fakes of test_chat_graph:
no Tor, LM Studio, llama.cpp, ComfyUI or Docker."""

import json
from dataclasses import replace

import pytest
from langchain_core.messages import HumanMessage
from langgraph.graph import END
from langgraph.types import Command

from furry_agent import chat_graph, control_nodes as cn, modes
from furry_agent.job_lock import job_lock
from furry_agent.router import CODE, SEARCH, WRITE, Route, compound_kinds, is_compound, route

from test_chat_graph import (World, _config, _hitl_graph, _interrupt, _no_shared_log_handler,  # noqa: F401
                             _run, _runs, _settings, models_dir)


def _tool(tool, text, reason="r"):
    return {"action": "tool", "tool": tool, "text": text, "reason": reason}


def _final(answer="答えです。"):
    return {"action": "final", "answer": answer, "reason": "done"}


# --- routing into the loop ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("text, expected", [
    ("Rust の最新版を調べてから要点を教えて", True),
    ("最新の Python のリリースを調べて記事を書いて", True),
    ("根拠を確認して手順書にして", True),
    ("1 から 3 を表示する Python スクリプトを書いて、動くか試して直して", True),
    ("猫の短編小説を書いて", False),
    ("ROG Ally X の発売日を検索して", False),
    ("1 から 3 を表示する Python スクリプトを書いて実行して", False),
    ("こんにちは", False),
    ("その上でどう思う？", False),
])
def test_compound_detection(text, expected):
    assert is_compound(route(text)) is expected


def test_prefixes_media_and_docs_are_never_compound():
    assert not is_compound(route("/search 調べてから記事を書いて"))
    assert not is_compound(route("/write 調べてから記事を書いて"))
    assert not is_compound(route("調べてから記事にして", has_media=True))
    assert not is_compound(route("/docs notes 調べてから記事にして"))
    assert not is_compound(route("調べてから記事を書いて", task="search"))
    assert compound_kinds("調べてから記事を書いて Python スクリプトも作って") == [SEARCH, WRITE, CODE]


def test_auto_mode_thinks_for_compound_requests():
    decision = route("最新の Python のリリースを調べて記事を書いて")
    assert modes.choose("auto", decision, compound=True).mode == modes.THINK
    assert modes.choose("fast", decision, compound=True).mode == modes.FAST
    assert modes.choose("auto", route("手短に、調べて記事を書いて"), compound=True).mode == modes.FAST


async def test_explicit_search_does_not_enter_the_controller(models_dir):
    world = World()
    state, message = await _run("/search 調べてから記事を書いて", world, _settings(models_dir), mode="think")
    assert world.controller_inputs == [] and not state.get("control")
    assert world.synth and "最終回答" not in message.content


async def test_fast_compound_request_keeps_the_single_path(models_dir):
    world = World()
    state, message = await _run("Rust の最新版を調べてから要点を教えて", world, _settings(models_dir), mode="fast")
    assert world.controller_inputs == [] and not state.get("control")
    assert world.critiques == 0 and world.synth  # one fast search, as before


async def test_auto_compound_request_enters_the_loop(models_dir):
    world = World()
    state, message = await _run("Rust の最新版を調べてから要点を教えて", world, _settings(models_dir), mode="auto")
    assert state["mode"] == "think" and state["control"]["active"]
    assert "複数の道具" in state["mode_info"]["reason"]


# --- stage 1: decide, search once, answer ----------------------------------------------------------------------------


async def test_search_then_final(models_dir):
    world = World()
    state, message = await _run("Rust の最新版を調べてから要点を教えて", world, _settings(models_dir), mode="think")
    control = state["control"]
    assert [e["tool"] for e in control["trace"]] == ["search"] and control["trace"][0]["ok"]
    assert control["stop_reason"] == "final" and control["steps"] == 1
    assert len(world.controller_inputs) == 2
    assert "まだ道具を使っていません" in world.controller_inputs[0]
    assert "1. search「" in world.controller_inputs[1] and "まとめた回答です" in world.controller_inputs[1]
    assert world.searches and world.synth
    assert message.content == "最終回答です。"
    # The search answer stays in the thread as its own message; the loop's answer is a new one.
    contents = [m.content for m in state["messages"]]
    assert any(c.startswith("まとめた回答です") for c in contents)
    assert message.additional_kwargs["task_trace"]["kind"] == "control"
    assert job_lock.holder is None and world.live == set()


async def test_trace_keeps_summaries_not_pages(models_dir):
    world = World()
    state, _ = await _run("Rust の最新版を調べてから要点を教えて", world, _settings(models_dir), mode="think")
    entry = state["control"]["trace"][0]
    assert len(entry["summary"]) <= cn.SUMMARY_CHARS
    assert "page text for" not in entry["summary"]  # page bodies never reach the controller
    assert "出典:" in entry["summary"] and len(entry["args_hash"]) == 64


async def test_same_text_twice_is_not_run_again(models_dir):
    world = World()
    world.controller_replies = [_tool("search", "Rust 最新版"), _tool("search", "  rust   最新版 ")]
    state, message = await _run("Rust の最新版を調べてから要点を教えて", world, _settings(models_dir), mode="think")
    assert state["control"]["stop_reason"] == "repeat"
    assert len(state["control"]["trace"]) == 1
    assert world.synth == ["bonsai-2-27b-abliterated"]  # one search only
    assert "同じ依頼の繰り返し" in message.content and "上のメッセージ" in message.content
    assert "まとめた回答です" not in message.content  # the search answer is not posted twice


async def test_max_steps_never_runs_another_tool(models_dir):
    world = World()
    world.controller_replies = [_tool("search", "A"), _tool("search", "B")]
    settings = _settings(models_dir, controller_max_steps=1)
    state, message = await _run("Rust の最新版を調べてから要点を教えて", world, settings, mode="think")
    assert "残りの手数: 0" in world.controller_inputs[-1]
    assert state["control"]["stop_reason"] == "max_steps" and len(state["control"]["trace"]) == 1
    assert len(world.synth) == 1 and "手数の上限" in message.content


async def test_wall_clock_ends_after_the_running_tool(models_dir):
    world = World()
    settings = _settings(models_dir, controller_wall_clock_s=0.001)
    state, message = await _run("Rust の最新版を調べてから要点を教えて", world, settings, mode="think")
    # The first decision ran (the clock starts at ingest); no second decision after the search.
    assert state["control"]["stop_reason"] == "wall_clock"
    assert len(world.controller_inputs) <= 1 and "時間の上限" in message.content
    assert job_lock.holder is None


def test_wall_clock_never_exceeds_the_search_budget(models_dir):
    settings = _settings(models_dir, controller_wall_clock_s=99999.0, search_wall_clock_s=1200.0)
    assert cn.new_control(settings)["max_wall_clock_s"] == 1200.0
    assert cn.new_control(_settings(models_dir))["max_wall_clock_s"] == 1200.0


def test_after_synthesize_goes_to_the_record_only_in_the_loop():
    base = {"route": {"kind": "search", "claim_verify": False}, "search": {}, "error": None}
    assert chat_graph._after_synthesize({**base, "control": {"active": True}}) == "controller_record"
    assert chat_graph._after_synthesize({**base, "control": {}}) == END
    first = {**base, "route": {**base["route"], "search_first": True}}
    assert chat_graph._after_synthesize({**first, "control": {}}) == "write_brief"
    assert chat_graph._after_drop({**first, "control": {"active": True}}) == "controller_record"


async def test_broken_json_retries_once_then_ends_with_a_short_message(models_dir):
    world = World()
    world.controller_replies = ["broken", "still broken"]
    state, message = await _run("Rust の最新版を調べてから要点を教えて", world, _settings(models_dir), mode="think")
    assert len(world.controller_inputs) == 2  # the same call once more, then no third
    assert state["control"]["stop_reason"] == "error" and world.searches == []
    assert "次の手を決められませんでした" in message.content and "Traceback" not in message.content
    assert job_lock.holder is None


async def test_final_without_answer_is_asked_again_then_uses_the_last_summary(models_dir):
    world = World()
    world.controller_replies = [_tool("search", "A"), {"action": "final", "answer": ""},
                                {"action": "final", "answer": " "}]
    state, message = await _run("Rust の最新版を調べてから要点を教えて", world, _settings(models_dir), mode="think")
    assert state["control"]["stop_reason"] == "final"
    assert "上のメッセージ（出典付き）" in message.content


async def test_failed_search_is_recorded_and_the_controller_answers(models_dir):
    world = World()
    settings = replace(_settings(models_dir), llama_server="")
    state, message = await _run("Rust の最新版を調べてから要点を教えて", world, settings, mode="think")
    trace = state["control"]["trace"]
    assert trace[0]["tool"] == "search" and trace[0]["ok"] is False
    assert "setup-llamacpp" in trace[0]["summary"]
    assert message.content == "最終回答です。" and job_lock.holder is None
    assert not state.get("error")


async def test_lm_studio_down_decides_with_the_proxy_and_stops_it(models_dir):
    from test_chat_graph import FakeLMStudio

    world = World()
    world.controller_replies = [_final("代理で答えました。")]
    lm = FakeLMStudio(world, up=False)
    state, message = await _run("Rust の最新版を調べてから要点を教えて", world, _settings(models_dir), lmstudio=lm,
                                mode="think")
    assert message.content == "代理で答えました。"
    assert world.started == ["bonsai-2-27b-abliterated"] and world.live == set()


# --- stage 2: search, then write -----------------------------------------------------------------------------------


async def test_search_then_write_then_final_without_posting_the_draft_twice(models_dir):
    world = World()
    state, message = await _run("最新の猫の研究を調べてから記事にして", world, _settings(models_dir), mode="think")
    tools = [e["tool"] for e in state["control"]["trace"]]
    assert tools == ["search", "write"]
    assert [k for k, _ in world.writer_inputs] == ["outline", "draft", "revise"]
    outline_input = world.writer_inputs[0][1]
    assert "参考資料" in outline_input and "まとめた回答です" in outline_input
    assert len([s for s in world.searches]) >= 1 and world.synth == ["bonsai-2-27b-abliterated"]  # no 2nd search
    assert state["route"]["search_skipped"] is True
    draft = state["artifact"]["draft"]
    assert draft and draft not in message.content  # the draft is not posted again
    assert "本文は上のメッセージに書きました" in message.content
    assert any(m.content == draft for m in state["messages"])
    assert job_lock.holder is None


async def test_single_write_neither_searches_nor_controls(models_dir):
    world = World()
    state, message = await _run("/write 実在の事件を調べてから小説にして", world, _settings(models_dir), mode="think")
    assert world.controller_inputs == [] and not state.get("control")


async def test_chapter_reject_inside_the_loop_stops_it(models_dir):
    world = World()
    world.chapters = 3
    world.controller_replies = [_tool("write", "猫の長編小説を章立てで")]
    graph = _hitl_graph()
    config = _config(world, _settings(models_dir), mode="think", thread="c-ch")
    state = await graph.ainvoke({"messages": [HumanMessage(content="猫の研究を調べてから長編小説を章立てで書いて")]}, config)
    assert _interrupt(state)["action_requests"][0]["name"] == "continue_writing"
    state = await graph.ainvoke(Command(resume={"decisions": [{"type": "reject"}]}), config)
    assert _interrupt(state) is None
    assert state["control"]["stop_reason"] == "rejected" and len(world.controller_inputs) == 1
    assert "利用者が止めた" in state["messages"][-1].content


# --- stage 3: code and image ---------------------------------------------------------------------------------------


async def test_code_keeps_the_approval_card_and_returns_to_the_loop(models_dir):
    world = World()
    graph = _hitl_graph()
    config = _config(world, _settings(models_dir), mode="think", thread="c-code")
    text = "1 から 3 を表示する Python スクリプトを書いて、動くか試して直して"
    state = await graph.ainvoke({"messages": [HumanMessage(content=text)]}, config)
    card = _interrupt(state)
    assert card["action_requests"][0]["name"] == "run_code" and _runs(world) == []
    assert "1 から 3 を表示する Python スクリプト" in world.code_inputs[-1]  # the controller's request goes in
    state = await graph.ainvoke(Command(resume={"decisions": [{"type": "approve"}]}), config)
    assert len(_runs(world)) == 1 and "--network" in " ".join(_runs(world)[0])
    trace = state["control"]["trace"]
    assert [e["tool"] for e in trace] == ["code"] and trace[0]["ok"]
    assert "stdout 末尾" in trace[0]["summary"]
    assert state["messages"][-1].content == "最終回答です。" and job_lock.holder is None


async def test_rejected_code_run_stops_the_loop(models_dir):
    world = World()
    graph = _hitl_graph()
    config = _config(world, _settings(models_dir), mode="think", thread="c-rej")
    text = "1 から 3 を表示する Python スクリプトを書いて、動くか試して直して"
    await graph.ainvoke({"messages": [HumanMessage(content=text)]}, config)
    state = await graph.ainvoke(Command(resume={"decisions": [{"type": "reject"}]}), config)
    assert _runs(world) == [] and state["control"]["stop_reason"] == "rejected"


async def test_image_decision_points_to_the_image_tab_without_the_image_graph(models_dir, monkeypatch):
    from furry_agent import graph as image_graph

    async def boom(*a, **kw):
        raise AssertionError("graph.py must not run")

    monkeypatch.setattr(image_graph.graph, "ainvoke", boom)
    world = World()
    world.controller_replies = [_tool("image", "猫の絵")]
    state, message = await _run("Rust の最新版を調べてから要点を教えて", world, _settings(models_dir), mode="think")
    assert state["control"]["stop_reason"] == "image" and "画像タブ" in message.content
    assert world.searches == []


def test_node_names_do_not_collide_with_the_code_observe_node():
    nodes = set(chat_graph.graph.nodes)
    assert {"controller", "controller_record", "finish", "observe"} <= nodes


# --- pure helpers ------------------------------------------------------------------------------------------------


def test_check_rules(models_dir):
    control = cn.new_control(_settings(models_dir))
    D = cn.Decision
    assert cn.check(None, control) == ("stop", "error")
    assert cn.check(D(action="tool", tool="none", text="x"), control) == ("stop", "error")
    assert cn.check(D(action="tool", tool="search", text=""), control) == ("stop", "error")
    assert cn.check(D(action="tool", tool="search", text="x"), control) == ("tool", "")
    assert cn.check(D(action="tool", tool="image", text="x"), control) == ("stop", "image")
    control["trace"] = [{"args_hash": cn.args_hash("search", "X  y")}]
    assert cn.check(D(action="tool", tool="search", text="x Y"), control) == ("stop", "repeat")
    assert cn.check(D(action="tool", tool="write", text="x Y"), control) == ("tool", "")
    control["steps"] = control["max_steps"]
    assert cn.check(D(action="tool", tool="write", text="z"), control) == ("stop", "max_steps")
    assert cn.check(D(action="final", answer="ok"), control) == ("final", "")


def test_decision_ignores_unknown_fields():
    d = cn.Decision.model_validate({"action": "tool", "tool": "code", "text": "x", "argv": ["rm", "-rf", "/"]})
    assert d.tool == "code" and not hasattr(d, "argv")


def test_controller_input_is_short():
    control = {"max_steps": 3, "steps": 1,
               "trace": [{"tool": "search", "text": "q", "ok": True, "summary": "あ" * 5000}]}
    text = cn.controller_input("依頼", control)
    assert "あ" * cn.PROMPT_SUMMARY_CHARS in text and "あ" * (cn.PROMPT_SUMMARY_CHARS + 1) not in text
    assert "残りの手数: 2" in text


def test_route_kinds_for_tools():
    assert cn.KIND == {"search": SEARCH, "write": WRITE, "code": CODE}
    assert json.dumps(cn.control_task({"trace": [], "steps": 0}))


def test_fallback_after_a_failed_tool_shows_its_summary():
    control = {"trace": [{"tool": "search", "ok": False, "summary": "Tor に接続できません"}], "stop_reason": "wall_clock"}
    text = cn.final_text(control)
    assert "ここまでの結果" in text and "Tor に接続できません" in text and "時間の上限" in text
