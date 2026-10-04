from pathlib import Path

import httpx
import pytest

from furry_agent.html_text import html_to_text, parse_ddg
from furry_agent.search_client import SearchError, TorSearchClient, UrlRefused, check_url

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/admin", "https://10.0.0.5/", "http://[::1]/", "http://192.168.1.10:8188/view",
    "http://localhost:2024/", "https://abcdefghijklmnop.onion/", "javascript:alert(1)", "file:///C:/x",
    "ftp://example.com/", "https://user:pw@example.com/", "https://example.com:8188/", "http://intranet/",
    "http://router.lan/",
])
def test_refused_urls(url):
    with pytest.raises(UrlRefused):
        check_url(url)


def test_public_url_allowed():
    assert check_url("https://docs.langchain.com/oss/python/langgraph") == "https://docs.langchain.com/oss/python/langgraph"


def test_socks5_without_h_is_rejected():
    with pytest.raises(SearchError, match="socks5h"):
        TorSearchClient("socks5://127.0.0.1:9050")


def test_parse_ddg_lite_fixture_unwraps_redirects():
    hits = parse_ddg((FIXTURES / "ddg_lite.html").read_text(encoding="utf-8"))
    assert [h.url for h in hits] == [
        "https://reference.langchain.com/python/langgraph/types/Send",
        "https://docs.langchain.com/oss/python/langgraph/use-graph-api",
        "https://dev.to/sreeni5018/leveraging-langgraphs-send-api-for-dynamic-and-parallel-workflow-execution-4pgd",
    ]
    assert hits[0].title.startswith("Send | langgraph")
    assert "Python API reference" in hits[0].snippet


def test_parse_ddg_html_fixture():
    hits = parse_ddg((FIXTURES / "ddg_html.html").read_text(encoding="utf-8"), "https://html.duckduckgo.com/html/")
    assert len(hits) == 3
    assert hits[1].url.startswith("https://medium.com/")
    assert hits[1].snippet


def test_html_to_text_drops_scripts_and_keeps_title():
    title, text = html_to_text("<html><head><title>T</title><script>evil()</script></head>"
                               "<body><p>一行目</p><style>.x{}</style><div>二行目</div></body></html>")
    assert title == "T"
    assert text == "一行目\n二行目"


def _client(handler) -> TorSearchClient:
    return TorSearchClient("socks5h://127.0.0.1:9050", 5, 5, 1, transport=httpx.MockTransport(handler))


async def test_search_and_fetch_through_policy():
    lite = (FIXTURES / "ddg_lite.html").read_text(encoding="utf-8")
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.host == "lite.duckduckgo.com":
            assert b"q=LangGraph" in request.content
            return httpx.Response(200, text=lite, headers={"content-type": "text/html; charset=utf-8"})
        if request.url.host == "reference.langchain.com":
            return httpx.Response(200, text="<title>Send</title><p>Send は map-reduce に使う</p>",
                                  headers={"content-type": "text/html"})
        return httpx.Response(404)

    result = await _client(handler).search("LangGraph Send")
    assert result.provider == "ddg_lite" and len(result.hits) == 3
    assert result.pages[0].text == "Send は map-reduce に使う"
    assert seen[0] == "https://lite.duckduckgo.com/lite/"


async def test_news_tool_sets_date_filter_and_html_fallback_when_lite_empty():
    html = (FIXTURES / "ddg_html.html").read_text(encoding="utf-8")
    bodies = []

    def handler(request):
        bodies.append(request.content)
        if request.url.host == "lite.duckduckgo.com":
            return httpx.Response(200, text="<html></html>", headers={"content-type": "text/html"})
        return httpx.Response(200, text=html, headers={"content-type": "text/html"})

    result = await _client(handler).search("x", "news", fetch_pages=0)
    assert result.provider == "ddg_html" and len(result.hits) == 3
    assert all(b"df=m" in b for b in bodies)


async def test_fetch_refuses_unlisted_host_and_private_redirect():
    def handler(request):
        if request.url.host == "good.example.com":
            return httpx.Response(302, headers={"location": "http://192.168.0.1/secret"})
        return httpx.Response(200, text="ok")

    client = _client(handler)
    assert await client.fetch("https://other.example.com/") is None  # not allowed
    client.allow("https://good.example.com/")
    assert await client.fetch("https://good.example.com/") is None  # redirect to a private IP refused


async def test_redirect_limit():
    def handler(request):
        n = int(request.url.path.strip("/") or 0)
        return httpx.Response(301, headers={"location": f"https://loop.example.com/{n + 1}"})

    client = _client(handler)
    client.allow("https://loop.example.com/0")
    assert await client.fetch("https://loop.example.com/0") is None
