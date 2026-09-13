# Qball 命令行工具(Windows PowerShell 5.1+)
# 用法见 "qball help"
[CmdletBinding()]
param(
  [Parameter(Position = 0)][string]$Command = "help",
  [Parameter(Position = 1)][string]$Arg,
  [int]$Tail = 30,
  [switch]$Purge
)

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$AppDir = $PSScriptRoot
$TaskName = "Qball"
$Repo = "woshi32s/Qball"
$StateDir = if ($env:QBALL_HOME) { $env:QBALL_HOME } else { Join-Path $env:USERPROFILE ".qball" }
$LogFile = Join-Path $StateDir "logs\qball.log"
$VersionFile = Join-Path $StateDir "version.txt"
$PortFile = Join-Path $StateDir "port.txt"

function Find-Exe {
  foreach ($c in @((Join-Path $AppDir "Qball.exe"), (Join-Path $AppDir "dist\Qball.exe"))) {
    if (Test-Path $c) { return $c }
  }
  return $null
}
$Exe = Find-Exe

function Write-Ok($m) { Write-Host "  [OK] $m" -ForegroundColor Green }
function Write-Bad($m) { Write-Host "  [!!] $m" -ForegroundColor Red }
function Write-Tip($m) { Write-Host "  [--] $m" -ForegroundColor Yellow }
function Write-Info($m) { Write-Host $m }

function Get-QballPort {
  $ports = @()
  if (Test-Path $PortFile) {
    $saved = Get-Content $PortFile -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($saved -match '^\d+$') { $ports += [int]$saved }
  }
  $ports += @(8600..8610 | Where-Object { $ports -notcontains $_ })
  foreach ($p in $ports) {
    try {
      $r = Invoke-RestMethod ("http://127.0.0.1:{0}/api/health" -f $p) -TimeoutSec 2
      if ($r.app -eq "qball") { return $p }
    } catch { }
  }
  return $null
}

function Wait-Healthy([int]$TimeoutSec = 40) {
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    $p = Get-QballPort
    if ($p) { return $p }
    Start-Sleep -Milliseconds 500
  }
  return $null
}

function Format-Uptime([int]$sec) {
  if ($sec -ge 3600) { return ("{0} 小时 {1} 分" -f [int]($sec / 3600), [int](($sec % 3600) / 60)) }
  if ($sec -ge 60) { return ("{0} 分 {1} 秒" -f [int]($sec / 60), ($sec % 60)) }
  return "$sec 秒"
}

function Invoke-Schtasks {
  # schtasks 在非管理员下会失败;单独封装,避免 $ErrorActionPreference=Stop 把 stderr 当异常抛出
  param([Parameter(ValueFromRemainingArguments = $true)][string[]]$SchtasksArgs)
  $prev = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    $out = & schtasks @SchtasksArgs 2>&1 | Out-String
    return @{ Code = $LASTEXITCODE; Text = $out.Trim() }
  } finally {
    $ErrorActionPreference = $prev
  }
}

function Get-StartupLnk {
  return (Join-Path ([Environment]::GetFolderPath("Startup")) "Qball.lnk")
}

function Test-Autostart {
  $r = Invoke-Schtasks /Query /TN $TaskName
  if ($r.Code -eq 0) { return $true }
  return (Test-Path (Get-StartupLnk))
}

