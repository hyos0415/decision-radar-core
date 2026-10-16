#!/usr/bin/env bash
# 등급 예시 측정 실행 (tier-examples.md §1-2·§3-2): 지금 체크아웃한 코드로 빈 등급 파일에서 95개를 k번 분류한다. Sonnet 5.5 API — 돈이 든다.
#   bash eval/importance/run_tier_examples.sh <출력 폴더> <쪽 이름> <k> <상한 $>
#   예: bash eval/importance/run_tier_examples.sh eval/runs/hyo128 after-v2 3 2
# 출력: <폴더>/<쪽>-<i>.json · <쪽>-<i>-calls.jsonl. 실행마다 그 폴더의 *-calls.jsonl 비용을 모두 더해 상한을 넘으면 멈춘다.
# HYO-128: HYO-124 때 스크립트는 경로를 파이썬 코드 문자열 안에 넣어(`r'/c/…'`) Windows 파이썬이 파일을 못 찾고 늘 $0을 냈다.
# 이제 경로는 따로 인자로 넘긴다 — Git Bash가 인자의 /c/… 는 Windows 경로로 바꿔 준다.
set -u
OUT=${1:?출력 폴더} ARM=${2:?쪽 이름} K=${3:?k} CAP=${4:?상한 달러}
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
PY=$ROOT/.venv/Scripts/python; [ -x "$PY" ] || PY=$ROOT/.venv/bin/python
export PYTHONIOENCODING=utf-8
mkdir -p "$OUT"
cd "$ROOT"
echo "== $ARM @ $(git rev-parse --short HEAD)$(git diff --quiet HEAD -- pipeline || echo ' (pipeline에 커밋 안 된 변경 있음)')"

spent() {
  "$PY" -c "
import glob, json, os, sys
s = 0.0
for p in glob.glob(os.path.join(sys.argv[1], '*-calls.jsonl')):
    for line in open(p, encoding='utf-8'):
        s += json.loads(line).get('cost_usd') or 0
print(round(s, 4))" "$OUT"
}

total=$(spent)
echo "시작 전 누적 비용 \$$total (상한 \$$CAP)"
for i in $(seq 1 "$K"); do
  if "$PY" -c "import sys; sys.exit(0 if float(sys.argv[1]) >= float(sys.argv[2]) else 1)" "$total" "$CAP"; then
    echo "상한 \$$CAP 넘음 — 멈춘다"; exit 2
  fi
  f=$OUT/$ARM-$i.json
  rm -f "$f"
  "$PY" -m pipeline.importance classify-stack --targets --extra pipeline/importance/stack-extra.json \
    --tiers "$f" --model claude-sonnet-5-5 --real-api --call-log "$OUT/$ARM-$i-calls.jsonl" 2>&1 | tail -4
  total=$(spent)
  echo "누적 비용 \$$total"
done
echo "== 끝"
