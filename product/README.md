# product — Decision Radar 제품 (실험군)

스킬(`.claude/skills/jhs-decisions/`, 기준선)과 같은 명세(R1~R16)·같은 eval·같은 규칙층(`radar.py`) 위에서 비교할 제품 쪽 코드.
재판정 `docs/decisions/2026-09-29-rejudgment.md` · 설계 `docs/design/overview.md` · 비교 규칙 `docs/design/comparison-protocol.md`.

| 경로 | 무엇 |
|---|---|
| `requirements.txt` | 제품 의존성 — 스킬·eval(`/requirements.txt`)과 섞지 않는다 (스킬은 배포 단위, CLAUDE.md 규율 6) |
| `spike/agents_sdk/` | 런타임 스파이크 1번 후보 (HYO-62). 기준: `docs/design/runtime-spike.md` |
| `spike/langgraph/` | 런타임 스파이크 2번 후보 — Agents SDK와 짝 비교 (변경 2026-09-30, `runtime-spike.md`) |
| `spike/plain/` | 두 후보가 모두 기준을 못 넘으면 생긴다 (ADR `adr/0006`) |

- 규칙층은 **복사하지 않고** 스킬의 `radar.py`를 그대로 부른다 (기준 P4: 수정 0줄)
- API 키는 레포 루트 `.env` (gitignore). 비교 모드 모델은 Claude Sonnet 5.5 · effort medium
- 파이프라인은 스텁 모델로 먼저 만들고, API 실험은 나중에 (사용자 2026-09-30)
