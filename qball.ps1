# Qball 命令行工具(Windows PowerShell 5.1+)
# 用法见 "qball help"
[CmdletBinding()]
param(
  [Parameter(Position = 0)][string]$Command = "help",
  [Parameter(Position = 1)][string]$Arg,
  [string]$From,
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
$ModeFile = Join-Path $StateDir "mode.json"

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

function Get-SourceMode {
  if (-not (Test-Path $ModeFile)) { return $null }
  try {
    $m = Get-Content $ModeFile -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($m.mode -ne 'source') { return $null }
    $pyW = Join-Path $m.venv 'Scripts\pythonw.exe'
    $srv = Join-Path $m.app_dir 'server.py'
    if ((Test-Path $pyW) -and (Test-Path $srv)) { return $m }
  } catch { }
  return $null
}

function Write-SourceMode($appDir, $venv) {
  New-Item -ItemType Directory -Path $StateDir -Force | Out-Null
  @{ mode = 'source'; app_dir = $appDir; venv = $venv; since = (Get-Date -Format 's') } |
    ConvertTo-Json | Set-Content $ModeFile -Encoding UTF8
}

function Clear-SourceMode {
  Remove-Item $ModeFile -Force -ErrorAction SilentlyContinue
}

function Invoke-Native {
  # 原生命令安全执行:stderr 不当异常(避免 git/pip 的正常进度输出中断脚本)
  param([string]$Exe, [string[]]$Arguments)
  $prev = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    $out = & $Exe @Arguments 2>&1 | Out-String
    return @{ Code = $LASTEXITCODE; Text = $out.Trim() }
  } finally {
    $ErrorActionPreference = $prev
  }
}