function Enable-Autostart {
  if (-not $Exe) { Write-Bad "未找到 Qball.exe,请先运行安装脚本 install.ps1"; exit 1 }
  $r = Invoke-Schtasks /Create /F /TN $TaskName /SC ONLOGON /TR "`"$Exe`" --no-browser"
  if ($r.Code -eq 0) { Write-Ok "已开启开机自启(计划任务)"; return }
  $lnk = Get-StartupLnk
  $ws = New-Object -ComObject WScript.Shell
  $sc = $ws.CreateShortcut($lnk)
  $sc.TargetPath = $Exe
  $sc.Arguments = "--no-browser"
  $sc.WorkingDirectory = (Split-Path $Exe -Parent)
  $sc.Save()
  if (Test-Path $lnk) { Write-Ok "已开启开机自启(启动文件夹方式)" }
  else { Write-Bad "设置失败,请手动把 Qball 快捷方式放入启动文件夹"; exit 1 }
}

function Disable-Autostart {
  $null = Invoke-Schtasks /Delete /F /TN $TaskName
  $lnk = Get-StartupLnk
  if (Test-Path $lnk) { Remove-Item $lnk -Force }
  Write-Ok "已关闭开机自启"
}

function Start-Qball {
  $port = Get-QballPort
  if ($port) {
    Write-Info "Qball 已在运行: http://127.0.0.1:$port/"
    Start-Process ("http://127.0.0.1:{0}/" -f $port)
    return
  }
  if (-not $Exe) { Write-Bad "未找到 Qball.exe,请先运行安装脚本 install.ps1"; exit 1 }
  Start-Process -FilePath $Exe -ArgumentList "--no-browser" | Out-Null
  Write-Info "正在启动..."
  $port = Wait-Healthy 40
  if ($port) {
    Write-Ok "已启动: http://127.0.0.1:$port/"
    Start-Process ("http://127.0.0.1:{0}/" -f $port)
  } else {
    Write-Bad "启动超时,查看日志: qball logs"
    exit 1
  }
}

function Stop-Qball {
  $port = Get-QballPort
  if (-not $port) { Write-Info "Qball 未在运行"; return }
  try { Invoke-RestMethod ("http://127.0.0.1:{0}/api/shutdown" -f $port) -Method Post -TimeoutSec 5 | Out-Null } catch { }
  for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Milliseconds 300
    if (-not (Get-QballPort)) { Write-Ok "Qball 已退出"; return }
  }
  Write-Bad "未能正常退出,请在任务管理器中结束 Qball.exe"
  exit 1
}

function Show-Status {
  $port = Get-QballPort
  if ($port) {
    Write-Host "状态    : 运行中"
    Write-Host "地址    : http://127.0.0.1:$port/"
    try {
      $h = Invoke-RestMethod ("http://127.0.0.1:{0}/api/health" -f $port) -TimeoutSec 5
      Write-Host "版本    : $($h.version)"
      Write-Host ("运行时长: " + (Format-Uptime $h.uptime))
      Write-Host "模型    : $($h.model)"
      Write-Host ("语音    : " + $(if ($h.tts) { "开" } else { "关" }))
      Write-Host ("API Key : " + $(if ($h.key) { "已配置" } else { "未配置(打开页面跟随引导填写)" }))
    } catch { }
  } else {
    Write-Host "状态    : 未运行"
  }
  Write-Host ("开机自启: " + $(if (Test-Autostart) { "开" } else { "关" }))
  Write-Host "配置目录: $StateDir"
  if ($Exe) { Write-Host "程序位置: $Exe" } else { Write-Host "程序位置: 未找到 Qball.exe" }
}

function Show-Logs([int]$Lines) {
  if (-not (Test-Path $LogFile)) { Write-Info "暂无日志($LogFile)"; return }
  Get-Content $LogFile -Tail $Lines
}

function Open-Page {
  $port = Get-QballPort
  if (-not $port) { Write-Bad "Qball 未在运行,先执行: qball start"; exit 1 }
  Start-Process ("http://127.0.0.1:{0}/" -f $port)
}

function Invoke-Doctor {
  Write-Host "Qball 体检" -ForegroundColor Cyan
  $bad = 0
  if ($Exe) { Write-Ok "程序: $Exe" } else { Write-Bad "未找到 Qball.exe"; $bad++ }
  $port = Get-QballPort
  if ($port) {
    Write-Ok "运行中: http://127.0.0.1:$port/"
    try {
      $h = Invoke-RestMethod ("http://127.0.0.1:{0}/api/health" -f $port) -TimeoutSec 5
      if ($h.key) { Write-Ok "API Key: 已配置" } else { Write-Tip "API Key: 未配置(首次打开页面时填写)"; }
      if ($h.tts) { Write-Ok "语音合成: 可用" } else { Write-Tip "语音合成: 已关闭" }
    } catch { Write-Tip "健康检查接口无响应" }
  } else {
    Write-Tip "未运行(qball start 启动)"
  }
  $cfg = Join-Path $StateDir "config.json"
  if (Test-Path $cfg) { Write-Ok "配置: $cfg" } else { Write-Tip "尚无配置文件(首次打开页面时自动生成)" }
  if (Test-Path $LogFile) { Write-Ok "日志: $LogFile" } else { Write-Tip "暂无日志" }
  if (Test-Autostart) { Write-Ok "开机自启: 开" } else { Write-Tip "开机自启: 关(qball autostart on 开启)" }
  if ($bad -gt 0) { exit 1 }
}

function Get-RemoteVersion {
  foreach ($u in @(
      "https://cdn.jsdelivr.net/gh/$Repo@main/version.json",
      "https://raw.githubusercontent.com/$Repo/main/version.json")) {
    try { return Invoke-RestMethod $u -TimeoutSec 15 } catch { }
  }
  return $null
}

function Get-LocalVersion {
  $port = Get-QballPort
  if ($port) {
    try { return (Invoke-RestMethod ("http://127.0.0.1:{0}/api/health" -f $port) -TimeoutSec 5).version } catch { }
  }
  if (Test-Path $VersionFile) { return (Get-Content $VersionFile -Raw).Trim() }
  return $null
}

function Invoke-Update {
  $local = Get-LocalVersion
  Write-Info ("当前版本: " + $(if ($local) { $local } else { "未知" }))
  $remote = Get-RemoteVersion
  if (-not $remote -or -not $remote.version) { Write-Bad "无法获取最新版本信息(网络受限?),请稍后再试"; exit 1 }
  if ($local -eq $remote.version) { Write-Ok "已是最新版($($remote.version))"; return }
  Write-Info "发现新版本: $($remote.version),开始下载..."
  $tmp = Join-Path $env:TEMP ("Qball-update-" + $remote.version + ".exe")
  $urls = @($remote.url) + @($remote.mirrors | Where-Object { $_ })
  $ok = $false
  foreach ($u in $urls) {
    try {
      Invoke-WebRequest $u -OutFile $tmp -TimeoutSec 300 -UseBasicParsing
      if ($remote.sha256) {
        $hash = (Get-FileHash $tmp -Algorithm SHA256).Hash.ToLower()
        if ($hash -ne $remote.sha256.ToLower()) { Write-Tip "校验失败,换源重试..."; continue }
      }
      $ok = $true; break
    } catch { Write-Tip "下载失败,换源重试..." }
  }
  if (-not $ok) { Write-Bad "下载失败,请检查网络或稍后重试"; exit 1 }
  if (-not $Exe) { Write-Bad "未找到 Qball.exe"; exit 1 }
  Stop-Qball
  Copy-Item $tmp $Exe -Force
  Remove-Item $tmp -Force -ErrorAction SilentlyContinue
  Write-Ok "已更新到 $($remote.version)"
  Start-Qball
}

function Invoke-Uninstall {
  Write-Info "正在卸载 Qball..."
  $port = Get-QballPort
  if ($port) { Stop-Qball }
  $null = Invoke-Schtasks /Delete /F /TN $TaskName
  $lnk = Get-StartupLnk
  if (Test-Path $lnk) { Remove-Item $lnk -Force }
  $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
  if ($userPath) {
    $parts = $userPath.Split(';') | Where-Object { $_ -and ($_.TrimEnd('\') -ne $AppDir.TrimEnd('\')) }
    [Environment]::SetEnvironmentVariable("Path", ($parts -join ';'), "User")
  }
  if ($Purge -and (Test-Path $StateDir)) {
    Remove-Item $StateDir -Recurse -Force
    Write-Info "已删除配置目录: $StateDir"
  }
  Write-Ok "卸载完成。卸载自启后如目录仍在,可直接删除: $AppDir"
}

function Show-Help {
  Write-Host @"
Qball 命令行工具

用法: qball <命令> [参数]

  qball start                    启动(已在运行则直接打开页面)
  qball stop                     退出 Qball
  qball status                   查看运行状态
  qball doctor                   环境体检
  qball logs [-Tail 50]          查看最近日志(默认 30 行)
  qball open                     打开界面
  qball autostart on|off|status  开机自启
  qball update                   检查并更新到最新版
  qball uninstall [-Purge]       卸载(-Purge 同时删除配置与数据)
"@
}

switch ($Command.ToLower()) {
  "start" { Start-Qball }
  "stop" { Stop-Qball }
  "status" { Show-Status }
  "doctor" { Invoke-Doctor }
  "logs" { Show-Logs $Tail }
  "open" { Open-Page }
  "autostart" {
    switch (($Arg + "").ToLower()) {
      "on" { Enable-Autostart }
      "off" { Disable-Autostart }
      default { Write-Host ("开机自启: " + $(if (Test-Autostart) { "开" } else { "关" })) }
    }
  }
  "update" { Invoke-Update }
  "uninstall" { Invoke-Uninstall }
  default { Show-Help }
}
