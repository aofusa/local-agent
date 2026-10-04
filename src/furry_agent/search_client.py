"""Web search and page fetch through the local Tor SOCKS proxy (design doc §5.7, §5.10).

Only the LangGraph orchestrator opens these sockets; the search workers (llama-server) never see the proxy.
Every request goes through ``socks5h://`` so DNS is resolved by Tor as well. Allowed targets are the search
engine itself and the result URLs it returned (plus URLs the user pasted); IP literals, private networks,
``.onion`` and non-HTTP schemes are refused, and every redirect hop is checked again.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import secrets
import socket
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import httpx

from furry_agent.html_text import SearchHit, html_to_text, parse_ddg

log = logging.getLogger("furry_agent.search")

USER_AGENT = "local-image-gen-agent/search"
MAX_REDIRECTS = 3
MAX_BYTES = int(1.5 * 1024 * 1024)
TEXT_LIMIT = 4000
PROVIDERS = (
    ("ddg_lite", "https://lite.duckduckgo.com/lite/"),
    ("ddg_html", "https://html.duckduckgo.com/html/"),  # only when Lite came back empty
)
SEARCH_HOSTS = frozenset({"lite.duckduckgo.com", "html.duckduckgo.com"})


class SearchError(RuntimeError):
    pass


class UrlRefused(SearchError):
    pass


def check_url(url: str) -> str:
    """Raise UrlRefused unless the URL is a public clearnet http(s) URL with a host name."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise UrlRefused(f"http/https 以外は取得しません: {parsed.scheme or url[:20]}")
    host = (parsed.hostname or "").rstrip(".").lower()
    if not host:
        raise UrlRefused("ホスト名がありません")
    if parsed.username or parsed.password:
        raise UrlRefused("認証情報付きの URL は取得しません")
    if host.endswith(".onion"):
        raise UrlRefused(".onion は取得しません")
    if host in ("localhost",) or host.endswith((".localhost", ".local", ".internal", ".lan", ".home.arpa")):
        raise UrlRefused(f"ローカルのホストは取得しません: {host}")
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        pass
    else:
        raise UrlRefused(f"IP アドレス直指定は取得しません: {host}")
    if "." not in host:
        raise UrlRefused(f"ドメイン名ではありません: {host}")
    if parsed.port not in (None, 80, 443):
        raise UrlRefused(f"80/443 以外のポートは取得しません: {parsed.port}")
    return url


