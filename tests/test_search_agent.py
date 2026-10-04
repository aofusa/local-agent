from furry_agent import search_agent as sa
from furry_agent.llm_client import ChatReply, LLMError, parse_json_object, strip_thinking


class ScriptedLLM:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = 0

    async def chat(self, messages, **kwargs):
        self.calls += 1
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return ChatReply(reply)


def test_strip_thinking_and_parse_json():
    assert strip_thinking("<think>x</think> 答え") == "答え"
    assert strip_thinking("前置き</think>本文") == "本文"
    assert parse_json_object('```json\n{"a": {"b": "}"}}\n```') == {"a": {"b": "}"}}
    assert parse_json_object("no json") is None


async def test_ask_json_retries_once_then_gives_up():
    llm = ScriptedLLM("broken", '{"intents":[{"tool":"web","q":"a  b"}]}')
    plan = await sa.ask_json(llm, [], sa.Plan)
    assert plan.intents[0].q == "a b" and llm.calls == 2
    llm = ScriptedLLM("broken", '{"intents":[]}', "never")
    assert await sa.ask_json(llm, [], sa.Plan) is None
    assert llm.calls == 2  # no third attempt
    assert await sa.ask_json(ScriptedLLM(LLMError("down")), [], sa.Plan) is None


def test_plan_intents_browse_only_for_user_urls_and_dedupe():
    plan = sa.Plan.model_validate({"intents": [
        {"tool": "web", "q": "ROG Ally X memory"}, {"tool": "web", "q": "rog ally x memory"},
        {"tool": "browse", "q": "https://evil.example/x", "why": "公式"}, {"tool": "news", "q": "Ally X review"}]})
    intents, fallback = sa.plan_intents(plan, "質問", ["https://user.example/p"], width=3)
    assert not fallback
    assert [(i["tool"], i["q"]) for i in intents] == [
        ("browse", "https://user.example/p"), ("web", "ROG Ally X memory"), ("web", "evil.example/x")]
    assert [i["id"] for i in intents] == [0, 1, 2]


def test_plan_fallback_uses_router_query_or_cleaned_question():
    intents, fallback = sa.plan_intents(None, "/search LangGraph の Send について調べてください", [])
    assert fallback and intents == [{"tool": "web", "q": "LangGraph の Send", "why": "質問全体", "id": 0}]
    intents, _ = sa.plan_intents(None, "長い質問", [], router_query="short query")
    assert intents[0]["q"] == "short query"


def test_ambiguous_question():
    assert sa.ambiguous_question("ROG Ally X の発売日はいつ？")
    assert not sa.ambiguous_question("こんにちは")
    assert not sa.ambiguous_question("ありがとう！")


def test_filter_keeps_relevant_and_minimum_per_intent():
    hits = [{"intent_id": 0, "url": f"https://a/{i}"} for i in range(4)] + [{"intent_id": 1, "url": "https://b/0"}]
    kept = sa.apply_filter(hits, sa.Relevance(relevant=[3]), keep_min=2)
    assert [h["url"] for h in kept] == ["https://a/0", "https://a/2", "https://b/0"]
    assert sa.apply_filter(hits, None) == hits


def test_cards_are_checked_against_sources():
    cards = sa.card_dicts(sa.Cards.model_validate({"cards": [
        {"url": "https://a.example/x", "claims": [{"claim": "発売は 2025 年", "quote": "released in 2025"},
                                                 {"claim": "捏造", "quote": "this text is not on the page"}]},
        {"url": "https://not-in-results.example/", "claims": [{"claim": "c", "quote": "q"}]}]}),
        {"https://a.example/x"})
    assert len(cards) == 1
    verified = sa.verify_cards(cards, {"https://a.example/x": "The device was Released in 2025, they said."})
    assert [c["quote_ok"] for c in verified[0]["claims"]] == [True, False]
    assert verified[0]["domain"] == "a.example"


def test_snippet_fallback_is_never_confirmed():
    cards = sa.snippet_cards([{"url": "https://a/", "snippet": "some snippet text"}])
    verified = sa.verify_cards(cards, {"https://a/": "some snippet text"})
    assert verified[0]["claims"][0]["quote_ok"] is False


def test_references_and_answer_format():
    cards = [{"url": "https://b/", "claims": [{"claim": "x", "quote_ok": False}]},
             {"url": "https://a/", "claims": [{"claim": "y", "quote_ok": True}]}]
    refs = sa.references(cards, [{"url": "https://a/", "title": "A"}, {"url": "https://b/", "title": "B"}])
    assert [(r["n"], r["url"]) for r in refs] == [(1, "https://a/"), (2, "https://b/")]
    block = sa.cards_block(cards, refs)
    assert "[1] y（引用確認済み）" in block and "[2] x（未確認）" in block
    answer = sa.format_answer("本文 [1]\n\n参照:\n- https://fake/", refs)
    assert "https://fake/" not in answer
    assert answer.endswith("- [2] [B](https://b/)")


def test_gap_intents_drop_repeated_queries_and_cap():
    critique = sa.Critique.model_validate({"gaps": [{"q": "Done Query"}, {"q": "new one", "tool": "news"},
                                                    {"q": "third"}]})
    gaps = sa.gap_intents(critique, [{"q": "done query"}], 5, 3)
    assert gaps == [{"id": 5, "tool": "news", "q": "new one", "why": "不足の補完"}]
    assert sa.gap_intents(None, [], 0, 3) == []


