"""POST /presence/turn (vrc-pilot docs/04 "local-agent 側のテスト")."""

from __future__ import annotations

import asyncio
import json

import pytest
from starlette.testclient import TestClient

from furry_agent import presence
from furry_agent.llm_client import ChatReply, ToolCall

BODY = {"world": {"place": {"label": "広場"}, "avatars": [{"track_id": "np:Kumo", "nameplate": "Kumo"}],
                  "recent_utterances": [{"ts": "t", "text": "aofusa、やあ", "speaker_hint": "Kumo"}],
                  "self": {"armed": True, "mode": "chat"}},
        "persona": {"name": "aofusa", "style": "短く"},
        "policy": {"allow_move": False, "allow_say": True, "max_actions": 2}}


class FixedLLM:
    """A model stand-in without tools: returns the scripted replies in order."""

    def __init__(self, *replies, delay_s: float = 0.0):
        self.replies = list(replies)
        self.delay_s = delay_s
        self.calls: list[list[dict]] = []

    async def chat(self, messages, **kw):
        self.calls.append(messages)
        assert kw.get("thinking") is False and kw.get("json_mode") is True
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        r = self.replies.pop(0)
        if isinstance(r, ChatReply):
            return r
        return ChatReply(content=r if isinstance(r, str) else json.dumps(r, ensure_ascii=False))


@pytest.fixture
def client(monkeypatch):
    holder = {}

    def factory(model_id, timeout_s):
        return holder["llm"], "fixed"

    monkeypatch.setattr(presence, "LLM_FACTORY", factory)
    from furry_agent.coder_app import app

    c = TestClient(app)
    c.holder = holder
    return c


def test_route_is_registered_next_to_coder_turn():
    from furry_agent.coder_app import app

    paths = {r.path for r in app.routes}
    assert {"/presence/turn", "/presence/health", "/coder/turn"} <= paths


def test_fixed_mock_returns_say(client):
    client.holder["llm"] = FixedLLM({"actions": [{"kind": "say", "say": {"text": "やあ、Kumo"}}], "belief": "呼ばれた"})
    r = client.post("/presence/turn", json=BODY)
    assert r.status_code == 200
    out = r.json()
    act = out["actions"][0]
    assert act["kind"] == "say" and act["say"]["text"] == "やあ、Kumo"
    assert act["say"]["notification"] is False and act["action_id"] and act["ts"]
    assert out["belief"] == "呼ばれた"
    system = client.holder["llm"].calls[0][0]
    assert system["role"] == "system" and "判断係" in system["content"]


def test_flattened_say_is_accepted(client):
    client.holder["llm"] = FixedLLM({"actions": [{"kind": "say", "text": "こんにちは"}]})
    r = client.post("/presence/turn", json=BODY)
    assert r.status_code == 200 and r.json()["actions"][0]["say"]["text"] == "こんにちは"


def test_145_characters_is_422_not_truncated(client):
    long = {"actions": [{"kind": "say", "say": {"text": "あ" * 145}}]}
    client.holder["llm"] = FixedLLM(long, long)
    r = client.post("/presence/turn", json=BODY)
    assert r.status_code == 422
    assert any("145 characters" in e for e in r.json()["errors"])
    assert len(client.holder["llm"].calls) == 2  # the model was asked to rewrite once


def test_too_long_then_rewritten(client):
    client.holder["llm"] = FixedLLM({"actions": [{"kind": "say", "say": {"text": "あ" * 145}}]},
                                    {"actions": [{"kind": "say", "say": {"text": "短くした"}}]})
    r = client.post("/presence/turn", json=BODY)
    assert r.status_code == 200 and r.json()["actions"][0]["say"]["text"] == "短くした"


def test_move_with_allow_move_false_is_422(client):
    bad = {"actions": [{"kind": "move", "move": {"forward": 0.4, "duration_ms": 600}}]}
    client.holder["llm"] = FixedLLM(bad, bad)
    r = client.post("/presence/turn", json=BODY)
    assert r.status_code == 422 and "allow_move" in json.dumps(r.json())
    assert "allow_move" in client.holder["llm"].calls[1][-1]["content"]  # the rewrite named the problem


def test_policy_violation_rewritten_keeps_the_say(client):
    client.holder["llm"] = FixedLLM({"actions": [{"kind": "say", "say": {"text": "行くね"}},
                                                 {"kind": "look", "look": {"yaw": 0.3, "duration_ms": 400}}]},
                                    {"actions": [{"kind": "say", "say": {"text": "行くね"}}]})
    r = client.post("/presence/turn", json=BODY)
    assert r.status_code == 200 and [a["kind"] for a in r.json()["actions"]] == ["say"]


def test_move_allowed_by_policy(client):
    body = {**BODY, "policy": {"allow_move": True, "allow_say": True, "max_actions": 2}}
    client.holder["llm"] = FixedLLM({"actions": [{"kind": "move", "forward": 0.4, "duration_ms": 600}]})
    r = client.post("/presence/turn", json=body)
    assert r.status_code == 200 and r.json()["actions"][0]["move"] == {"forward": 0.4, "duration_ms": 600}


def test_tool_call_becomes_noop(client):
    client.holder["llm"] = FixedLLM(ChatReply(content="", tool_calls=[ToolCall("bash", {"cmd": "ls"})]))
    r = client.post("/presence/turn", json=BODY)
    assert r.status_code == 200
    assert [a["kind"] for a in r.json()["actions"]] == ["noop"] and r.json()["dropped"] == "tool_calls"


def test_three_actions_is_422(client):
    client.holder["llm"] = FixedLLM({"actions": [{"kind": "noop"}] * 3}, {"actions": [{"kind": "noop"}] * 3})
    assert client.post("/presence/turn", json=BODY).status_code == 422


def test_chatter_gets_one_json_rewrite(client):
    client.holder["llm"] = FixedLLM("了解です", {"actions": [{"kind": "noop"}], "belief": ""})
    r = client.post("/presence/turn", json=BODY)
    assert r.status_code == 200 and r.json()["actions"][0]["kind"] == "noop"


def test_timeout_is_504(client, monkeypatch):
    monkeypatch.setattr(presence, "TIMEOUT_S", 0.2)
    client.holder["llm"] = FixedLLM({"actions": []}, delay_s=1.0)
    r = client.post("/presence/turn", json=BODY)
    assert r.status_code == 504 and r.json()["actions"][0]["kind"] == "noop"


def test_bad_body_is_400(client):
    client.holder["llm"] = FixedLLM()
    assert client.post("/presence/turn", json={"world": "x"}).status_code == 400


def test_prompt_file_exists_and_names_the_rules():
    text = presence.system_prompt()
    assert "144" in text and "allow_move" in text and "persona.name" in text


async def test_keep_alive_reuses_one_client():
    from furry_agent.llm_client import RemoteLLM

    llm = presence.keep_alive(RemoteLLM("http://127.0.0.1:9/v1", "m", 5, kind="llamacpp"))
    async with llm._http(5) as a:
        pass
    async with llm._http(5) as b:
        pass
    assert a is b and not a.is_closed
    await a.aclose()
