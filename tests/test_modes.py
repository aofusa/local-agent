import httpx

from furry_agent import modes
from furry_agent.llm_client import LMStudio, OpenAICompatClient, split_thinking
from furry_agent.router import route


def _choose(text, requested="auto", **kw):
    return modes.choose(requested, route(text, **{k: v for k, v in kw.items() if k == "has_draft"}),
                        **{k: v for k, v in kw.items() if k != "has_draft"})


def test_missing_or_unknown_mode_is_fast():
    assert modes.requested_mode(None) == "fast"
    assert modes.requested_mode("") == "fast"
    assert modes.requested_mode("THINK") == "think"
    assert modes.requested_mode("turbo") == "fast"


def test_explicit_modes_are_kept():
    assert _choose("Python と Rust の違いを比較して調べて", "fast").mode == "fast"
    assert _choose("こんにちは", "think").mode == "think"


def test_auto_search_depth():
    assert _choose("ROG Ally X の発売日を調べて").mode == "fast"
    choice = _choose("ROG Ally X と Steam Deck OLED の違いを比較して調べて")
    assert choice.mode == "think" and choice.requested == "auto" and "違い" in choice.reason
    assert "自動" in choice.label


def test_auto_writing_and_code():
    assert _choose("キャッチコピーを一行書いて").mode == "fast"
    assert _choose("猫が主人公の短編小説を書いて").mode == "think"
    assert _choose("長編小説を章立てで書いて").mode == "think"
    assert _choose("素数を列挙する Python スクリプトを書いて").mode == "fast"
    assert _choose("素数を列挙する Python スクリプトを書いて実行して").mode == "think"


def test_auto_chat_and_user_cues():
    assert _choose("こんにちは").mode == "fast"
    assert _choose("この確率の問題を解いて").mode == "think"
    assert _choose("手短に、量子コンピュータの原理を比較して").mode == "fast"
    assert _choose("じっくり考えて、明日の予定は？").mode == "think"


def test_auto_uses_the_router_deep_flag_only_as_a_tiebreak():
    assert modes.auto_mode(route("それってどう？"), router_deep=True)[0] == "think"
    assert modes.auto_mode(route("それってどう？"), router_deep=False)[0] == "fast"


def test_split_thinking_keeps_thoughts_out_of_the_answer():
    assert split_thinking("<think>考え</think>答え") == ("答え", "考え")
    assert split_thinking("前置き</think>本文") == ("本文", "前置き")
    assert split_thinking("答え<think>途中") == ("答え", "途中")
    assert split_thinking("答えだけ") == ("答えだけ", "")


async def _body(client_cls, thinking, reply=None):
    seen = {}

    def handler(request: httpx.Request):
        import json

        seen.update(json.loads(request.content))
        return httpx.Response(200, json=reply or {"choices": [{"message": {"content": "ok"}}]})

    client = client_cls("http://127.0.0.1:1/v1", transport=httpx.MockTransport(handler))
    result = await client.chat([{"role": "user", "content": "x"}], thinking=thinking)
    return seen, result


async def test_enable_thinking_follows_the_mode():
    body, _ = await _body(OpenAICompatClient, True)
    assert body["chat_template_kwargs"] == {"enable_thinking": True}
    body, _ = await _body(OpenAICompatClient, False)
    assert body["chat_template_kwargs"] == {"enable_thinking": False} and "reasoning_effort" not in body
    body, _ = await _body(OpenAICompatClient, None)
    assert body["chat_template_kwargs"] == {"enable_thinking": False}  # default stays off


async def test_lm_studio_thinking_uses_reasoning_effort_and_separates_the_thoughts():
    reply = {"choices": [{"message": {"content": "答え", "reasoning": "思考の中身"}}]}
    body, result = await _body(LMStudio, True, reply)
    assert body["reasoning_effort"] == "medium" and body["chat_template_kwargs"]["enable_thinking"] is True
    assert result.content == "答え" and result.reasoning == "思考の中身"
    body, result = await _body(LMStudio, False, {"choices": [{"message": {"content": "<think>x</think>答え"}}]})
    assert "reasoning_effort" not in body and result.content == "答え" and result.reasoning == "x"
