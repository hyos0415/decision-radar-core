# 런타임 스파이크 2번 — LangGraph (HYO-64)

기준: `docs/design/runtime-spike.md` (P1~P6) · 결정: ADR `adr/0006` · 짝: `../agents_sdk/` · 차이 기록: `../COMPARISON.md`

```
revisit:  StateGraph  list(radar.py list) ─Send로 조건마다─▶ judge(create_agent + Verdict) ─▶ decide(radar.py decide --apply)
capture:  create_agent(match_adr · propose_adr) + HumanInTheLoopMiddleware(propose_adr: approve/reject) + SqliteSaver
          1턴  승인 대기에서 interrupt → 체크포인트가 SQLite 파일에 남는다
          2턴  (새 프로세스) 같은 파일·같은 thread_id → Command(resume={"decisions": [...]})
```

| 파일 | 무엇 |
|---|---|
| `runtime.py` | 두 흐름. 도구는 `common/ops` 본체를 `@tool`로 감싸고 `ToolRuntime[Ctx]`로 컨텍스트를 받는다 |
| `resume.py` | capture 2턴을 **다른 프로세스**로 (P6). 체크포인트 파일과 thread_id만 이어 받는다 |
| `stub_model.py` | 가짜 LangChain 채팅 모델 — 판단은 `common/stub_policy.py` (Agents SDK 스텁과 같은 함수) |
| `models.py` | 실제 모델 — `ChatAnthropic`(langchain-anthropic, 공식 통합) + 요청 모양 기록 |
| `run_eval.py` | `common/eval_adapter.py`를 이 런타임으로 부르는 얇은 진입점 |

```bash
.venv/Scripts/python product/spike/langgraph_spike/run_eval.py --stub                 # 11케이스 + 음성 대조 7 — 종료 코드 0
.venv/Scripts/python product/spike/langgraph_spike/run_eval.py --stub JHS-047 --k 3
.venv/Scripts/python product/spike/langgraph_spike/run_eval.py --model anthropic JHS-001   # 실제 Claude (API 과금) — 메인 .venv 그대로
```

## 설계에서 정한 것

- **구조화 출력은 `ProviderStrategy`를 명시**한다. `create_agent`에 스키마만 넘기면 모델 profile로 고르는데, profile 없는 스텁은 `ToolStrategy`(강제 `tool_choice="any"`)로 간다 — 스텁과 실제 모델이 다른 경로를 타면 스텁 통과가 더 약한 증거가 된다
- **HITL은 `HumanInTheLoopMiddleware`** (LangChain v1 표준, Agents SDK의 `needs_approval`에 해당). 허용 결정은 approve·reject만 — edit·respond는 Agents SDK 쪽에 없어서 뺐다 (짝 비교)
- **체크포인터는 `SqliteSaver` 파일** — 새 프로세스가 같은 파일·같은 `thread_id`로 연다. 런타임 컨텍스트(`context=ctx`)는 체크포인트에 저장되지 않는다. 실행마다 파일에 환경변수 이름(`RADAR_SNAPSHOT_DIR`)이 없는지 검사한다 (P6 `state_context_free`)
- **LangSmith 끔** — `runtime.py` import 때 `LANGSMITH_TRACING=false`. 트레이스 카운터가 `langsmith.utils.tracing_is_enabled()`를 센다 (P5)
- judge는 `Send`로 **병렬**. 도구 호출 수(P3 「도구 호출 누락」)는 공유 카운터 대신 그 판정의 `ToolMessage` 수로 센다
- 도구 시그니처는 `ToolRuntime[Ctx]` — `ToolRuntime`만 쓰면 LangChain이 도구 입력을 직렬화할 때 pydantic 경고를 낸다 (context를 None으로 기대)

## 결과 기록

| 날짜 | 모델 | P1 | P2 | P3 | P4 | P5 | P6 | 비고 |
|---|---|---|---|---|---|---|---|---|
| 2026-09-30 | stub | 11/11 | 6케이스 × k=3 = 18/18 | 해당 없음 | diff 0 · 사본 동일 | 연결 0 · trace 0 | 5/5 (체크포인트 49–74KB, 컨텍스트 없음) | 음성 대조 7/7. **통과 판정 아님** — 실제 모델 실행에만 적용 |
| 2026-09-30 | **Sonnet 5.5 · medium** | **11/11** | **18/18** | **0** | diff 0 · 사본 동일 | 연결 0 · trace 0 | 전부 통과 | **스파이크 통과.** $1.131. 요청 184건 모두 `output_config.format`·tool_choice 없음·adaptive thinking. 짝 비교: `../COMPARISON.md` |
