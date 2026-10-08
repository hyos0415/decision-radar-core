#!/usr/bin/env bash
# 개발 환경 한 번에 준비·검사 (macOS · Linux) — HYO-93. 다시 돌려도 안전하다 (있는 것은 건너뛴다).
#
#   bash scripts/setup_dev.sh            # 전부: Python .venv · 백엔드 회귀 · npm ci · lint · build
#   bash scripts/setup_dev.sh --quick    # 설치만 (회귀·lint·build 건너뜀)
#
# API 키·.env·형제 폴더는 필요 없다 — 앱은 스텁 모드로 돈다 (ONBOARDING.md 4절). Windows는 scripts/setup_dev.ps1.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
QUICK=0
[ "${1:-}" = "--quick" ] && QUICK=1

step() { printf '\n== %s\n' "$1"; }
fail() { printf '\n실패: %s\n' "$1" >&2; exit 1; }

step "1/5 Python 3.12"
PY=""
for c in python3.12 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)' 2>/dev/null; then
    PY="$c"; break
  fi
done
[ -n "$PY" ] || fail "Python 3.12가 없다 — macOS: brew install python@3.12 (CI와 같은 버전)"
echo "$($PY --version) ($(command -v $PY)) · $(uname -m)"

step "2/5 .venv + 의존성"
if [ ! -x .venv/bin/python ]; then
  "$PY" -m venv .venv
  echo ".venv 만듦"
else
  .venv/bin/python -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)' \
    || fail ".venv가 3.12가 아니다 — rm -rf .venv 하고 다시 돌린다"
  echo ".venv 있음 — 그대로 쓴다"
fi
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt -r product/requirements.txt
.venv/bin/pip check

step "3/5 Node.js"
command -v node >/dev/null 2>&1 || fail "Node.js가 없다 — LTS(22, CI와 같음) 설치: brew install node@22 또는 https://nodejs.org"
NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]')"
echo "node $(node -v) · npm $(npm -v)"
[ "$NODE_MAJOR" -ge 20 ] || fail "Node $NODE_MAJOR는 오래됐다 — 20 이상 (CI는 22)"

step "4/5 desktop npm ci"
(cd desktop && npm ci)

if [ "$QUICK" = 1 ]; then
  step "5/5 검사 건너뜀 (--quick)"
else
  step "5/5 검사 — 백엔드 회귀(스텁, 키 없음) · lint · build"
  PYTHONUTF8=1 .venv/bin/python desktop/backend/test_backend.py | tail -1
  (cd desktop && npm run lint && npm run build >/dev/null && echo "lint · build 통과")
fi

printf '\n준비 끝. 앱 띄우기:  cd desktop && npm run dev\n'
printf '처음 뜰 때 손쉬운 사용(전역 단축키 Cmd+Shift+Space)·알림 권한을 물을 수 있다 — 막히는 곳은 HYO-77에 적는다\n'
