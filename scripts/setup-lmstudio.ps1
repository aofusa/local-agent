<#
.SYNOPSIS
  Configure LM Studio for local-agent (idempotent).

.DESCRIPTION
  1. Update the llama.cpp runtimes (Qwen3.8 GGUFs with an MTP layer need a recent runtime).
  2. Server: loopback only (127.0.0.1:1234), JIT model loading on, JIT idle TTL (insurance for eject).
  3. Find the downloaded Huihui Qwen3.8 27B Abliterated GGUF (+ mmproj).
  4. Optionally requantize it (default IQ3_M) with llama.cpp's llama-quantize so it fits in ~24 GB of
     shared memory next to the OS and ComfyUI. The original file is kept.
  5. Write per-model load/inference defaults used by JIT loading (context, GPU offload, flash attention,
     1 parallel slot, thinking off, temperature).
  6. Save the model key to .env (LMSTUDIO_MODEL) and regenerate workflows/ if the key differs.

.EXAMPLE
  .\scripts\setup-lmstudio.ps1
  .\scripts\setup-lmstudio.ps1 -Quant none -GpuOffload 1     # enough memory: use the downloaded Q4 as-is
#>
param(
    [string]$SourceModel = "",        # GGUF path (absolute or relative to the LM Studio models folder)
    [string]$ModelPattern = "Qwen3.8-27B-abliterated",
    [ValidateSet("IQ3_M", "IQ3_S", "IQ3_XXS", "Q3_K_M", "IQ4_XS", "none")]
    [string]$Quant = "IQ3_M",
    [double]$GpuOffload = 0.45,
    [int]$ContextLength = 4096,
    [double]$Temperature = 0.4,
    [int]$JitTtlSeconds = 300,
    [int]$Port = 1234,
    [string]$LlamaCppBuild = "b11376",
    [switch]$SkipRuntimeUpdate
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib\common.ps1")
Initialize-DotEnv | Out-Null
$lmHome = Get-LmStudioHome

Write-Step "lms CLI"
if (-not (Get-LmsPath)) {
    throw "lms CLI が見つかりません。LM Studio を一度起動し、必要なら '$lmHome\bin\lms.exe bootstrap' を実行してください。"
}
Write-Ok (Get-LmsPath)

if (-not $SkipRuntimeUpdate) {
    Write-Step "llama.cpp ランタイムを更新"
    Invoke-Lms runtime update --all -y | Where-Object { $_ -match "Updated|up-to-date|→" } | ForEach-Object { Write-Ok $_.Trim() }
}

Write-Step "サーバ設定（127.0.0.1:$Port、JIT ロード、JIT TTL ${JitTtlSeconds}s）"
$serverConfig = Join-Path $lmHome ".internal\http-server-config.json"
if (Test-Path $serverConfig) {
    Backup-File $serverConfig
    $cfg = Read-JsonFile $serverConfig
    Set-JsonProperty $cfg "networkInterface" "127.0.0.1"
    Set-JsonProperty $cfg "justInTimeModelLoading" $true
    Set-JsonProperty $cfg "port" $Port
    Write-JsonFile $serverConfig $cfg
}
$settingsFile = Join-Path $lmHome "settings.json"
if (Test-Path $settingsFile) {
    Backup-File $settingsFile
    $settings = Read-JsonFile $settingsFile
    if (-not $settings.developer) { Set-JsonProperty $settings "developer" ([pscustomobject]@{}) }
    Set-JsonProperty $settings.developer "jitModelTTL" ([pscustomobject]@{ enabled = $true; ttlSeconds = $JitTtlSeconds })
    Set-JsonProperty $settings.developer "unloadPreviousJITModelOnLoad" $true
    Write-JsonFile $settingsFile $settings
}
Invoke-Lms server stop | Out-Null
$out = Invoke-Lms server start --port $Port --bind 127.0.0.1
if ($LASTEXITCODE -ne 0) { throw "lms server start に失敗しました: $out" }
$addresses = Get-ListenAddresses $Port
if ($addresses | Where-Object { $_ -ne "127.0.0.1" }) { Write-Warn2 "ポート $Port がループバック以外でも待ち受けています: $($addresses -join ', ')" }
else { Write-Ok "listening on 127.0.0.1:$Port" }

Write-Step "モデルを探す（$ModelPattern）"
$modelsDir = Get-LmStudioModelsDir
function Get-LmsModels { ConvertTo-ObjectArray (Invoke-Lms ls --json | Out-String | ConvertFrom-Json) }
if ($SourceModel) {
    $sourcePath = if ([IO.Path]::IsPathRooted($SourceModel)) { $SourceModel } else { Join-Path $modelsDir $SourceModel }
} else {
    # The downloaded original is the largest variant; requantized copies made by this script are smaller.
    $candidates = @(Get-LmsModels | Where-Object { $_.path -like "*$ModelPattern*" } | Sort-Object sizeBytes -Descending)
    if (-not $candidates) {
        throw "'$ModelPattern' を含むモデルが LM Studio にありません。README の「事前準備」に従ってダウンロードしてください。"
    }
    $sourcePath = Join-Path $modelsDir ($candidates[0].path -replace "/", "\")
}
if (-not (Test-Path $sourcePath)) { throw "GGUF が見つかりません: $sourcePath" }
$sourceDir = Split-Path -Parent $sourcePath
$mmproj = Get-ChildItem $sourceDir -Filter "mmproj*.gguf" | Select-Object -First 1
if (-not $mmproj) { Write-Warn2 "mmproj が $sourceDir にありません。参照画像（Vision）が使えません。" }
Write-Ok "source: $sourcePath"

$targetPath = $sourcePath
if ($Quant -ne "none") {
    Write-Step "$Quant に再量子化（元ファイルは残します）"
    $stem = [IO.Path]::GetFileNameWithoutExtension($sourcePath) -replace "-(UD-)?(DW-)?Q\d.*$", ""
    # e.g. ...\Huihui-Qwen3.8-27B-abliterated-GGUF -> ...\Huihui-Qwen3.8-27B-abliterated-IQ3_M-GGUF
    $targetDir = ($sourceDir -replace "-GGUF$", "") + "-$Quant-GGUF"
    $targetPath = Join-Path $targetDir "$stem-$Quant.gguf"
    if (Test-Path $targetPath) {
        Write-Ok "already exists: $targetPath"
    } else {
        $toolDir = Join-Path (Get-RepoRoot) "tools\llama.cpp\$LlamaCppBuild"
        $quantize = Join-Path $toolDir "llama-quantize.exe"
        if (-not (Test-Path $quantize)) {
            $zip = Join-Path $toolDir "llama-cpu.zip"
            New-Item -ItemType Directory -Force $toolDir | Out-Null
            $url = "https://github.com/ggml-org/llama.cpp/releases/download/$LlamaCppBuild/llama-$LlamaCppBuild-bin-win-cpu-x64.zip"
            Write-Ok "download $url"
            Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
            Expand-Archive -Path $zip -DestinationPath $toolDir -Force
            Remove-Item $zip
        }
        New-Item -ItemType Directory -Force $targetDir | Out-Null
        $threads = [Math]::Max(1, [Environment]::ProcessorCount / 2)
        Write-Ok "llama-quantize（20〜30 分かかります）"
        & $quantize --allow-requantize $sourcePath $targetPath $Quant $threads 2>&1 |
            Where-Object { $_ -match "model size|quant size|error|failed" } | ForEach-Object { Write-Host "    $_" }
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path $targetPath)) { throw "llama-quantize に失敗しました" }
    }
    if ($mmproj) {
        $mmprojTarget = Join-Path $targetDir $mmproj.Name
        if (-not (Test-Path $mmprojTarget)) {
            try { New-Item -ItemType HardLink -Path $mmprojTarget -Target $mmproj.FullName | Out-Null }
            catch { Copy-Item $mmproj.FullName $mmprojTarget }
            Write-Ok "mmproj -> $mmprojTarget"
        }
    }
}

