# Qball 安装器(Windows,免管理员)
# 一行安装:
#   iwr -useb https://cdn.jsdelivr.net/gh/woshi32s/Qball@main/install.ps1 | iex
# 带参数(不设开机自启):
#   & ([scriptblock]::Create((iwr -useb https://cdn.jsdelivr.net/gh/woshi32s/Qball@main/install.ps1))) -NoStartup
#
# 本地测试(在源码目录运行,使用本地 dist\Qball.exe):
#   powershell -ExecutionPolicy Bypass -File .\install.ps1 -NoStartup -NoLaunch
[CmdletBinding()]
param(
  [string]$Version = "",
  [string]$Dir = "",
  [switch]$NoStartup,
  [switch]$NoLaunch,
  [switch]$Force
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Repo = "woshi32s/Qball"
$InstallDir = if ($Dir) { $Dir } else { Join-Path $env:LOCALAPPDATA "Qball" }
$ExePath = Join-Path $InstallDir "Qball.exe"

function Write-Step($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Write-Ok($m) { Write-Host "  [OK] $m" -ForegroundColor Green }
function Write-Bad($m) { Write-Host "  [!!] $m" -ForegroundColor Red }

function Download-File($Primary, $Mirrors, $Out, $Sha) {
  foreach ($u in (@($Primary) + @($Mirrors | Where-Object { $_ }))) {
    try {
      Invoke-WebRequest $u -OutFile $Out -TimeoutSec 300 -UseBasicParsing
      if ($Sha) {
        $h = (Get-FileHash $Out -Algorithm SHA256).Hash.ToLower()
        if ($h -ne $Sha.ToLower()) { Write-Host "    校验失败,换源重试..."; continue }
      }
      Unblock-File $Out -ErrorAction SilentlyContinue
      return $true
    } catch { Write-Host "    下载失败,换源重试..." }
  }
  return $false
}

Write-Host ""
Write-Host "Qball 安装器" -ForegroundColor Cyan
Write-Host ""

# ---------- 0. 本地模式(在源码目录运行时使用本地文件) ----------
$localRoot = $PSScriptRoot
$localExe = $null
$localScripts = $null
if ($localRoot) {
  foreach ($cand in @((Join-Path $localRoot "dist\Qball.exe"), (Join-Path $localRoot "Qball.exe"))) {
    if (Test-Path $cand) { $localExe = $cand; break }
  }
  if ($localExe -and (Test-Path (Join-Path $localRoot "qball.ps1")) -and (Test-Path (Join-Path $localRoot "qball.cmd"))) {
    $localScripts = $localRoot
  }
}

# ---------- 1. 版本信息 ----------
$remote = $null
if (-not $localExe) {
  Write-Step "获取最新版本信息..."
  foreach ($u in @("https://raw.githubusercontent.com/$Repo/main/version.json", "https://cdn.jsdelivr.net/gh/$Repo@main/version.json", "https://fastly.jsdelivr.net/gh/$Repo@main/version.json")) {
    try { $remote = Invoke-RestMethod $u -TimeoutSec 20; break } catch { }
  }
  if (-not $remote) { Write-Bad "无法获取版本信息,请检查网络后重试"; exit 1 }
  if (-not $Version) { $Version = $remote.version }
  Write-Ok "最新版本: $Version"
} else {
  if (-not $Version) {
    $vj = Join-Path $localRoot "version.json"
    if (Test-Path $vj) { try { $Version = (Get-Content $vj -Raw -Encoding UTF8 | ConvertFrom-Json).version } catch { } }
    if (-not $Version) { $Version = "dev" }
  }
  Write-Ok "本地安装模式,版本: $Version"
}

New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
$marker = Join-Path $InstallDir "version.txt"
$needDownload = $true
if ((Test-Path $ExePath) -and (Test-Path $marker) -and -not $Force) {
  $cur = (Get-Content $marker -Raw).Trim()
  if ($cur -eq $Version) { $needDownload = $false; Write-Ok "已安装 $Version,跳过下载(可用 -Force 强制重装)" }
}

# ---------- 2. 获取 Qball.exe ----------
$tmpExe = Join-Path $env:TEMP ("Qball-setup-" + $Version + ".exe")
if ($needDownload) {
  if ($localExe) {
    Copy-Item $localExe $tmpExe -Force
    Write-Ok "使用本地构建: $localExe"
  } else {
    Write-Step "下载 Qball.exe ..."
    if (-not (Download-File $remote.url $remote.mirrors $tmpExe $remote.sha256)) {
      Write-Bad "下载失败,请检查网络(可尝试开启代理)后重试"
      exit 1
    }
    Write-Ok "下载完成,校验通过"
  }
}

# ---------- 3. 停止旧版本 ----------
Write-Step "停止正在运行的旧版本..."
$installedCmd = Join-Path $InstallDir "qball.cmd"
if (Test-Path $installedCmd) { $null = cmd /c "`"$installedCmd`" stop 2>nul" }
Get-Process -Name Qball -ErrorAction SilentlyContinue |
  Where-Object { $_.Path -eq $ExePath } |
  ForEach-Object { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 500

# ---------- 4. 安装文件 ----------
if ($needDownload) {
  $retry = 0
  while ($true) {
    try { Copy-Item $tmpExe $ExePath -Force; break }
    catch {
      $retry++
      if ($retry -ge 10) { throw }
      Start-Sleep -Milliseconds 500
    }
  }
  Set-Content $marker $Version -Encoding ascii
  Remove-Item $tmpExe -Force -ErrorAction SilentlyContinue
  Write-Ok "已安装: $ExePath"
}

Write-Step "安装命令行工具..."
if ($localScripts) {
  Copy-Item (Join-Path $localScripts "qball.ps1") $InstallDir -Force
  Copy-Item (Join-Path $localScripts "qball.cmd") $InstallDir -Force
} else {
  foreach ($f in @("qball.ps1", "qball.cmd")) {
    $out = Join-Path $InstallDir $f
    if (-not (Download-File "https://raw.githubusercontent.com/$Repo/main/$f" @("https://cdn.jsdelivr.net/gh/$Repo@main/$f", "https://fastly.jsdelivr.net/gh/$Repo@main/$f") $out $null)) {
      Write-Bad "下载 $f 失败"
      exit 1
    }
  }
}
Write-Ok "qball 命令已就绪"

# ---------- 5. PATH ----------
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
$hasPath = $false
if ($userPath) {
  $hasPath = @($userPath.Split(';') | Where-Object { $_ -and ($_.TrimEnd('\') -ieq $InstallDir.TrimEnd('\')) }).Count -gt 0
}
if (-not $hasPath) {
  $newPath = if ($userPath) { $userPath.TrimEnd(';') + ";" + $InstallDir } else { $InstallDir }
  [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
  Write-Ok "已加入 PATH(新开终端可用 qball 命令)"
} else {
  Write-Ok "PATH 已包含安装目录"
}

# ---------- 6. 开始菜单快捷方式 ----------
try {
  $lnk = Join-Path ([Environment]::GetFolderPath("Programs")) "Qball.lnk"
  $ws = New-Object -ComObject WScript.Shell
  $sc = $ws.CreateShortcut($lnk)
  $sc.TargetPath = $ExePath
  $sc.WorkingDirectory = $InstallDir
  $sc.IconLocation = "$ExePath,0"
  $sc.Save()
  Write-Ok "开始菜单快捷方式已创建"
} catch {
  Write-Host "  [--] 快捷方式创建失败(不影响使用)" -ForegroundColor Yellow
}

# ---------- 7. 开机自启 ----------
if (-not $NoStartup) {
  Write-Step "设置开机自启..."
  $null = cmd /c "`"$installedCmd`" autostart on"
} else {
  Write-Host "  [--] 已跳过开机自启(-NoStartup)" -ForegroundColor Yellow
}

# ---------- 8. 启动 ----------
if (-not $NoLaunch) {
  Write-Step "启动 Qball..."
  Start-Process -FilePath $ExePath
  Write-Ok "浏览器将自动打开(未打开则访问 http://127.0.0.1:8600/)"
} else {
  Write-Host "  [--] 已跳过启动(-NoLaunch)" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Qball v$Version 安装完成" -ForegroundColor Green
Write-Host "  命令: qball start / stop / status / doctor / update / uninstall"
Write-Host "  目录: $InstallDir"
Write-Host "  数据: $env:USERPROFILE\.qball"
Write-Host ""