function Find-Python {
  foreach ($c in @('python', 'py')) {
    try {
      $v = & $c --version 2>$null
      if ($LASTEXITCODE -eq 0 -and $v) { return $c }
    } catch { }
  }
  return $null
}

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
  $src = Get-SourceMode
  if ($src) {
    $target = Join-Path $src.venv 'Scripts\pythonw.exe'
    $args = "`"$(Join-Path $src.app_dir 'server.py')`" --no-browser"
    $working = $src.app_dir
  } else {
    if (-not $Exe) { Write-Bad "未找到 Qball.exe,请先运行安装脚本 install.ps1"; exit 1 }
    $target = $Exe
    $args = "--no-browser"
    $working = (Split-Path $Exe -Parent)
  }
  $r = Invoke-Schtasks /Create /F /TN $TaskName /SC ONLOGON /TR "`"$target`" $args"
  if ($r.Code -eq 0) { Write-Ok "已开启开机自启(计划任务)"; return }
  $lnk = Get-StartupLnk
  $ws = New-Object -ComObject WScript.Shell
  $sc = $ws.CreateShortcut($lnk)
  $sc.TargetPath = $target
  $sc.Arguments = $args
  $sc.WorkingDirectory = $working
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
  $src = Get-SourceMode
  if ($src) {
    $pyW = Join-Path $src.venv 'Scripts\pythonw.exe'
    Start-Process -FilePath $pyW -ArgumentList "server.py", "--no-browser" -WorkingDirectory $src.app_dir | Out-Null
    Write-Info "正在启动(源码模式)..."
  } else {
    if (-not $Exe) { Write-Bad "未找到 Qball.exe,请先运行安装脚本 install.ps1"; exit 1 }
    Start-Process -FilePath $Exe -ArgumentList "--no-browser" | Out-Null
    Write-Info "正在启动..."
  }
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
  $src = Get-SourceMode
  $port = Get-QballPort
  if ($port) {
    Write-Host "状态    : 运行中"
    Write-Host "地址    : http://127.0.0.1:$port/"
    try {
      $h = Invoke-RestMethod ("http://127.0.0.1:{0}/api/health" -f $port) -TimeoutSec 5
      Write-Host "版本    : $($h.version)"
      Write-Host ("运行模式: " + $(if ($h.mode -eq 'source') { "源码模式" } else { "安装版" }))
      Write-Host ("运行时长: " + (Format-Uptime $h.uptime))
      Write-Host "模型    : $($h.model)"
      Write-Host ("语音    : " + $(if ($h.tts) { "开" } else { "关" }))
      Write-Host ("API Key : " + $(if ($h.key) { "已配置" } else { "未配置(打开页面跟随引导填写)" }))
    } catch { }
  } else {
    Write-Host "状态    : 未运行"
  }
  Write-Host ("开发者: " + $(if ($src) { "源码模式已启用 ($($src.app_dir))" } else { "未启用(qball dev-setup 开启)" }))
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
      "https://raw.githubusercontent.com/$Repo/main/version.json",
      "https://cdn.jsdelivr.net/gh/$Repo@main/version.json",
      "https://fastly.jsdelivr.net/gh/$Repo@main/version.json")) {
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
  $src = Get-SourceMode
  if ($src) {
    Write-Info "源码模式:从来源仓库更新代码…"
    $r = Invoke-Native git @('-C', $src.app_dir, 'pull', '--ff-only')
    Write-Host $r.Text
    $pyExe = Join-Path $src.venv 'Scripts\python.exe'
    Write-Info "同步依赖…"
    $null = Invoke-Native $pyExe @('-m', 'pip', 'install', '-r', (Join-Path $src.app_dir 'requirements.txt'), '-q')
    Write-Ok "源码已更新(stop 后再 start 生效)"
    return
  }
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

function Invoke-DevSetup {
  [CmdletBinding()]
  param([string]$From)
  $appDir = Join-Path $StateDir 'app'
  $venv = Join-Path $appDir '.venv'
  if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Write-Bad "需要 git,请先安装: https://git-scm.com"; exit 1 }
  if (-not $From) { $From = 'https://github.com/woshi32s/Qball.git' }
  $hasRepo = (Test-Path (Join-Path $appDir '.git')) -and (Test-Path (Join-Path $appDir 'server.py'))
  if ($hasRepo) {
    Write-Info "更新源码: $appDir"
    $r = Invoke-Native git @('-C', $appDir, 'pull', '--ff-only')
    Write-Host $r.Text
  } else {
    Write-Info "克隆源码: $From"
    if (Test-Path $appDir) { Remove-Item $appDir -Recurse -Force -ErrorAction SilentlyContinue }
    $r = Invoke-Native git @('clone', '--depth', '1', $From, $appDir)
    if (-not (Test-Path (Join-Path $appDir 'server.py'))) {
      Write-Bad "克隆失败:"
      Write-Host $r.Text
      exit 1
    }
  }
  $py = Find-Python
  if (-not $py) { Write-Bad "未找到 Python,请先安装 Python 3.10+(https://www.python.org)"; exit 1 }
  $pyExe = Join-Path $venv 'Scripts\python.exe'
  if (-not (Test-Path $pyExe)) {
    Write-Info "创建独立 Python 环境…"
    $null = Invoke-Native $py @('-m', 'venv', $venv)
    if (-not (Test-Path $pyExe)) { Write-Bad "创建虚拟环境失败"; exit 1 }
  }
  Write-Info "安装依赖(首次约 1-3 分钟)…"
  $null = Invoke-Native $pyExe @('-m', 'pip', 'install', '--upgrade', 'pip', '-q')
  $r = Invoke-Native $pyExe @('-m', 'pip', 'install', '-r', (Join-Path $appDir 'requirements.txt'), '-q')
  if ($r.Code -ne 0) {
    Write-Tip "默认源不可用,切换国内镜像重试…"
    $r = Invoke-Native $pyExe @('-m', 'pip', 'install', '-r', (Join-Path $appDir 'requirements.txt'), '-q',
                                '-i', 'https://pypi.tuna.tsinghua.edu.cn/simple')
  }
  if ($r.Code -ne 0) { Write-Bad "依赖安装失败,请检查网络后重试"; Write-Host $r.Text; exit 1 }
  Write-SourceMode $appDir $venv
  Write-Ok "源码模式已启用"
  Write-Host "  源码目录: $appDir"
  Write-Host "  现在可用: qball start(以源码模式运行)"
  Write-Host "  恢复安装版: qball devmode off"
}

function Invoke-DevMode {
  param([string]$OnOff)
  switch (($OnOff + '').ToLower()) {
    "off" {
      Clear-SourceMode
      Write-Ok "已恢复安装版模式(下次 qball start 生效;正在运行的先 qball stop)"
    }
    default {
      $src = Get-SourceMode
      if ($src) { Write-Ok "源码模式已启用: $($src.app_dir)" }
      else { Write-Tip "未启用源码模式。运行 qball dev-setup 开启(为自我进化做准备)" }
    }
  }
}

