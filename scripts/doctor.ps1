<#
.SYNOPSIS
  Check that the LLM router (llama.cpp), ComfyUI, Tor, the search models, LangGraph and agent-chat-ui are configured
  and reachable. The chat tab's search checks are warnings: the image tab works without them.
  Exit code 1 when a required check fails.
#>
param(
    [int]$LLMPort = 0,
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
$modelKey = $envValues["LLM_MODEL"]
$ckptName = $envValues["CKPT_NAME"]
$workflow = Read-JsonFile (Join-Path (Get-RepoRoot) "workflows\furry_ja_api.json")
if (-not $modelKey) { $modelKey = $workflow.llm_backend.inputs.model }
if (-not $ckptName) { $ckptName = $workflow.ckpt.inputs.ckpt_name }
if (-not $LLMPort) { $LLMPort = [int](Get-DotEnvValue "LLM_PORT" "8080") }

Write-Step "LLM ルータ（llama.cpp）"
Check "llama-server (LLM_SERVER)" {
    $exe = Get-LlamaServer
    if (-not $exe) { throw "未導入（scripts\setup-llamacpp.ps1）" }
    (Invoke-Native $exe --version | Where-Object { $_ -match "version" } | Select-Object -First 1).Trim()
}
Check "preset (LLM_PRESET)" {
    $preset = Get-DotEnvValue "LLM_PRESET" (Join-Path (Get-RepoRoot) "tools\llm\models.ini")
    if (-not (Test-Path $preset)) { throw "ありません（scripts\setup-llm.ps1）" }
    $text = [IO.File]::ReadAllText($preset)
    if ($text -notmatch "\[$([regex]::Escape($modelKey))\]") { throw "$modelKey の節がありません（scripts\setup-llm.ps1）" }
    foreach ($key in "model", "mmproj") {
        if ($text -match "(?m)^$key\s*=\s*(.+)$" -and -not (Test-Path $Matches[1].Trim())) { throw "$key のファイルがありません: $($Matches[1].Trim())" }
    }
    $preset
}
Check "router is loopback only" { Assert-LoopbackOnly $LLMPort }
Check "model $modelKey" {
    $models = @((Get-Json "http://127.0.0.1:$LLMPort/models").data)
    $m = $models | Where-Object { $_.id -eq $modelKey } | Select-Object -First 1
    if (-not $m) { throw "ルータのプリセットにありません（scripts\setup-llm.ps1 の後に start-llm.ps1 を再起動）" }
    if ($m.architecture.input_modalities -notcontains "image") { throw "画像入力が無効（mmproj が無い）" }
    "status=$($m.status.value), image input"
}
Check "workflow uses $modelKey at http://127.0.0.1:$LLMPort/v1" {
    $backend = $workflow.llm_backend.inputs
    if ($backend.model -ne $modelKey) { throw "workflows は $($backend.model)（scripts\setup-llm.ps1 が作り直します）" }
    if ($backend.base_url -ne "http://127.0.0.1:$LLMPort/v1") { throw "workflows の接続先は $($backend.base_url)" }
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
              "FurryJaEjectLLM", "FurryJaSplitTags", "FurryJaCheckpointLoaderAfterEject"
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

Write-Step "チャットタブの検索（Tor / PrismML llama.cpp / Bonsai）"
Check "Tor SOCKS 127.0.0.1:9050" {
    $addr = Assert-LoopbackOnly 9050
    $socks = Get-DotEnvValue "TOR_SOCKS_URL" "socks5h://127.0.0.1:9050"
    if (-not $socks.StartsWith("socks5h://")) { throw "TOR_SOCKS_URL は socks5h:// にしてください（DNS 漏れ）" }
    $addr
} -Optional
Check "llama-server for the search workers (BONSAI_LLAMA_SERVER)" {
    $exe = Get-DotEnvValue "BONSAI_LLAMA_SERVER"
    if (-not $exe -or -not (Test-Path $exe)) { throw "未導入（scripts\setup-llamacpp.ps1）" }
    $version = (Invoke-Native $exe --version | Where-Object { $_ -match "version" } | Select-Object -First 1)
    $vulkan = Invoke-Native $exe --list-devices | Where-Object { $_ -match "Vulkan" } | Select-Object -First 1
    if (-not $vulkan) { throw "Vulkan デバイスが見えません: $version" }
    "$($version.Trim()) / $($vulkan.Trim())"
} -Optional
Check "search models (BONSAI_MODELS_DIR)" {
    $dir = Get-DotEnvValue "BONSAI_MODELS_DIR" (Join-Path (Get-RepoRoot) "tools\models")
    $catalog = Read-JsonFile (Join-Path (Get-RepoRoot) "config\search_models.json")
    $models = @(ConvertTo-ObjectArray $catalog.models)
    $present = @($models | Where-Object { (Test-Path (Join-Path $dir $_.file)) -and (Get-Item (Join-Path $dir $_.file)).Length -eq [int64]$_.size })
    $absent = @($models | Where-Object { $_ -notin $present } | ForEach-Object { $_.id })
    if (-not $present) { throw "1 つもありません（scripts\setup-search-models.ps1）" }
    if ($absent) { throw "$($present.Count)/$($models.Count)。無い: $($absent -join ', ')（自動選択から外れます）" }
    "$($present.Count)/$($models.Count)"
} -Optional
Check "probe rank (tools\bonsai\rank.json)" {
    $rankPath = Get-DotEnvValue "BONSAI_RANK" (Join-Path (Get-RepoRoot) "tools\bonsai\rank.json")
    if (-not [IO.Path]::IsPathRooted($rankPath)) { $rankPath = Join-Path (Get-RepoRoot) $rankPath }
    if (-not (Test-Path $rankPath)) { throw "未検証（scripts\probe-bonsai.ps1。無くても既定の順で動きます）" }
    $rank = Read-JsonFile $rankPath
    ($rank.order.PSObject.Properties | ForEach-Object { "$($_.Name)=$(@($_.Value)[0])" }) -join " "
} -Optional
Check "no orphan llama-server" {
    # The 27B's router (listening on LLM_PORT) and the model processes it starts are not search workers.
    $router = @(Get-NetTCPConnection -State Listen -LocalPort $LLMPort -ErrorAction SilentlyContinue | ForEach-Object { $_.OwningProcess })
    $children = @(Get-CimInstance Win32_Process -Filter "Name='llama-server.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $router -contains $_.ParentProcessId } | ForEach-Object { $_.ProcessId })
    $orphans = @(Get-Process llama-server -ErrorAction SilentlyContinue | Where-Object { $_.Id -notin ($router + $children) })
    if ($orphans) { throw "残っています: PID $($orphans.Id -join ', ')（検索中でなければ Stop-Process で止めてください）" }
    "none"
} -Optional

Write-Step "チャットタブのコード実行（Docker サンドボックス、思考モードのみ）"
Check "docker (Linux engine)" {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw "docker コマンドが無い（コードは書くが実行しない。Docker Desktop も起動しない）" }
    $os = Invoke-Native docker version --format "{{.Server.Os}}" | Select-Object -First 1
    if ($LASTEXITCODE -ne 0) { throw "エンジンに接続できない（承認した実行のときに Docker Desktop を起動します）" }
    if ("$os".Trim() -ne "linux") { throw "エンジンが $os（Linux コンテナに切り替え）" }
    "linux"
} -Optional
Check "image python:3.12-slim" {
    Invoke-Native docker image inspect python:3.12-slim | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "未取得（scripts\setup-sandbox.ps1）" }
    "ok"
} -Optional

Write-Step "LangGraph / agent-chat-ui"
$lan = Get-LanIPv4
Check "LangGraph graphs 'agent' (image) and 'chat' on :$LangGraphPort" {
    $assistants = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$LangGraphPort/assistants/search" `
        -ContentType "application/json" -Body "{}" -TimeoutSec 10
    $ids = @((ConvertTo-ObjectArray $assistants) | ForEach-Object { $_.graph_id })
    $absent = @("agent", "chat") | Where-Object { $_ -notin $ids }
    if ($absent) { throw "graph_id がありません: $($absent -join ', ')（LangGraph を再起動）" }
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
