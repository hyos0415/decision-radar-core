# jhs-decisions

기술 결정 기록(ADR)을 쌓고 다시 꺼내 쓰는 Claude Code 스킬. 내부 코드명 Project JHS.

- **revisit** — 보류·기각한 결정의 재검토 조건이 충족됐는지 주기적으로 확인
- **detect** — 내 스택에 영향을 줄 새 릴리스·모델·기술 후보를 ADR 초안으로
- **capture** — 조사 대화를 승인 후 ADR 초안으로

결정 자체(채택·기각)는 하지 않는다. 그건 사람의 몫이다.

## 구조

| 경로 | 내용 |
|---|---|
| `.claude/skills/jhs-decisions/` | 스킬 본체 (SKILL.md + `scripts/jhs.py` 결정론 CLI) |
| `docs/spec-work-order.md` | 명세 — 「B부 명세 v1 개정」 R1~R16 |
| `docs/decisions/` | 이 프로젝트 자신의 결정 기록 (검시 판정문 포함) |
| `eval/` | 평가셋·픽스처·러너·실험 기록 |

## 빠른 시작

```bash
pip install -r requirements.txt
python .claude/skills/jhs-decisions/scripts/test_jhs.py      # 스크립트 층 회귀 21건
python eval/runner/run_case.py JHS-043 --model sonnet        # 모델 층 1케이스 (claude -p 세션 1개 = 구독 사용량)
python eval/runner/batch.py --flow revisit --jobs 6          # 전체 (273세션)
```

이관 경위와 열린 항목: `MIGRATION.md`.
