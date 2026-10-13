# Decision Radar — 이 레포에서 일하는 법

이 레포는 비공개 개발 레포에서 공개할 코드만 이력째 떼어 낸 사본이다. 처음이면 README부터 읽는다.

이 파일에는 「스택」 절만 남겼다. 레이더가 이 레포를 감시할 때 읽는 의존성 → component 표이고, 코드가 이 표를 읽는다. 나머지 작업 지침은 비공개 레포에만 있다. 문서 · 커밋 메시지의 `HYO-###`는 비공개 작업 관리(Linear)의 이슈 번호다.

## 스택 · 평가셋 · 알려진 병목

기술 결정 기록(결정 기록 repo)의 `jhs-decisions` 스킬이 이 절을 읽고, 이 스택에 영향을 주는 새 릴리스·모델만 골라낸다. 의존성이나 모델을 바꾸면 이 절도 같이 고친다.

**지금 코드에 있는 것만** 적는다 (2026-10-02, 사용자 결정) — 아직 설계에만 있는 것(Gemini 임베딩 변형, BGE-M3, SPLADE 등)은 코드에 들어올 때 추가한다. manifest: `requirements.txt` · `product/requirements.txt` · `arena/requirements.txt` · `infra/compose.yaml` · `desktop/package.json`.

### 의존성 → component

| 의존성 | component |
|---|---|
| PyYAML | skill (`radar.py`) · collector · product |
| openai-agents · openai | product (`spike/agents_sdk` — 최적화 모드, ADR 0008) |
| langgraph · langgraph-checkpoint · langgraph-checkpoint-sqlite · langchain · langchain-core · langchain-anthropic | product (`spike/langgraph_spike` — 비교 모드, ADR 0009) |
| anthropic | product |
| litellm (별도 venv) | product (`spike/agents_sdk`의 Claude 경로) |
| fastapi · uvicorn | desktop (`desktop/backend` — 앱 백엔드, localhost) |
| ranx · tokenizers (+ ranx가 끄는 numpy·scipy·pandas·numba) | arena (`arena/` — 하네스 지표 · 청크 상한 계량) |
| Electron 39 · electron-vite 5 · React 19 · Tailwind 4 · shadcn/ui(Radix) · Geist · Pretendard 글꼴 · react-markdown + remark-gfm · remark-frontmatter (npm, `desktop/package.json`) | desktop (E6 데스크톱 앱 화면·껍데기, HYO-49) |
| Elasticsearch (docker `infra/`, 9.5.4 + analysis-nori, basic) | arena |
| Neo4j (docker `infra/`, 5.26.31 + n10s) | arena |

### 외부 모델·서비스

| 모델·서비스 | component |
|---|---|
| Anthropic `claude-sonnet-5-5` (effort medium) — `claude -p`(eval 러너) · API(스파이크) | eval · product |
| Claude Code routines (스킬 실사용, HYO-21) | skill |
| OpenAI `gpt-6-sol` — `codex exec` (judge, HYO-20) | eval |
| OpenAI `gpt-6-luna` (tier-screen, ADR 0007) | eval |
| LangSmith (개발 실행 트레이싱만, ADR 0009) | product |

### 평가셋

- 스킬 회귀: 53케이스 정답지(`eval/cases/`)는 비공개다 — 공개 사본에는 없다
- 스크립트 테스트: `test_radar.py` 36/36 · `eval/runner/test_leak.py` · `collector/tests/test_collector.py` · `collector/tests/test_snapshot.py` · `infra/smoke.py`

### 알려진 병목

- **검색 능력은 측정된 적이 없다** — `radar.py snapshot search`가 질의를 무시한다 (`docs/design/doc-pipeline.md` §1-2). 수집 파이프라인(E9)과 `skill@v2` 짝 비교(HYO-75)가 메운다
