# Build the cirka CUI (the client-side CUI in client\) in release mode and pack it as
# dist\cirka-<version>-<os>-<arch>.zip. Needs Rust 1.85 or later (https://rustup.rs).
# macOS / Linux: `cd client && cargo build --release` (the binary is target/release/cirka).
param(
    [switch]$NoZip
)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
    Write-Error "cargo が見つかりません。Rust（https://rustup.rs）を入れてください"
}
Push-Location client
try {
    cargo build --release
    if ($LASTEXITCODE -ne 0) { throw "cargo build が失敗しました（終了コード $LASTEXITCODE）" }
} finally {
    Pop-Location
}
$exe = Join-Path "client" "target\release\cirka.exe"
if (-not (Test-Path $exe)) { throw "$exe がありません" }
$version = (& $exe --version).Split(" ")[-1]
Write-Host "built $exe ($version)"
if ($NoZip) { return }
$arch = if ([Environment]::Is64BitOperatingSystem) { "x86_64" } else { "x86" }
if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64") { $arch = "aarch64" }
New-Item -ItemType Directory -Force dist | Out-Null
$zip = Join-Path "dist" "cirka-$version-windows-$arch.zip"
Compress-Archive -Force -Path $exe, "LICENSE-MIT", "LICENSE-APACHE" -DestinationPath $zip
Write-Host "packed $zip"
Write-Host "PATH の通った場所に cirka.exe を置き、'cirka config set host http://<ホストの LAN IP>:2024' で接続先を設定してください"
