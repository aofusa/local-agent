# Start the LangGraph dev server (graph id "agent") reachable from other hosts.
# ComfyUI (127.0.0.1:8188) and LM Studio (127.0.0.1:1234) must already be running.
param(
    [int]$Port = 2024
)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
# Japanese Windows defaults to cp932; langgraph-api reads its files as UTF-8.
$env:PYTHONUTF8 = "1"
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env" }
uv run langgraph dev --host 0.0.0.0 --port $Port --no-browser --no-reload
