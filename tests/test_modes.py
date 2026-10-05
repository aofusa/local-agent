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


# --- fitting LM Studio's 4096-token window ----------------------------------------------------------------------


class _Recorder:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    async def chat(self, messages, **kw):
        from furry_agent.llm_client import ChatReply

        self.calls.append({"messages": messages, **kw})
        content, reasoning = self.replies.pop(0)
        return ChatReply(content, reasoning=reasoning)


def _state(mode):
    return {"mode": mode, "thinking": []}


async def test_ask_keeps_max_tokens_inside_the_window():
    from furry_agent.chat_common import _ask, prompt_tokens
    from furry_agent.config import ChatSettings

    settings = ChatSettings()
    llm = _Recorder(("答え", "考え"))
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "質問" * 50}]
    reply, thoughts = await _ask(_state("think"), settings, llm, messages, base=3500, answer_min=1200,
                                 temperature=0.2, stage="コード")
    call = llm.calls[0]
    assert call["thinking"] is True and call["max_tokens"] + prompt_tokens(messages) <= settings.lmstudio_ctx
    assert reply.content == "答え" and thoughts == [{"stage": "コード", "text": "考え"}]


async def test_ask_turns_thinking_off_when_the_window_is_too_small_and_trims_history():
    from furry_agent.chat_common import _ask, prompt_tokens
    from furry_agent.config import ChatSettings

    settings = ChatSettings()
    llm = _Recorder(("答え", ""))
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": "長い" * 600} for i in range(6)]
    messages = [{"role": "system", "content": "s"}, *history, {"role": "user", "content": "最後の質問"}]
    await _ask(_state("think"), settings, llm, messages, base=1536, answer_min=512, temperature=0.6, stage="回答")
    sent = llm.calls[0]["messages"]
    assert sent[0]["content"] == "s" and sent[-1]["content"] == "最後の質問" and len(sent) < len(messages)
    assert prompt_tokens(sent) + llm.calls[0]["max_tokens"] <= settings.lmstudio_ctx


async def test_ask_retries_without_thinking_when_no_answer_came():
    from furry_agent.chat_common import _ask
    from furry_agent.config import ChatSettings

    llm = _Recorder(("", "延々と考えた"), ("答え", ""))
    reply, thoughts = await _ask(_state("think"), ChatSettings(), llm, [{"role": "user", "content": "q"}],
                                 base=1000, answer_min=500, temperature=0.6, stage="回答")
    assert [c["thinking"] for c in llm.calls] == [True, False]
    assert reply.content == "答え" and thoughts[0]["text"] == "延々と考えた"


async def test_ask_never_thinks_in_fast_mode():
    from furry_agent.chat_common import _ask
    from furry_agent.config import ChatSettings

    llm = _Recorder(("答え", ""))
    await _ask(_state("fast"), ChatSettings(), llm, [{"role": "user", "content": "q"}], base=1000, answer_min=500,
               temperature=0.6, stage="回答")
    assert llm.calls[0]["thinking"] is False and llm.calls[0]["max_tokens"] == 1000


async def test_lm_studio_budget_follows_the_measured_speed():
    import json as _json

    from furry_agent import chat_common
    from furry_agent.chat_common import _ask, record_speed, time_cap
    from furry_agent.config import ChatSettings
    from furry_agent.llm_client import ChatReply

    chat_common._speeds.clear()
    sent = []

    def handler(request: httpx.Request):
        sent.append(_json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    lm = LMStudio("http://127.0.0.1:9/v1", transport=httpx.MockTransport(handler))
    settings = ChatSettings()  # 1.0 token/s until measured, 20-minute calls
    assert time_cap(settings, lm) == 1080
    assert time_cap(settings, OpenAICompatClient("http://127.0.0.1:8/v1")) is None  # llama-server: not guessed
    await _ask(_state("think"), settings, lm, [{"role": "user", "content": "q"}], base=3500, answer_min=1200,
               temperature=0.2, stage="コード")
    # ~1000 tokens fit in 20 minutes: no room for thinking next to a 1200-token answer.
    assert sent[0]["max_tokens"] == 1080 and sent[0]["chat_template_kwargs"]["enable_thinking"] is False
    record_speed(lm, ChatReply("x", raw={"usage": {"completion_tokens": 900}}, seconds=100.0))
    assert time_cap(settings, lm) == int(9.0 * 1200 * 0.9)
    chat_common._speeds.clear()


async def test_model_unloaded_race_is_retried_once():
    calls = []

    def handler(request: httpx.Request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(400, json={"error": "Model is unloaded."})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    lm = LMStudio("http://127.0.0.1:9/v1", transport=httpx.MockTransport(handler))
    assert (await lm.chat([{"role": "user", "content": "x"}])).content == "ok" and len(calls) == 2


async def test_think_chat_still_thinks_within_the_time_budget():
    import json as _json

    from furry_agent import chat_common
    from furry_agent.chat_common import _ask
    from furry_agent.config import ChatSettings

    chat_common._speeds.clear()
    sent = []

    def handler(request: httpx.Request):
        sent.append(_json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "60", "reasoning": "12=2^2*3"}}]})

    lm = LMStudio("http://127.0.0.1:9/v1", transport=httpx.MockTransport(handler))
    reply, thoughts = await _ask(_state("think"), ChatSettings(), lm, [{"role": "user", "content": "q"}], base=1536,
                                 answer_min=512, temperature=0.6, stage="回答")
    assert sent[0]["reasoning_effort"] == "medium" and sent[0]["max_tokens"] == 1080
    assert thoughts == [{"stage": "回答", "text": "12=2^2*3"}]


def test_auto_docs():
    from furry_agent.router import route as route_rules

    assert modes.choose("auto", route_rules("/docs a.md 比較して")).mode == "think"
    assert modes.choose("auto", route_rules("/docs a.md")).mode == "fast"
