# 수집기 정기 실행을 Windows 작업 스케줄러에 등록한다 (doc-pipeline §11 D3: 지금은 이 PC, 나중에 M1으로 이관).
#
#   powershell -ExecutionPolicy Bypass -File collector\schedule_windows.ps1 -WeeklyDay Tuesday -WeeklyTime 16:00 -AwsDay Thursday -AwsTime 16:00
#   powershell -ExecutionPolicy Bypass -File collector\schedule_windows.ps1 -Remove
#
# - 로그온한 동안에만 돈다 (비밀번호를 저장하지 않는다)
# - 예약 시각에 PC가 꺼져 있었으면 다음에 켜졌을 때 바로 돈다 (StartWhenAvailable) — 놓친 주가 생기면 run.json의 시각으로 드러난다
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

function Register-Collector($name, $day, $at, $label, $collectorArgs, $after = "") {
    $cmd = "Set-Location '$repo'; `$env:PYTHONIOENCODING='utf-8'; & '$python' -m collector.run --label $label $collectorArgs *> 'collector\out\last-$label.log'$after"
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -WindowStyle Hidden -Command `"$cmd`"" -WorkingDirectory $repo
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $day -At $at
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 45) -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Description "Decision Radar 수집기 ($label) — collector/README.md" -Force | Out-Null
    "registered: $name ($day $at, label=$label)"
}

New-Item -ItemType Directory -Force (Join-Path $repo "collector\out") | Out-Null
# weekly는 수집이 끝나면 바로 주간 스냅숏으로 얼려 data repo에 push한다 (HYO-72). 한 출처가 죽어도 얼린다 — manifest에 남는다
$freeze = "; & '$python' -m collector.snapshot freeze --push *>> 'collector\out\last-weekly.log'"
Register-Collector "JHS-Collector-Weekly" $WeeklyDay $WeeklyTime "weekly" "" $freeze
Register-Collector "JHS-Collector-AWS" $AwsDay $AwsTime "aws" "--sources vendor --only aws-bedrock"
Get-ScheduledTask -TaskName $names | Select-Object TaskName, State, @{n="Next";e={(Get-ScheduledTaskInfo $_).NextRunTime}}
