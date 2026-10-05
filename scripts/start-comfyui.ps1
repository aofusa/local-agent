# Start ComfyUI (tools\comfyui, installed by scripts\setup-comfyui.ps1) headless on 127.0.0.1:8188 (loopback only),
# with --cache-none. Paths come from .env (COMFYUI_*).
param(
    [int]$Port = 8188
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib\common.ps1")

if (Test-Listening $Port) { Write-Host "ComfyUI is already listening on $Port"; return }
$layout = Get-ComfyLayout
if (-not $layout -or -not $layout.Python) {
    throw "ComfyUI の場所が分かりません。先に .\scripts\setup-comfyui.ps1 を実行してください。"
}
$arguments = Get-ComfyServerArgs $layout $Port
$extra = Get-DotEnvValue "COMFYUI_EXTRA_ARGS" ""
if ($extra) { $arguments += ($extra -split "\s+" | Where-Object { $_ }) }
Write-Host "ComfyUI: $($layout.Python) $($arguments -join ' ')"
Set-Location $layout.MainDir
# Japanese Windows defaults to cp932: a custom node printing other characters (LM Connect's Turkish messages)
# would fail to import when the output is redirected.
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
& $layout.Python @arguments
