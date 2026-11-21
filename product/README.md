# product — Decision Radar 제품 (실험군)

스킬(`.claude/skills/jhs-decisions/`, 기준선)과 같은 명세(R1~R16)·같은 eval·같은 규칙층(`radar.py`) 위에서 비교할 제품 쪽 코드.
재판정 `docs/decisions/2026-09-29-rejudgment.md` · 설계 `docs/design/overview.md` · 비교 규칙 `docs/design/comparison-protocol.md`.

| 경로 | 무엇 |
|---|---|
| `requirements.txt` | 제품 의존성 — 스킬·eval(`/requirements.txt`)과 섞지 않는다 (스킬은 배포 단위, CLAUDE.md 규율 6) |
| `radar/` | **제품 패키지** (plain Python 흐름 + 직접 쓴 에이전트 루프 — ADR 0030, 처음엔 LangGraph) — revisit·capture(스파이크에서 옮김) + detect(새로) + 53케이스 스텁 eval. `radar/README.md` |
| `spike/common/` | 두 런타임이 같이 쓰는 것 — 프롬프트·스키마·도구 본체·보고서·스텁 판단·가드·채점 어댑터 (한 벌이어야 런타임 차이만 남는다) |
| `spike/agents_sdk/` | 런타임 스파이크 1번 (HYO-63). 기준: `docs/design/runtime-spike.md` |
| `spike/langgraph_spike/` | 런타임 스파이크 2번 (HYO-64) — Agents SDK와 짝 비교 (변경 2026-09-30). 폴더 이름이 `langgraph`면 설치된 `langgraph` 패키지를 가린다 |
| `spike/COMPARISON.md` | 두 런타임의 차이 관찰 기록 (HYO-62) — 통과 기준 아님 |
| `spike/plain/` | 두 후보가 모두 기준을 못 넘으면 생긴다 (ADR `adr/0006`) |

- 규칙층은 **복사하지 않고** 스킬의 `radar.py`를 그대로 부른다 (기준 P4: 수정 0줄)
- API 키는 레포 루트 `.env` (gitignore). 비교 모드 모델은 Claude Sonnet 5.5 · effort medium
- 파이프라인은 스텁 모델로 먼저 만들고, API 실험은 나중에 (사용자 2026-09-30)
