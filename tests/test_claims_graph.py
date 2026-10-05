"""Claim verification in the chat graph, with the fakes of test_chat_graph (no Tor, LM Studio, llama.cpp).

docs/claim-verification-design.md §7.
"""

import json
import re

import pytest
from langchain_core.messages import HumanMessage

from furry_agent import chat_graph
from furry_agent.job_lock import job_lock
from furry_agent.llm_client import ChatReply

from test_chat_graph import (FakeLLM, World, _config, _no_shared_log_handler,  # noqa: F401
                             _run, _settings, _system, models_dir)

_real_chat = FakeLLM.chat


def _section(messages) -> str:
    text = messages[-1]["content"]
    match = re.search(r"```text\n(.*)\n```", text, re.DOTALL)
    return match.group(1) if match else text


async def _chat(self, messages, tools=None, **kw):
    """test_chat_graph's FakeLLM plus the prompts of claim verification."""
    w, system = self.world, _system(messages)
    user = messages[1]["content"] if len(messages) > 1 else ""  # the task, also when the last turn asks to repair
    w.prompts.append(json.dumps(messages, ensure_ascii=False))
    repair = any(m["role"] == "assistant" for m in messages[1:])
    if "You extract checkable claims" in system:
        w.calls.append((self.name, "extract"))
        if w.extract_broken:
            return ChatReply("not json")
        quotes = re.findall(r"^  quote: (.+)$", user, re.MULTILINE)
        claims = [{"claim_id": f"c{i}", "text": q} for i, q in enumerate(quotes[:2], start=1)]
        claims.append({"claim_id": "c9", "text": "根拠のない値は 12345 円だった"})
        return ChatReply(json.dumps({"claims": claims}, ensure_ascii=False))
    if "You check claims against evidence" in system:
        audit = "判定する主張（回答の文" in user
        w.calls.append((self.name, "audit" if audit else "verify"))
        if w.verify_broken and not (w.repair_ok and repair):
            return ChatReply("{broken")
        ids = re.findall(r"^- (e\d+) \[", user, re.MULTILINE)
        claims = re.findall(r"^- ([cs]\d+): ", _claims_part(user), re.MULTILINE)
        verdicts = []
        for cid in claims:
            status = w.verdict.get(cid, "supported")
            verdicts.append({"claim_id": cid, "status": status, "evidence_ids": ids,
                             "contradict_ids": ids[:1] if status == "contradicted" else [], "note": "理由"})
        return ChatReply(json.dumps({"claims": verdicts}, ensure_ascii=False))
    if "調査チームの統合役" in system and w.answer is not None:
        w.synth.append(self.name)
        w.synth_inputs.append(user)
        w.calls.append((self.name, "synth"))
        return ChatReply(w.answer)
    return await _real_chat(self, messages, tools=tools, **kw)


def _claims_part(user: str) -> str:
    return user.split("判定する主張", 1)[-1]


@pytest.fixture(autouse=True)
def _fake_prompts(monkeypatch):
    monkeypatch.setattr(FakeLLM, "chat", _chat)


def _world(**kw):
    w = World(**kw)
    w.prompts = []
    w.extract_broken = w.verify_broken = w.repair_ok = False
    w.verdict = {}
    w.answer = None
    return w


def _vsettings(models_dir, **kw):
    kw.setdefault("claim_verify", True)
    return _settings(models_dir, **kw)


def _leader_calls(world):
    return [c for c in world.calls if isinstance(c[1], str) and c[1] in ("extract", "verify", "synth", "audit")]


# --- claim verification on the search path -------------------------------------------------------------------------


async def test_verified_search_order_one_leader_and_dropped_sentence(models_dir):
    world = _world()
    world.answer = ("page text for https://alpha.example/0 によると要点はこうだ [1]。"
                    "根拠のない値は 12345 円だった [1]。したがって高い。")
    state, message = await _run("/search topic", world, _vsettings(models_dir), mode="think")
    steps = [c[1] for c in _leader_calls(world)]
    assert steps == ["extract", "verify", "synth", "audit"]
    assert {c[0] for c in _leader_calls(world)} == {"bonsai-2-27b-abliterated"}
    assert world.started.count("bonsai-2-27b-abliterated") == 1  # critic, claims, synthesis and audit: one process
    # critique before extract
    order = [c[1] for c in world.calls]
    assert world.critiques == 1 and order.index("extract") > max(i for i, c in enumerate(world.calls)
                                                                 if "critic" in str(c[1]))
    assert world.live == set() and job_lock.holder is None
    assert "12345" not in message.content and "したがって" not in message.content
    assert message.content.startswith("page text for https://alpha.example/0") and "**参照**" in message.content
    trace = message.additional_kwargs["claim_trace"]
    statuses = {row["claim_id"]: row["status"] for row in trace["claims"]}
    assert statuses["c1"] == "supported" and statuses["c3"] == "unsupported"  # 12345 is in no card
    # 12345 is in no card; 「したがって高い」 has no anchor in any card either
    assert trace["audit"]["checked"] == 3 and len(trace["audit"]["dropped"]) == 2
    assert "search_trace" in message.additional_kwargs
    # The synthesizer saw the verified claims only.
    assert "検証済みの主張" in world.synth_inputs[-1] and "12345" not in world.synth_inputs[-1].split("検証済みの主張")[1]


