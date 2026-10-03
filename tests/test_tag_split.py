import pytest

from furry_ja.tag_split import DEFAULT_NEGATIVE, LLMCallError, split_tags, strip_think

PREFIX = "masterpiece, best quality"


def test_valid_json():
    raw = '{"positive":"1girl, anthro, wolf, white fur, kimono, beach, sunset","negative":"low quality, glasses"}'
    r = split_tags(raw, quality_prefix=PREFIX)
    assert r.parsed
    assert r.positive == "masterpiece, best quality, 1girl, anthro, wolf, white fur, kimono, beach, sunset"
    assert r.negative == "low quality, glasses"


def test_json_wrapped_in_think_and_fence():
    raw = '<think>let me think {"positive":"wrong"}</think>\n```json\n{"positive": "fox, anthro", "negative": "blurry"}\n```'
    r = split_tags(raw, quality_prefix="")
    assert r.parsed
    assert r.positive == "fox, anthro"
    assert r.negative == "blurry"


def test_unterminated_think_is_removed():
    assert strip_think('<think>still thinking...') == ""
    assert strip_think('reasoning</think>{"a":1}') == '{"a":1}'


def test_broken_json_falls_back_to_raw_string():
    raw = '{"positive": "wolf, anthro, kimono", "negative": '
    r = split_tags(raw, quality_prefix=PREFIX)
    assert not r.parsed
    assert r.positive == 'masterpiece, best quality, {"positive": "wolf, anthro, kimono", "negative":'
    assert r.negative == DEFAULT_NEGATIVE


def test_prose_falls_back_without_retry():
    r = split_tags("A white wolf woman in a kimono at sunset.", quality_prefix="")
    assert not r.parsed
    assert r.positive == "A white wolf woman in a kimono at sunset."
    assert r.negative == DEFAULT_NEGATIVE


def test_missing_negative_uses_default():
    r = split_tags('{"positive":"cat, anthro"}', quality_prefix="")
    assert r.parsed and r.negative == DEFAULT_NEGATIVE


def test_prefix_not_duplicated():
    r = split_tags('{"positive":"best quality, masterpiece, dragon","negative":"x"}', quality_prefix=PREFIX)
    assert r.positive == "best quality, masterpiece, dragon"


def test_whitespace_and_empty_tags_normalized():
    r = split_tags('{"positive":"fox,  ,anthro ,\\n red fur","negative":"x"}', quality_prefix="")
    assert r.positive == "fox, anthro, red fur"


def test_lm_connect_error_is_raised():
    with pytest.raises(LLMCallError):
        split_tags("[LM Connect Error] LM Studio'ya bağlanılamadı")


def test_none_input_falls_back():
    r = split_tags(None, quality_prefix="q")
    assert not r.parsed and r.positive == "q"
