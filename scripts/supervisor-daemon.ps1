# Qball 监督守护:每 30 分钟生成一次报告;出现 ~/.qball/reports/.stop 文件即退出
$ErrorActionPreference = "Continue"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$StateDir = if ($env:QBALL_HOME) { $env:QBALL_HOME } else { Join-Path $env:USERPROFILE ".qball" }
$StopFile = Join-Path $StateDir "reports\.stop"
$Supervisor = Join-Path $Root "scripts\supervisor.ps1"

while (-not (Test-Path $StopFile)) {
  & powershell -NoProfile -ExecutionPolicy Bypass -File $Supervisor -Notify | Out-Null
  Start-Sleep -Seconds 1800
}
