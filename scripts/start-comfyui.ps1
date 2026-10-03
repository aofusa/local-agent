# Start the existing ComfyUI installation headless on 127.0.0.1:8188 (loopback only).
# Paths come from .env (written by scripts\setup-comfyui.ps1) or are detected from Comfy Desktop.
# Alternative: start the instance from Comfy Desktop after `setup-comfyui.ps1 -ConfigureComfyDesktop`.
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
& $layout.Python @arguments
