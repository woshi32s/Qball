# Qball 全套测试运行器
# 用法:
#   powershell -File tests\run_all.ps1                  # 跑全部(自动起/停夹具)
#   powershell -File tests\run_all.ps1 -Only byok      # 只跑名字包含 byok 的套件
#   powershell -File tests\run_all.ps1 -AppRoot <dir>  # 针对指定代码目录(自进化测试门用)
#   powershell -File tests\run_all.ps1 -KeepRunning    # 跑完不关夹具(调试用)
[CmdletBinding()]
param(
  [string]$AppRoot = "",
  [switch]$KeepRunning,
  [string]$Only = ""
)

$ErrorActionPreference = "Continue"
$TestsDir = $PSScriptRoot
$Artifacts = Join-Path $TestsDir "artifacts"
New-Item -ItemType Directory -Path $Artifacts -Force | Out-Null

if (-not $AppRoot) { $AppRoot = (Resolve-Path (Join-Path $TestsDir "..")).Path }

Write-Host "== 启动测试夹具(app root: $AppRoot) =="
& (Join-Path $TestsDir "serve_fixtures.ps1") -AppRoot $AppRoot
if ($LASTEXITCODE -ne 0) { Write-Host "夹具启动失败"; exit 1 }

$results = @()
function Add-Result($name, $pass, $fail, $exitCode, $note) {
  $script:results += @{ suite = $name; pass = $pass; fail = $fail; exit = $exitCode; note = $note }
}

# ---------- 0) 内联 JS 语法检查 ----------
if (-not $Only -or "syntax" -like "*$Only*") {
  $qballHtml = Join-Path $AppRoot "qball.html"
  $inlineJs = Join-Path $Artifacts "inline_check.js"
  $out = & node (Join-Path $TestsDir "tools\extract_inline.js") $qballHtml $inlineJs 2>&1
  $ok = ($LASTEXITCODE -eq 0)
  if ($ok) { $out2 = & node --check $inlineJs 2>&1; $ok = ($LASTEXITCODE -eq 0); if (-not $ok) { Write-Host $out2 } }
  if ($ok) { Write-Host "PASS  syntax · qball.html 内联脚本"; Add-Result "syntax" 1 0 0 "" }
  else { Write-Host "FAIL  syntax · qball.html 内联脚本"; Add-Result "syntax" 0 1 1 ($out -join " ") }
}

# ---------- 各套件 ----------
$suites = @(
  @{ File = "suites\byok_test.js"; Env = @{} },
  @{ File = "suites\security_test.js"; Env = @{} },
  @{ File = "suites\features_test.js"; Env = @{ EB_BASE = "http://127.0.0.1:8462" } },
  @{ File = "suites\auth_ui_test.js"; Env = @{} },
  @{ File = "suites\cfg_ui_test.js"; Env = @{} },
  @{ File = "suites\onboard2_test.js"; Env = @{} },
  @{ File = "suites\agora_test.js"; Env = @{ EB_BASE = "http://127.0.0.1:8462" } }
)
if ($Only) { $suites = $suites | Where-Object { $_.File -like "*$Only*" } }

foreach ($s in $suites) {
  $file = Join-Path $TestsDir $s.File
  if (-not (Test-Path $file)) { Write-Host "SKIP  $($s.File)(文件不存在)"; continue }
  Write-Host ""
  Write-Host "== $($s.File) =="
  foreach ($k in $s.Env.Keys) { Set-Item -Path ("env:" + $k) -Value $s.Env[$k] }
  $output = & node $file 2>&1 | Out-String
  Write-Host $output.TrimEnd()
  $pass = ([regex]::Matches($output, "(?m)^PASS")).Count
  $fail = ([regex]::Matches($output, "(?m)^FAIL")).Count
  $note = ""
  if ($fail -eq 0 -and $pass -eq 0) { $note = "无断言输出"; $fail = 1 }
  Add-Result $s.File $pass $fail $LASTEXITCODE $note
  foreach ($k in $s.Env.Keys) { Remove-Item -Path ("env:" + $k) -ErrorAction SilentlyContinue }
}

if (-not $KeepRunning) {
  & (Join-Path $TestsDir "serve_fixtures.ps1") -Stop | Out-Null
}

# ---------- 汇总 ----------
Write-Host ""
Write-Host "================ 汇总 ================"
$totalPass = 0; $totalFail = 0
foreach ($r in $results) {
  $totalPass += $r.pass; $totalFail += $r.fail
  $mark = if ($r.fail -eq 0) { "PASS" } else { "FAIL" }
  Write-Host ("{0}  {1}  pass={2} fail={3} {4}" -f $mark, $r.suite, $r.pass, $r.fail, $r.note)
}
Write-Host ("合计: pass={0} fail={1}" -f $totalPass, $totalFail)

$summary = @{
  when = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
  app_root = $AppRoot
  pass = $totalPass
  fail = $totalFail
  ok = ($totalFail -eq 0)
  suites = $results
}
$summary | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $Artifacts "last_run.json") -Encoding UTF8

if ($totalFail -gt 0) { exit 1 }
exit 0
