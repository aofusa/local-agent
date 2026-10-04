"""The Docker sandbox: argv, constraints, approval and timeouts without Docker; a real run when Docker is up."""

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from furry_agent import sandbox
from furry_agent.sandbox import PYTHON, RUST, NotApproved, SandboxError


def _argv(tmp_path, argv=("python", "main.py"), **kw):
    root = tmp_path / "code"
    run_dir = root / "run1"
    run_dir.mkdir(parents=True)
    return sandbox.build_argv(docker="docker", name="n", run_dir=run_dir, root=root, argv=list(argv), profile=PYTHON,
                              user="10001:10001", **kw), run_dir


def test_argv_has_every_fixed_constraint(tmp_path):
    argv, run_dir = _argv(tmp_path)
    joined = " ".join(argv)
    assert argv[:3] == ["docker", "run", "--rm"]
    assert argv[argv.index("--network") + 1] == "none"
    assert "--read-only" in argv and argv[argv.index("--memory") + 1] == "2g"
    assert argv[argv.index("--cpus") + 1] == "2" and argv[argv.index("--pids-limit") + 1] == "256"
    assert argv[argv.index("--cap-drop") + 1] == "ALL"
    assert argv[argv.index("--user") + 1] == "10001:10001"
    assert argv[argv.index("--tmpfs") + 1] == "/tmp:rw,size=64m"
    assert argv[argv.index("--workdir") + 1] == "/work"
    assert argv[argv.index("--pull") + 1] == "never"
    assert argv[-3:] == ["python:3.12-slim", "python", "main.py"]
    # The run directory is the only mount; no docker.sock, no env file, no host variables.
    mounts = [argv[i + 1] for i, a in enumerate(argv) if a in ("--mount", "-v", "--volume")]
    assert mounts == [f"type=bind,src={run_dir.resolve()},dst=/work"]
    assert "docker.sock" not in joined and "--env-file" not in joined and ".env" not in joined
    envs = [argv[i + 1] for i, a in enumerate(argv) if a in ("--env", "-e")]
    assert all("=" in e for e in envs) and {e.split("=")[0] for e in envs} <= {k for k, _ in PYTHON.env}


def test_argv_is_a_list_not_a_shell_string(tmp_path):
    with pytest.raises(SandboxError):
        _argv(tmp_path, argv="python main.py")


@pytest.mark.parametrize("argv", [
    ["python", "main.py", ";", "rm", "-rf", "/"], ["python", "main.py", "&&", "ls"], ["python", "a.py;cat"],
    ["sh", "-c", "python main.py"], ["bash", "run.sh"], ["python", "-c", "print(1)"], ["python", "$(id)"],
    ["curl", "http://x"], ["python", "main.py", ">", "out.txt"]])
def test_shell_operators_and_other_programs_are_refused(argv):
    with pytest.raises(SandboxError):
        sandbox.check_argv(argv, PYTHON)


def test_parse_command_splits_without_a_shell():
    assert sandbox.parse_command("python main.py --n 3", PYTHON) == ["python", "main.py", "--n", "3"]
    assert sandbox.parse_command("`python3 'my file.py'`", PYTHON) == ["python3", "my file.py"]
    with pytest.raises(SandboxError):
        sandbox.parse_command("python main.py && echo hi", PYTHON)
    assert sandbox.parse_command("cargo run --offline", RUST) == ["cargo", "run", "--offline"]


