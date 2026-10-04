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


def test_writing_requests_are_write():
    from furry_agent.router import WRITE

    for text in ("猫の小説を書いて", "この物語の設定を作って", "次の文章を推敲して", "記事の下書きをお願い"):
        assert route(text).kind == WRITE, text
    assert route("続きを書いて", has_draft=True).kind == WRITE
    assert route("続きを書いて", has_draft=True).continuation
    assert route("続きは？", has_draft=True).kind == WRITE
    assert route("続きは？").kind == CHAT  # no draft in the thread: plain chat
    r = route("実在の事件を調べてから小説にして")
    assert r.kind == WRITE and r.needs_search
    assert route("長編小説を章立てで書いて").long


def test_code_requests_are_code_and_win_over_writing():
    from furry_agent.router import CODE

    for text in ("素数を数えるコードを書いて", "この関数を実装して", "スクリプトを作って実行して", "テストして",
                 "Rust で FizzBuzz を書いて"):
        assert route(text).kind == CODE, text
    assert route("Python の GIL って何？").kind == CHAT


def test_drawing_still_goes_to_the_image_tab():
    assert route("この文章の場面を画像にして", has_draft=True).kind == TO_IMAGE_TAB
    assert route("小説の挿絵を描いて").kind == TO_IMAGE_TAB


def test_prefixes_and_task_fix_the_kind():
    from furry_agent.router import CODE, WRITE

    assert route("/write 夏の詩").kind == WRITE
    assert route("/code fizzbuzz").kind == CODE
    assert route("/chat 小説について話そう").kind == CHAT
    assert route("夏の詩", task="write").kind == WRITE
    assert route("こんにちは", task="search").kind == SEARCH
