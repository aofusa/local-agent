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
def test_comfy_layout_from_env_and_server_args(shell, tmp_path):
    env = tmp_path / ".env"
    main = tmp_path / "tools" / "comfyui"
    env.write_text(f"COMFYUI_MAIN_DIR={main}\nCOMFYUI_PYTHON={main}\\.venv\\Scripts\\python.exe\n"
                   f"COMFYUI_EXTRA_MODEL_PATHS={main}\\extra_model_paths.yaml\n", encoding="utf-8")
    body = (f"function Get-DotEnvPath {{ '{env}' }}; $l = Get-ComfyLayout; "
            "[pscustomobject]@{ layout = $l; args = @(Get-ComfyServerArgs $l 8188) } | ConvertTo-Json -Depth 5 -Compress")
    result = json.loads(_run_ps(shell, body, tmp_path))
    layout, args = result["layout"], result["args"]
    assert layout["MainDir"] == str(main) and layout["Source"] == ".env"
    assert layout["CustomNodesDir"] == str(main / "custom_nodes")
    assert layout["ModelsDir"] == str(main / "models")
    assert args[:6] == ["-s", "main.py", "--listen", "127.0.0.1", "--port", "8188"]
    assert "--cache-none" in args  # IP-Adapter needs it on ComfyUI 0.38
    assert "--base-directory" not in args
    assert args[args.index("--extra-model-paths-config") + 1] == str(main / "extra_model_paths.yaml")


@pytest.mark.parametrize("shell", SHELLS)
def test_extra_model_paths_yaml_and_model_lookup(shell, tmp_path):
    own, shared = tmp_path / "own", tmp_path / "shared models"
    (shared / "checkpoints").mkdir(parents=True)
    (shared / "checkpoints" / "model.safetensors").write_bytes(b"x")
    (own / "loras").mkdir(parents=True)
    (own / "loras" / "a.safetensors").write_bytes(b"y")
    yaml = tmp_path / "extra.yaml"
    body = (f"Write-TextFile '{yaml}' (ConvertTo-ComfyExtraModelPathsYaml @('{shared}')); "
            f"$l = New-ComfyLayout '{tmp_path}' 'py' 'test'; $l.ModelsDir = '{own}'; $l.ExtraModelPaths = '{yaml}'; "
            "@((Find-ComfyModel $l 'checkpoints' 'model.safetensors'), (Find-ComfyModel $l 'loras' 'a.safetensors'), "
            "[string](Find-ComfyModel $l 'vae' 'none.safetensors')) -join '|'")
    found = _run_ps(shell, body, tmp_path).split("|")
    assert found == [str(shared / "checkpoints" / "model.safetensors"), str(own / "loras" / "a.safetensors"), ""]
    text = yaml.read_text(encoding="utf-8")
    assert f"base_path: '{shared}'" in text and "  checkpoints: checkpoints/" in text and "ipadapter: ipadapter/" in text


@pytest.mark.parametrize("shell", SHELLS)
def test_llm_preset_ini_and_router_args(shell, tmp_path):
    body = ("$ini = ConvertTo-LlmPresetIni 'qwen' ([ordered]@{ 'model' = 'C:/m/q.gguf'; 'ctx-size' = 4096; 'jinja' = $true; "
            "'load-mode' = 'mmap' }); "
            "[pscustomobject]@{ ini = $ini; args = @(Get-LlmServerArgs 'C:\\p\\models.ini' 8080) } | ConvertTo-Json -Compress")
    result = json.loads(_run_ps(shell, body, tmp_path))
    assert result["ini"].splitlines() == ["version = 1", "", "[qwen]", "model = C:/m/q.gguf", "ctx-size = 4096",
                                          "jinja = true", "load-mode = mmap"]
    args = result["args"]
    assert args[args.index("--host") + 1] == "127.0.0.1"  # loopback only (AGENTS.md)
    assert args[args.index("--models-max") + 1] == "1" and args[args.index("--port") + 1] == "8080"
    assert args[args.index("--models-preset") + 1] == "C:\\p\\models.ini"