async def test_fast_search_verifies_too_and_contradiction_line(models_dir):
    world = _world()
    world.verdict = {"c2": "contradicted"}
    world.answer = "page text for https://alpha.example/0 である [1]。"
    state, message = await _run("/search topic", world, _vsettings(models_dir), mode="fast")
    assert world.critiques == 0 and [c[1] for c in _leader_calls(world)] == ["extract", "verify", "synth", "audit"]
    assert "一部の候補は出典と矛盾したため本文から除いた。" in message.content
    assert message.content.index("矛盾") < message.content.index("**参照**")


async def test_claim_verify_off_is_the_old_synthesis(models_dir):
    world = _world()
    state, message = await _run("/search topic", world, _vsettings(models_dir, claim_verify=False), mode="think")
    assert [c[1] for c in _leader_calls(world)] == []
    assert message.content.startswith("まとめた回答です [1]") and "claim_trace" not in message.additional_kwargs


async def test_broken_json_twice_returns_excerpts_only(models_dir):
    world = _world()
    world.verify_broken = True
    world.answer = "書かれてはいけない統合"
    state, message = await _run("/search topic", world, _vsettings(models_dir), mode="think")
    verify_calls = [c for c in world.calls if c[1] == "verify"]
    assert len(verify_calls) == 2  # one call and one repair, the same process
    assert world.synth == [] and "書かれてはいけない統合" not in message.content
    assert "主張の突き合わせに失敗した。抜粋は末尾に残す" in message.content and "- [1]" in message.content
    assert message.additional_kwargs["claim_trace"]["error"] == "json"
    assert world.live == set() and job_lock.holder is None


async def test_repair_once_then_go_on(models_dir):
    world = _world()
    world.verify_broken, world.repair_ok = True, True
    world.answer = "page text for https://alpha.example/0 である [1]。"
    state, message = await _run("/search topic", world, _vsettings(models_dir), mode="fast")
    assert [c[1] for c in _leader_calls(world)] == ["extract", "verify", "verify", "synth", "audit", "audit"]
    assert not message.additional_kwargs["claim_trace"]["error"]


async def test_fail_open_writes_the_unaudited_answer_marked(models_dir):
    world = _world()
    world.extract_broken = True
    world.answer = "無監査の統合 [1]。"
    state, message = await _run("/search topic", world, _vsettings(models_dir, claim_fail_open=True), mode="fast")
    assert world.synth and message.content.startswith("（突き合わせ失敗")
    assert "audit" not in [c[1] for c in world.calls]
    assert world.live == set() and job_lock.holder is None


async def test_time_budget_stops_verification(models_dir):
    world = _world()
    state, message = await _run("/search topic", world, _vsettings(models_dir, claim_timeout_s=0.0), mode="fast")
    assert "突き合わせを打ち切った" in message.content and world.synth == []
    assert job_lock.holder is None


async def test_no_supported_claim_writes_no_answer(models_dir):
    world = _world()
    world.verdict = {"c1": "unsupported", "c2": "unsupported", "c3": "unsupported"}
    world.answer = "書かれない"
    state, message = await _run("/search topic", world, _vsettings(models_dir), mode="fast")
    assert world.synth == [] and "出典カードで確かめられた主張がありませんでした" in message.content


async def test_zero_results_skip_verification(models_dir):
    world = _world()
    world.no_hits = True
    state, message = await _run("/search nothing", world, _vsettings(models_dir), mode="think")
    assert "検索結果がありません" in message.content and _leader_calls(world) == []


async def test_write_after_search_gets_the_audited_research(models_dir):
    world = _world()
    world.answer = "page text for https://alpha.example/0 である [1]。根拠のない値は 12345 円だった。"
    state, message = await _run("実在の事件を調べてから小説にして", world, _vsettings(models_dir), mode="think")
    research = state["artifact"]["research"]
    assert "page text for" in research and "12345" not in research
    assert [k for k, _ in world.writer_inputs] == ["outline", "draft", "revise"]