def is_private_address(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return not ip.is_global


@dataclass
class Page:
    url: str
    title: str
    text: str


@dataclass
class SearchResult:
    query: str
    provider: str
    hits: list[SearchHit] = field(default_factory=list)
    pages: list[Page] = field(default_factory=list)
    error: str | None = None


def tor_listening(socks_url: str, timeout: float = 2.0) -> bool:
    parsed = urlparse(socks_url)
    try:
        with socket.create_connection((parsed.hostname or "127.0.0.1", parsed.port or 9050), timeout=timeout):
            return True
    except OSError:
        return False


class TorSearchClient:
    """One client per search branch: its SOCKS credentials put the branch on its own Tor circuit."""

    def __init__(self, socks_url: str, timeout_s: float = 30.0, max_results: int = 5, fetch_pages: int = 1,
                 isolation: str | None = None, transport: httpx.AsyncBaseTransport | None = None):
        if not socks_url.startswith("socks5h://"):
            raise SearchError("Tor の SOCKS は socks5h:// で指定してください（DNS 漏れ防止）")
        self.timeout_s = timeout_s
        self.max_results = max_results
        self.fetch_pages = fetch_pages
        self.allowed: set[str] = set(SEARCH_HOSTS)
        parsed = urlparse(socks_url)
        tag = isolation or secrets.token_hex(4)
        self.proxy = f"socks5h://branch-{tag}:x@{parsed.hostname}:{parsed.port or 9050}"
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        kwargs: dict = {"timeout": httpx.Timeout(self.timeout_s, connect=min(self.timeout_s, 30.0)),
                        "headers": {"User-Agent": USER_AGENT, "Accept-Language": "ja,en;q=0.8"},
                        "follow_redirects": False, "trust_env": False}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        else:
            kwargs["proxy"] = self.proxy
        return httpx.AsyncClient(**kwargs)

    def allow(self, url: str) -> None:
        """Allow a host that the user pasted or that the search engine returned."""
        host = (urlparse(url).hostname or "").lower()
        if host:
            self.allowed.add(host)

    async def _get(self, http: httpx.AsyncClient, method: str, url: str, data: dict | None = None) -> tuple[str, bytes, str]:
        for _ in range(MAX_REDIRECTS + 1):
            check_url(url)
            host = (urlparse(url).hostname or "").lower()
            if host not in self.allowed:
                raise UrlRefused(f"許可していないホストです: {host}")
            async with http.stream(method, url, data=data) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("location")
                    if not location:
                        raise SearchError(f"リダイレクト先がありません: {url}")
                    target = urljoin(url, location)
                    check_url(target)
                    # A redirect may leave the result's host (e.g. http -> https, www.); allow the new host once.
                    self.allowed.add((urlparse(target).hostname or "").lower())
                    url, method, data = target, "GET", None
                    continue
                if response.status_code >= 400:
                    raise SearchError(f"HTTP {response.status_code}: {url}")
                content_type = response.headers.get("content-type", "")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_BYTES:
                        break
                return url, bytes(body[:MAX_BYTES]), content_type
        raise SearchError(f"リダイレクトが {MAX_REDIRECTS} 回を超えました: {url}")

    @staticmethod
    def _decode(body: bytes, content_type: str) -> str:
        charset = "utf-8"
        if "charset=" in content_type:
            charset = content_type.split("charset=")[-1].split(";")[0].strip() or "utf-8"
        try:
            return body.decode(charset, errors="replace")
        except LookupError:
            return body.decode("utf-8", errors="replace")

    async def search(self, query: str, tool: str = "web", fetch_pages: int | None = None) -> SearchResult:
        """``tool``: "web", or "news" (DuckDuckGo's date filter: the past month)."""
        query = " ".join(query.split())[:200]
        result = SearchResult(query, "")
        form = {"q": query, "kl": "jp-jp"}
        if tool == "news":
            form["df"] = "m"
        pages = self.fetch_pages if fetch_pages is None else fetch_pages
        async with self._client() as http:
            for provider, endpoint in PROVIDERS:
                result.provider = provider
                try:
                    _, body, ctype = await self._get(http, "POST", endpoint, form)
                except (httpx.HTTPError, SearchError) as exc:
                    result.error = f"{provider}: {exc!r}"[:300]
                    log.warning("search provider=%s failed: %s", provider, result.error)
                    continue
                hits = parse_ddg(self._decode(body, ctype), endpoint)[: self.max_results]
                if hits:
                    result.hits, result.error = hits, None
                    break
                result.error = f"{provider}: 0 件"
            for hit in result.hits:
                self.allow(hit.url)
            for hit in result.hits[:pages]:
                page = await self._fetch_with(http, hit.url)
                if page:
                    result.pages.append(page)
        # Result URLs may be logged; the query text is not (design doc §5.10).
        log.info("search provider=%s hits=%d pages=%d urls=%s", result.provider, len(result.hits),
                 len(result.pages), [h.url for h in result.hits])
        return result

    async def _fetch_with(self, http: httpx.AsyncClient, url: str) -> Page | None:
        try:
            final, body, ctype = await self._get(http, "GET", url)
        except (httpx.HTTPError, SearchError) as exc:
            log.info("fetch failed url=%s: %r", url, exc)
            return None
        if ctype and not any(t in ctype for t in ("text/html", "text/plain", "application/xhtml")):
            return None
        text = self._decode(body, ctype)
        if "html" in ctype or "<html" in text[:500].lower():
            title, text = html_to_text(text, TEXT_LIMIT)
        else:
            title, text = "", " ".join(text.split())[:TEXT_LIMIT]
        return Page(final, title, text)

    async def fetch(self, url: str) -> Page | None:
        """Fetch one page (a URL the user pasted). The caller must have called allow(url)."""
        async with self._client() as http:
            return await self._fetch_with(http, url)


async def check_tor(socks_url: str, timeout_s: float = 30.0) -> bool:
    """True when the SOCKS port answers; a full circuit check is done by scripts/doctor.ps1."""
    return await asyncio.to_thread(tor_listening, socks_url, min(timeout_s, 3.0))
