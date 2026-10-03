# Build (when needed) and start agent-chat-ui on 0.0.0.0:3000.
# NEXT_PUBLIC_API_URL is baked in at build time, so it must be an address that the
# *other host's browser* can reach (this machine's LAN IP), not localhost.
param(
    [string]$HostAddress = "",
    [int]$Port = 3000,
    [int]$LangGraphPort = 2024
)
$ErrorActionPreference = "Stop"
$ui = Join-Path $PSScriptRoot "..\agent-chat-ui"
Set-Location $ui

if (-not $HostAddress) {
    $route = Get-NetRoute -DestinationPrefix "0.0.0.0/0" | Sort-Object RouteMetric | Select-Object -First 1
    $HostAddress = (Get-NetIPAddress -AddressFamily IPv4 -InterfaceIndex $route.InterfaceIndex |
        Where-Object { $_.IPAddress -notlike "169.254.*" } | Select-Object -First 1).IPAddress
}
$apiUrl = "http://${HostAddress}:${LangGraphPort}"
$env:NEXT_PUBLIC_API_URL = $apiUrl
$env:NEXT_PUBLIC_ASSISTANT_ID = "agent"
Write-Host "agent-chat-ui -> LangGraph $apiUrl (assistant: agent)"

$pnpm = @("--yes", "pnpm@10.5.1")
if (-not (Test-Path "node_modules")) { npx @pnpm install --frozen-lockfile }

# Rebuild only when the API URL changed since the last build.
$stamp = ".next\local-agent-api-url.txt"
if (-not (Test-Path ".next\BUILD_ID") -or -not (Test-Path $stamp) -or (Get-Content $stamp -Raw).Trim() -ne $apiUrl) {
    npx @pnpm build
    if ($LASTEXITCODE -ne 0) { throw "agent-chat-ui build failed" }
    Set-Content -Path $stamp -Value $apiUrl -Encoding utf8
}
Write-Host "Open http://${HostAddress}:${Port} from another host."
npx @pnpm start -H 0.0.0.0 -p $Port
