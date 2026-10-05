# Shared helpers for the setup / start scripts. Works on Windows PowerShell 5.1 and PowerShell 7.
# Dot-source it:  . (Join-Path $PSScriptRoot "lib\common.ps1")

$script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

function Get-RepoRoot { $script:RepoRoot }

function Write-Step([string]$Message) { Write-Host "==> $Message" -ForegroundColor Cyan }
function Write-Ok([string]$Message) { Write-Host "    OK  $Message" -ForegroundColor Green }
function Write-Warn2([string]$Message) { Write-Host "    !!  $Message" -ForegroundColor Yellow }

# --- .env ------------------------------------------------------------------

function Get-DotEnvPath { Join-Path (Get-RepoRoot) ".env" }

function Initialize-DotEnv {
    $path = Get-DotEnvPath
    if (-not (Test-Path $path)) {
        Copy-Item (Join-Path (Get-RepoRoot) ".env.example") $path
    }
    $path
}

function Read-DotEnv([string]$Path = (Get-DotEnvPath)) {
    $values = @{}
    if (-not (Test-Path $Path)) { return $values }
    foreach ($line in [IO.File]::ReadAllLines($Path)) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$') {
            $values[$Matches[1]] = $Matches[2].Trim().Trim('"')
        }
    }
    $values
}

function Get-DotEnvValue([string]$Name, [string]$Default = "", [string]$Path = (Get-DotEnvPath)) {
    $values = Read-DotEnv $Path
    if ($values.ContainsKey($Name) -and $values[$Name]) { return $values[$Name] }
    $Default
}

function Set-DotEnvValue([string]$Name, [string]$Value, [string]$Path = (Get-DotEnvPath)) {
    $lines = New-Object System.Collections.Generic.List[string]
    if (Test-Path $Path) { $lines.AddRange([string[]][IO.File]::ReadAllLines($Path)) }
    $found = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match "^\s*$([regex]::Escape($Name))\s*=") {
            $lines[$i] = "$Name=$Value"; $found = $true
        }
    }
    if (-not $found) { $lines.Add("$Name=$Value") }
    Write-TextFile $Path (($lines -join "`r`n") + "`r`n")
}

# --- files -------------------------------------------------------------------

function Write-TextFile([string]$Path, [string]$Text) {
    # UTF-8 without BOM on both PowerShell editions (llama-server's preset and ComfyUI's YAML are read as UTF-8).
    $dir = Split-Path -Parent $Path
    if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force $dir | Out-Null }
    [IO.File]::WriteAllText($Path, $Text, (New-Object System.Text.UTF8Encoding($false)))
}

function Read-JsonFile([string]$Path) {
    [IO.File]::ReadAllText($Path) | ConvertFrom-Json
}

function ConvertTo-ObjectArray($Value) {
    # Windows PowerShell 5.1's ConvertFrom-Json emits a JSON array as ONE pipeline object, so
    # @(...) / Where-Object would see a single item. foreach enumerates it on both editions;
    # the items are written to the pipeline one by one (collect them with @(...)).
    foreach ($item in $Value) { $item }
}

function Read-JsonArray([string]$Path) { ConvertTo-ObjectArray (Read-JsonFile $Path) }

function Write-JsonFile([string]$Path, $Object) {
    # -InputObject keeps single-element arrays as arrays on Windows PowerShell 5.1.
    Write-TextFile $Path (ConvertTo-Json -InputObject $Object -Depth 50)
}

function Backup-File([string]$Path) {
    if (Test-Path $Path) {
        $backup = "$Path.local-agent.bak"
        if (-not (Test-Path $backup)) { Copy-Item $Path $backup }
    }
}

function Set-JsonProperty($Object, [string]$Name, $Value) {
    if ($Object.PSObject.Properties.Name -contains $Name) { $Object.$Name = $Value }
    else { $Object | Add-Member -NotePropertyName $Name -NotePropertyValue $Value }
}

# --- network -----------------------------------------------------------------

function Get-LanIPv4 {
    $route = Get-NetRoute -DestinationPrefix "0.0.0.0/0" -ErrorAction SilentlyContinue |
        Sort-Object RouteMetric | Select-Object -First 1
    if (-not $route) { return $null }
    (Get-NetIPAddress -AddressFamily IPv4 -InterfaceIndex $route.InterfaceIndex |
        Where-Object { $_.IPAddress -notlike "169.254.*" } | Select-Object -First 1).IPAddress
}

function Test-Listening([int]$Port) {
    [bool](Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
}

function Get-ListenAddresses([int]$Port) {
    @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty LocalAddress -Unique)
}

