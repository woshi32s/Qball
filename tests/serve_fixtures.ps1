# 启动/停止 Qball 测试夹具环境(3 个服务端 + 1 个假模型服务)
# 用法:
#   powershell -File tests\serve_fixtures.ps1                 # 启动(基于当前仓库)
#   powershell -File tests\serve_fixtures.ps1 -AppRoot <dir>  # 基于指定目录(自进化测试门用)
#   powershell -File tests\serve_fixtures.ps1 -Stop           # 停止
[CmdletBinding()]
param(
  [string]$AppRoot = "",
  [switch]$Stop
)

$ErrorActionPreference = "Stop"
$TestsDir = $PSScriptRoot
if (-not $AppRoot) { $AppRoot = (Resolve-Path (Join-Path $TestsDir "..")).Path }
$Runtime = Join-Path $TestsDir ".runtime"
$PidFile = Join-Path $Runtime "pids.json"
$Kit = Join-Path $TestsDir "fixtures\fake_openai_kit.py"

function Stop-Tree($procId) {
  $prev = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try { $null = & taskkill /pid $procId /T /F 2>&1 } catch {}
  finally { $ErrorActionPreference = $prev }
}

function Stop-Fixtures {
  if (Test-Path $PidFile) {
    $pids = Get-Content $PidFile -Raw | ConvertFrom-Json
    foreach ($p in $pids) {
      Stop-Tree $p
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
  }
  # 兜底:按命令行特征清理(整套设备树 + 假模型服务)
  Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and ($_.CommandLine -match "fake_openai_kit\.py" -or $_.CommandLine -match "server\.py --no-browser") } |
    ForEach-Object { Stop-Tree $_.ProcessId }
  Write-Host "[fixtures] stopped"
}

if ($Stop) { Stop-Fixtures; exit 0 }

Stop-Fixtures | Out-Null
Remove-Item $Runtime -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path "$Runtime\app" -Force | Out-Null

$app = Join-Path $Runtime "app"
Copy-Item (Join-Path $AppRoot "server.py") $app -Force
Copy-Item (Join-Path $AppRoot "qball_tools.py") $app -Force
Copy-Item (Join-Path $AppRoot "evolution.py") $app -Force
Copy-Item (Join-Path $AppRoot "README.md") $app -Force
Copy-Item (Join-Path $AppRoot "qball.html") $app -Force
Copy-Item (Join-Path $AppRoot "sw.js") $app -Force
Copy-Item (Join-Path $AppRoot "manifest.webmanifest") $app -Force
Copy-Item (Join-Path $AppRoot "js") $app -Recurse -Force
Copy-Item (Join-Path $AppRoot "fonts") $app -Recurse -Force
Copy-Item (Join-Path $AppRoot "icons") $app -Recurse -Force

function Invoke-Quiet([string]$Exe, [string[]]$Arguments) {
  $prev = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try { $null = & $Exe @Arguments 2>&1 } catch { } finally { $ErrorActionPreference = $prev }
}

# 让测试应用目录成为 git 仓库(进化采纳/回滚依赖)
Invoke-Quiet git @('-C', $app, 'init', '-q')
Invoke-Quiet git @('-C', $app, 'config', 'user.name', 'Qball Test')
Invoke-Quiet git @('-C', $app, 'config', 'user.email', 'test@qball.local')
Invoke-Quiet git @('-C', $app, 'add', '-A')
Invoke-Quiet git @('-C', $app, 'commit', '-q', '-m', 'fixture baseline')

function Start-Hidden($cmdline) {
  $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmdline }
  if ($r.ReturnValue -ne 0) { throw "failed to start: $cmdline" }
  return [int]$r.ProcessId
}

$pids = @()

# 1) 假模型服务
$kitCmd = "cmd /c cd /d `"$app`" && python `"$Kit`" > `"$app\kit.log`" 2>&1"
Write-Host "[fixtures] kit cmd: $kitCmd"
$kitPid = Start-Hidden $kitCmd
Write-Host "[fixtures] kit pid: $kitPid"
$pids += $kitPid

# 2) 三个服务端(端口 / 环境变量与 CI 一致)
$variantDefs = @(
  @{ Name = "c"; Port = 8464; Envs = "set TTS_ENABLED=0&& set DAILY_LLM_LIMIT=1&& set RATE_CHAT_PER_MIN=30"; },
  @{ Name = "a"; Port = 8462; Envs = "set TTS_ENABLED=0&& set B_AI_BASE=http://127.0.0.1:8484/v1&& set B_AI_KEY=sk-fake&& set B_AI_MODEL=gpt-4o-mini"; },
  @{ Name = "b"; Port = 8463; Envs = "set TTS_ENABLED=0&& set B_AI_BASE=http://127.0.0.1:8484/v1&& set B_AI_KEY=sk-fake&& set B_AI_MODEL=gpt-4o-mini&& set ACCESS_CODE=testcode123&& set ADMIN_TOKEN=admintoken456&& set RATE_CHAT_PER_MIN=3&& set DAILY_LLM_LIMIT=5"; }
)
foreach ($v in $variantDefs) {
  $homeDir = "$Runtime\home_$($v.Port)"
  New-Item -ItemType Directory -Path $homeDir -Force | Out-Null
  $cmd = "cmd /c cd /d `"$app`" && set PORT=$($v.Port)&& set QBALL_HOME=$homeDir&& $($v.Envs)&& python server.py --no-browser > `"$Runtime\srv_$($v.Name).log`" 2>&1"
  $pids += Start-Hidden $cmd
}

$pids | ConvertTo-Json | Set-Content $PidFile -Encoding ascii

# 3) 等待就绪
$ok = $true
foreach ($v in @(
  @{ Port = 8484; Url = "http://127.0.0.1:8484/v1/models" },
  @{ Port = 8462; Url = "http://127.0.0.1:8462/api/health" },
  @{ Port = 8463; Url = "http://127.0.0.1:8463/api/health" },
  @{ Port = 8464; Url = "http://127.0.0.1:8464/api/health" })) {
  $ready = $false
  for ($i = 0; $i -lt 40; $i++) {
    try {
      $null = Invoke-WebRequest $v.Url -TimeoutSec 3 -UseBasicParsing
      $ready = $true; break
    } catch { Start-Sleep -Milliseconds 500 }
  }
  if ($ready) { Write-Host "[fixtures] $($v.Port) OK" } else { Write-Host "[fixtures] $($v.Port) DOWN"; $ok = $false }
}
if (-not $ok) { Write-Host "[fixtures] 有服务未就绪,日志见 $Runtime"; exit 1 }
exit 0
