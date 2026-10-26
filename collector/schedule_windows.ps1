# 수집기 정기 실행을 Windows 작업 스케줄러에 등록한다 (doc-pipeline §11 D3: 지금은 이 PC, 나중에 M1으로 이관).
#
#   powershell -ExecutionPolicy Bypass -File collector\schedule_windows.ps1 -WeeklyDay Tuesday -WeeklyTime 16:00 -AwsDay Thursday -AwsTime 16:00
#   powershell -ExecutionPolicy Bypass -File collector\schedule_windows.ps1 -Remove
#
# - 로그온한 동안에만 돈다 (비밀번호를 저장하지 않는다)
# - 예약 시각에 PC가 꺼져 있었으면 다음에 켜졌을 때 바로 돈다 (StartWhenAvailable) — 놓친 주가 생기면 run.json의 시각으로 드러난다
# - 주간 작업의 사슬(수집 → 얼림 → 등급 → 관문 → detect 입력 → 게시)은 이제 collector/weekly.py에 있다 — 여기는 그것을 한 줄로 부를 뿐 (맥은 schedule_mac.py)
# - 출력: collector\out\<UTC 시각>-<label>\ , 마지막 실행 로그: collector\out\last-<label>.log
param(
    [string]$WeeklyDay = "Tuesday", [string]$WeeklyTime = "16:00",
    [string]$AwsDay = "Thursday",  [string]$AwsTime = "16:00",
    [switch]$Remove
)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$python = (Get-Command python).Source
$names = @("JHS-Collector-Weekly", "JHS-Collector-AWS")

if ($Remove) {
    foreach ($n in $names) { Unregister-ScheduledTask -TaskName $n -Confirm:$false -ErrorAction SilentlyContinue }
    "removed: $($names -join ', ')"
    exit 0
}

function Register-Collector($name, $day, $at, $label, $py, $module, $collectorArgs, $minutes = 45) {
    $cmd = "Set-Location '$repo'; `$env:PYTHONIOENCODING='utf-8'; & '$py' -m $module $collectorArgs *> 'collector\out\last-$label.log'"
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -WindowStyle Hidden -Command `"$cmd`"" -WorkingDirectory $repo
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $day -At $at
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes $minutes) -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Description "Decision Radar 수집기 ($label) — collector/README.md" -Force | Out-Null
    "registered: $name ($day $at, label=$label)"
}

New-Item -ItemType Directory -Force (Join-Path $repo "collector\out") | Out-Null
# weekly = collector.weekly 한 줄 — 수집 → 얼림(push) → 등급 → 관문 → detect 입력 → 게시 (HYO-99 3단계). 사슬은 collector/weekly.py에 있다 (맥과 같은 코드).
# anthropic·openai가 필요해 .venv 파이썬으로 돈다. 관문은 .env의 OPENAI_API_KEY가 필요하다 — 없으면 거기서 멈춘다.
# 관문·feed가 끼어 45분을 넘길 수 있어 weekly만 90분으로 늘린다
$venvPython = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) { throw ".venv가 없다: $venvPython — 주간 사슬은 .venv의 anthropic·openai를 쓴다" }
Register-Collector "JHS-Collector-Weekly" $WeeklyDay $WeeklyTime "weekly" $venvPython "collector.weekly" "" 90
Register-Collector "JHS-Collector-AWS" $AwsDay $AwsTime "aws" $python "collector.run" "--label aws --sources vendor --only aws-bedrock"
Get-ScheduledTask -TaskName $names | Select-Object TaskName, State, @{n="Next";e={(Get-ScheduledTaskInfo $_).NextRunTime}}
