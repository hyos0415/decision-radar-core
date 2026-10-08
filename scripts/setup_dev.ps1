# 개발 환경 한 번에 준비·검사 (Windows PowerShell 5.1+) — HYO-93. 다시 돌려도 안전하다 (있는 것은 건너뛴다).
#
#   powershell -ExecutionPolicy Bypass -File scripts\setup_dev.ps1          # 전부
#   powershell -ExecutionPolicy Bypass -File scripts\setup_dev.ps1 -Quick   # 설치만
#
# API 키·.env·형제 폴더는 필요 없다 — 앱은 스텁 모드로 돈다. macOS는 scripts/setup_dev.sh.
# 이 파일은 UTF-8 BOM으로 저장한다 — PowerShell 5.1은 BOM 없는 UTF-8을 cp949로 읽어 한국어가 깨진다.
param([switch]$Quick)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Step($m) { Write-Host "`n== $m" }
function Fail($m) { Write-Host "`n실패: $m" -ForegroundColor Red; exit 1 }
function Run($exe, [string[]]$argv) { & $exe @argv; if ($LASTEXITCODE -ne 0) { Fail "$exe $($argv -join ' ') (exit $LASTEXITCODE)" } }

Step '1/5 Python 3.12'
$venvPy = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPy)) {
    if (-not (Get-Command py -ErrorAction SilentlyContinue)) { Fail 'py 런처가 없다 — python.org에서 Python 3.12 설치 (CI와 같은 버전)' }
    & py -3.12 -c 'import sys' 2>$null
    if ($LASTEXITCODE -ne 0) { Fail 'Python 3.12가 없다 — python.org에서 3.12 설치' }
    Step '2/5 .venv 만들기 + 의존성'
    Run py @('-3.12', '-m', 'venv', '.venv')
} else {
    & $venvPy -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)'
    if ($LASTEXITCODE -ne 0) { Fail '.venv가 3.12가 아니다 — .venv 폴더를 지우고 다시 돌린다' }
    Step '2/5 .venv 있음 — 의존성만 맞춘다'
}
Run $venvPy @('-m', 'pip', 'install', '-q', '--upgrade', 'pip')
Run $venvPy @('-m', 'pip', 'install', '-q', '-r', 'requirements.txt', '-r', 'product/requirements.txt')
Run $venvPy @('-m', 'pip', 'check')

Step '3/5 Node.js'
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { Fail 'Node.js가 없다 — LTS(22, CI와 같음) 설치: https://nodejs.org' }
$major = ([version](& node -p process.versions.node)).Major  # 따옴표 든 식은 5.1이 native 인자에서 깨뜨린다
Write-Host "node $(& node -v) · npm $(& npm -v)"
if ($major -lt 20) { Fail "Node $major 는 오래됐다 — 20 이상 (CI는 22)" }

Step '4/5 desktop npm ci'
Push-Location desktop
try { Run npm @('ci') } finally { Pop-Location }

if ($Quick) {
    Step '5/5 검사 건너뜀 (-Quick)'
} else {
    Step '5/5 검사 — 백엔드 회귀(스텁, 키 없음) · lint · build'
    $env:PYTHONUTF8 = '1'
    $out = & $venvPy desktop/backend/test_backend.py
    $code = $LASTEXITCODE
    $out | Select-Object -Last 1
    if ($code -ne 0) { Fail 'test_backend.py' }
    Push-Location desktop
    try { Run npm @('run', 'lint'); Run npm @('run', 'build'); Write-Host 'lint · build 통과' } finally { Pop-Location }
}

Write-Host "`n준비 끝. 앱 띄우기:  cd desktop; npm run dev"
