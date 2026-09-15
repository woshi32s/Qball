# Qball 监督进程:体检并生成进度报告
# 用法:
#   powershell -File scripts\supervisor.ps1            # 生成一次报告
#   powershell -File scripts\supervisor.ps1 -Notify    # 有问题时弹窗提醒
[CmdletBinding()]
param([switch]$Notify)

$ErrorActionPreference = "Continue"
$RepoDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$StateDir = if ($env:QBALL_HOME) { $env:QBALL_HOME } else { Join-Path $env:USERPROFILE ".qball" }
$Reports = Join-Path $StateDir "reports"
New-Item -ItemType Directory -Path $Reports -Force | Out-Null

$problems = @()
$lines = @()
$now = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
$lines += "# Qball 监督报告"
$lines += ""
$lines += "- 时间: $now"
$lines += ""

# ---------- 1) 应用健康 ----------
$appLine = "未运行"
try {
  $h = Invoke-RestMethod "http://127.0.0.1:8600/api/health" -TimeoutSec 4
  if ($h.app -eq "qball") {
    $appLine = "运行中 v$($h.version)(运行 $($h.uptime)s, Key: $(if ($h.key) {'已配置'} else {'未配置'}))"
  }
} catch { $appLine = "未运行或不可达" }
$lines += "## 应用"
$lines += "- 状态: $appLine"
$lines += ""

# ---------- 2) 代码仓库 ----------
$lines += "## 代码"
$branch = (& git -C $RepoDir branch --show-current 2>$null)
$dirty = (& git -C $RepoDir status --porcelain 2>$null | Measure-Object).Count
$lastCommits = (& git -C $RepoDir log --oneline -3 2>$null) -join " / "
$lines += "- 分支: $branch | 未提交改动: $dirty 个文件"
$lines += "- 最近提交: $lastCommits"
$lines += ""

# ---------- 3) 测试结果 ----------
$lines += "## 测试"
$lastRunFile = Join-Path $RepoDir "tests\artifacts\last_run.json"
if (Test-Path $lastRunFile) {
  try {
    $lr = Get-Content $lastRunFile -Raw -Encoding UTF8 | ConvertFrom-Json
    $lines += "- 最近一轮: $($lr.when) -> pass=$($lr.pass) fail=$($lr.fail)"
    if ($lr.fail -gt 0) { $problems += "测试有失败: $($lr.fail) 条(见 $lastRunFile)" }
  } catch { $lines += "- 最近一轮: 读取失败" }
} else {
  $lines += "- 尚无测试记录(运行: powershell -File tests\run_all.ps1)"
}
$lines += ""

# ---------- 4) 进化状态(占位,M1 后接入) ----------
$evoDir = Join-Path $StateDir "evolution"
if (Test-Path $evoDir) {
  $lines += "## 进化"
  $gen = Join-Path $evoDir "scores.jsonl"
  if (Test-Path $gen) {
    $count = (Get-Content $gen | Measure-Object -Line).Lines
    $lines += "- 已进化代数: $count"
  } else {
    $lines += "- 进化目录已创建,暂无记录"
  }
  $lines += ""
}

# ---------- 5) 汇总 ----------
if ($problems.Count -eq 0) {
  $lines += "## 结论"
  $lines += "- 一切正常"
} else {
  $lines += "## 结论"
  foreach ($p in $problems) { $lines += "- [需要关注] $p" }
}

$report = $lines -join "`r`n"
Set-Content (Join-Path $Reports "status-latest.md") $report -Encoding UTF8
Set-Content (Join-Path $Reports "heartbeat.txt") $now -Encoding ASCII
Write-Host $report

if ($Notify -and $problems.Count -gt 0) {
  try {
    $ws = New-Object -ComObject WScript.Shell
    $null = $ws.Popup("Qball 监督提醒:`r`n" + ($problems -join "`r`n"), 25, "Qball 监督", 48)
  } catch {}
}
if ($problems.Count -gt 0) { exit 1 }
exit 0
