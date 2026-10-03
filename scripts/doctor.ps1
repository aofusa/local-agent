<#
.SYNOPSIS
  Check that LM Studio, ComfyUI, LangGraph and agent-chat-ui are configured and reachable.
  Exit code 1 when a required check fails.
#>
param(
    [int]$LMStudioPort = 1234,
    [int]$ComfyPort = 8188,
    [int]$LangGraphPort = 2024,
    [int]$UIPort = 3000
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib\common.ps1")
$script:failed = 0

function Check([string]$Name, [scriptblock]$Test, [switch]$Optional) {
    try {
        $detail = & $Test
        Write-Host ("  [OK]   {0}  {1}" -f $Name, $detail) -ForegroundColor Green
    } catch {
        $tag = if ($Optional) { "[WARN]" } else { "[NG]  " }
        $color = if ($Optional) { "Yellow" } else { "Red" }
        Write-Host ("  {0} {1}  {2}" -f $tag, $Name, $_.Exception.Message) -ForegroundColor $color
        if (-not $Optional) { $script:failed++ }
    }
}

function Assert-LoopbackOnly([int]$Port) {
    $addresses = Get-ListenAddresses $Port
    if (-not $addresses) { throw "ポート $Port で待ち受けていません" }
    $public = $addresses | Where-Object { $_ -notin @("127.0.0.1", "::1") }
    if ($public) { throw "ループバック以外でも待ち受けています: $($public -join ', ')" }
    "127.0.0.1:$Port"
}

function Get-Json([string]$Url) { Invoke-RestMethod -Uri $Url -TimeoutSec 10 }

$envValues = Read-DotEnv
$modelKey = $envValues["LMSTUDIO_MODEL"]
$ckptName = $envValues["CKPT_NAME"]
$workflow = Read-JsonFile (Join-Path (Get-RepoRoot) "workflows\furry_ja_api.json")
if (-not $modelKey) { $modelKey = $workflow.llm_backend.inputs.model }
if (-not $ckptName) { $ckptName = $workflow.ckpt.inputs.ckpt_name }

Write-Step "LM Studio"
Check "server is loopback only" { Assert-LoopbackOnly $LMStudioPort }
Check "model $modelKey" {
    $models = (Get-Json "http://127.0.0.1:$LMStudioPort/api/v1/models").models
    $m = $models | Where-Object { $_.key -eq $modelKey } | Select-Object -First 1
    if (-not $m) { throw "LM Studio にありません（scripts\setup-lmstudio.ps1 を実行）" }
    if (-not $m.capabilities.vision) { throw "vision が無効（mmproj が無い）" }
    "vision=True"
}
Check "workflow uses $modelKey" {
    if ($workflow.llm_backend.inputs.model -ne $modelKey) { throw "workflows は $($workflow.llm_backend.inputs.model)" }
    "ok"
}

Write-Step "ComfyUI"
Check "server is loopback only" { Assert-LoopbackOnly $ComfyPort }
Check "node cache disabled (--cache-none)" {
    $argv = (Get-Json "http://127.0.0.1:$ComfyPort/system_stats").system.argv
    if ($argv -notcontains "--cache-none") { throw "ComfyUI を --cache-none 付きで起動してください（start-comfyui.ps1 は付けます。IP-Adapter の 2 回目以降が壊れます）" }
    "ok"
}
Check "custom nodes" {
    $needed = "LMConnectLMStudioBackend", "LMConnectVision", "LMConnectPromptWithSystem",
              "LMConnectEjectLMStudioModel", "FurryJaSplitTags", "FurryJaCheckpointLoaderAfterEject"
    $info = Get-Json "http://127.0.0.1:$ComfyPort/object_info"
    $absent = $needed | Where-Object { -not $info.PSObject.Properties[$_] }
    if ($absent) { throw "未登録: $($absent -join ', ')（setup-comfyui.ps1 の後に ComfyUI を再起動）" }
    "$($needed.Count) nodes"
}
Check "reference nodes and models (multi-image)" {
    $info = Get-Json "http://127.0.0.1:$ComfyPort/object_info"
    $needed = "IPAdapterUnifiedLoader", "IPAdapterAdvanced", "DWPreprocessor", "DepthAnythingV2Preprocessor",
              "DiffControlNetLoader", "SetUnionControlNetType", "FurryJaImageAfter"
    $absent = $needed | Where-Object { -not $info.PSObject.Properties[$_] }
    if ($absent) { throw "未登録: $($absent -join ', ')（setup-comfyui-refs.ps1 の後に ComfyUI を再起動）" }
    $controlnets = $info.DiffControlNetLoader.input.required.control_net_name[0]
    if ($controlnets -notcontains "controlnet-union-sdxl-1.0-promax.safetensors") { throw "ControlNet union のモデルがありません（setup-comfyui-refs.ps1）" }
    "$($needed.Count) nodes + ControlNet"
}
Check "LORAS" {
    $loras = Get-DotEnvValue "LORAS" ""
    if (-not $loras) { return "none" }
    $info = Get-Json "http://127.0.0.1:$ComfyPort/object_info/LoraLoader"
    $available = @($info.LoraLoader.input.required.lora_name[0]) | ForEach-Object { "$_".Replace('\', '/').ToLower() }
    $missing = foreach ($item in ($loras -split '[,;]')) {
        $name = ($item.Trim() -split ':')[0].Replace('\', '/').ToLower()
        if (-not $name) { continue }
        $hit = $available | Where-Object { $_ -eq $name -or ($_ -replace '\.[^.]+$', '') -eq $name -or ($_ -replace '^.*/', '' -replace '\.[^.]+$', '') -eq $name }
        if (-not $hit) { $name }
    }
    if ($missing) { throw "ComfyUI の loras にありません: $($missing -join ', ')" }
    $loras
}
Check "checkpoint $ckptName" {
    $info = Get-Json "http://127.0.0.1:$ComfyPort/object_info/FurryJaCheckpointLoaderAfterEject"
    $names = $info.FurryJaCheckpointLoaderAfterEject.input.required.ckpt_name[0]
    if ($names -notcontains $ckptName) { throw "ComfyUI の checkpoints にありません" }
    "found"
}

Write-Step "LangGraph / agent-chat-ui"
$lan = Get-LanIPv4
Check "LangGraph graph 'agent' on :$LangGraphPort" {
    $assistants = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$LangGraphPort/assistants/search" `
        -ContentType "application/json" -Body "{}" -TimeoutSec 10
    if (-not ((ConvertTo-ObjectArray $assistants) | Where-Object { $_.graph_id -eq "agent" })) { throw "graph_id=agent がありません" }
    "listening on $((Get-ListenAddresses $LangGraphPort) -join ', ')"
}
Check "agent-chat-ui on http://${lan}:$UIPort" {
    $r = Invoke-WebRequest -Uri "http://${lan}:$UIPort/" -UseBasicParsing -TimeoutSec 20
    "HTTP $($r.StatusCode)"
}
Check "UI build points at http://${lan}:$LangGraphPort" {
    $stamp = Join-Path (Get-RepoRoot) "agent-chat-ui\.next\local-agent-api-url.txt"
    if (-not (Test-Path $stamp)) { throw "未ビルド（start-ui.ps1 を実行）" }
    $built = ([IO.File]::ReadAllText($stamp)).Trim()
    if ($built -ne "http://${lan}:$LangGraphPort") { throw "ビルド時の接続先は $built（start-ui.ps1 を再実行すると再ビルドします）" }
    $built
} -Optional
Check "firewall rules (TCP $LangGraphPort / $UIPort, Private)" {
    $rules = @(Get-NetFirewallRule -DisplayName "local-agent*" -ErrorAction SilentlyContinue | Where-Object Enabled -eq "True")
    if ($rules.Count -lt 2) { throw "未設定（管理者で scripts\open-firewall.ps1）" }
    "$($rules.Count) rules"
} -Optional

Write-Host ""
if ($script:failed) { Write-Host "$($script:failed) 件の問題があります。" -ForegroundColor Red; exit 1 }
Write-Host "すべて OK。他ホストのブラウザで http://${lan}:$UIPort を開いてください。" -ForegroundColor Cyan
