"""Docker sandbox for the chat tab's code branch (docs/chat-deep-search-creative-sandbox.md §5).

Every container run of the project goes through this module; no other module calls ``docker`` or a shell. The
constraints are fixed here and are not configurable:

- image ``python:3.12-slim`` (Debian slim); ``rust:1.88-slim`` only for a request that names Rust;
- ``--network none``. A run may have one setup step (pip / cargo fetch) with network, only when the user asked
  for dependencies in that turn and approved the network on the confirmation card; the program itself always
  runs without network;
- ``--read-only`` root, ``/work`` (the run's ``artifacts/code/<run_id>``) is the only mount and the only writable
  path besides ``--tmpfs /tmp:rw,size=64m``;
- ``--memory 2g --cpus 2 --pids-limit 256 --cap-drop ALL --security-opt no-new-privileges``, a non-root
  ``--user``; no docker.sock, no host home, repository, ``.env``, SSH keys or browser profiles; no ``--env-file``
  and no host environment variables (only the fixed values below);
- 60 s per container, then ``docker kill``; stdout and stderr are cut to their last 8 KB;
- ``--pull never``: images are fetched by scripts/setup-sandbox.ps1, never during a chat run;
- argv lists only (never a shell string): shells, ``-c`` scripts and shell operators are refused;
- ``run`` refuses to start without ``approved=True`` (the confirmation card of the chat graph).

Outputs: ``/work`` is the run directory itself, so files the program writes are already on the host when the
container exits (``--rm`` removes the container; nothing is copied out of it).
"""

from __future__ import annotations

import asyncio
import logging
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from furry_agent.config import env_int

log = logging.getLogger("furry_agent.sandbox")

TIMEOUT_S = 60
MEMORY = "2g"
CPUS = "2"
PIDS_LIMIT = 256
TMPFS = "/tmp:rw,size=64m"
TAIL_BYTES = env_int("SANDBOX_TAIL_BYTES", 8192, 256)  # output kept of a run
WORKDIR = "/work"
MAX_FILES = env_int("SANDBOX_MAX_FILES", 20, 1, 1000)
MAX_FILE_BYTES = env_int("SANDBOX_MAX_FILE_BYTES", 200_000, 1000)
MAX_RUNS = 2  # first run + one fix (§5.1)
DESKTOP_START_S = env_int("SANDBOX_DESKTOP_START_S", 180, 10)

# Tokens that only mean something to a shell. An argv element equal to one of them, or containing a command
# substitution, is refused: the container never sees a shell string.
SHELL_TOKENS = {";", "&&", "||", "|", "&", ">", ">>", "<", "<<", "2>", "2>&1", "|&"}
_SUBSTITUTION = re.compile(r"`|\$\(|&&|\|\||;")
SHELLS = {"sh", "bash", "dash", "zsh", "ash", "fish", "cmd", "cmd.exe", "powershell", "pwsh", "env", "xargs",
          "nohup", "timeout", "busybox"}
_SAFE_PATH = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-/]*$")


class SandboxError(RuntimeError):
    pass


class NotApproved(SandboxError):
    """Raised when a run is attempted before the user approved it on the confirmation card."""


@dataclass(frozen=True)
class Profile:
    name: str
    image: str
    language: str
    commands: frozenset[str]
    setup_commands: frozenset[str]
    env: tuple[tuple[str, str], ...]
    default_command: tuple[str, ...]


PYTHON = Profile(
    "python", "python:3.12-slim", "python", frozenset({"python", "python3"}), frozenset({"python", "python3", "pip"}),
    (("HOME", "/tmp"), ("PYTHONDONTWRITEBYTECODE", "1"), ("PYTHONUNBUFFERED", "1"),
     ("PYTHONPATH", f"{WORKDIR}/.deps"), ("PIP_NO_CACHE_DIR", "1"), ("PIP_DISABLE_PIP_VERSION_CHECK", "1")),
    ("python", "main.py"))
RUST = Profile(
    "rust", "rust:1.88-slim", "rust", frozenset({"cargo", "rustc"}), frozenset({"cargo"}),
    (("HOME", "/tmp"), ("CARGO_HOME", f"{WORKDIR}/.cargo"), ("CARGO_TARGET_DIR", f"{WORKDIR}/target"),
     ("CARGO_TERM_COLOR", "never")),
    ("cargo", "run", "--quiet", "--offline"))
PROFILES = {p.name: p for p in (PYTHON, RUST)}
_RUST = re.compile(r"\brust\b|\bcargo\b|\.rs\b|ラスト言語", re.IGNORECASE)


def profile_for(text: str) -> Profile:
    """Rust only when the request names it; Python otherwise (§5.3)."""
    return RUST if _RUST.search(text or "") else PYTHON


