# Decision Radar — 이 레포에서 일하는 법

이 레포는 비공개 개발 레포에서 공개할 코드만 이력째 떼어 낸 사본이다. 처음이면 README부터 읽는다.

이 파일에는 「스택」 절만 남겼다. 레이더가 이 레포를 감시할 때 읽는 의존성 → component 표이고, 코드가 이 표를 읽는다. 나머지 작업 지침은 비공개 레포에만 있다. 문서 · 커밋 메시지의 `HYO-###`는 비공개 작업 관리(Linear)의 이슈 번호다.

## 스택 · 평가셋 · 알려진 병목

기술 결정 기록(결정 기록 repo)의 `jhs-decisions` 스킬이 이 절을 읽고, 이 스택에 영향을 주는 새 릴리스·모델만 골라낸다. 의존성이나 모델을 바꾸면 이 절도 같이 고친다.

**지금 코드에 있는 것만** 적는다 (2026-10-02, 사용자 결정) — 아직 설계에만 있는 것(Gemini 임베딩 변형, BGE-M3, SPLADE 등)은 코드에 들어올 때 추가한다. manifest: `requirements.txt` · `product/requirements.txt` · `arena/requirements.txt` · `registry/requirements.txt` · `infra/compose.yaml` · `desktop/package.json`.

### 의존성 → component

| 의존성 | component |
|---|---|
| PyYAML | skill (`radar.py`) · collector · product |
| openai | pipeline (`pipeline/importance/openai_backend.py` — 관문의 GPT 비교 경로, 운영 관문은 Claude) |
| mcp (MCP 파이썬 SDK 2.x) | product (`radar/mcp_tools.py` — 구독 CLI 엔진의 읽기 도구 서버, HYO-99) |
| anthropic | product (`radar/models.py` — API 엔진, 에이전트 루프는 `radar/loop.py`, ADR 0030) · pipeline (`pipeline/importance/claude_backend.py` — 관문 등급 재분류, HYO-122) |
| fastapi · uvicorn | desktop (`desktop/backend` — 앱 백엔드, localhost) |
| ranx · tokenizers (+ ranx가 끄는 numpy·scipy·pandas·numba) | arena (`arena/` — 하네스 지표 · 청크 상한 계량) |
| networkx | arena (`arena/graph.py` — S7 그래프 graph@v1 · PPR, HYO-149) |
| rdflib · pyshacl · owlrl | registry (`registry/build.py` — 데이터 등록부 빌드 · SHACL 검사만, 실행 경로는 `registry.json`, HYO-180) |
| Electron 45 · electron-vite 5 · React 19 · Tailwind 4 · shadcn/ui(Radix) · Geist · Pretendard 글꼴 · react-markdown + remark-gfm · remark-frontmatter (npm, `desktop/package.json`) | desktop (E6 데스크톱 앱 화면·껍데기, HYO-49) |
| electron-builder 26 (npm devDependency, `desktop/package.json`) | desktop (설치본 묶기 `npm run app:pack` — `electron-builder.config.cjs`, HYO-251) |
| Elasticsearch (docker `infra/`, 9.5.4 + analysis-nori, basic) | arena |
| Neo4j (docker `infra/`, 5.26.31 + n10s) | arena |

### 외부 모델·서비스

| 모델·서비스 | component |
|---|---|
| Anthropic `claude-sonnet-5-5` (effort medium) — `claude -p`(eval 러너 · eval judge `eval/judge/run_judge.py`, HYO-210 · 제품 구독 CLI 엔진 `engines.ClaudeCliRunner`, HYO-99 · 자리 질의 `pipeline/slots.py` · 관심사 질의 `pipeline/interest.py`, HYO-187) · API(스파이크 · 제품 API 엔진 · 관문 주간 등급 재분류 `weekly-tiers`, HYO-122) | eval · product · pipeline |
| Anthropic `claude-haiku-5-5` (effort medium) — `claude -p` 레인 A 관문 (주간 사슬 `DEFAULT_GATE`, 묶음 10, W48부터 — W47까지 OpenAI `gpt-6.1-sol` API, HYO-169) | pipeline |
| Claude Code routines (스킬 실사용, HYO-21) | skill |
| OpenAI `gpt-6-luna` (tier-screen, ADR 0007) | eval |

### 평가셋

- 스킬 회귀: 53케이스 정답지(`eval/cases/`)는 비공개다 — 공개 사본에는 없다
- 합성 케이스: `eval/synthetic/` (5케이스 + 음성 대조 6, `run_eval.py --stub --synthetic`) — 53케이스 정답지를 비공개로 두는 공개 사본에서 도는 것 (HYO-235). 스텁만 돌렸다
- 스크립트 테스트: `test_radar.py` 40/40 · `eval/runner/test_leak.py` · `collector/tests/test_collector.py` · `collector/tests/test_snapshot.py` · `collector/tests/test_health.py` · `infra/smoke.py`

### 알려진 병목

- **detect의 소식 검색은 실사용에서 꺼져 있고 효과를 잰 적이 없다** — `radar.py snapshot search`는 `RADAR_CORPUS`(얼린 코퍼스)가 있으면 BM25로 찾고 detect에 `corpus_grep`(S8 DCI)도 붙는다(HYO-201 1·2단계). 앱 정기 실행은 이를 켜지 않아 예전처럼 질의와 무관한 `search.json`을 돌려준다(`run_detect.py --corpus-weeks`로만 켠다, `docs/design/doc-pipeline.md` §1-2). 켤지 · 효과 측정은 HYO-201
