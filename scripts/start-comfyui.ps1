# Start the existing Comfy Desktop installation's ComfyUI server headless on 127.0.0.1:8188.
# Same Python, code, models, input/output and user directories as Comfy Desktop uses.
# Alternative: open Comfy Desktop and start the "ComfyUI" instance (its launch args are set to the same).
param(
    [string]$InstallDir = "$env:LOCALAPPDATA\Comfy-Desktop\ComfyUI-Installs\ComfyUI",
    [string]$BaseDir = "$env:USERPROFILE\Documents\ComfyUI",
    [string]$ModelPathsConfig = "$env:APPDATA\Comfy Desktop\instance-model-paths\<instance-id>.yaml"
)
$ErrorActionPreference = "Stop"
if (Get-NetTCPConnection -State Listen -LocalPort 8188 -ErrorAction SilentlyContinue) {
    Write-Host "ComfyUI is already listening on 8188"; return
}
Set-Location $InstallDir
& "$BaseDir\.venv\Scripts\python.exe" -s ComfyUI\main.py `
    --listen 127.0.0.1 --port 8188 --enable-manager --enable-assets `
    --base-directory $BaseDir `
    --user-directory "$BaseDir\user" `
    --database-url "sqlite:///$BaseDir\user\comfyui.db" `
    --extra-model-paths-config $ModelPathsConfig `
    --input-directory "$BaseDir\input" `
    --output-directory "$BaseDir\output"
