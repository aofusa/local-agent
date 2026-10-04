<#
.SYNOPSIS
  Verify the search models on this machine and write tools\bonsai\rank.json (design doc §5.6).

.DESCRIPTION
  Starts every downloaded model once on the PrismML llama-server and runs one short fixed test for each task it
  is listed for in config\search_models.json (route / plan / filter / worker / critique / synthesize). Records
  boot time, memory, tokens/s and pass/fail. The chat tab only uses models that passed. Run it with LM Studio's
  model unloaded and ComfyUI idle: the memory numbers are measured. Takes several minutes (the 27B models
  dominate). The rank file is machine-specific and git ignored.

.EXAMPLE
  .\scripts\probe-bonsai.ps1
  .\scripts\probe-bonsai.ps1 -Models ternary-8b,bonsai-4b
  .\scripts\probe-bonsai.ps1 -Tasks worker
#>
param(
    [string[]]$Models = @(),
    [string[]]$Tasks = @()
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib\common.ps1")
Set-Location (Get-RepoRoot)
$env:PYTHONUTF8 = "1"
$arguments = @("run", "python", "-m", "furry_agent.bonsai_probe")
if ($Models) { $arguments += @("--models", ($Models -join ",")) }
if ($Tasks) { $arguments += @("--tasks", ($Tasks -join ",")) }
& uv @arguments
if ($LASTEXITCODE -ne 0) { throw "probe に失敗しました" }
