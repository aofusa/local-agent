import base64
import hashlib
import io

import pytest
from PIL import Image

from furry_agent import media as media_module
from furry_agent.media import MediaError, parse_request


def _encoded(size=(8, 6), fmt="PNG") -> str:
    buf = io.BytesIO()
    Image.new("RGB", size, (10, 20, 30)).save(buf, format=fmt)
    return base64.b64encode(buf.getvalue()).decode()


PNG = _encoded()


def block(data=PNG, mime="image/png", **meta):
    return {"type": "image", "mimeType": mime, "data": data, "metadata": meta}


def test_plain_string():
    r = parse_request("  夕焼けの狼  ")
    assert r.text == "夕焼けの狼" and r.images == []


def test_agent_chat_ui_blocks():
    r = parse_request([{"type": "text", "text": "この子を和服で"}, block(name="ref.png")])
    assert r.text == "この子を和服で"
    assert len(r.images) == 1
    image = r.images[0]
    assert image.data == base64.b64decode(PNG) and image.name == "ref.png" and image.extension == "png"
    assert (image.width, image.height) == (8, 6)
    assert image.role == "auto" and image.strength is None
    assert image.sha256 == hashlib.sha256(image.data).hexdigest()


def test_langchain_and_data_url_blocks():
    jpeg, webp = _encoded(fmt="JPEG"), _encoded(fmt="WEBP")
    content = [
        {"type": "image", "mime_type": "image/jpeg", "base64": jpeg},
        {"type": "image_url", "image_url": {"url": f"data:image/webp;base64,{webp}"}},
    ]
    r = parse_request(content)
    assert [m.mime for m in r.images] == ["image/jpeg", "image/webp"]


def test_role_and_strength_from_metadata():
    r = parse_request([block(role="style", strength="0.7"), block(role="POSE"), block(role="character", strength=1)])
    assert [(m.role, m.strength) for m in r.images] == [("style", 0.7), ("pose", None), ("character", 1.0)]


@pytest.mark.parametrize("meta", [{"role": "face"}, {"strength": "strong"}])
def test_bad_role_or_strength(meta):
    with pytest.raises(MediaError):
        parse_request([block(**meta)])


def test_four_images_allowed_fifth_rejected_with_reason():
    assert len(parse_request([block()] * 4).images) == 4
    with pytest.raises(MediaError, match="4 枚まで"):
        parse_request([block()] * 5)


def test_size_limits(monkeypatch):
    monkeypatch.setattr(media_module, "MAX_IMAGE_SIDE", 7)
    with pytest.raises(MediaError, match="辺"):
        parse_request([block()])
    monkeypatch.setattr(media_module, "MAX_IMAGE_BYTES", 10)
    with pytest.raises(MediaError, match="10 MB"):
        parse_request([block()])


def test_not_an_image():
    with pytest.raises(MediaError, match="読み取れません"):
        parse_request([block(data=base64.b64encode(b"\x89PNG fake").decode())])


def test_video_rejected_with_reason():
    with pytest.raises(MediaError, match="VideoHelperSuite"):
        parse_request([{"type": "file", "mimeType": "video/mp4", "data": PNG}])


def test_pdf_rejected():
    with pytest.raises(MediaError):
        parse_request([{"type": "file", "mimeType": "application/pdf", "data": PNG}])
