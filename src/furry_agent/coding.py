"""The chat tab's code branch: the model's answer format and pure steps (docs/chat-deep-search-creative-sandbox.md §5).

The 27B answers in a plain format rather than JSON (code inside JSON strings breaks on escaping)::

    SPEC: what the program does
    FILE: main.py
    ```python
    ...
    ```
    SETUP: python -m pip install --target .deps -r requirements.txt   (optional)
    COMMAND: python main.py

Commands become argv lists through sandbox.parse_command (no shell). Nothing here runs anything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from furry_agent import sandbox
from furry_agent.config import env_int
from furry_agent.sandbox import Profile, SandboxError

_FILE = re.compile(r"^\s*(?:#+\s*)?FILE:\s*`?([^\s`]+)`?\s*\n```[^\n]*\n(.*?)\n```", re.MULTILINE | re.DOTALL)
_COMMAND = re.compile(r"^\s*COMMAND:\s*(.+)$", re.MULTILINE)
_SETUP = re.compile(r"^\s*SETUP:\s*(.+)$", re.MULTILINE)
_SPEC = re.compile(r"^\s*SPEC:\s*(.+)$", re.MULTILINE)
_LANG = {".py": "python", ".rs": "rust", ".toml": "toml", ".txt": "text", ".md": "markdown", ".json": "json",
         ".csv": "csv", ".yaml": "yaml", ".yml": "yaml"}
# Asking for dependencies (and so for network during the setup step) must be explicit in the turn (§5.3).
_NETWORK = re.compile(r"pip\s*install|pip で|cargo add|依存(関係|ライブラリ|パッケージ)?を(入れ|インストール|取得)|"
                      r"ネットワーク(を使|あり|許可)|インターネット(を使|接続)|外部(ライブラリ|パッケージ)を(使|入れ)|"
                      r"ライブラリを(入れ|インストール)|crates?\.io|pypi", re.IGNORECASE)


def wants_network(text: str) -> bool:
    return bool(_NETWORK.search(text or ""))


@dataclass
class CodePlan:
    spec: str = ""
    files: list[dict] = field(default_factory=list)
    command: list[str] = field(default_factory=list)
    setup: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def language_of(path: str) -> str:
    for ext, lang in _LANG.items():
        if path.lower().endswith(ext):
            return lang
    return "text"


def default_setup(profile: Profile, files: list[dict]) -> list[str]:
    paths = {f["path"] for f in files}
    if profile.name == "python" and "requirements.txt" in paths:
        return ["python", "-m", "pip", "install", "--no-cache-dir", "--target", ".deps", "-r", "requirements.txt"]
    if profile.name == "rust" and "Cargo.toml" in paths:
        return ["cargo", "fetch"]
    return []


def parse_plan(text: str, profile: Profile, previous: list[dict] | None = None) -> CodePlan:
    """Files, command and setup from the model's answer. ``previous`` files are kept unless replaced (a fix
    returns only the files that change)."""
    plan = CodePlan()
    spec = _SPEC.search(text or "")
    plan.spec = spec.group(1).strip()[:400] if spec else ""
    files: dict[str, dict] = {f["path"]: dict(f) for f in previous or []}
    for match in _FILE.finditer(text or ""):
        try:
            path = sandbox.safe_relpath(match.group(1))
        except SandboxError as exc:
            plan.problems.append(str(exc))
            continue
        files[path] = {"path": path, "content": match.group(2).rstrip() + "\n", "language": language_of(path)}
    plan.files = list(files.values())[:sandbox.MAX_FILES]
    command = _COMMAND.search(text or "")
    try:
        plan.command = sandbox.parse_command(command.group(1), profile) if command else list(profile.default_command)
    except SandboxError as exc:
        plan.problems.append(f"COMMAND: {exc}")
        plan.command = list(profile.default_command)
    setup = _SETUP.search(text or "")
    try:
        plan.setup = sandbox.parse_command(setup.group(1), profile, setup=True) if setup else default_setup(profile, plan.files)
    except SandboxError as exc:
        plan.problems.append(f"SETUP: {exc}")
        plan.setup = default_setup(profile, plan.files)
    if not plan.files:
        plan.problems.append("ファイルがありません")
    return plan


# Output tails of a failed run handed to the model for the fix.
FIX_STDOUT_CHARS = env_int("CODE_FIX_STDOUT_CHARS", 1000, 100)
FIX_STDERR_CHARS = env_int("CODE_FIX_STDERR_CHARS", 1500, 100)


def fix_input(request: str, code: dict) -> str:
    """The failed run for the model to fix: files, command, exit code and the output tails."""
    files = "\n\n".join(f"FILE: {f['path']}\n```{f.get('language', '')}\n{f['content']}\n```" for f in code["files"])
    status = "時間切れ（60 秒）" if code.get("timed_out") else f"終了コード {code.get('last_exit')}"
    return (f"依頼: {request}\n\n前回のファイル:\n{files}\n\nCOMMAND: {' '.join(code['command'])}\n\n"
            f"実行結果: {status}\n\nstdout（末尾）:\n```text\n{code.get('stdout_tail', '')[-FIX_STDOUT_CHARS:]}\n```\n\n"
            f"stderr（末尾）:\n```text\n{code.get('stderr_tail', '')[-FIX_STDERR_CHARS:]}\n```\n\n"
            "失敗の原因を直してください。変わるファイルは全文で返してください。")


def files_markdown(files: list[dict], limit: int = 12000) -> str:
    out, used = [], 0
    for f in files:
        block = f"`{f['path']}`\n```{f.get('language', '')}\n{f['content'].rstrip()}\n```"
        if used + len(block) > limit:
            out.append(f"`{f['path']}`（長いため省略。artifacts/code にあります）")
            continue
        out.append(block)
        used += len(block)
    return "\n\n".join(out)


def command_text(argv: list[str]) -> str:
    import shlex

    return " ".join(shlex.quote(a) for a in argv)