# --- argv and files -------------------------------------------------------------------------------------------


def check_argv(argv, profile: Profile, *, setup: bool = False) -> list[str]:
    """A validated argv list for ``profile``. Raises SandboxError for anything that would need a shell."""
    if isinstance(argv, str) or not isinstance(argv, (list, tuple)) or not argv:
        raise SandboxError("コマンドは argv の配列で指定してください（シェル文字列は使えません）")
    out = [str(a) for a in argv]
    if len(out) > 32 or any(len(a) > 400 for a in out):
        raise SandboxError("コマンドが長すぎます")
    for arg in out:
        if arg in SHELL_TOKENS or _SUBSTITUTION.search(arg) or "\n" in arg or "\x00" in arg:
            raise SandboxError(f"シェルの演算子はコマンドに使えません: {arg!r}")
    exe = PurePosixPath(out[0]).name.lower()
    if exe in SHELLS or (len(out) > 1 and out[1] == "-c" and exe not in ("python", "python3")):
        raise SandboxError(f"シェル経由の実行はできません: {out[0]}")
    allowed = profile.setup_commands if setup else profile.commands
    if out[0] not in allowed:
        raise SandboxError(f"{profile.name} のコンテナで使えるコマンドは {', '.join(sorted(allowed))} です（{out[0]}）")
    if exe in ("python", "python3") and len(out) > 1 and out[1] == "-c":
        raise SandboxError("python -c は使えません。ファイルに書いて実行してください")
    return out


def parse_command(text: str, profile: Profile, *, setup: bool = False) -> list[str]:
    """A command line written by the model or edited on the confirmation card -> argv (no shell)."""
    text = (text or "").strip().strip("`").strip()
    if not text:
        raise SandboxError("コマンドが空です")
    try:
        argv = shlex.split(text, posix=True)
    except ValueError as exc:
        raise SandboxError(f"コマンドを解釈できません: {exc}") from exc
    return check_argv(argv, profile, setup=setup)


def safe_relpath(path: str) -> str:
    """A relative POSIX path inside /work: no absolute path, drive, '..', hidden top-level or odd characters."""
    raw = (path or "").strip().replace("\\", "/")
    while raw.startswith("./"):
        raw = raw[2:]
    pure = PurePosixPath(raw)
    if (not raw or pure.is_absolute() or ":" in raw or ".." in pure.parts or not _SAFE_PATH.match(raw)
            or raw.startswith(".") or len(raw) > 120 or len(pure.parts) > 6):
        raise SandboxError(f"使えないファイルパスです: {path!r}")
    return str(pure)


def safe_run_id(run_id: str) -> str:
    value = re.sub(r"[^A-Za-z0-9\-]", "", run_id or "")[:48]
    if not value:
        raise SandboxError("run_id が空です")
    return value


def run_dir_for(root: Path, run_id: str) -> Path:
    return Path(root) / safe_run_id(run_id)


