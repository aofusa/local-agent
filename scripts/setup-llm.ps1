<#
.SYNOPSIS
  Prepare the 27B for the llama.cpp router (idempotent): model files, requantization, router preset, .env.

.DESCRIPTION
  The Qwen3.8 27B abliterated that writes the image tags (ComfyUI workflow), answers in the chat tab and serves
  /coder/turn runs on llama-server in router mode (scripts\start-llm.ps1, 127.0.0.1 only). This script:

  1. Finds llama-server / llama-quantize (scripts\setup-llamacpp.ps1; run here when missing).
  2. Puts the model files (config\llm_model.json) into tools\models\llm. Files already on this machine are used first
     and hard-linked in (no download): LM Studio's model folder (an earlier version of this project used LM Studio),
     the Hugging Face cache (what `hf download` fetched) and -SourceModel. Otherwise the source GGUF and its mmproj are
     fetched with `hf download` into the Hugging Face cache and linked from there. Every file is checked against its
     pinned SHA-256.
  3. Requantizes the source (default IQ3_M) with llama-quantize so it fits in ~24 GB of shared memory next to the
     OS, unless the requantized file was found in step 2. -Quant none uses the source as it is.
  4. Writes the router preset tools\llm\models.ini (scripts\host_models.py): one section per inference model of
     config\host_models.json whose GGUF exists, with its context and sampling parameters, GPU layers
     (-GpuOffload share of the 27B's layers; the Bonsai 2 27B runs fully on the GPU), flash attention, 1 slot,
     mmap loading, thinking off by default, idle sleep (frees the memory after sleep_idle_s seconds).
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
$pinnedQuant = if ($Quant -ne "none" -and $spec.quantized) { $spec.quantized.$Quant } else { $null }
$lmModels = Get-LmStudioModelsDir
function Find-OnMachine([string]$Name, [int64]$Size) {
    # A file of this name and size in LM Studio's model folder (where an earlier version of this project kept the 27B).
    if (-not $lmModels) { return $null }
    Get-ChildItem $lmModels -Recurse -File -Filter $Name -ErrorAction SilentlyContinue |
        Where-Object { $_.Length -eq $Size } | Select-Object -First 1 -ExpandProperty FullName
}
function Add-Mmproj([string]$Folder) {
    $sibling = Join-Path $Folder $spec.mmproj.file
    if ((Test-Path $sibling) -and (Get-Item $sibling).Length -eq [int64]$spec.mmproj.size -and -not (Test-Path $mmproj)) {
        New-FileLink $mmproj $sibling; Write-Ok "linked $mmproj"
    }
}
$created = $false
if ($SourceModel) {
    if (-not (Test-Path $SourceModel)) { throw "-SourceModel が見つかりません: $SourceModel" }
    $SourceModel = (Resolve-Path $SourceModel).Path
    if ((Split-Path -Leaf $SourceModel) -eq $spec.file) {
        if (-not (Test-Path $source)) { New-FileLink $source $SourceModel; Write-Ok "linked $source" }
    } elseif (-not (Test-Path $target)) {
        # Another file (e.g. an already requantized one): linked into tools\models\llm and used as it is.
        $target = Join-Path $modelDir (Split-Path -Leaf $SourceModel)
        New-FileLink $target $SourceModel
        Write-Warn2 "$($spec.file) ではないため、再量子化せずにそのまま使います: $target"
    }
    Add-Mmproj (Split-Path $SourceModel)
}
if (-not (Test-Path $target) -and $pinnedQuant) {
    $found = Find-OnMachine $pinnedQuant.file ([int64]$pinnedQuant.size)
    if ($found) { New-FileLink $target $found; $created = $true; Write-Ok "linked $target（$found）"; Add-Mmproj (Split-Path $found) }
}
$needSource = -not (Test-Path $target)
if ($needSource -and -not (Test-Path $source)) {
    $found = Find-OnMachine $spec.file ([int64]$spec.size)
    if ($found) { New-FileLink $source $found; Write-Ok "linked $source（$found）"; Add-Mmproj (Split-Path $found) }
}
if (-not (Test-Path $mmproj)) {
    $found = Find-OnMachine $spec.mmproj.file ([int64]$spec.mmproj.size)
    if ($found) { New-FileLink $mmproj $found; Write-Ok "linked $mmproj（$found）" }
}
if ($needSource) {
    if (-not (Test-Path $source)) { Get-HfFile $spec.repo $spec.file $source $spec.sha256 ([int64]$spec.size) }
    Test-Pinned $source $spec
}
if (-not (Test-Path $mmproj)) { Get-HfFile $spec.repo $spec.mmproj.file $mmproj $spec.mmproj.sha256 ([int64]$spec.mmproj.size) }
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
    $created = $true
}
if ($created -and $pinnedQuant -and (Split-Path -Leaf $target) -eq $pinnedQuant.file) {
    # The same source and llama-quantize give the same bytes; another build may differ slightly (warning only).
    try { Test-Pinned $target $pinnedQuant }
    catch { Write-Warn2 "$Quant のファイルが固定した版と一致しません（llama.cpp の版の違いなど）。そのまま使います: $($_.Exception.Message)" }
}
Write-Ok "model: $target"

Write-Step "ルータのプリセット（config\host_models.json の推論モデルごと。GPU offload $GpuOffload、thinking off）"
# One section per inference model whose GGUF exists (the 27B above, the Bonsai 2 27B abliterated from
# scripts\setup-search-models.ps1), with the model's own context and sampling (config\host_models.json "params").
$preset = Join-Path $root "tools\llm\models.ini"
$modelsDir = Get-DotEnvValue "BONSAI_MODELS_DIR" (Join-Path $root "tools\models")
Push-Location $root
try {
    $out = Invoke-Native uv run --quiet python scripts\host_models.py preset --out $preset --offload $GpuOffload `
        --qwen-model $target --qwen-mmproj $mmproj --models-dir $modelsDir --sleep-idle $spec.sleep_idle_s
} finally { Pop-Location }
if ($LASTEXITCODE -ne 0) { throw "ルータのプリセットを書けません: $($out -join ' ')" }
foreach ($section in (($out | Select-Object -Last 1) | ConvertFrom-Json)) {
    if ($section.skipped) { Write-Warn2 "$($section.skipped.id): $($section.skipped.reason)（モデル一覧では使えないと表示します）" }
    else { Write-Ok "[$($section.id)] ctx $($section.settings.'ctx-size'), n-gpu-layers $($section.settings.'n-gpu-layers')" }
}
Write-Ok $preset
if ($ContextLength -ne [int]$spec.context) {
    Write-Warn2 "-ContextLength は config\host_models.json の context を使うようになりました（$ContextLength は .env の LLM_CONTEXT にだけ入れます）"
}

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