def test_mount_outside_the_code_root_is_refused(tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    with pytest.raises(SandboxError):
        sandbox.build_argv(docker="docker", name="n", run_dir=other, root=tmp_path / "code", argv=["python", "a.py"],
                           profile=PYTHON, user="10001:10001")


def test_root_user_and_network_for_the_program_are_refused(tmp_path):
    root = tmp_path / "code"
    (root / "r").mkdir(parents=True)
    with pytest.raises(SandboxError):
        sandbox.build_argv(docker="docker", name="n", run_dir=root / "r", root=root, argv=["python", "a.py"],
                           profile=PYTHON, user="0:0")
    with pytest.raises(SandboxError):
        sandbox.build_argv(docker="docker", name="n", run_dir=root / "r", root=root, argv=["python", "a.py"],
                           profile=PYTHON, user="10001:10001", network=True)
    setup = sandbox.build_argv(docker="docker", name="n", run_dir=root / "r", root=root,
                               argv=["python", "-m", "pip", "install", "--target", ".deps", "rich"], profile=PYTHON,
                               user="10001:10001", network=True, setup=True)
    assert setup[setup.index("--network") + 1] == "bridge"


@pytest.mark.parametrize("path", ["../x.py", "/etc/passwd", "C:/x.py", "a/../../b", ".env", ".ssh/id_rsa", "a b.py", ""])
def test_unsafe_file_paths_are_refused(path):
    with pytest.raises(SandboxError):
        sandbox.safe_relpath(path)


def test_write_files_stays_in_the_run_dir(tmp_path):
    written = sandbox.write_files(tmp_path / "r", [{"path": "src/main.py", "content": "print(1)\n"}])
    assert written == [{"path": "src/main.py", "bytes": 9}]
    assert (tmp_path / "r" / "src" / "main.py").read_text() == "print(1)\n"


def test_profile_is_python_unless_rust_is_named():
    assert sandbox.profile_for("素数を数えるプログラム") is PYTHON
    assert sandbox.profile_for("Rust で書いて") is RUST
    assert RUST.image == "rust:1.88-slim" and PYTHON.image == "python:3.12-slim"


async def test_run_refuses_without_approval_and_calls_nothing(tmp_path):
    calls = []
    root = tmp_path / "code"
    (root / "r").mkdir(parents=True)
    with pytest.raises(NotApproved):
        await sandbox.run(approved=False, docker="docker", run_dir=root / "r", root=root, argv=["python", "a.py"],
                          profile=PYTHON, user="10001:10001", name="n", runner=lambda a, t: calls.append(a))
    assert calls == []


async def test_timeout_kills_the_container_and_tails_output(tmp_path):
    calls = []
    root = tmp_path / "code"
    (root / "r").mkdir(parents=True)

    def runner(argv, timeout_s):
        calls.append(argv)
        if argv[1] == "run":
            assert timeout_s <= sandbox.TIMEOUT_S
            raise subprocess.TimeoutExpired(argv, timeout_s, output=b"x" * 20000, stderr=b"")
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    result = await sandbox.run(approved=True, docker="docker", run_dir=root / "r", root=root, argv=["python", "a.py"],
                               profile=PYTHON, user="10001:10001", name="box", runner=runner)
    assert result.timed_out and not result.ok
    assert ["docker", "kill", "box"] in calls
    assert len(result.stdout_tail.encode()) == sandbox.TAIL_BYTES


async def test_docker_status_without_docker():
    def missing(argv, timeout_s):
        raise FileNotFoundError("docker")

    ok, reason = await sandbox.docker_status("docker", missing)
    assert not ok and "インストール" in reason

    def stopped(argv, timeout_s):
        return subprocess.CompletedProcess(argv, 1, b"", b"error during connect")

    ok, reason = await sandbox.docker_status("docker", stopped)
    assert not ok and "起動していません" in reason


def _docker_ready() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        ok, _ = asyncio.run(sandbox.docker_status())
        return ok and asyncio.run(sandbox.image_present(PYTHON.image))
    except Exception:
        return False


@pytest.mark.skipif(not _docker_ready(), reason="Docker Desktop and python:3.12-slim are needed")
async def test_real_container_runs_read_only_without_network(tmp_path):
    root = tmp_path / "code"
    run_dir = root / "real"
    sandbox.write_files(run_dir, [{"path": "main.py", "content": (
        "import os, socket, pathlib\n"
        "print('uid', os.getuid())\n"
        "pathlib.Path('out.txt').write_text('ok')\n"
        "try:\n    open('/etc/x', 'w')\nexcept OSError:\n    print('root fs read-only')\n"
        "try:\n    socket.create_connection(('1.1.1.1', 53), 3)\nexcept OSError:\n    print('no network')\n")}])
    result = await sandbox.run(approved=True, docker="docker", run_dir=run_dir, root=root, argv=["python", "main.py"],
                               profile=PYTHON, user="10001:10001", name=f"local-agent-sbx-test-{run_dir.name}")
    assert result.ok, result.stderr_tail
    assert "uid 10001" in result.stdout_tail and "root fs read-only" in result.stdout_tail
    assert "no network" in result.stdout_tail
    assert (run_dir / "out.txt").read_text() == "ok"
    assert sandbox.list_outputs(run_dir, {"main.py"}) == [{"path": "out.txt", "bytes": 2}]
