from furry_agent.router import CHAT, SEARCH, TO_IMAGE_TAB, find_urls, route


def test_greeting_is_plain_chat():
    assert route("こんにちは").kind == CHAT


def test_search_keywords_and_prefix():
    assert route("今日のニュースを教えて").kind == SEARCH
    assert route("LangGraph の Send について調べて").kind == SEARCH
    r = route("/search ROG Ally X メモリ")
    assert r.kind == SEARCH and r.text == "ROG Ally X メモリ"


def test_url_summary_is_search_with_urls():
    r = route("https://example.com/a?b=1 を要約して")
    assert r.kind == SEARCH and r.urls == ["https://example.com/a?b=1"]


def test_attachments_and_drawing_go_to_image_tab():
    assert route("この画像の説明", has_media=True).kind == TO_IMAGE_TAB
    assert route("猫の獣人を描いて").kind == TO_IMAGE_TAB
    # An explicit /search wins over a drawing word.
    assert route("/search イラストを描くコツ").kind == SEARCH


def test_generate_summary_is_not_image():
    assert route("この文章の要約を生成して").kind == CHAT


def test_find_urls_dedupes_and_strips_punctuation():
    assert find_urls("見て https://a.example/x。 と https://a.example/x") == ["https://a.example/x"]