def write_files(run_dir: Path, files: list[dict]) -> list[dict]:
    """Write the generated files into the run directory. Returns [{path, bytes}]."""
    if len(files) > MAX_FILES:
        raise SandboxError(f"ファイルは {MAX_FILES} 個までです")
    run_dir.mkdir(parents=True, exist_ok=True)
    base = run_dir.resolve()
    written = []
    for item in files:
        rel = safe_relpath(item["path"])
        data = (item.get("content") or "").encode("utf-8")
        if len(data) > MAX_FILE_BYTES:
            raise SandboxError(f"{rel} が大きすぎます（{len(data)} バイト）")
        target = (base / rel).resolve()
        if base not in target.parents:
            raise SandboxError(f"使えないファイルパスです: {rel}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        written.append({"path": rel, "bytes": len(data)})
    return written


# --- docker argv --------------------------------------------------------------------------------------------------


def container_name(run_id: str, round_no: int, step: str = "run") -> str:
    return f"local-agent-sbx-{safe_run_id(run_id)[:32]}-{round_no}-{step}"


def build_argv(*, docker: str, name: str, run_dir: Path, root: Path, argv: list[str], profile: Profile,
               user: str, network: bool = False, setup: bool = False) -> list[str]:
    """The ``docker run`` argv (§5.3). ``run_dir`` must be a directory inside ``root`` (artifacts/code).
    Only the setup step (dependencies) may have network; the program itself never has."""
    if network and not setup:
        raise SandboxError("プログラム本体はネットワークなしで実行します")
    run_dir, root = Path(run_dir).resolve(), Path(root).resolve()
    if root not in run_dir.parents:
        raise SandboxError("マウントできるのは artifacts/code/<run_id> だけです")
    if "," in str(run_dir) or "=" in str(run_dir):
        raise SandboxError(f"run ディレクトリのパスに , や = は使えません: {run_dir}")
    if not re.fullmatch(r"[0-9]{1,6}:[0-9]{1,6}", user or "") or user.split(":")[0] == "0":
        raise SandboxError(f"--user は root 以外の uid:gid にしてください（{user!r}）")
    env: list[str] = []
    for key, value in profile.env:
        env += ["--env", f"{key}={value}"]
    return [docker, "run", "--rm", "--name", name, "--pull", "never",
            "--network", "bridge" if network else "none",
            "--read-only",
            "--memory", MEMORY, "--memory-swap", MEMORY, "--cpus", CPUS, "--pids-limit", str(PIDS_LIMIT),
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--user", user,
            "--mount", f"type=bind,src={run_dir},dst={WORKDIR}",
            "--workdir", WORKDIR,
            "--tmpfs", TMPFS,
            *env,
            profile.image, *check_argv(argv, profile, setup=setup)]


# --- running --------------------------------------------------------------------------------------------------


@dataclass
class RunResult:
    argv: list[str]
    exit_code: int | None
    stdout_tail: str = ""
    stderr_tail: str = ""
    timed_out: bool = False
    seconds: float = 0.0
    step: str = "run"
    error: str = ""
    files: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and not self.error


def tail(data: bytes | str | None, limit: int = TAIL_BYTES) -> str:
    if data is None:
        return ""
    raw = data.encode("utf-8", "replace") if isinstance(data, str) else data
    return raw[-limit:].decode("utf-8", "replace")


def _flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


def _exec(argv: list[str], timeout_s: float) -> subprocess.CompletedProcess:
    """The docker CLI itself, never a shell. Raises subprocess.TimeoutExpired."""
    return subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout_s,
                          creationflags=_flags())


# --- the engine ------------------------------------------------------------------------------------------------
# The docker CLI comes first: whatever engine it reaches (Docker Desktop, Docker Engine, a remote context) is used
# as it is. Only when the CLI is installed but cannot reach an engine is Docker Desktop started, for one approved
# run. Without a docker command nothing is started at all.

READY, STOPPED, MISSING, WRONG_OS = "ready", "stopped", "missing", "wrong_os"


async def engine_state(docker: str = "docker", runner=None) -> tuple[str, str]:
    """(state, reason): READY, STOPPED (CLI present, no engine), MISSING (no docker command) or WRONG_OS."""
    runner = runner or _exec
    try:
        out = await asyncio.to_thread(runner, [docker, "version", "--format", "{{.Server.Os}}"], 20)
    except FileNotFoundError:
        return MISSING, "docker コマンドがありません（Docker を入れると実行できます。Docker Desktop の起動も試みません）"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return STOPPED, f"Docker に接続できません（{exc}）"
    os_name = tail(out.stdout).strip()
    if out.returncode != 0:
        return STOPPED, "Docker のエンジンに接続できません（Docker Desktop などのエンジンを起動してから送り直してください）"
    if os_name and os_name != "linux":
        return WRONG_OS, f"Docker のエンジンが Linux ではありません（{os_name}）。Linux コンテナに切り替えてください"
    return READY, "ok"


async def docker_status(docker: str = "docker", runner=None) -> tuple[bool, str]:
    """(ok, reason): the engine the docker CLI reaches runs Linux containers."""
    state, reason = await engine_state(docker, runner)
    return state == READY, reason


def desktop_exe() -> Path | None:
    """Docker Desktop's executable on this machine (Windows / macOS), or None."""
    import os

    candidates = []
    if sys.platform == "win32":
        for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramW6432")):
            if base:
                candidates.append(Path(base) / "Docker" / "Docker" / "Docker Desktop.exe")
    elif sys.platform == "darwin":
        candidates.append(Path("/Applications/Docker.app/Contents/MacOS/Docker Desktop"))
    return next((c for c in candidates if c.is_file()), None)


async def desktop_cli(docker: str = "docker", runner=None) -> bool:
    """Docker Desktop's CLI plugin (``docker desktop start/stop``) is installed."""
    runner = runner or _exec
    try:
        out = await asyncio.to_thread(runner, [docker, "desktop", "version"], 20)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return out.returncode == 0


async def can_start_desktop(docker: str = "docker", runner=None, exe: Path | None = None) -> bool:
    """Docker Desktop can be started: through the CLI plugin, else its executable ``exe``."""
    return await desktop_cli(docker, runner) or exe is not None


