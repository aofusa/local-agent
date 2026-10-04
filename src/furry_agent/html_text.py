"""HTML to plain text and search-result parsing with the standard library only (design doc §5.7, §5.10).

Fetched pages are never rendered or executed: scripts, styles and other non-text elements are dropped.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import parse_qs, urljoin, urlparse

_SKIP = {"title", "script", "style", "noscript", "template", "svg", "canvas", "iframe", "object", "head", "form", "nav",
         "footer", "button", "select", "option"}
_BLOCK = {"p", "div", "br", "li", "ul", "ol", "tr", "table", "section", "article", "h1", "h2", "h3", "h4", "h5",
          "h6", "pre", "blockquote", "header", "main", "dd", "dt", "td", "th"}
_VOID = {"br", "img", "hr", "meta", "link", "input", "source", "wbr", "area", "base", "col", "embed", "param",
         "track"}


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title: list[str] = []
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        if tag in _VOID:
            if tag == "br":
                self.parts.append("\n")
            return
        if tag in _SKIP:
            self._skip += 1
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in _VOID:
            return
        if tag in _SKIP and self._skip:
            self._skip -= 1
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title.append(data)
        if not self._skip:
            self.parts.append(data)


def html_to_text(markup: str, limit: int = 4000) -> tuple[str, str]:
    """Return ``(title, text)``; whitespace collapsed, at most ``limit`` characters of text."""
    parser = _TextParser()
    try:
        parser.feed(markup)
        parser.close()
    except Exception:  # malformed markup: keep what was parsed
        pass
    lines = []
    for line in "".join(parser.parts).splitlines():
        line = re.sub(r"[ \t 　]+", " ", line).strip()
        if line:
            lines.append(line)
    text = "\n".join(lines)
    title = re.sub(r"\s+", " ", "".join(parser.title)).strip()
    return title, text[:limit]


@dataclass
class SearchHit:
    title: str
    url: str
    snippet: str


def unwrap_redirect(href: str, base: str) -> str:
    """DuckDuckGo may wrap result links as //duckduckgo.com/l/?uddg=<target>."""
    url = urljoin(base, html.unescape(href))
    parsed = urlparse(url)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg")
        if target:
            return target[0]
    return url


class _DdgParser(HTMLParser):
    """Collect result links and snippets from DuckDuckGo Lite (class result-link / result-snippet) and the HTML
    version (class result__a / result__snippet)."""

    def __init__(self, base: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base = base
        self.hits: list[SearchHit] = []
        self._mode: str | None = None
        self._buf: list[str] = []
        self._href = ""

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        classes = (a.get("class") or "").split()
        if tag == "a" and ("result-link" in classes or "result__a" in classes):
            self._mode, self._buf, self._href = "title", [], a.get("href") or ""
        elif ("result-snippet" in classes or "result__snippet" in classes) and self.hits:
            self._mode, self._buf = "snippet", []

    def handle_endtag(self, tag):
        if self._mode == "title" and tag == "a":
            url = unwrap_redirect(self._href, self.base)
            self.hits.append(SearchHit(" ".join("".join(self._buf).split()), url, ""))
            self._mode = None
        elif self._mode == "snippet" and tag in ("td", "a"):
            if not self.hits[-1].snippet:
                self.hits[-1].snippet = " ".join("".join(self._buf).split())
            self._mode = None

    def handle_data(self, data):
        if self._mode:
            self._buf.append(data)


def parse_ddg(markup: str, base: str = "https://lite.duckduckgo.com/lite/") -> list[SearchHit]:
    parser = _DdgParser(base)
    try:
        parser.feed(markup)
        parser.close()
    except Exception:
        pass
    seen, hits = set(), []
    for hit in parser.hits:
        if hit.url.startswith(("http://", "https://")) and hit.url not in seen:
            # Sponsored results point back to duckduckgo.com (y.js); drop them.
            if urlparse(hit.url).netloc.endswith("duckduckgo.com"):
                continue
            seen.add(hit.url)
            hits.append(hit)
    return hits
