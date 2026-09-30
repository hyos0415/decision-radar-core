# 런타임 짝 비교 — Agents SDK ↔ LangGraph (HYO-62)

> **관찰 기록이다. 통과 기준이 아니다.** 통과 기준은 `docs/design/runtime-spike.md` P1~P6이고 두 런타임에 똑같이 적용한다.
> 같게 둔 것: 모델(Claude Sonnet 5.5 · effort medium) · 케이스 · 채점기(`common/eval_adapter.py` → 스킬의 `run_case`·`capture` 채점) · 규칙층(`radar.py`, 수정 0줄) · 프롬프트·스키마·도구 본체(`common/`) · 스텁 판단(`common/stub_policy.py`).
> 다른 것: 런타임 — 아래 표. 표는 만들면서 채운다 (사전 등록한 예측은 없다 — 사용자 결정, 예측 격자는 리트리버 한정).

## 흐름·상태

| 축 | Agents SDK | LangGraph | 이 프로젝트에서 드러난 곳 |
|---|---|---|---|
| 흐름을 누가 쥐나 | 에이전트 루프는 SDK, 그 바깥 순서는 plain Python | 바깥 순서도 `StateGraph` 노드·엣지 | revisit: SDK는 for 루프, LangGraph는 list → judge → decide 그래프 |
| 조건 판정 병렬 | 직접 짜야 한다 (지금은 순서대로) | `Send`로 fan-out, 결과는 리듀서(`operator.add`)로 합쳐진다 | 병렬이라 공유 카운터(`ctx.calls`)를 못 쓴다 → 판정별 `ToolMessage` 수로 센다 |
| HITL (멈춤) | 도구에 `needs_approval=True` → `result.interruptions` | `HumanInTheLoopMiddleware(interrupt_on=...)` → `result["__interrupt__"]` | 둘 다 도구 실행 **전에** 멈춘다. 승인 채널이 텍스트가 아니다 (JHS-039의 가짜 승인문이 구조적으로 닿지 못한다) |
| 상태 저장 | `RunState.to_json()`을 **내가** 파일로 쓴다 | 체크포인터(`SqliteSaver`)가 **단계마다 자동으로** 쓴다 | SDK: 멈췄을 때만 파일이 생긴다. LangGraph: 멈추지 않아도 체크포인트 파일이 있다 |
| 재개 | `RunState.from_json(agent, data)` → `approve()`/`reject()` → `Runner.run(agent, state)` | 같은 파일·같은 `thread_id` → `invoke(Command(resume={"decisions": [...]}))` | 둘 다 새 프로세스에서 대기 승인 1건 복원 (P6) |
| 컨텍스트와 상태 파일 | 기본은 컨텍스트도 직렬화 시도 → `context_serializer=lambda _: {}`로 **내가** 막았다 | 런타임 컨텍스트는 체크포인트에 **원래** 안 들어간다 | Ctx에 환경변수 전체가 있다 — 파일로 새면 `.env` 밖 토큰도 샌다. 양쪽 다 검사(`state_context_free`) |
| 상태 파일 크기 (스텁 JHS-014) | 약 16KB (JSON) | 약 74KB (SQLite, 단계별 체크포인트) | 실제 모델 JHS-014 SDK: 51KB (thinking 서명 5개 포함) |
| 스텁 모델 만들기 | `Model.get_response`가 Responses 형식 항목을 돌려준다 | `BaseChatModel._generate` + `bind_tools`가 `AIMessage`를 돌려준다 | 둘 다 판단은 같은 `stub_policy` — 형식 변환만 런타임별 |

## Claude 연결 (P3 축)

| 축 | Agents SDK (LiteLLM 어댑터, 베타) | LangGraph (langchain-anthropic, 공식 통합) |
|---|---|---|
| 설치 | litellm 1.103.1이 `openai<3` 선언 — SDK의 `openai>=3`과 충돌. 별도 venv에 설치 순서로 맞춤 | 메인 venv에 충돌 없이 (`pip check` 통과) |
| Sonnet 5.5 인식 | 모델 표에 `claude-sonnet-5-5` 없음 → `register_model`로 등록 (사용자 채택) | 1.7.5가 알고 있다 — 강제 tool_choice 미지원 목록, effort → `output_config.effort` |
| 구조화 출력 요청 | 등록 시 `output_format` (**폐기 예정 이름**) · 미등록 시 가짜 도구 `json_tool_call` | `ProviderStrategy` → 현행 `output_config.format` |
| P5 기본값 | 두 번 샌다: tiktoken 파일(openaipublic.blob), beta 헤더 표(raw.githubusercontent) → 캐시·로컬 설정으로 막음 | 실제 모델 실행 전 — 같은 NetGuard로 본다 |
| 실측 | JHS-001 revisit·JHS-014 capture: 400 없음, thinking 블록이 새 프로세스로 온전히 (agents_sdk/README) | 아직 (스텁만) |

## 코드 크기 (기록만 — runtime-spike.md §4)

런타임 전용 파일만 센다 (`common/`은 공통 855줄이라 뺀다). `wc -l`, 주석·docstring 포함. 2026-09-30 스텁 단계.

| 파일 | Agents SDK | LangGraph | 메모 |
|---|---|---|---|
| runtime.py (두 흐름 + 인터페이스) | 196 | 226 | LangGraph는 revisit 그래프 정의(상태 타입·노드 3·엣지)가 더 든다. SDK는 병렬이 없어서 짧다 |
| models.py | 75 | 40 | SDK는 LiteLLM의 P5 누수 우회(캐시·환경변수)와 모델 표 등록이 든다 |
| resume.py | 53 | 50 | |
| stub_model.py | 64 | 51 | |
| run_eval.py | 18 | 18 | |
| **합계** | **406** | **385** | "더 얇다"는 Agents SDK를 고른 이유였다 — 이 단계에선 차이가 작다. 어댑터 우회 코드가 SDK 쪽을 불렸다 |