@pytest.mark.parametrize("shell", SHELLS)
def test_known_model_dirs_and_import(shell, tmp_path):
    """Models of an earlier ComfyUI (Comfy Desktop, Documents\\ComfyUI) are hard-linked into tools\\comfyui\\models."""
    appdata, local, profile = tmp_path / "Roaming", tmp_path / "Local", tmp_path / "User"
    shared = local / "Comfy-Desktop" / "ComfyUI-Shared" / "models"
    docs = profile / "Documents" / "ComfyUI" / "models"
    extra = tmp_path / "D" / "models"
    (appdata / "Comfy Desktop" / "instance-model-paths").mkdir(parents=True)
    (appdata / "Comfy Desktop" / "instance-model-paths" / "inst-1.yaml").write_text(
        f"comfy.desktop_0:\n  base_path: '{extra}'\n  checkpoints: checkpoints/\n", encoding="utf-8")
    for d in (shared, docs, extra):
        d.mkdir(parents=True)
    (docs / "checkpoints").mkdir()
    (docs / "checkpoints" / "model.safetensors").write_bytes(b"ckpt")
    (extra / "loras").mkdir()
    (extra / "loras" / "Style A.safetensors").write_bytes(b"lora")
    own = tmp_path / "comfy" / "models"
    body = (f"$env:APPDATA = '{appdata}'; $env:LOCALAPPDATA = '{local}'; $env:USERPROFILE = '{profile}'; "
            "$dirs = @(Get-KnownModelDirs); "
            f"$l = New-ComfyLayout '{tmp_path / 'comfy'}' 'py' 'test'; "
            "$ckpt = Import-ComfyModel $l @('checkpoints') 'model.safetensors' $dirs; "
            "$lora = Resolve-LoraName 'style a' $dirs; "
            "$lp = Import-ComfyModel $l @('loras') $lora $dirs; "
            "$none = [string](Import-ComfyModel $l @('vae') 'ae.safetensors' $dirs); "
            "[pscustomobject]@{ dirs = $dirs; ckpt = $ckpt; lora = $lora; lp = $lp; none = $none } | ConvertTo-Json -Compress")
    result = json.loads(_run_ps(shell, body, tmp_path))
    assert set(result["dirs"]) == {str(extra), str(shared), str(docs)}
    assert result["ckpt"] == str(own / "checkpoints" / "model.safetensors")
    assert (own / "checkpoints" / "model.safetensors").read_bytes() == b"ckpt"
    assert result["lora"] == "Style A.safetensors"  # name without extension, any case
    assert (own / "loras" / "Style A.safetensors").read_bytes() == b"lora"
    assert result["none"] == ""
    # Hard links: the import takes no extra disk space on the same drive.
    assert (own / "checkpoints" / "model.safetensors").stat().st_nlink == 2


@pytest.mark.parametrize("shell", SHELLS)
def test_lm_studio_models_dir(shell, tmp_path):
    profile = tmp_path / "User"
    (profile / ".lmstudio" / "models").mkdir(parents=True)
    other = tmp_path / "elsewhere"
    other.mkdir()
    body = f"$env:USERPROFILE = '{profile}'; Get-LmStudioModelsDir"
    assert _run_ps(shell, body, tmp_path) == str(profile / ".lmstudio" / "models")
    (profile / ".lmstudio" / "settings.json").write_text(json.dumps({"downloadsFolder": str(other)}), encoding="utf-8")
    assert _run_ps(shell, body, tmp_path) == str(other)


@pytest.mark.parametrize("shell", SHELLS)
def test_hf_cache_dir_follows_the_hf_variables(shell, tmp_path):
    body = (f"$env:USERPROFILE = '{tmp_path}'; $env:HF_HOME = ''; $env:HF_HUB_CACHE = ''; $a = Get-HfCacheDir; "
            f"$env:HF_HOME = '{tmp_path / 'hfhome'}'; $b = Get-HfCacheDir; "
            f"$env:HF_HUB_CACHE = '{tmp_path / 'hub2'}'; $c = Get-HfCacheDir; @($a, $b, $c) -join '|'")
    assert _run_ps(shell, body, tmp_path).split("|") == [
        str(tmp_path / ".cache" / "huggingface" / "hub"), str(tmp_path / "hfhome" / "hub"), str(tmp_path / "hub2")]


