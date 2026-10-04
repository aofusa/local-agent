"""Tests for the PowerShell setup/start scripts (Windows only)."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = sorted((ROOT / "scripts").rglob("*.ps1"))
SHELLS = [s for s in ("powershell", "pwsh") if shutil.which(s)]

pytestmark = pytest.mark.skipif(sys.platform != "win32" or not SHELLS, reason="needs Windows PowerShell")


def _run_ps(shell: str, body: str, tmp_path: Path) -> str:
    script = tmp_path / "t.ps1"
    common = ROOT / "scripts" / "lib" / "common.ps1"
    script.write_text(f'$ErrorActionPreference = "Stop"\n. "{common}"\n{body}\n', encoding="utf-8-sig")
    out = subprocess.run([shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                         capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    assert out.returncode == 0, out.stderr + out.stdout
    return out.stdout.strip()


def test_scripts_have_utf8_bom():
    # Windows PowerShell 5.1 reads BOM-less .ps1 files as the ANSI code page and breaks on Japanese text.
    missing = [str(p.relative_to(ROOT)) for p in SCRIPTS if not p.read_bytes().startswith(b"\xef\xbb\xbf")]
    assert not missing


@pytest.mark.parametrize("shell", SHELLS)
def test_scripts_parse(shell, tmp_path):
    paths = ",".join(f"'{p}'" for p in SCRIPTS)
    body = (f"foreach ($p in @({paths})) {{ $errors = $null; "
            "[System.Management.Automation.Language.Parser]::ParseFile($p, [ref]$null, [ref]$errors) | Out-Null; "
            "if ($errors) { throw \"$p : $($errors[0].Message)\" } }; 'parsed'")
    assert _run_ps(shell, body, tmp_path).endswith("parsed")


@pytest.mark.parametrize("shell", SHELLS)
def test_dotenv_roundtrip(shell, tmp_path):
    env = tmp_path / ".env"
    env.write_text("# comment\nA=1\nB=two\n", encoding="utf-8")
    body = (f"Set-DotEnvValue 'B' 'three' '{env}'; Set-DotEnvValue 'C' 'C:\\x y' '{env}'; "
            f"(Get-DotEnvValue 'C' '' '{env}') + '|' + (Get-DotEnvValue 'Z' 'dflt' '{env}')")
    assert _run_ps(shell, body, tmp_path) == "C:\\x y|dflt"
    lines = env.read_text(encoding="utf-8").splitlines()
    assert lines == ["# comment", "A=1", "B=three", "C=C:\\x y"]


@pytest.mark.parametrize("shell", SHELLS)
def test_write_json_keeps_single_item_arrays_and_no_bom(shell, tmp_path):
    out = tmp_path / "x.json"
    body = f"Write-JsonFile '{out}' @([pscustomobject]@{{ id = 'a'; fields = @([pscustomobject]@{{ k = 1 }}) }})"
    _run_ps(shell, body, tmp_path)
    raw = out.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    data = json.loads(raw)
    assert isinstance(data, list) and isinstance(data[0]["fields"], list)


@pytest.mark.parametrize("shell", SHELLS)
def test_comfy_desktop_layout_and_server_args(shell, tmp_path):
    install = tmp_path / "Installs" / "ComfyUI"
    (install / "ComfyUI").mkdir(parents=True)
    (install / "ComfyUI" / "main.py").write_text("", encoding="utf-8")
    base = tmp_path / "Base"
    python = base / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    desktop = tmp_path / "Comfy Desktop"
    (desktop / "instance-model-paths").mkdir(parents=True)
    (desktop / "instance-model-paths" / "inst-1.yaml").write_text("x: 1\n", encoding="utf-8")
    (desktop / "installations.json").write_text(json.dumps([
        {"id": "inst-0", "sourceId": "cloud", "remoteUrl": "https://example.invalid/"},
        {"id": "inst-1", "sourceId": "standalone", "installPath": str(install), "adoptedBaseDir": str(base),
         "adoptedPythonPath": str(python), "inputDir": str(base / "input"), "outputDir": str(base / "output")},
    ]), encoding="utf-8")
    body = (f"$l = Get-ComfyDesktopLayout '{desktop}'; "
            "[pscustomobject]@{ layout = $l; args = @(Get-ComfyServerArgs $l 8188) } | ConvertTo-Json -Depth 5 -Compress")
    result = json.loads(_run_ps(shell, body, tmp_path))
    layout, args = result["layout"], result["args"]
    assert layout["MainDir"] == str(install / "ComfyUI")
    assert layout["Python"] == str(python)
    assert layout["CustomNodesDir"] == str(base / "custom_nodes")
    assert layout["InstallationId"] == "inst-1"
    assert layout["ExtraModelPaths"].endswith("inst-1.yaml")
    assert args[:6] == ["-s", "main.py", "--listen", "127.0.0.1", "--port", "8188"]
    assert "--base-directory" in args and str(base) in args
    assert "--output-directory" in args
    assert "--cache-none" in args  # IP-Adapter needs it on ComfyUI 0.38


@pytest.mark.parametrize("shell", SHELLS)
def test_json_arrays_enumerate_in_pipeline(shell, tmp_path):
    # Windows PowerShell 5.1 emits a parsed JSON array as one object; the helper must enumerate it.
    body = ("$m = ConvertTo-ObjectArray ('[{\"path\":\"a\",\"n\":1},{\"path\":\"b\",\"n\":2}]' | ConvertFrom-Json); "
            "$picked = @($m | Where-Object { $_.n -ge 1 } | Sort-Object n -Descending); "
            "\"$($picked.Count)|$($picked[0].path)\"")
    assert _run_ps(shell, body, tmp_path) == "2|b"


@pytest.mark.parametrize("shell", SHELLS)
def test_file_sha256_matches_hashlib(shell, tmp_path):
    import hashlib

    data = tmp_path / "blob.bin"
    data.write_bytes(bytes(range(256)) * 1000)
    assert _run_ps(shell, f"Get-FileSha256 '{data}'", tmp_path) == hashlib.sha256(data.read_bytes()).hexdigest()


def test_search_catalog_pins_sha256():
    catalog = json.loads((ROOT / "config" / "search_models.json").read_text(encoding="utf-8"))
    assert len(catalog["llama_sha256"]) == 64
    assert all(len(m["sha256"]) == 64 and m["size"] > 0 for m in catalog["models"])
