"""Local documents for the chat tab's /docs: which files may be read (docs/local-doc-mapreduce-design.md §5.2).

Only the orchestrator opens files, and only here. No model ever chooses a path: the user names one file or
directory, it must resolve (real path, symlinks and junctions followed) inside one of LOCAL_DOC_ROOTS, and every
file is checked against the deny list before the extension allow list. Denied files are reported by name only;
their contents never reach a prompt or the progress text. Nothing is written, executed, moved or deleted.
"""

from __future__ import annotations

import fnmatch
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path

EXTENSIONS = (".md", ".txt", ".log", ".json", ".toml", ".yaml", ".yml")
DENY = (".env", ".env.*", "id_rsa", "*.pem", "*.key", "*.pfx", "credentials*", "secret*", "node_modules", ".git",
        "__pycache__", "outputs", "output", "*.safetensors", "*.gguf", "*.png", "*.jpg")
# Windows device names: never a regular file, whatever the folder.
_DEVICES = re.compile(r"^(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?$", re.IGNORECASE)
SCAN_LIMIT = 20000  # directory entries looked at before giving up on a huge tree


class DocError(RuntimeError):
    """A refusal for the user (design §5.9). ``code``: unset / outside / empty / denied / missing / bad_path."""

    def __init__(self, code: str, message: str, denied: list[dict] | None = None):
        super().__init__(message)
        self.code = code
        self.denied = denied or []


@dataclass
class DocFile:
    path: Path        # real path, inside the root
    rel: str          # relative to the root, forward slashes (shown to the user and to the models)
    size: int
    depth: int


@dataclass
class DocTarget:
    root: Path
    root_name: str
    files: list[DocFile] = field(default_factory=list)
    denied: list[dict] = field(default_factory=list)    # {rel, reason}
    skipped: int = 0    # files with another extension (counted, not listed)
    dropped: int = 0    # files past DOC_MAX_FILES
    single: bool = False


def parse_roots(value: str) -> list[Path]:
    """LOCAL_DOC_ROOTS: comma separated absolute paths; relative entries are ignored. No file system access here
    (settings are read on the event loop); ``real_roots`` resolves them when a request is served."""
    roots = []
    for raw in (value or "").split(","):
        raw = raw.strip().strip('"')
        if raw and Path(raw).is_absolute():
            roots.append(Path(raw))
    return roots


def real_roots(roots: list[Path]) -> list[Path]:
    """The roots that exist, as real paths (blocking: call in a thread)."""
    out = []
    for root in roots:
        try:
            real = Path(root).resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if real.is_dir() and real not in out:
            out.append(real)
    return out


def _key(path: Path | str) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def inside(path: Path, root: Path) -> bool:
    """``path`` is ``root`` or below it, compared per path component (never a string prefix: C:\\docs2 is not
    inside C:\\docs)."""
    try:
        return os.path.commonpath([_key(path), _key(root)]) == _key(root)
    except ValueError:  # different drives
        return False


def deny_reason(rel_parts: list[str]) -> str | None:
    """The deny-list pattern that matches any component (case-insensitive), or None."""
    for part in rel_parts:
        low = part.lower()
        for pattern in DENY:
            if fnmatch.fnmatchcase(low, pattern):
                return pattern
        if _DEVICES.match(low):
            return "デバイス名"
    return None


def _check_text(raw: str) -> str:
    text = raw.strip().strip('"').strip("'")
    if not text:
        raise DocError("bad_path", "パスを指定してください（例: /docs README.md 要点は？）")
    if "\x00" in text:
        raise DocError("bad_path", "パスに使えない文字があります")
    parts = re.split(r"[\\/]+", text)
    if any(p == ".." for p in parts):
        raise DocError("bad_path", "「..」を含むパスは読みません")
    # NTFS alternate data streams (file.txt:secret) and device paths (\\?\, \\.\): only "C:" may hold a colon.
    body = text[2:] if re.match(r"^[A-Za-z]:", text) else text
    if ":" in body or text.startswith(("\\\\?\\", "\\\\.\\", "//?/", "//./")):
        raise DocError("bad_path", "代替データストリームやデバイスのパスは読みません")
    return text


def _candidates(text: str, roots: list[Path]) -> list[Path]:
    path = Path(text)
    if path.is_absolute():
        return [path]
    if re.match(r"^[A-Za-z]:", text) or text.startswith(("\\", "/")):
        raise DocError("bad_path", "ドライブ相対やルート相対のパスは使えません。絶対パスか、許可ルートからの相対パスで指定してください")
    return [root / path for root in roots]


def _rel(path: Path, root: Path) -> str:
    rel = os.path.relpath(path, root)
    return "." if rel == "." else rel.replace("\\", "/")