@pytest.mark.parametrize("shell", SHELLS)
def test_hf_cached_files_are_linked_not_downloaded(shell, tmp_path):
    """A file `hf download` put in the cache (snapshot, or a blob named by its SHA-256 in another repository) is
    hard-linked into place; nothing is fetched."""
    hub = tmp_path / "hub"
    snap = hub / "models--org--repo" / "snapshots" / "abc123" / "sub"
    snap.mkdir(parents=True)
    (snap / "model.gguf").write_bytes(b"weights")
    blobs = hub / "models--mirror--other" / "blobs"
    blobs.mkdir(parents=True)
    sha = "ab" * 32
    (blobs / sha).write_bytes(b"mirrored")
    d1, d2 = tmp_path / "out" / "a.gguf", tmp_path / "out" / "b.gguf"
    body = (f"$env:HF_HUB_CACHE = '{hub}'; "
            "function Get-HfCli { $null }; function Save-Download { throw 'downloaded' }; "  # no network allowed
            f"Get-HfFile 'org/repo' 'sub/model.gguf' '{d1}' '' 7 | Out-Null; "
            f"Get-HfFile 'org/repo' 'missing.gguf' '{d2}' '{sha}' 8 | Out-Null; "
            f"'missing=' + [string](Find-HfCachedFile 'org/repo' 'sub/model.gguf' '' 999)")
    assert _run_ps(shell, body, tmp_path).splitlines()[-1] == "missing="  # a size that does not match is not taken
    assert d1.read_bytes() == b"weights" and d1.stat().st_nlink == 2
    assert d2.read_bytes() == b"mirrored" and d2.stat().st_nlink == 2


@pytest.mark.parametrize("shell", SHELLS)
def test_torch_variant_follows_the_gpu(shell, tmp_path):
    body = ("@((Get-TorchVariant @('AMD Radeon(TM) 890M Graphics', 'Parsec Virtual Display Adapter')), "
            "(Get-TorchVariant @('NVIDIA GeForce RTX 4090')), (Get-TorchVariant @('Microsoft Basic Display Adapter')), "
            "(Get-TorchVariant @())) -join '|'")
    assert _run_ps(shell, body, tmp_path) == "rocm|cuda|cpu|cpu"


@pytest.mark.parametrize("shell", SHELLS)
def test_llama_server_from_env(shell, tmp_path):
    exe = tmp_path / "llama-server.exe"
    exe.write_bytes(b"")
    env = tmp_path / ".env"
    env.write_text(f"LLM_SERVER=\nBONSAI_LLAMA_SERVER={exe}\n", encoding="utf-8")
    body = f"function Get-DotEnvPath {{ '{env}' }}; Get-LlamaServer"
    assert _run_ps(shell, body, tmp_path) == str(exe)  # falls back to the search workers' build


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


def test_llm_catalog_pins_sha256():
    spec = json.loads((ROOT / "config" / "llm_model.json").read_text(encoding="utf-8"))
    assert len(spec["sha256"]) == 64 and spec["size"] > 0
    assert len(spec["mmproj"]["sha256"]) == 64 and spec["mmproj"]["size"] > 0
    workflow = json.loads((ROOT / "workflows" / "furry_ja_api.json").read_text(encoding="utf-8"))
    backend = workflow["llm_backend"]["inputs"]
    assert backend["model"] == spec["name"]  # the templates name the preset's model
    assert backend["base_url"] == f"http://127.0.0.1:{spec['port']}/v1"


def test_no_lm_studio_left_in_scripts():
    lm = [str(p.relative_to(ROOT)) for p in SCRIPTS if "lms.exe" in p.read_text(encoding="utf-8-sig")
          or "setup-lmstudio" in p.read_text(encoding="utf-8-sig")]
    assert not lm and not (ROOT / "scripts" / "setup-lmstudio.ps1").exists()


def test_llm_catalog_pins_the_requantized_file():
    spec = json.loads((ROOT / "config" / "llm_model.json").read_text(encoding="utf-8"))
    pinned = spec["quantized"][spec["quant"]]
    assert len(pinned["sha256"]) == 64 and pinned["size"] > 0
    assert pinned["file"].endswith(f"-{spec['quant']}.gguf")