function Invoke-Run {
  param([string]$Task)
  if (-not $Task) { Write-Tip '用法: qball run "任务描述"(非交互执行,完成后打印结果与产物)'; return }
  $port = Get-QballPort
  if (-not $port) { Write-Bad "Qball 未在运行(先 qball start)"; exit 1 }
  Write-Tip "任务执行中(可能需要几分钟,请稍候)…"
  $body = @{ task = $Task } | ConvertTo-Json -Compress
  try {
    $r = Invoke-RestMethod "http://127.0.0.1:$port/api/run" -Method Post `
      -ContentType "application/json; charset=utf-8" `
      -Body ([Text.Encoding]::UTF8.GetBytes($body)) -TimeoutSec 3600
  } catch {
    Write-Bad ("执行失败: " + $_.Exception.Message)
    exit 1
  }
  if ($r.text) { Write-Host ""; Write-Host $r.text }
  if ($r.error) { Write-Bad ("执行出错: " + $r.error) }
  if ($r.deliverables -and $r.deliverables.Count -gt 0) { Write-Ok ("产物: " + ($r.deliverables -join "、")) }
  if ($r.tools -and $r.tools.Count -gt 0) { Write-Tip ("工具调用: " + ($r.tools -join "、")) }
}

function Invoke-Evolve {
  param([string]$Action = "status")  if (($Action + '').ToLower() -eq "ideas") {
    $f = Join-Path $HOME ".qball\evolution\reports\ux-ideas.md"
    if (Test-Path -LiteralPath $f) {
      Get-Content -LiteralPath $f -Encoding UTF8 -TotalCount 80 | ForEach-Object { Write-Host $_ }
    } else {
      Write-Tip "还没有体验建议(进化跑过体验类任务后会自动写入)"
    }
    return
  }
  $port = Get-QballPort
  if (-not $port) { Write-Bad "Qball 未在运行(先 qball start)"; exit 1 }
  $base = "http://127.0.0.1:$port"
  if (($Action + '').ToLower() -eq "status" -or -not $Action) {
    try { $st = Invoke-RestMethod "$base/api/evolution/status" -TimeoutSec 8 } catch { Write-Bad "读取失败"; exit 1 }
    if (-not $st.available) { Write-Bad "进化模块不可用"; exit 1 }
    Write-Host ("启用    : " + $(if ($st.enabled) { "是" } else { "否" }))
    Write-Host ("运行    : " + $(if ($st.running) { "是" } else { "否" }) + $(if ($st.paused) { "(已暂停)" } else { "" }))
    Write-Host ("代数    : " + $st.generation + "  | 最近得分: " + $st.last_score)
    Write-Host ("模型    : 执行 " + $st.executor_model + " / 评审 " + $st.judge_model)
    Write-Host ("模式    : " + $(if ($st.source_mode) { "源码" } else { "安装版(需 dev-setup)" }))
    Write-Host ("今日调用: " + $st.calls_today)
    if ($st.recent -and $st.recent.Count -gt 0) {
      Write-Host "最近几代:"
      $st.recent | Select-Object -Last 5 | ForEach-Object { Write-Host ("  #" + $_.gen + "  " + $_.title + "  score=" + $_.score) }
    }
    if ($st.last_error) { Write-Tip ("上次错误: " + $st.last_error) }
    return
  }
  $map = @{ start = "enable"; stop = "disable"; pause = "pause"; resume = "resume"; once = "run_once"; revert = "revert" }
  $act = $map[($Action + '').ToLower()]
  if (-not $act) { Write-Tip "用法: qball evolve status|start|stop|pause|resume|once|revert|ideas"; return }
  try {
    $null = Invoke-RestMethod "$base/api/evolution/control" -Method Post -ContentType "application/json" -Body (@{ action = $act } | ConvertTo-Json -Compress) -TimeoutSec 80
    Write-Ok ("已执行: " + $Action)
  } catch {
    $b = ''
    try { $sr = New-Object IO.StreamReader($_.Exception.Response.GetResponseStream()); $b = $sr.ReadToEnd() } catch { }
    Write-Bad ("失败: " + $b)
  }
}

function Invoke-Uninstall {
  Write-Info "正在卸载 Qball..."
  $port = Get-QballPort
  if ($port) { Stop-Qball }
  $null = Invoke-Schtasks /Delete /F /TN $TaskName
  $lnk = Get-StartupLnk
  if (Test-Path $lnk) { Remove-Item $lnk -Force }
  Clear-SourceMode
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
  qball update                   检查并更新(源码模式下 git pull)
  qball dev-setup [-From 路径]   开启源码模式(自进化前提;默认从 GitHub 克隆)
  qball devmode on|off           查看/关闭源码模式
  qball run "任务描述"           非交互执行一个任务(结果与产物直接打印;脚本化用)
  qball evolve [子命令]          自我进化:status|start|stop|pause|resume|once|revert|ideas
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
  "dev-setup" { Invoke-DevSetup -From $(if ($From) { $From } else { $Arg }) }
  "devmode" { Invoke-DevMode $Arg }
  "evolve" { Invoke-Evolve $Arg }
  "run" { Invoke-Run $Arg }
  "uninstall" { Invoke-Uninstall }
  default { Show-Help }
}
