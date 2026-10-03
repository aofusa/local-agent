import base64

import pytest

from furry_agent.media import MediaError, parse_request

PNG = base64.b64encode(b"\x89PNG fake").decode()


def test_plain_string():
    r = parse_request("  夕焼けの狼  ")
    assert r.text == "夕焼けの狼" and r.images == []


def test_agent_chat_ui_blocks():
    content = [
        {"type": "text", "text": "この子を和服で"},
        {"type": "image", "mimeType": "image/png", "data": PNG, "metadata": {"name": "ref.png"}},
    ]
    r = parse_request(content)
    assert r.text == "この子を和服で"
    assert len(r.images) == 1
    assert r.images[0].data == b"\x89PNG fake" and r.images[0].name == "ref.png" and r.images[0].extension == "png"


def test_langchain_and_data_url_blocks():
    content = [
        {"type": "image", "mime_type": "image/jpeg", "base64": PNG},
        {"type": "image_url", "image_url": {"url": f"data:image/webp;base64,{PNG}"}},
    ]
    r = parse_request(content)
    assert [m.mime for m in r.images] == ["image/jpeg", "image/webp"]


def test_more_than_two_images_rejected():
    block = {"type": "image", "mimeType": "image/png", "data": PNG}
    with pytest.raises(MediaError):
        parse_request([block, block, block])


def test_video_rejected_with_reason():
    with pytest.raises(MediaError, match="VideoHelperSuite"):
        parse_request([{"type": "file", "mimeType": "video/mp4", "data": PNG}])


def test_pdf_rejected():
    with pytest.raises(MediaError):
        parse_request([{"type": "file", "mimeType": "application/pdf", "data": PNG}])
