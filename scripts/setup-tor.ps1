<#
.SYNOPSIS
  Install the Tor Expert Bundle (Windows x86_64) for the chat tab's web search (idempotent).

.DESCRIPTION
  Downloads the latest stable tor-expert-bundle from dist.torproject.org, checks its SHA-256 against the
  release's sha256sums-signed-build.txt, and extracts it to tools\tor\bin (git ignored). The configuration is
  tools\tor\torrc (tracked): SOCKS on 127.0.0.1:9050 only. The tor.exe path is saved to .env as TOR_EXE.
  Start it with scripts\start-tor.ps1 (scripts\start-all.ps1 does this).

.EXAMPLE
  .\scripts\setup-tor.ps1
  .\scripts\setup-tor.ps1 -Version 15.0.24
#>
param(
    [string]$Version = "",
    [switch]$Force
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib\common.ps1")
Initialize-DotEnv | Out-Null
$root = Get-RepoRoot
$torDir = Join-Path $root "tools\tor"
$binDir = Join-Path $torDir "bin"
$dist = "https://dist.torproject.org/torbrowser"

$existing = Get-ChildItem $binDir -Recurse -Filter "tor.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($existing -and -not $Force -and -not $Version) {
    Write-Step "Tor"
    Write-Ok "導入済み: $($existing.FullName)"
    Set-DotEnvValue "TOR_EXE" $existing.FullName
    return
}

Write-Step "Tor Expert Bundle の版"
if (-not $Version) {
    $index = (Invoke-WebRequest "$dist/" -UseBasicParsing -TimeoutSec 30).Content
    # Stable versions only (alpha versions contain "a").
    $Version = [regex]::Matches($index, 'href="(\d+\.\d+(?:\.\d+)*)/"') | ForEach-Object { $_.Groups[1].Value } |
        Sort-Object { [version]($_ + (".0" * (3 - ($_.Split('.').Count - 1)))) } | Select-Object -Last 1
}
if (-not $Version) { throw "Tor の版を特定できません。-Version で指定してください" }
Write-Ok $Version

$file = "tor-expert-bundle-windows-x86_64-$Version.tar.gz"
$archive = Join-Path $torDir $file
New-Item -ItemType Directory -Force $torDir | Out-Null
Write-Step "ダウンロードと SHA-256 の確認"
Invoke-Native curl.exe -L --fail --retry 3 -o $archive "$dist/$Version/$file" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "ダウンロードに失敗しました: $dist/$Version/$file" }
$sums = (Invoke-WebRequest "$dist/$Version/sha256sums-signed-build.txt" -UseBasicParsing -TimeoutSec 30).Content
if ($sums -is [byte[]]) { $sums = [Text.Encoding]::UTF8.GetString($sums) }
$expected = ($sums -split "`n" | Where-Object { $_ -match [regex]::Escape($file) + '\s*$' } | Select-Object -First 1) -replace '\s.*$', ''
$actual = Get-FileSha256 $archive
if (-not $expected -or $expected.ToLowerInvariant() -ne $actual) { Remove-Item $archive; throw "SHA-256 が一致しません（期待 $expected / 実際 $actual）" }
Write-Ok "sha256 $actual"

Write-Step "展開（$binDir）"
if (Test-Path $binDir) { Remove-Item -Recurse -Force $binDir }
New-Item -ItemType Directory -Force $binDir | Out-Null
# Windows bsdtar (a Git Bash tar on PATH cannot read C:\ paths).
& (Join-Path $env:SystemRoot "System32\tar.exe") -xzf $archive -C $binDir
if ($LASTEXITCODE -ne 0) { throw "展開に失敗しました" }
Remove-Item $archive
$exe = Get-ChildItem $binDir -Recurse -Filter "tor.exe" | Select-Object -First 1
if (-not $exe) { throw "tor.exe が見つかりません" }
Write-Ok (Invoke-Native $exe.FullName --version | Select-Object -First 1)
Set-DotEnvValue "TOR_EXE" $exe.FullName
Write-Ok "TOR_EXE を .env に保存しました。起動は .\scripts\start-tor.ps1"
