<#
.SYNOPSIS
  Prepare the 27B for the llama.cpp router (idempotent): model files, requantization, router preset, .env.

.DESCRIPTION
  The Qwen3.8 27B abliterated that writes the image tags (ComfyUI workflow), answers in the chat tab and serves
  /coder/turn runs on llama-server in router mode (scripts\start-llm.ps1, 127.0.0.1 only). This script:

  1. Finds llama-server / llama-quantize (scripts\setup-llamacpp.ps1; run here when missing).
  2. Gets the source GGUF and its mmproj (config\llm_model.json) into tools\models\llm: downloaded from Hugging Face
     and checked against the pinned SHA-256, or hard-linked from -SourceModel (a GGUF already on this machine).
  3. Requantizes it (default IQ3_M) with llama-quantize so it fits in ~24 GB of shared memory next to the OS.
     -Quant none uses the source as it is.
  4. Writes the router preset tools\llm\models.ini: context, GPU layers (-GpuOffload share of the model's layers),
     flash attention, 1 slot, mmap loading, thinking off by default, idle sleep (frees the memory after
     sleep_idle_s seconds without a request).
  5. Saves LLM_SERVER, LLM_PRESET, LLM_URL, LLM_MODEL, LLM_CONTEXT to .env and regenerates workflows\ when the
     model name or the URL differs from the templates.

.EXAMPLE
  .\scripts\setup-llm.ps1
  .\scripts\setup-llm.ps1 -SourceModel D:\models\Huihui-Qwen3.8-27B-abliterated-UD-DW-Q4_K_S.gguf
  .\scripts\setup-llm.ps1 -Quant none -GpuOffload 1     # enough memory: the downloaded Q4 on the GPU
#>
param(
    [string]$SourceModel = "",        # an existing source GGUF (its mmproj is looked for next to it)
    [ValidateSet("IQ3_M", "IQ3_S", "IQ3_XXS", "Q3_K_M", "IQ4_XS", "none", "")]
    [string]$Quant = "",              # default: config\llm_model.json (IQ3_M)
    [double]$GpuOffload = -1,         # share of the layers on the GPU; default: config\llm_model.json (0.45)
    [int]$ContextLength = 0,          # default: config\llm_model.json (4096)
    [int]$Port = 0,                   # default: config\llm_model.json (8080)
    [switch]$SkipHashCheck
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib\common.ps1")
Initialize-DotEnv | Out-Null
$root = Get-RepoRoot
$spec = Read-JsonFile (Join-Path $root "config\llm_model.json")
if (-not $Quant) { $Quant = $spec.quant }
if ($GpuOffload -lt 0) { $GpuOffload = [double]$spec.gpu_offload }
if (-not $ContextLength) { $ContextLength = [int]$spec.context }
if (-not $Port) { $Port = [int]$spec.port }

Write-Step "llama.cpp（llama-server / llama-quantize）"
$server = Get-LlamaServer
if (-not $server) {
    & (Join-Path $PSScriptRoot "setup-llamacpp.ps1")
    $server = Get-LlamaServer
}
if (-not $server) { throw "llama-server が見つかりません（scripts\setup-llamacpp.ps1）" }
$quantize = Join-Path (Split-Path $server) "llama-quantize.exe"
Write-Ok $server

function Test-Pinned([string]$Path, $Entry) {
    if ((Get-Item $Path).Length -ne [int64]$Entry.size) { throw "サイズが一致しません: $Path（期待 $($Entry.size)）" }
    if ($SkipHashCheck) { Write-Warn2 "SHA-256 の照合を省きました: $Path"; return }
    Write-Host "    sha256 を照合しています（大きなファイルは数分かかります）"
    $actual = Get-FileSha256 $Path
    if ($actual -ne $Entry.sha256) { throw "SHA-256 が一致しません: $Path（期待 $($Entry.sha256) / 実際 $actual）" }
    Write-Ok "sha256 $actual"
}

Write-Step "元のモデル（$($spec.repo)）"
$modelDir = Join-Path $root "tools\models\llm"
New-Item -ItemType Directory -Force $modelDir | Out-Null
$source = Join-Path $modelDir $spec.file
$mmproj = Join-Path $modelDir $spec.mmproj.file
$stem = [IO.Path]::GetFileNameWithoutExtension($spec.file) -replace "-(UD-)?(DW-)?Q\d.*$", ""
$target = if ($Quant -eq "none") { $source } else { Join-Path $modelDir "$stem-$Quant.gguf" }
$needSource = -not (Test-Path $target)
if ($SourceModel) {
    if (-not (Test-Path $SourceModel)) { throw "-SourceModel が見つかりません: $SourceModel" }
    $SourceModel = (Resolve-Path $SourceModel).Path
    $sibling = Join-Path (Split-Path $SourceModel) $spec.mmproj.file
    if ((Split-Path -Leaf $SourceModel) -eq $spec.file) {
        if ($needSource) { New-FileLink $source $SourceModel; Write-Ok "linked $source" }
    } elseif ($needSource) {
        # Another file (e.g. an already requantized one): linked into tools\models\llm and used as it is.
        $target = Join-Path $modelDir (Split-Path -Leaf $SourceModel)
        New-FileLink $target $SourceModel
        $needSource = $false
        Write-Warn2 "$($spec.file) ではないため、再量子化せずにそのまま使います: $target"
    }
    if ((Test-Path $sibling) -and -not (Test-Path $mmproj)) { New-FileLink $mmproj $sibling; Write-Ok "linked $mmproj" }
}
$hf = "https://huggingface.co/$($spec.repo)/resolve/main"
if ($needSource) {
    if (-not (Test-Path $source)) { Save-Download "$hf/$($spec.file)" $source }
    Test-Pinned $source $spec
}
if (-not (Test-Path $mmproj)) { Save-Download "$hf/$($spec.mmproj.file)" $mmproj }
Test-Pinned $mmproj $spec.mmproj

if ($Quant -ne "none" -and -not (Test-Path $target)) {
    Write-Step "$Quant に再量子化（元ファイルは残します。20〜30 分かかります）"
    if (-not (Test-Path $quantize)) { throw "llama-quantize.exe が $(Split-Path $server) にありません" }
    $threads = [Math]::Max(1, [Environment]::ProcessorCount / 2)
    $partial = "$target.partial"
    Invoke-Native $quantize --allow-requantize $source $partial $Quant $threads |
        Where-Object { $_ -match "model size|quant size|error|failed" } | ForEach-Object { Write-Host "    $_" }
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $partial)) { throw "llama-quantize に失敗しました" }
    Move-Item -Force $partial $target
}
Write-Ok "model: $target"