def _launch(exe: Path) -> None:
    flags = (getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
             if sys.platform == "win32" else 0)
    subprocess.Popen([str(exe)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=flags, close_fds=True)


async def start_desktop(docker: str = "docker", runner=None, timeout_s: int = DESKTOP_START_S, *,
                        exe: Path | None = None, launcher=None) -> tuple[bool, str]:
    """Start Docker Desktop for one approved run (its VM holds ~1.5 GB this machine needs for the 27B and
    ComfyUI the rest of the time) and wait for the Linux engine. ``docker desktop start`` when the CLI plugin is
    there, else Docker Desktop's executable ``exe``."""
    runner = runner or _exec
    log.info("starting Docker Desktop for a sandbox run")
    try:
        if await desktop_cli(docker, runner):
            await asyncio.to_thread(runner, [docker, "desktop", "start", "--timeout", str(timeout_s)], timeout_s + 30)
        elif exe is not None:
            await asyncio.to_thread(launcher or _launch, exe)
        else:
            return False, "Docker Desktop を起動する手段がありません"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"Docker Desktop を起動できません（{exc}）"
    deadline = time.monotonic() + timeout_s
    while True:
        ok, reason = await docker_status(docker, runner)
        if ok or time.monotonic() > deadline:
            return ok, reason
        await asyncio.sleep(3)


async def stop_desktop(docker: str = "docker", runner=None) -> None:
    """Stop Docker Desktop again after a run it was started for (needs the CLI plugin; without it, it stays up)."""
    runner = runner or _exec
    if not await desktop_cli(docker, runner):
        log.warning("docker desktop CLI plugin missing: Docker Desktop is left running")
        return
    try:
        await asyncio.to_thread(runner, [docker, "desktop", "stop", "--timeout", "120"], 150)
        log.info("stopped Docker Desktop after the sandbox run")
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("docker desktop stop failed: %s", exc)


async def image_present(image: str, docker: str = "docker", runner=None) -> bool:
    runner = runner or _exec
    try:
        out = await asyncio.to_thread(runner, [docker, "image", "inspect", "--format", "{{.Id}}", image], 20)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return out.returncode == 0


async def _kill(docker: str, name: str, runner) -> None:
    for args in ([docker, "kill", name], [docker, "rm", "-f", name]):
        try:
            await asyncio.to_thread(runner, args, 20)
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.warning("%s failed: %s", " ".join(args[1:]), exc)


async def run(*, approved: bool, docker: str, run_dir: Path, root: Path, argv: list[str], profile: Profile,
              user: str, name: str, network: bool = False, step: str = "run", timeout_s: float = TIMEOUT_S,
              runner=None) -> RunResult:
    """One container. Never called before approval: ``approved`` must be True."""
    if approved is not True:
        raise NotApproved("承認されていないコードは実行しません")
    runner = runner or _exec
    # build_argv resolves paths (file system calls): off the event loop, which langgraph dev guards.
    command = await asyncio.to_thread(build_argv, docker=docker, name=name, run_dir=run_dir, root=root, argv=argv,
                                      profile=profile, user=user, network=network, setup=step == "setup")
    log.info("sandbox %s start name=%s image=%s network=%s argv=%s", step, name, profile.image, network,
             command[command.index(profile.image) + 1:])
    started = time.monotonic()
    try:
        out = await asyncio.to_thread(runner, command, min(timeout_s, TIMEOUT_S))
    except subprocess.TimeoutExpired as exc:
        await _kill(docker, name, runner)
        log.info("sandbox %s timed out after %ss: killed %s", step, timeout_s, name)
        return RunResult(command, None, tail(exc.stdout), tail(exc.stderr), True, time.monotonic() - started, step)
    except OSError as exc:
        return RunResult(command, None, "", "", False, time.monotonic() - started, step, f"docker を起動できません: {exc}")
    result = RunResult(command, out.returncode, tail(out.stdout), tail(out.stderr), False,
                       time.monotonic() - started, step)
    if out.returncode == 125:  # docker itself failed (image missing, bad flag): not the program's exit code
        result.error = tail(out.stderr, 600).strip() or "docker run が失敗しました"
    log.info("sandbox %s done name=%s exit=%s seconds=%.1f", step, name, out.returncode, result.seconds)
    return result


def list_outputs(run_dir: Path, before: set[str]) -> list[dict]:
    """Files the program created in /work (dependency and build folders left out)."""
    out = []
    base = Path(run_dir)
    for path in sorted(base.rglob("*")):
        rel = path.relative_to(base).as_posix()
        if not path.is_file() or rel in before or rel.split("/")[0] in (".deps", ".cargo", "target"):
            continue
        out.append({"path": rel, "bytes": path.stat().st_size})
    return out[:50]