def resolve(raw: str, roots: list[Path], *, max_files: int = 30, max_depth: int = 4,
            extensions: tuple[str, ...] = EXTENSIONS) -> DocTarget:
    """The files of one /docs request. Raises DocError for every refusal."""
    if not roots:
        raise DocError("unset", "LOCAL_DOC_ROOTS が未設定です")
    roots = real_roots(roots)
    if not roots:
        raise DocError("unset", "LOCAL_DOC_ROOTS のディレクトリが見つかりません")
    text = _check_text(raw)
    found: tuple[Path, Path] | None = None
    exists = False
    for candidate in _candidates(text, roots):
        try:
            real = candidate.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        exists = True
        root = next((r for r in roots if inside(real, r)), None)
        if root is not None:
            found = (real, root)
            break
    if found is None:
        if exists:
            raise DocError("outside", "許可したディレクトリの外です")
        raise DocError("missing", "指定したパスが見つかりません（許可ルートからの相対パスか絶対パスで指定してください）")
    real, root = found
    target = DocTarget(root=root, root_name=root.name or str(root))
    rel = _rel(real, root)
    rel_parts = [] if rel == "." else rel.split("/")
    reason = deny_reason(rel_parts)
    if reason:
        raise DocError("denied", f"拒否名に当たるため読みません（{reason}）", [{"rel": rel, "reason": reason}])
    if real.is_file():
        target.single = True
        _add(target, real, root, 0, max_files, extensions)
    elif real.is_dir():
        _walk(target, real, root, max_files, max_depth, extensions)
    else:
        raise DocError("denied", "通常のファイルでもディレクトリでもありません", [{"rel": rel, "reason": "通常のファイルではない"}])
    if not target.files:
        if target.denied and not target.skipped:
            raise DocError("denied", "対象のファイルはすべて拒否名に当たりました", target.denied)
        raise DocError("empty", "読めるテキストがありません", target.denied)
    return target


def _add(target: DocTarget, path: Path, root: Path, depth: int, max_files: int,
         extensions: tuple[str, ...]) -> None:
    rel = _rel(path, root)
    reason = deny_reason(rel.split("/"))
    if reason:  # the deny list comes before the allow list (§8)
        target.denied.append({"rel": rel, "reason": reason})
        return
    try:
        real = path.resolve(strict=True)
        info = os.stat(real)
    except (OSError, RuntimeError):
        return
    if not inside(real, root):
        target.denied.append({"rel": rel, "reason": "リンク先が許可ルートの外"})
        return
    if not stat.S_ISREG(info.st_mode):
        target.denied.append({"rel": rel, "reason": "通常のファイルではない"})
        return
    if path.suffix.lower() not in extensions:
        target.skipped += 1
        return
    if len(target.files) >= max_files:
        target.dropped += 1
        return
    target.files.append(DocFile(real, rel, info.st_size, depth))


def _walk(target: DocTarget, top: Path, root: Path, max_files: int, max_depth: int,
          extensions: tuple[str, ...]) -> None:
    """Breadth first (shallow files first: README before deep sub-folders), sorted, at most ``max_depth``
    levels below ``top``. Links and junctions to directories are not followed; a directory whose real path
    leaves the root is skipped."""
    level = [top]
    scanned = 0
    for depth in range(max_depth + 1):
        next_level: list[Path] = []
        for directory in level:
            try:
                entries = sorted(os.scandir(directory), key=lambda e: e.name.lower())
            except OSError:
                continue
            for entry in entries:
                scanned += 1
                if scanned > SCAN_LIMIT:
                    return
                path = Path(entry.path)
                rel = _rel(path, root)
                try:
                    is_link = entry.is_symlink() or bool(getattr(os.path, "isjunction", lambda _p: False)(path))
                    is_dir = entry.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                if is_dir or (is_link and path.is_dir()):
                    reason = deny_reason(rel.split("/"))
                    if reason:
                        target.denied.append({"rel": rel + "/", "reason": reason})
                    elif is_link:
                        target.denied.append({"rel": rel + "/", "reason": "リンクのディレクトリは辿らない"})
                    elif depth < max_depth:
                        next_level.append(path)
                    continue
                _add(target, path, root, depth, max_files, extensions)
        level = next_level
        if not level:
            return


def read_text(doc: DocFile, max_bytes: int) -> tuple[str, bool]:
    """(text, truncated). At most ``max_bytes`` from the start; binary files (NUL bytes) give ""."""
    with open(doc.path, "rb") as fh:
        data = fh.read(max_bytes + 1)
    truncated = len(data) > max_bytes
    data = data[:max_bytes]
    if b"\x00" in data[:8192]:
        return "", truncated
    return data.decode("utf-8-sig", errors="replace"), truncated