# --- llama.cpp router (the 27B for ComfyUI, the chat tab and /coder/turn) ---------------------------------------
# scripts\setup-llm.ps1 writes the preset (tools\llm\models.ini) and LLM_* to .env; scripts\start-llm.ps1 runs
# llama-server in router mode, which loads the model on the first request and unloads it on POST /models/unload.

function Get-LlamaServer {
    # One llama.cpp build (scripts\setup-llamacpp.ps1) serves the router and the search workers.
    foreach ($name in @("LLM_SERVER", "BONSAI_LLAMA_SERVER")) {
        $exe = Get-DotEnvValue $name
        if ($exe -and (Test-Path $exe)) { return $exe }
    }
    $null
}

function ConvertTo-LlmPresetIni([string]$Name, [System.Collections.IDictionary]$Settings) {
    # llama-server --models-preset: one [section] per model, keys are the long option names without "--".
    $lines = @("version = 1", "", "[$Name]")
    foreach ($key in $Settings.Keys) {
        $value = $Settings[$key]
        if ($value -is [bool]) { $value = if ($value) { "true" } else { "false" } }
        $lines += "$key = $value"
    }
    ($lines -join "`n") + "`n"
}

function Get-LlmServerArgs([string]$Preset, [int]$Port = 8080) {
    # Loopback only (AGENTS.md); one model at a time: the 27B never shares memory with a second LLM here.
    @("--models-preset", $Preset, "--models-max", "1", "--host", "127.0.0.1", "--port", "$Port")
}

function Invoke-Native {
    # Usage: Invoke-Native <exe> <args...>. Returns stdout+stderr as strings and sets $LASTEXITCODE.
    # A plain function ($args) so tokens like -m / --quiet pass through untouched. Windows PowerShell 5.1
    # turns redirected stderr into terminating errors under ErrorActionPreference=Stop, so relax it here.
    $FilePath = $args[0]
    $Arguments = @($args | Select-Object -Skip 1)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $out = & $FilePath @Arguments 2>&1 | ForEach-Object { "$_" }
        $global:LASTEXITCODE = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
    $out
}

# --- ComfyUI layout ------------------------------------------------------------
# scripts\setup-comfyui.ps1 installs ComfyUI into tools\comfyui with its own venv and writes the paths to .env.

function Get-RepoComfyDir { Join-Path (Get-RepoRoot) "tools\comfyui" }

function New-ComfyLayout([string]$MainDir, [string]$Python, [string]$Source) {
    [pscustomobject]@{
        Source          = $Source
        MainDir         = $MainDir
        Python          = $Python
        CustomNodesDir  = Join-Path $MainDir "custom_nodes"
        ModelsDir       = Join-Path $MainDir "models"
        ExtraModelPaths = ""
        InputDir        = ""
        OutputDir       = ""
    }
}

function Get-ComfyLayout {
    # .env (written by setup-comfyui.ps1) > tools\comfyui when it is installed.
    $envValues = Read-DotEnv
    if ($envValues["COMFYUI_MAIN_DIR"]) {
        $layout = New-ComfyLayout $envValues["COMFYUI_MAIN_DIR"] $envValues["COMFYUI_PYTHON"] ".env"
        foreach ($pair in @(@("CustomNodesDir", "COMFYUI_CUSTOM_NODES_DIR"), @("ModelsDir", "COMFYUI_MODELS_DIR"),
                            @("ExtraModelPaths", "COMFYUI_EXTRA_MODEL_PATHS"), @("InputDir", "COMFYUI_INPUT_DIR"),
                            @("OutputDir", "COMFYUI_OUTPUT_DIR"))) {
            if ($envValues[$pair[1]]) { $layout.($pair[0]) = $envValues[$pair[1]] }
        }
        return $layout
    }
    $dir = Get-RepoComfyDir
    if (Test-Path (Join-Path $dir "main.py")) {
        return (New-ComfyLayout $dir (Join-Path $dir ".venv\Scripts\python.exe") "tools")
    }
    $null
}

function Save-ComfyLayout($Layout) {
    Set-DotEnvValue "COMFYUI_MAIN_DIR" $Layout.MainDir
    Set-DotEnvValue "COMFYUI_PYTHON" $Layout.Python
    Set-DotEnvValue "COMFYUI_CUSTOM_NODES_DIR" $Layout.CustomNodesDir
    Set-DotEnvValue "COMFYUI_MODELS_DIR" $Layout.ModelsDir
    Set-DotEnvValue "COMFYUI_EXTRA_MODEL_PATHS" $Layout.ExtraModelPaths
    Set-DotEnvValue "COMFYUI_INPUT_DIR" $Layout.InputDir
    Set-DotEnvValue "COMFYUI_OUTPUT_DIR" $Layout.OutputDir
}