Write-Step "モデルキーを確認"
$relative = $targetPath.Substring($modelsDir.TrimEnd('\').Length + 1) -replace "\\", "/"
$entry = $null
for ($i = 0; $i -lt 10 -and -not $entry; $i++) {
    $entry = Get-LmsModels | Where-Object { $_.path -eq $relative } | Select-Object -First 1
    if (-not $entry) { Start-Sleep 3 }
}
if (-not $entry) { throw "LM Studio に $relative が表示されません。LM Studio の My Models を開いて再読込してから再実行してください。" }
$modelKey = $entry.modelKey
Write-Ok "key: $modelKey  vision=$($entry.vision)"
if (-not $entry.vision) { Write-Warn2 "Vision が無効です（mmproj を同じフォルダに置いてください）。" }

Write-Step "JIT ロード時の既定値（context $ContextLength、GPU offload $GpuOffload、並列 1、thinking off）"
$configFile = Join-Path $lmHome (".internal\user-concrete-model-default-config\" + ($relative -replace "/", "\") + ".json")
$modelConfig = [pscustomobject]@{
    preset    = ""
    operation = [pscustomobject]@{ fields = @(
        [pscustomobject]@{ key = "llm.prediction.temperature"; value = $Temperature },
        [pscustomobject]@{ key = "llm.prediction.reasoning.enableThinking"; value = $false }
    ) }
    load      = [pscustomobject]@{ fields = @(
        [pscustomobject]@{ key = "llm.load.contextLength"; value = $ContextLength },
        [pscustomobject]@{ key = "llm.load.llama.acceleration.offloadRatio"; value = $GpuOffload },
        [pscustomobject]@{ key = "llm.load.llama.flashAttention"; value = $true },
        [pscustomobject]@{ key = "llm.load.numParallelSessions"; value = 1 },
        [pscustomobject]@{ key = "llm.load.llama.keepModelInMemory"; value = $false },
        [pscustomobject]@{ key = "llm.load.llama.tryMmap"; value = $true }
    ) }
}
Backup-File $configFile
Write-JsonFile $configFile $modelConfig
Write-Ok $configFile

Write-Step "ワークフローのモデル名"
Set-DotEnvValue "LMSTUDIO_MODEL" $modelKey
$apiWorkflow = Join-Path (Get-RepoRoot) "workflows\furry_ja_api.json"
$current = (Read-JsonFile $apiWorkflow).llm_backend.inputs.model
if ($current -eq $modelKey) {
    Write-Ok "workflows already use $modelKey"
} else {
    $env:LMSTUDIO_MODEL = $modelKey
    Push-Location (Get-RepoRoot)
    try { Invoke-Native uv run python scripts/build_workflows.py | Write-Host } finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { throw "build_workflows.py に失敗しました" }
    Write-Ok "regenerated workflows with $modelKey (was $current)"
}
Write-Host ""
Write-Host "LM Studio の設定が完了しました。モデルは事前ロード不要です（ワークフローが JIT ロードし、終わると unload します）。" -ForegroundColor Cyan
