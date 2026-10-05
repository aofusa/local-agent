<#
.SYNOPSIS
  Install the PrismML llama.cpp fork (Vulkan): the 27B's router and the chat tab's search workers (idempotent).

.DESCRIPTION
  Bonsai's Q1_0 / PTQ1_0 / PQ2_0 kernels exist only in the PrismML fork, so the search agent never uses stock
  llama.cpp for these models. The same build also runs the Qwen3.8 27B in router mode (scripts\setup-llm.ps1,
  scripts\start-llm.ps1) and provides llama-quantize. Two ways to get llama-server.exe:

    release (default)  download the fork's prebuilt Windows Vulkan zip (config\search_models.json llama_release)
    -FromSource        clone the fork's "prism" branch and build llama-server with -DGGML_VULKAN=ON
                       (needs Visual Studio C++ tools, CMake, Ninja and the Vulkan SDK)

  The result goes under tools\llama-prism\ (git ignored) and its path is saved to .env as BONSAI_LLAMA_SERVER
  (search workers) and LLM_SERVER (the 27B's router).
  ROCm/HIP builds are not used: on the ROG Ally X only the Vulkan build is supported (design doc §4.2).

.EXAMPLE
  .\scripts\setup-llamacpp.ps1
  .\scripts\setup-llamacpp.ps1 -FromSource
  .\scripts\setup-llamacpp.ps1 -Release latest
#>
param(
    [string]$Release = "",          # tag, "latest", or empty = the tag pinned in config\search_models.json
    [switch]$FromSource,
    [string]$SourceRef = "",        # branch / tag for -FromSource (default: the pinned branch "prism")
    [switch]$Force
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib\common.ps1")
Initialize-DotEnv | Out-Null
$root = Get-RepoRoot
$catalog = Read-JsonFile (Join-Path $root "config\search_models.json")
$base = Join-Path $root "tools\llama-prism"
New-Item -ItemType Directory -Force $base | Out-Null

function Test-LlamaServer([string]$Exe) {
    Write-Step "llama-server の確認"
    $version = Invoke-Native $Exe --version
    if ($LASTEXITCODE -ne 0) { throw "llama-server --version に失敗しました: $($version -join ' ')" }
    $version | Where-Object { $_ -match "version|built" } | ForEach-Object { Write-Ok $_.Trim() }
    $devices = Invoke-Native $Exe --list-devices
    $vulkan = $devices | Where-Object { $_ -match "Vulkan" }
    if ($vulkan) { $vulkan | ForEach-Object { Write-Ok $_.Trim() } }
    else { Write-Warn2 "Vulkan デバイスが見つかりません（CPU で動きます）: $($devices -join ' / ')" }
}

if ($FromSource) {
    $ref = if ($SourceRef) { $SourceRef } else { $catalog.llama_branch }
    $src = Join-Path $base "src"
    Write-Step "PrismML fork を取得（$($catalog.llama_repo) $ref）"
    if (-not (Test-Path (Join-Path $src ".git"))) {
        Invoke-Native git clone --depth 1 --branch $ref "https://github.com/$($catalog.llama_repo).git" $src | Select-Object -Last 2 | ForEach-Object { Write-Host "    $_" }
        if ($LASTEXITCODE -ne 0) { throw "git clone に失敗しました" }
    } else {
        Invoke-Native git -C $src fetch --depth 1 origin $ref | Out-Null
        Invoke-Native git -C $src checkout --force FETCH_HEAD | Out-Null
    }
    Write-Ok (Invoke-Native git -C $src log -1 --format="%h %s" | Select-Object -First 1)

    Write-Step "Vulkan ビルド（cmake + ninja + MSVC）"
    if (-not $env:VULKAN_SDK) {
        $sdk = Get-ChildItem "C:\VulkanSDK" -Directory -ErrorAction SilentlyContinue | Sort-Object Name -Descending | Select-Object -First 1
        if (-not $sdk) { throw "Vulkan SDK が見つかりません（winget install KhronosGroup.VulkanSDK）" }
        $env:VULKAN_SDK = $sdk.FullName
    }
    $vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
    $vs = if (Test-Path $vswhere) { Invoke-Native $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath | Select-Object -First 1 } else { $null }
    if (-not $vs) { throw "Visual Studio の C++ ビルドツールが見つかりません" }
    $vcvars = Join-Path $vs "VC\Auxiliary\Build\vcvars64.bat"
    $build = Join-Path $src "build-vulkan"
    $cmd = "call `"$vcvars`" >nul && cmake -S `"$src`" -B `"$build`" -G Ninja -DCMAKE_BUILD_TYPE=Release -DGGML_VULKAN=ON -DLLAMA_CURL=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF && cmake --build `"$build`" --target llama-server llama-quantize -j"
    Invoke-Native cmd.exe /c $cmd | Select-Object -Last 5 | ForEach-Object { Write-Host "    $_" }
    if ($LASTEXITCODE -ne 0) { throw "ビルドに失敗しました（$build）" }
    $exe = Get-ChildItem $build -Recurse -Filter "llama-server.exe" | Select-Object -First 1
    if (-not $exe) { throw "llama-server.exe がビルド出力に見つかりません" }
    $server = $exe.FullName
} else {
    $tag = if ($Release) { $Release } else { $catalog.llama_release }
    if ($tag -eq "latest") {
        $tag = (Invoke-RestMethod "https://api.github.com/repos/$($catalog.llama_repo)/releases/latest" -TimeoutSec 30).tag_name
    }
    $asset = $catalog.llama_asset.Replace("{tag}", $tag)
    $dir = Join-Path $base $tag
    $server = Join-Path $dir "llama-server.exe"
    Write-Step "PrismML fork のリリース（$tag、Vulkan）"
    if ($Force -or -not (Test-Path $server)) {
        $zip = Join-Path $base $asset
        $url = "https://github.com/$($catalog.llama_repo)/releases/download/$tag/$asset"
        Invoke-Native curl.exe -L --fail --retry 3 -o $zip $url | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "ダウンロードに失敗しました: $url" }
        # The pinned release's SHA-256 is in the catalog; another tag uses the digest GitHub publishes.
        $expected = if ($tag -eq $catalog.llama_release) { $catalog.llama_sha256 } else {
            $release = Invoke-RestMethod "https://api.github.com/repos/$($catalog.llama_repo)/releases/tags/$tag" -TimeoutSec 30
            ((ConvertTo-ObjectArray $release.assets | Where-Object { $_.name -eq $asset }).digest -replace '^sha256:', '')
        }
        $actual = Get-FileSha256 $zip
        if (-not $expected) { Write-Warn2 "この版には公開された SHA-256 がありません（sha256 $actual）" }
        elseif ($expected -ne $actual) { Remove-Item $zip; throw "SHA-256 が一致しません（期待 $expected / 実際 $actual）" }
        else { Write-Ok "sha256 $actual" }
        if (Test-Path $dir) { Remove-Item -Recurse -Force $dir }
        Expand-Archive -Path $zip -DestinationPath $dir
        Remove-Item $zip
        $found = Get-ChildItem $dir -Recurse -Filter "llama-server.exe" | Select-Object -First 1
        if (-not $found) { throw "zip に llama-server.exe がありません: $asset" }
        $server = $found.FullName
    }
    Write-Ok $server
}

Test-LlamaServer $server
Set-DotEnvValue "BONSAI_LLAMA_SERVER" $server
Set-DotEnvValue "LLM_SERVER" $server
Write-Ok "BONSAI_LLAMA_SERVER と LLM_SERVER を .env に保存しました"
