# Build (when needed) and start agent-chat-ui on 0.0.0.0:3000.
# NEXT_PUBLIC_API_URL is baked in at build time, so it must be an address that the
# *other host's browser* can reach (this machine's LAN IP), not localhost.
param(
    [string]$HostAddress = "",   # default: LAN IPv4 of the default-route interface
    [int]$Port = 3000,
    [int]$LangGraphPort = 2024
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib\common.ps1")
Set-Location (Join-Path (Get-RepoRoot) "agent-chat-ui")

if (-not $HostAddress) { $HostAddress = Get-LanIPv4 }
if (-not $HostAddress) { throw "LAN の IPv4 アドレスを特定できません。-HostAddress で指定してください。" }
$apiUrl = "http://${HostAddress}:${LangGraphPort}"
$env:NEXT_PUBLIC_API_URL = $apiUrl
$env:NEXT_PUBLIC_ASSISTANT_ID = "agent"
# Next.js sends anonymous usage data during builds by default; this app makes no outside connection but search.
$env:NEXT_TELEMETRY_DISABLED = "1"
Write-Host "agent-chat-ui -> LangGraph $apiUrl (assistant: agent)"

$pnpm = @("--yes", "pnpm@10.5.1")
if (-not (Test-Path "node_modules")) { npx @pnpm install --frozen-lockfile }

# Rebuild when the API URL changed or a UI source file is newer than the last build.
$stamp = ".next\local-agent-api-url.txt"
$stale = $true
if ((Test-Path ".next\BUILD_ID") -and (Test-Path $stamp) -and ([IO.File]::ReadAllText((Resolve-Path $stamp))).Trim() -eq $apiUrl) {
    $built = (Get-Item ".next\BUILD_ID").LastWriteTimeUtc
    $sources = @(Get-ChildItem -Recurse -File "src") + @(Get-Item "package.json", "next.config.mjs")
    $stale = [bool]($sources | Where-Object { $_.LastWriteTimeUtc -gt $built } | Select-Object -First 1)
}
if ($stale) {
    npx @pnpm build
    if ($LASTEXITCODE -ne 0) { throw "agent-chat-ui build failed" }
    Write-TextFile (Join-Path (Get-Location) $stamp) $apiUrl
}
Write-Host "Open http://${HostAddress}:${Port} from another host."
npx @pnpm start -H 0.0.0.0 -p $Port
