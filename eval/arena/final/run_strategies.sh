#!/bin/bash
# 최종 비교 — 14전략을 등록 고정값(CLI 기본값)으로 한 번씩 돈다 (HYO-155 D1)
#   S1(K만) · S2 · S3a · S3a.ko · S3b · S4 · S4.int8 · S4.bbq · S4.kure · S5a · S5b.ko · S5b(SPLADE-v3) · S6 · S7
#
# bash eval/arena/final/run_strategies.sh <입력 폴더> <split> <결과 폴더> <gold 버전 이름> [rank-only]
#   입력 폴더: split_gold.py 출력 (<split>-queries.jsonl · <split>-k-queries.jsonl · 채점이면 <split>-qrels.json · <split>-k-qrels.json)
#   rank-only: qrels 없이 순위만 (D2 — test 잔여 판정 풀)
# 형제 폴더(radar-snapshots · adr · finance_verifier · fineprint)는 이 레포의 부모 폴더 아래에 있어야 한다. 레포 루트에서 실행한다
set -u
IN="$1"; SPLIT="$2"; OUT="$3"; GOLD="$4"; MODE="${5:-scored}"
REPO="$(pwd)"; P="${ARENA_SIBLINGS:-$(dirname "$REPO")}"       # worktree에서 돌리면 ARENA_SIBLINGS · ARENA_PY로 바꾼다
PY="${ARENA_PY:-$REPO/.venv/Scripts/python}"; [ -x "$PY" ] || PY="$REPO/.venv/bin/python"
[ -f "$REPO/arena/cli.py" ] || { echo "레포 루트에서 실행한다"; exit 1; }
COMMON=(--gold-version "$GOLD" --snapshots "$P/radar-snapshots" --adr "$P/adr" --repo "decision-radar=$REPO"
        --repo "finance_verifier=$P/finance_verifier" --repo "fineprint=$P/fineprint"
        --supplement "source-hints@2026-10-12=$P/radar-snapshots/supplements/source-hints@2026-10-12/items.jsonl" --results "$OUT")
FAILED=0
run() {   # run <queries> <qrels> <전략 인자...>
  local q="$1" r="$2"; shift 2
  local gold=(--rank-only); [ "$MODE" = "rank-only" ] || gold=(--qrels "$r")
  echo "=== $* ($MODE)"
  "$PY" arena/cli.py run "$@" --queries "$q" "${gold[@]}" "${COMMON[@]}" 2>&1 | grep -v "HF Hub" | tail -2
  [ "${PIPESTATUS[0]}" = "0" ] || { echo "실패: $*"; FAILED=$((FAILED + 1)); }
}
run "$IN/$SPLIT-k-queries.jsonl" "$IN/$SPLIT-k-qrels.json" --strategy s1
for s in s2 s3a s3a.ko s3b s4 s4.int8 s4.bbq s4.kure s5a s5b s6 s7; do
  run "$IN/$SPLIT-queries.jsonl" "$IN/$SPLIT-qrels.json" --strategy "$s"
done
run "$IN/$SPLIT-queries.jsonl" "$IN/$SPLIT-qrels.json" --strategy s5b --s5b-sparse s3a
echo "RUN_DONE 실패 $FAILED"
[ "$FAILED" = "0" ]
