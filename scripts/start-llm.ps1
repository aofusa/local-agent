# Start llama-server in router mode on 127.0.0.1 (loopback only) with the preset from scripts\setup-llm.ps1.
# The router holds no model at start: the first request (ComfyUI's prompt node, the chat tab, /coder/turn) loads the
# 27B, POST /models/unload (the workflow's eject, the chat tab) or the idle sleep frees it again.
param(
    [int]$Port = 0
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib\common.ps1")

if (-not $Port) { $Port = [int](Get-DotEnvValue "LLM_PORT" "8080") }
if (Test-Listening $Port) { Write-Host "LLM router is already listening on $Port"; return }
$server = Get-LlamaServer
$preset = Get-DotEnvValue "LLM_PRESET" (Join-Path (Get-RepoRoot) "tools\llm\models.ini")
if (-not $server) { throw "llama-server が見つかりません。先に .\scripts\setup-llamacpp.ps1 を実行してください。" }
if (-not (Test-Path $preset)) { throw "プリセットがありません（$preset）。先に .\scripts\setup-llm.ps1 を実行してください。" }
$arguments = Get-LlmServerArgs $preset $Port
Write-Host "LLM router: $server $($arguments -join ' ')"
& $server @arguments
