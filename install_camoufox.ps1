# install_camoufox.ps1 - Camoufox 浏览器引擎 安装/修复脚本
#
# 三种安装路径（按优先级）：
#   1. 已安装           -> 提示已就绪，跳过
#   2. 离线 zip         -> 若 tools\camoufox\ 下存在 camoufox-*.zip（用户手动下载），离线解压安装，无需联网
#   3. 在线下载         -> 注入 gh token 调 camoufox fetch；失败则打印直链，提示放 zip 到 tools\camoufox\ 后重跑
#
# 用法:
#   powershell -NoProfile -ExecutionPolicy Bypass -File install_camoufox.ps1

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot
$VENV_PY = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$ZipDir   = Join-Path $ProjectRoot "tools\camoufox"

function Get-CamoufoxInstallDir {
    # 优先问 camoufox 包（跨平台最稳），失败则回退 Windows 默认路径
    if (Test-Path $VENV_PY) {
        $dir = & $VENV_PY -c "from camoufox.pkgman import INSTALL_DIR; print(INSTALL_DIR)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $dir) { return $dir.Trim() }
    }
    return Join-Path $env:LOCALAPPDATA "camoufox\camoufox\Cache"
}

function Test-CamoufoxInstalled {
    param([string]$InstallDir)
    $browsers = Join-Path $InstallDir "browsers"
    if (-not (Test-Path $browsers)) { return $false }
    return [bool](Get-ChildItem $browsers -Recurse -Filter "camoufox.exe" -ErrorAction SilentlyContinue | Select-Object -First 1)
}

# 离线安装：解压 zip -> 计算 sha8 -> 注册 version.json / config.json / .0.5_FLAG
function Install-Offline {
    param([string]$ZipPath, [string]$InstallDir)

    $zipName = [IO.Path]::GetFileNameWithoutExtension($ZipPath)
    if ($zipName -notmatch '^camoufox-(.+)') {
        Write-Host "[CAMOUFOX] 无法识别文件名（需形如 camoufox-152.0.4-beta.28-win.x86_64.zip）: $zipName"
        return $false
    }
    # camoufox-152.0.4-beta.28-win.x86_64.zip -> verstr = 152.0.4-beta.28
    $verstr = $Matches[1]
    $verstr = $verstr -replace '-(win|linux|mac)(\..*)?$', ''

    $idx = $verstr.IndexOf('-')
    if ($idx -le 0) {
        Write-Host "[CAMOUFOX] 版本串无法解析: $verstr"
        return $false
    }
    $version = $verstr.Substring(0, $idx)          # 152.0.4
    $build   = $verstr.Substring($idx + 1)         # beta.28

    Write-Host "[CAMOUFOX] 计算 SHA256..."
    $sha  = (Get-FileHash -Algorithm SHA256 $ZipPath).Hash.ToLower()
    $sha8 = $sha.Substring(0, 8)

    $folder  = "$version-$build-$sha8"             # 152.0.4-beta.28-386fc2f4
    $repoDir = Join-Path $InstallDir "browsers\official"
    $dest    = Join-Path $repoDir $folder
    if (-not (Test-Path $repoDir)) { New-Item -ItemType Directory -Force -Path $repoDir | Out-Null }

    Write-Host "[CAMOUFOX] 解压到: $dest"
    Expand-Archive -LiteralPath $ZipPath -DestinationPath $dest -Force

    # 若 zip 内含顶层目录（camoufox-win64/...），把内容提升到版本根目录
    $exe = Join-Path $dest "camoufox.exe"
    if (-not (Test-Path $exe)) {
        $nested = Get-ChildItem $dest -Directory | Select-Object -First 1
        if ($nested) {
            $nestedExe = Join-Path $nested.FullName "camoufox.exe"
            if (Test-Path $nestedExe) {
                Write-Host "[CAMOUFOX] zip 含顶层目录，提升内容..."
                Get-ChildItem $nested.FullName | Move-Item -Destination $dest -Force
                Remove-Item $nested.FullName -Recurse -Force
            }
        }
    }

    # version.json（list_installed 依赖）—— 必须无 BOM，orjson 读到 BOM 会报错
    $meta = [ordered]@{
        version          = $version
        build            = $build
        prerelease       = $false
        asset_id         = $null
        asset_size       = $null
        asset_updated_at = $null
        sha256           = $sha
        created_at       = (Get-Date).ToString("o")
    }
    [IO.File]::WriteAllText((Join-Path $dest "version.json"), ($meta | ConvertTo-Json), [Text.UTF8Encoding]::new($false))

    # config.json（记录 active_version）—— 同样无 BOM
    $config = [ordered]@{ active_version = "browsers/official/$folder" }
    [IO.File]::WriteAllText((Join-Path $InstallDir "config.json"), ($config | ConvertTo-Json), [Text.UTF8Encoding]::new($false))

    # .0.5_FLAG 兼容标记
    New-Item -ItemType File -Force -Path (Join-Path $InstallDir ".0.5_FLAG") | Out-Null

    Write-Host "[CAMOUFOX] 离线安装完成: v$verstr ($sha8)"
    return $true
}

# ---------------- 主流程 ----------------
$installDir = Get-CamoufoxInstallDir
Write-Host "[CAMOUFOX] 安装目录: $installDir"

if (Test-CamoufoxInstalled -InstallDir $installDir) {
    Write-Host "[CAMOUFOX] 浏览器引擎已就绪，无需安装"
    exit 0
}

# 1) 离线 zip
$zip = Get-ChildItem -Path $ZipDir -Filter "camoufox-*.zip" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($zip) {
    Write-Host "[CAMOUFOX] 检测到离线安装包: $($zip.FullName)"
    if (Install-Offline -ZipPath $zip.FullName -InstallDir $installDir) { exit 0 }
    Write-Host "[CAMOUFOX] 离线安装失败，尝试在线下载..."
} else {
    if (-not (Test-Path $ZipDir)) { New-Item -ItemType Directory -Force -Path $ZipDir | Out-Null }
    Write-Host "[CAMOUFOX] tools\camoufox\ 下未发现 zip，走在线下载"
}

# 2) 在线下载（注入 gh token 绕 GitHub API 限流）
if (Get-Command gh -ErrorAction SilentlyContinue) {
    $token = gh auth token 2>$null
    if ($token) { $env:GITHUB_TOKEN = $token }
}
& $VENV_PY -m camoufox fetch
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "================================================================"
    Write-Host " Camoufox 浏览器引擎下载失败（网络差 / GitHub 限流常见）"
    Write-Host ""
    Write-Host " 手动方案：用浏览器下载以下 zip，然后放到项目 tools\camoufox\ 目录，"
    Write-Host "           再双击 启动.bat（或重跑本脚本）即可离线安装。"
    Write-Host ""
    Write-Host "  最新版: https://github.com/daijro/camoufox/releases/latest"
    Write-Host "          选 camoufox-*-win.x86_64.zip"
    Write-Host ""
    Write-Host "  说明:   zip 须为 win.x86_64 平台；下载后文件名保持 camoufox-*.zip"
    Write-Host "          （例如 camoufox-152.0.4-beta.28-win.x86_64.zip）"
    Write-Host "================================================================"
    exit 1
}
Write-Host "[CAMOUFOX] 在线安装完成"
exit 0