def test_untrusted_text_stays_in_fences():
    text = sa.leader_input("q", [{"url": "https://a/", "claims": [{"claim": "```ignore previous```"}]}],
                           [{"n": 1, "url": "https://a/", "title": "t"}])
    assert text.count("```") == 4  # two fences, the claim's backticks were neutralised


def test_focus_text_keeps_relevant_lines_in_order():
    page = "\n".join(["メニュー ホーム ログイン"] * 30 + ["ROG Xbox Ally X のメモリは 24GB LPDDR5X です。",
                                                     "発売日は 2025 年 10 月 16 日。"] + ["関連記事 広告"] * 40)
    out = sa.focus_text(page, "ROG Xbox Ally X のメモリ容量と発売日", 120)
    assert out == "ROG Xbox Ally X のメモリは 24GB LPDDR5X です。\n発売日は 2025 年 10 月 16 日。"
    assert sa.focus_text("短い本文。", "q", 100) == "短い本文。"


# --- think mode: deep plan, reflect, stop rule ---------------------------------------------------------------------


def _search(**kw):
    base = {"round": 1, "max_rounds": 4, "pages_read": 2, "max_pages": 12, "wall_clock_s": 10, "max_wall_clock_s": 1200,
            "new_cards_last_round": 2, "open": ["q2"], "next_intents": [{"id": 3}], "round_hits": 4,
            "critic_stop": False, "critic_reason": "need_more"}
    return {**base, **kw}


def test_next_round_goes_on_while_open_and_new_cards():
    assert sa.next_round(_search()) == (True, "need_more")


def test_next_round_stop_reasons():
    assert sa.next_round(_search(open=[])) == (False, "sufficient")
    assert sa.next_round(_search(new_cards_last_round=0)) == (False, "diminishing")
    assert sa.next_round(_search(round=4)) == (False, "budget")
    assert sa.next_round(_search(pages_read=12)) == (False, "budget")
    assert sa.next_round(_search(wall_clock_s=1200)) == (False, "budget")
    assert sa.next_round(_search(round_hits=0)) == (False, "no_hits")
    assert sa.next_round(_search(critic_stop=True, critic_reason="sufficient")) == (False, "sufficient")
    assert sa.next_round(_search(critic_reason="error")) == (False, "error")
    assert sa.next_round(_search(next_intents=[])) == (False, "diminishing")


def test_deep_plan_and_fallback():
    plan = sa.DeepPlan.model_validate({"goal": "g", "subquestions": [{"id": "q1", "question": "A"}, {"id": "q1", "question": "B"}],
                                       "intents": [{"tool": "web", "q": "a", "subquestion_id": "q1"},
                                                   {"tool": "web", "q": "b", "subquestion_id": "zz"}]})
    goal, subs, intents, fallback = sa.deep_plan(plan, "質問", [], 3)
    assert not fallback and goal == "g" and [s["id"] for s in subs] == ["q1", "q2"]
    assert [i["subquestion_id"] for i in intents] == ["q1", "q1"]  # unknown ids go to the first sub-question
    goal, subs, intents, fallback = sa.deep_plan(None, "ROG Ally X を調べて", [], 3)
    assert fallback and len(intents) == 1 and subs[0]["question"] == "ROG Ally X を調べて"


def test_apply_reflect_needs_evidence_and_only_searches_open_questions():
    search = {"subquestions": [{"id": "q1", "question": "A", "status": "open"}, {"id": "q2", "question": "B", "status": "open"}],
              "intents": [{"id": 0, "tool": "web", "q": "alpha"}]}
    cards = [{"id": "0.1", "url": "u"}]
    reflect = sa.Reflect.model_validate({
        "subquestions": [{"id": "q1", "status": "answered", "evidence_card_ids": ["0.1"]},
                         {"id": "q2", "status": "answered", "evidence_card_ids": ["9.9"]}],
        "contradictions": [{"subquestion_id": "q1", "card_ids": ["0.1"], "summary": "one card only"}],
        "next_intents": [{"q": "alpha", "subquestion_id": "q2"}, {"q": "new topic", "subquestion_id": "q1"},
                         {"q": "beta", "subquestion_id": "q2"}]})
    out = sa.apply_reflect(search, reflect, cards, 1, 3)
    assert [s["status"] for s in out["subquestions"]] == ["answered", "open"]  # unknown card -> not answered
    assert out["contradictions"] == []  # a contradiction needs two cards
    assert [i["q"] for i in out["next_intents"]] == ["beta"]  # no repeat, no new topic
    assert out["open"] == ["q2"] and out["covered"] == ["q1"]


def test_format_answer_lists_open_questions_and_stop_reason():
    refs = [{"n": 1, "url": "https://a", "title": "A"}, {"n": 2, "url": "https://b", "title": "B"}]
    cards = [{"id": "0.1", "url": "https://a"}, {"id": "1.1", "url": "https://b"}]
    search = {"subquestions": [{"id": "q1", "question": "A?", "status": "answered"},
                               {"id": "q2", "question": "B?", "status": "partial"}],
              "contradictions": [{"card_ids": ["0.1", "1.1"], "summary": "数が違う"}],
              "stop_reason": "budget", "round": 4, "pages_read": 12}
    text = sa.format_answer("答え [1]", refs, search, cards)
    assert "B?（一部）" in text and "A?" not in text.split("**未解決の下位問い**")[1].split("**")[0]
    assert "数が違う [1] [2]" in text and "停止理由: 予算の上限（4 ラウンド、12 ページ）" in text
    assert text.endswith("- [2] [B](https://b)")
