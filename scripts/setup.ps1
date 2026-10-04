<#
.SYNOPSIS
  One-shot setup for local-agent on Windows (idempotent; safe to re-run).

.DESCRIPTION
  1. Check prerequisites (git, uv, Node.js).
  2. Create .env from .env.example.
  3. Python environment for LangGraph (uv sync) and agent-chat-ui dependencies (pnpm via npx).
  4. LM Studio setup (scripts\setup-lmstudio.ps1).
  5. ComfyUI setup (scripts\setup-comfyui.ps1), then the multi-image reference nodes and models
     (scripts\setup-comfyui-refs.ps1: IP-Adapter, ControlNet, DWPose / depth; about 6 GB of downloads).
  6. Chat tab search: Tor Expert Bundle (setup-tor.ps1), the PrismML llama.cpp fork (setup-llamacpp.ps1),
     the search models (setup-search-models.ps1, about 20 GB) and their probe (probe-bonsai.ps1). -SkipSearch skips it.
  7. Optionally open the Windows firewall for TCP 2024/3000 on Private networks (-OpenFirewall, asks for admin).

.EXAMPLE
  .\scripts\setup.ps1 -OpenFirewall
  .\scripts\setup.ps1 -ComfyUIDir D:\ComfyUI_windows_portable\ComfyUI -Quant none -GpuOffload 1
#>
param(
    [string]$ComfyUIDir = "",
    [string]$ComfyPython = "",
    [switch]$ConfigureComfyDesktop,
    [ValidateSet("IQ3_M", "IQ3_S", "IQ3_XXS", "Q3_K_M", "IQ4_XS", "none")]
    [string]$Quant = "IQ3_M",
    [double]$GpuOffload = 0.45,
    [switch]$SkipLMStudio,
    [switch]$SkipComfyUI,
    [switch]$SkipReferenceModels,      # text-only / plain img2img work without them
    [switch]$SkipSearch,               # the chat tab's web search (Tor, llama.cpp fork, ~20 GB of models)
    [switch]$SkipProbe,
    [switch]$OpenFirewall
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib\common.ps1")
$root = Get-RepoRoot

Write-Step "前提ツール"
$missing = @()
foreach ($tool in @(
    @{ Name = "git"; Hint = "https://git-scm.com/download/win  (winget install Git.Git)" },
    @{ Name = "uv"; Hint = "https://docs.astral.sh/uv/  (winget install astral-sh.uv)" },
    @{ Name = "node"; Hint = "https://nodejs.org/  Node.js 20 以上 (winget install OpenJS.NodeJS.LTS)" },
    @{ Name = "npx"; Hint = "Node.js に同梱" }
)) {
    $cmd = Get-Command $tool.Name -ErrorAction SilentlyContinue
    if ($cmd) { Write-Ok "$($tool.Name): $($cmd.Source)" } else { $missing += "$($tool.Name) — $($tool.Hint)" }
}
if ($missing) { throw ("次のツールをインストールしてから再実行してください:`n  " + ($missing -join "`n  ")) }
$nodeMajor = [int]((Invoke-Native node --version | Select-Object -First 1).TrimStart("v").Split(".")[0])
if ($nodeMajor -lt 20) { throw "Node.js 20 以上が必要です（現在 v$nodeMajor）" }

Write-Step ".env"
Write-Ok (Initialize-DotEnv)

Write-Step "LangGraph の Python 環境（uv sync）"
Push-Location $root
try {
    Invoke-Native uv sync | Select-Object -Last 3 | ForEach-Object { Write-Host "    $_" }
    if ($LASTEXITCODE -ne 0) { throw "uv sync に失敗しました" }
} finally { Pop-Location }

Write-Step "agent-chat-ui の依存（pnpm install）"
Push-Location (Join-Path $root "agent-chat-ui")
try {
    Invoke-Native npx --yes pnpm@10.5.1 install --frozen-lockfile | Select-Object -Last 3 | ForEach-Object { Write-Host "    $_" }
    if ($LASTEXITCODE -ne 0) { throw "pnpm install に失敗しました" }
} finally { Pop-Location }

if (-not $SkipLMStudio) {
    & (Join-Path $PSScriptRoot "setup-lmstudio.ps1") -Quant $Quant -GpuOffload $GpuOffload
}
if (-not $SkipComfyUI) {
    $comfyArgs = @{}
    if ($ComfyUIDir) { $comfyArgs["ComfyUIDir"] = $ComfyUIDir }
    if ($ComfyPython) { $comfyArgs["ComfyPython"] = $ComfyPython }
    if ($ConfigureComfyDesktop) { $comfyArgs["ConfigureComfyDesktop"] = $true }
    & (Join-Path $PSScriptRoot "setup-comfyui.ps1") @comfyArgs
    if (-not $SkipReferenceModels) { & (Join-Path $PSScriptRoot "setup-comfyui-refs.ps1") }
}

if (-not $SkipSearch) {
    & (Join-Path $PSScriptRoot "setup-tor.ps1")
    & (Join-Path $PSScriptRoot "setup-llamacpp.ps1")
    & (Join-Path $PSScriptRoot "setup-search-models.ps1")
    if (-not $SkipProbe) { & (Join-Path $PSScriptRoot "probe-bonsai.ps1") }
}

if ($OpenFirewall) {
    Write-Step "ファイアウォール（TCP 2024 / 3000、Private のみ）"
    $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
    $script = Join-Path $PSScriptRoot "open-firewall.ps1"
    if ($isAdmin) { & $script }
    else {
        $shell = (Get-Process -Id $PID).Path
        Start-Process $shell -Verb RunAs -Wait -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$script`"")
    }
}

Write-Host ""
Write-Host "セットアップ完了。次の手順:" -ForegroundColor Cyan
Write-Host "  1. LM Studio を起動したままにする（サーバは 127.0.0.1:1234）"
Write-Host "  2. .\scripts\start-all.ps1          # ComfyUI / LangGraph / agent-chat-ui を別ウィンドウで起動"
Write-Host "  3. .\scripts\doctor.ps1             # 設定と待受を確認"
Write-Host "  4. 他ホストのブラウザで http://$(Get-LanIPv4):3000 を開く"