function Get-ComfyServerArgs($Layout, [int]$Port = 8188) {
    # ComfyUI stays on loopback (AGENTS.md); only LangGraph and the UI face the LAN.
    # --cache-none: with ComfyUI 0.38 a cached IP-Adapter loader output produced corrupt images on every
    # run after the first one; re-executing each node per run fixed it.
    $arguments = @("-s", "main.py", "--listen", "127.0.0.1", "--port", "$Port", "--cache-none")
    if ($Layout.ExtraModelPaths) { $arguments += @("--extra-model-paths-config", $Layout.ExtraModelPaths) }
    if ($Layout.InputDir) { $arguments += @("--input-directory", $Layout.InputDir) }
    if ($Layout.OutputDir) { $arguments += @("--output-directory", $Layout.OutputDir) }
    $arguments
}

$script:ComfyModelFolders = @("checkpoints", "clip_vision", "controlnet", "diffusion_models", "embeddings", "ipadapter",
                              "loras", "text_encoders", "clip", "unet", "upscale_models", "vae")

function ConvertTo-ComfyExtraModelPathsYaml([string[]]$Dirs) {
    # extra_model_paths.yaml for folders that already hold models (e.g. an older ComfyUI's models folder),
    # so they are used in place instead of being downloaded or copied again.
    $lines = @("# Written by scripts\setup-comfyui.ps1 (-ModelsDir). Model folders ComfyUI reads besides its own.")
    $i = 0
    foreach ($dir in $Dirs) {
        $lines += "local_agent_${i}:"
        $lines += "  base_path: '$($dir.Replace("'", "''"))'"
        foreach ($folder in $script:ComfyModelFolders) { $lines += "  ${folder}: ${folder}/" }
        $i++
    }
    ($lines -join "`n") + "`n"
}

function Get-ComfyModelDirs($Layout) {
    # Every models folder ComfyUI reads: its own, then each base_path of the extra model paths.
    $dirs = @($Layout.ModelsDir)
    if ($Layout.ExtraModelPaths -and (Test-Path $Layout.ExtraModelPaths)) {
        foreach ($line in Get-Content $Layout.ExtraModelPaths) {
            if ($line -match "base_path:\s*'?([^']+)'?\s*$") { $dirs += $Matches[1].Trim() }
        }
    }
    $dirs
}

function Find-ComfyModel($Layout, [string]$Folder, [string]$Name) {
    # The first models\<Folder>\<Name> in any folder ComfyUI reads, or $null.
    foreach ($dir in Get-ComfyModelDirs $Layout) {
        $path = Join-Path (Join-Path $dir $Folder) $Name
        if ((Test-Path $path) -and (Get-Item $path).Length -gt 0) { return $path }
    }
    $null
}

function Get-TorchVariant([string[]]$GpuNames) {
    # PyTorch build for ComfyUI: CUDA for NVIDIA, AMD's ROCm (Windows) wheels for Radeon, else CPU.
    if ($GpuNames | Where-Object { $_ -match "NVIDIA" }) { return "cuda" }
    if ($GpuNames | Where-Object { $_ -match "AMD|Radeon" }) { return "rocm" }
    "cpu"
}

# --- downloads ---------------------------------------------------------------------

function Get-FileSha256([string]$Path) {
    # .NET instead of Get-FileHash: the cmdlet is missing when the module path comes from another PowerShell.
    $stream = [IO.File]::OpenRead($Path)
    try {
        $hasher = [Security.Cryptography.SHA256]::Create()
        -join ($hasher.ComputeHash($stream) | ForEach-Object { $_.ToString("x2") })
    } finally { $stream.Dispose() }
}

function Save-Download([string]$Url, [string]$Path) {
    # curl with resume into <Path>.partial, renamed when complete. An existing non-empty file is kept.
    if ((Test-Path $Path) -and (Get-Item $Path).Length -gt 0) { Write-Ok "exists: $Path"; return }
    New-Item -ItemType Directory -Force (Split-Path $Path) | Out-Null
    $partial = "$Path.partial"
    Write-Host "    downloading $Url"
    Invoke-Native curl.exe -L --fail --retry 3 -C - -o $partial $Url | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "ダウンロードに失敗しました: $Url" }
    Move-Item -Force $partial $Path
    Write-Ok "saved: $Path"
}

function New-FileLink([string]$Path, [string]$Target) {
    # A hard link (no extra disk space) to a file on the same volume, else a copy.
    if (Test-Path $Path) { return }
    New-Item -ItemType Directory -Force (Split-Path $Path) | Out-Null
    try { New-Item -ItemType HardLink -Path $Path -Target $Target -ErrorAction Stop | Out-Null }
    catch { Copy-Item $Target $Path }
}
