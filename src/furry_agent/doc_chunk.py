"""Chunks of local documents for /docs (docs/local-doc-mapreduce-design.md §5.3). No model.

Markdown is cut at headings (# to ###, outside code fences); a section longer than DOC_CHUNK_CHARS is cut again
by characters with DOC_CHUNK_OVERLAP and every piece keeps the heading name. Text without headings, logs and
JSON are cut by characters (JSON is not parsed or reformatted). Chunk ids are ``f<file>-c<chunk>``; the
locator shown to the user is ``relative/path#heading`` (or ``#L<first>-L<last>`` without a heading).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")


@dataclass
class Chunk:
    id: str
    file_no: int
    rel: str
    heading: str
    text: str
    first_line: int
    last_line: int
    truncated: bool = False

    @property
    def locator(self) -> str:
        return f"{self.rel}#{self.heading}" if self.heading else f"{self.rel}#L{self.first_line}-L{self.last_line}"

    @property
    def chars(self) -> int:
        return len(self.text)

    def meta(self) -> dict:
        return {"id": self.id, "locator": self.locator, "chars": self.chars, "file_no": self.file_no,
                "rel": self.rel, "heading": self.heading, "truncated": self.truncated}


def sections(text: str) -> list[tuple[str, int, list[str]]]:
    """(heading, first line number, lines) per Markdown section. Lines before the first heading form a section
    with an empty heading."""
    out: list[tuple[str, int, list[str]]] = []
    heading, start, lines = "", 1, []
    in_fence = False
    for number, line in enumerate(text.split("\n"), start=1):
        if _FENCE.match(line):
            in_fence = not in_fence
        match = None if in_fence else _HEADING.match(line)
        if match:
            if any(x.strip() for x in lines):
                out.append((heading, start, lines))
            heading, start, lines = match.group(2).strip()[:80], number, [line]
            continue
        lines.append(line)
    if any(x.strip() for x in lines):
        out.append((heading, start, lines))
    return out


def _pieces(text: str, first_line: int, size: int, overlap: int) -> list[tuple[str, int, int]]:
    """Cut ``text`` into pieces of at most ``size`` characters with ``overlap`` (line numbers kept)."""
    size = max(200, size)
    overlap = max(0, min(overlap, size // 2))
    if len(text) <= size:
        return [(text, first_line, first_line + text.count("\n"))]
    out = []
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            # Prefer to end at a line break in the last fifth of the piece.
            cut = text.rfind("\n", start + size * 4 // 5, end)
            if cut > start:
                end = cut + 1
        piece = text[start:end]
        line = first_line + text.count("\n", 0, start)
        out.append((piece, line, line + piece.count("\n")))
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return out


def chunk_file(text: str, rel: str, file_no: int, size: int = 3000, overlap: int = 200,
               truncated: bool = False) -> list[Chunk]:
    markdown = rel.lower().endswith(".md")
    parts = sections(text) if markdown else [("", 1, text.split("\n"))]
    chunks: list[Chunk] = []
    for heading, start, lines in parts:
        body = "\n".join(lines)
        if not body.strip():
            continue
        for piece, first, last in _pieces(body, start, size, overlap):
            if not piece.strip():
                continue
            chunks.append(Chunk(f"f{file_no}-c{len(chunks) + 1}", file_no, rel, heading, piece, first, last,
                                truncated))
    return chunks


def outline(chunks: list[Chunk], files: list[dict], limit: int = 80) -> str:
    """The planner's view: files (path, bytes) and their chunk ids with heading and size. No body text."""
    lines = []
    shown = 0
    for f in files:
        mine = [c for c in chunks if c.file_no == f["file_no"]]
        lines.append(f"{f['rel']}（{f['size']} バイト、{len(mine)} チャンク{'、先頭のみ' if f.get('truncated') else ''}）")
        for chunk in mine:
            if shown >= limit:
                break
            lines.append(f"  {chunk.id} {chunk.heading or f'L{chunk.first_line}-L{chunk.last_line}'}（{chunk.chars} 字）")
            shown += 1
    if shown < len(chunks):
        lines.append(f"（ほか {len(chunks) - shown} チャンクは省略）")
    return "\n".join(lines)