Write-Step "ルータのプリセット（context $ContextLength、GPU offload $GpuOffload、thinking off）"
Push-Location $root
try { $info = (Invoke-Native uv run --quiet python scripts\gguf_info.py $target --offload $GpuOffload | Select-Object -Last 1) | ConvertFrom-Json }
finally { Pop-Location }
if ($LASTEXITCODE -ne 0 -or -not $info.layers) { throw "GGUF のメタデータを読めません: $target" }
Write-Ok "$($info.architecture), $($info.layers) layers -> n-gpu-layers $($info.gpu_layers)"
$settings = [ordered]@{
    "model"              = $target.Replace("\", "/")
    "mmproj"             = $mmproj.Replace("\", "/")
    "ctx-size"           = $ContextLength
    "n-gpu-layers"       = $info.gpu_layers
    "flash-attn"         = "on"
    "parallel"           = 1
    # Vulkan on a shared-memory iGPU: without mmap the CPU-side weights go to pinned memory and the load fails
    # with ErrorOutOfDeviceMemory (measured on the Radeon 890M).
    "load-mode"          = "mmap"
    "jinja"              = $true
    "reasoning-format"   = "deepseek"
    "reasoning"          = "off"
    "temp"               = $spec.temperature
    # LM Studio's default sampling had a repeat penalty of 1.1; without it the tag JSON could loop on the negative
    # tags until max_tokens and come back unclosed (split fell back to the raw text).
    "repeat-penalty"     = $spec.repeat_penalty
    "sleep-idle-seconds" = $spec.sleep_idle_s
}
$preset = Join-Path $root "tools\llm\models.ini"
Write-TextFile $preset (ConvertTo-LlmPresetIni $spec.name $settings)
Write-Ok $preset

Write-Step ".env"
$url = "http://127.0.0.1:$Port/v1"
Set-DotEnvValue "LLM_SERVER" $server
Set-DotEnvValue "LLM_PRESET" $preset
Set-DotEnvValue "LLM_PORT" "$Port"
Set-DotEnvValue "LLM_URL" $url
Set-DotEnvValue "LLM_MODEL" $spec.name
Set-DotEnvValue "LLM_CONTEXT" "$ContextLength"
Write-Ok "LLM_URL=$url LLM_MODEL=$($spec.name)"

Write-Step "ワークフローの接続先"
$apiWorkflow = Join-Path $root "workflows\furry_ja_api.json"
$backend = (Read-JsonFile $apiWorkflow).llm_backend.inputs
if ($backend.model -eq $spec.name -and $backend.base_url -eq $url) {
    Write-Ok "workflows already use $($spec.name) at $url"
} else {
    $env:LLM_MODEL = $spec.name
    $env:LLM_URL = $url
    Push-Location $root
    try { Invoke-Native uv run python scripts/build_workflows.py | Write-Host } finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { throw "build_workflows.py に失敗しました" }
    Write-Ok "regenerated workflows ($($backend.model) at $($backend.base_url) -> $($spec.name) at $url)"
}
Write-Host ""
Write-Host "完了。.\scripts\start-llm.ps1 でルータを起動します（モデルは最初の要求でロードされ、使い終わると unload されます）。" -ForegroundColor Cyan
