<#
.SYNOPSIS
  Download the GGUF weights of the chat tab's search agent (idempotent, resumable).

.DESCRIPTION
  Bonsai-8B, Ternary-Bonsai-8B, Bonsai-4B, Ternary-Bonsai-2-27B, Ternary-Bonsai-2-27B abliterated (PTQ1_0),
  Qwen3.5-4B-heretic Q4_K_M, Qwen3-1.7B-heretic and Qwen3-0.6B-heretic (config\search_models.json).
  About 20 GB in total. They are run only by the PrismML llama.cpp fork (scripts\setup-llamacpp.ps1);
  they are not added to LM Studio or ComfyUI.

  Every download is checked against the SHA-256 in the catalog (Hugging Face's LFS hash) before it is used;
  -Verify re-checks files that are already there. The folder is saved to .env as BONSAI_MODELS_DIR
  (default tools\models, git ignored).
  A missing model is simply left out of the automatic selection.

.EXAMPLE
  .\scripts\setup-search-models.ps1
  .\scripts\setup-search-models.ps1 -Models bonsai-8b,qwen3-0.6b-heretic
  .\scripts\setup-search-models.ps1 -Dir D:\models\bonsai
#>
param(
    [string[]]$Models = @(),    # ids from config\search_models.json; empty = all
    [string]$Dir = "",
    [switch]$Verify             # re-hash files that are already downloaded
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib\common.ps1")
Initialize-DotEnv | Out-Null
$root = Get-RepoRoot
$catalog = Read-JsonFile (Join-Path $root "config\search_models.json")

if (-not $Dir) { $Dir = Get-DotEnvValue "BONSAI_MODELS_DIR" (Join-Path $root "tools\models") }
New-Item -ItemType Directory -Force $Dir | Out-Null
$Dir = (Resolve-Path $Dir).Path
$selected = @(ConvertTo-ObjectArray $catalog.models | Where-Object { -not $Models -or $_.id -in $Models })
$unknown = @($Models | Where-Object { $_ -notin @(ConvertTo-ObjectArray $catalog.models | ForEach-Object { $_.id }) })
if ($unknown) { throw "不明なモデル id: $($unknown -join ', ')" }

$needed = ($selected | Where-Object { -not (Test-Path (Join-Path $Dir $_.file)) -or (Get-Item (Join-Path $Dir $_.file)).Length -ne [int64]$_.size } |
    Measure-Object -Property size -Sum).Sum
$free = (Get-PSDrive -Name (Split-Path -Qualifier $Dir).TrimEnd(':')).Free
if ($needed -and $needed -gt $free) { throw ("空き容量が足りません: 必要 {0:N1} GB / 空き {1:N1} GB" -f ($needed / 1GB), ($free / 1GB)) }

foreach ($m in $selected) {
    $path = Join-Path $Dir $m.file
    Write-Step ("{0}  {1:N2} GB" -f $m.label, ([int64]$m.size / 1GB))
    if ((Test-Path $path) -and (Get-Item $path).Length -eq [int64]$m.size) {
        if ($Verify -and (Get-FileSha256 $path) -ne $m.sha256) { throw "SHA-256 が一致しません: $path（削除して再実行してください）" }
        Write-Ok "取得済み: $path"; continue
    }
    $url = "https://huggingface.co/$($m.repo)/resolve/main/$($m.file)"
    $part = "$path.part"
    # -C - resumes a partial download.
    & curl.exe -L --fail --retry 5 --retry-delay 5 -C - -o $part $url
    if ($LASTEXITCODE -ne 0) { throw "ダウンロードに失敗しました: $url" }
    if ((Get-Item $part).Length -ne [int64]$m.size) { throw "サイズが一致しません: $part（期待 $($m.size)）" }
    $actual = Get-FileSha256 $part
    if ($actual -ne $m.sha256) { Remove-Item $part; throw "SHA-256 が一致しません: $($m.file)（期待 $($m.sha256) / 実際 $actual）" }
    Move-Item -Force $part $path
    Write-Ok $path
}
Set-DotEnvValue "BONSAI_MODELS_DIR" $Dir
Write-Ok "BONSAI_MODELS_DIR=$Dir を .env に保存しました。次は .\scripts\probe-bonsai.ps1 で検証してください"
