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
    # UTF-8 without BOM on both PowerShell editions (LM Studio's JSON parser rejects a BOM).
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

# --- LM Studio ---------------------------------------------------------------

function Get-LmStudioHome { Join-Path $env:USERPROFILE ".lmstudio" }

function Get-LmsPath {
    $candidates = @(
        (Join-Path (Get-LmStudioHome) "bin\lms.exe"),
        (Get-Command lms -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -First 1)
    ) | Where-Object { $_ -and (Test-Path $_) }
    $candidates | Select-Object -First 1
}

function Invoke-Lms {
    $Arguments = @($args)
    $lms = Get-LmsPath
    if (-not $lms) { throw "lms CLI が見つかりません。LM Studio を一度起動してから再実行してください。" }
    Invoke-Native $lms @Arguments | ForEach-Object { $_ -replace "\x1b\[[0-9;?]*[A-Za-z]", "" }
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

function Get-LmStudioModelsDir {
    $settings = Join-Path (Get-LmStudioHome) "settings.json"
    if (Test-Path $settings) {
        $folder = (Read-JsonFile $settings).downloadsFolder
        if ($folder) { return $folder }
    }
    Join-Path (Get-LmStudioHome) "models"
}

# --- ComfyUI layout ------------------------------------------------------------

function New-ComfyLayout([string]$MainDir, [string]$Python, [string]$BaseDir, [string]$Source) {
    $customNodes = if ($BaseDir) { Join-Path $BaseDir "custom_nodes" } else { Join-Path $MainDir "custom_nodes" }
    [pscustomobject]@{
        Source          = $Source
        MainDir         = $MainDir
        Python          = $Python
        BaseDir         = $BaseDir
        CustomNodesDir  = $customNodes
        ExtraModelPaths = ""
        InputDir        = ""
        OutputDir       = ""
        InstallationId  = ""
    }
}

function Find-ComfyPython([string]$MainDir) {
    $candidates = @(
        (Join-Path $MainDir "..\python_embeded\python.exe"),
        (Join-Path $MainDir ".venv\Scripts\python.exe"),
        (Join-Path $MainDir "venv\Scripts\python.exe"),
        (Join-Path $MainDir "..\.venv\Scripts\python.exe")
    )
    foreach ($c in $candidates) { if (Test-Path $c) { return (Resolve-Path $c).Path } }
    $null
}

function Get-ComfyDesktopLayout([string]$DesktopDataDir = (Join-Path $env:APPDATA "Comfy Desktop")) {
    # Comfy Desktop (v1.x) keeps its local installations in installations.json.
    $file = Join-Path $DesktopDataDir "installations.json"
    if (-not (Test-Path $file)) { return $null }
    $inst = (Read-JsonArray $file) | Where-Object { $_.installPath -and $_.sourceId -ne "cloud" } | Select-Object -First 1
    if (-not $inst) { return $null }
    $mainDir = $inst.installPath
    if (Test-Path (Join-Path $mainDir "ComfyUI\main.py")) { $mainDir = Join-Path $mainDir "ComfyUI" }
    $baseDir = if ($inst.adoptedBaseDir) { $inst.adoptedBaseDir } else { $null }
    $python = if ($inst.adoptedPythonPath) { $inst.adoptedPythonPath } else { $null }
    if (-not $python -and $baseDir) { $python = Find-ComfyPython $baseDir }
    if (-not $python) { $python = Find-ComfyPython $inst.installPath }
    if (-not $python) { $python = Find-ComfyPython $mainDir }
    $layout = New-ComfyLayout $mainDir $python $baseDir "comfy-desktop"
    $layout.InstallationId = $inst.id
    $yaml = Join-Path $DesktopDataDir "instance-model-paths\$($inst.id).yaml"
    if (Test-Path $yaml) { $layout.ExtraModelPaths = $yaml }
    if ($inst.inputDir) { $layout.InputDir = $inst.inputDir }
    if ($inst.outputDir) { $layout.OutputDir = $inst.outputDir }
    $layout
}

function Get-ComfyLayout([string]$MainDir = "", [string]$Python = "") {
    # Priority: explicit parameter > .env > Comfy Desktop installation.
    $envValues = Read-DotEnv
    if (-not $MainDir -and $envValues["COMFYUI_MAIN_DIR"]) {
        $layout = New-ComfyLayout $envValues["COMFYUI_MAIN_DIR"] $envValues["COMFYUI_PYTHON"] $envValues["COMFYUI_BASE_DIR"] ".env"
        foreach ($pair in @(@("CustomNodesDir", "COMFYUI_CUSTOM_NODES_DIR"), @("ExtraModelPaths", "COMFYUI_EXTRA_MODEL_PATHS"),
                            @("InputDir", "COMFYUI_INPUT_DIR"), @("OutputDir", "COMFYUI_OUTPUT_DIR"))) {
            if ($envValues[$pair[1]]) { $layout.($pair[0]) = $envValues[$pair[1]] }
        }
        return $layout
    }
    if ($MainDir) {
        if (Test-Path (Join-Path $MainDir "ComfyUI\main.py")) { $MainDir = Join-Path $MainDir "ComfyUI" }
        if (-not (Test-Path (Join-Path $MainDir "main.py"))) { throw "main.py が見つかりません: $MainDir" }
        $MainDir = (Resolve-Path $MainDir).Path
        if (-not $Python) { $Python = Find-ComfyPython $MainDir }
        return (New-ComfyLayout $MainDir $Python $null "parameter")
    }
    Get-ComfyDesktopLayout
}

function Save-ComfyLayout($Layout) {
    Set-DotEnvValue "COMFYUI_MAIN_DIR" $Layout.MainDir
    Set-DotEnvValue "COMFYUI_PYTHON" $Layout.Python
    Set-DotEnvValue "COMFYUI_BASE_DIR" $Layout.BaseDir
    Set-DotEnvValue "COMFYUI_CUSTOM_NODES_DIR" $Layout.CustomNodesDir
    Set-DotEnvValue "COMFYUI_EXTRA_MODEL_PATHS" $Layout.ExtraModelPaths
    Set-DotEnvValue "COMFYUI_INPUT_DIR" $Layout.InputDir
    Set-DotEnvValue "COMFYUI_OUTPUT_DIR" $Layout.OutputDir
}

function Get-ComfyServerArgs($Layout, [int]$Port = 8188) {
    # ComfyUI stays on loopback (AGENTS.md); only LangGraph and the UI face the LAN.
    # --cache-none: with ComfyUI 0.38 a cached IP-Adapter loader output produced corrupt images on every
    # run after the first one; re-executing each node per run fixed it.
    $arguments = @("-s", "main.py", "--listen", "127.0.0.1", "--port", "$Port", "--cache-none")
    if ($Layout.BaseDir) {
        $arguments += @("--base-directory", $Layout.BaseDir, "--user-directory", (Join-Path $Layout.BaseDir "user"),
                        "--database-url", ("sqlite:///" + (Join-Path $Layout.BaseDir "user\comfyui.db")))
    }
    if ($Layout.ExtraModelPaths) { $arguments += @("--extra-model-paths-config", $Layout.ExtraModelPaths) }
    if ($Layout.InputDir) { $arguments += @("--input-directory", $Layout.InputDir) }
    if ($Layout.OutputDir) { $arguments += @("--output-directory", $Layout.OutputDir) }
    $arguments
}
