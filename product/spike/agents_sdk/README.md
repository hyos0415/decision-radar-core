# 런타임 스파이크 1번 — OpenAI Agents SDK (HYO-62)

기준: `docs/design/runtime-spike.md` (P1~P5, 등록 202a8d4) · 결정: ADR `adr/0006`

```
radar.py list --in-scope → 조건마다 판정 에이전트(모델 + 읽기 도구 3개) → radar.py decide --apply → run_report
```

| 파일 | 무엇 |
|---|---|
| `pipeline.py` | 흐름. 코드가 순서를 쥐고 모델은 조건 하나만 판정한다 (`Verdict` 구조화 출력). human 조건은 모델에게 묻지 않는다 |
| `stub_model.py` | 정해진 답을 돌려주는 가짜 `Model` — API 없음 |
| `stub_script.json` | 스텁의 답 + 음성 대조 5개 |
| `run_eval.py` | eval 어댑터. `eval/runner/run_case.py`의 `build`·`grade`를 **그대로** 쓴다 |

```bash
python -m venv .venv && .venv/Scripts/pip install -r product/requirements.txt   # 레포 루트에서
.venv/Scripts/python product/spike/agents_sdk/run_eval.py --stub                 # 6케이스 + 음성 대조 5 — 종료 코드 0이어야 한다
.venv/Scripts/python product/spike/agents_sdk/run_eval.py --stub JHS-037 --k 3
```

## 스킬과 다른 점 (설계)

- **모델은 도구로 ADR을 쓸 수 없다.** 도구는 `snapshot_fetch`·`snapshot_search`·`read_repo_file` — 읽기뿐. ADR을 쓰는 길은 `decide --apply` 하나라서 R8(status 불변)이 프롬프트가 아니라 구조로 선다
- **모델은 prior(`revisit_last_checked`)와 ADR 본문을 보지 않는다.** 조건 하나(기술·용도·조건 문구·source_hint·threshold)만 받는다. 스킬은 `list` 출력 전체를 본다
- decide는 코드가 한 번만 부른다 — 스킬의 「decide 1회」 규칙이 구조로 선다

## 채점 연결

- 실행한 도구 호출을 스킬 transcript와 같은 `tool_use` 모양으로 남긴다 (`radar.py` → `Bash`, repo 파일 → `Read`). 기록은 **실제로 실행한 명령**이다. 모델이 낸 tool call 원문은 기록하지 않는다
- 격리: `radar.py`는 작업 디렉터리 사본(`runroot/scripts/`)에서 돈다. 사본 해시 = 스킬 파일 해시를 실행마다 대조하고(P4), 스킬 파일의 작업 트리 diff가 0인지도 본다
- P5: 기본 트레이스 exporter를 세기만 하는 processor로 **갈아 끼우고** tracing을 끈다. `NetGuard`가 DNS 해석과 `socket.connect`를 막고 기록한다 (한계: IP 리터럴로 이벤트 루프가 직접 여는 연결은 못 본다)

## 음성 대조 — 검사가 무는가 (CLAUDE.md 규율 3)

| 대조 | 넣은 결함 | 잡는 곳 |
|---|---|---|
| CTRL-false-alarm | 조용한 주에 met | 채점: result·pr·alerts |
| CTRL-hallucinated-url | 열지 않은 URL을 근거로 met | radar.py INV-2 → R2가 undetermined로 → 채점 |
| CTRL-malformed-output | 스키마에 안 맞는 출력 | 파이프라인이 undetermined로 안전 실패 + P3 `model_error` |
| CTRL-no-tool-call | 아무것도 안 열고 not_met | **채점은 통과한다** — P3 `tool_call_missing`만 잡는다 |
| CTRL-repo-path | radar.py를 레포 경로에서 호출 | 채점: 격리 검사 |

그 밖에 따로 확인한 것 (2026-09-30, 1회): NetGuard는 `api.openai.com` 해석과 IP 직접 연결을 둘 다 막았다. tracing을 켜면 counting processor가 trace 1·span 3을 셌다 — 「trace 0」이 빈 검사가 아니다.

## 결과 기록

| 날짜 | 모델 | P1 | P2 | P3 | P4 | P5 | 비고 |
|---|---|---|---|---|---|---|---|
| 2026-09-30 | stub | 6/6 | k=3 3/3 | 해당 없음 | diff 0 · 사본 동일 | 연결 0 · trace 0 | 음성 대조 5/5 잡힘. **통과 판정 아님** — 기준은 실제 모델 실행에만 적용 (사용자 결정) |

## 찾은 것

1. **의존성 충돌**: openai-agents 0.22.3은 `openai>=3`, litellm 최신 1.103.1은 `openai<3`. SDK 문서의 Claude 경로(LiteLLM 어댑터)가 설치 단계에서 이미 어긋난다. import는 되지만 런타임 동작은 미확인. P3와 같은 부류의 어댑터 위험 — 실제 실행 전에 경로를 골라야 한다
2. **eval 틈**: 근거 없이 낸 not_met도 채점을 통과한다(CTRL-no-tool-call). 스킬도 같다. radar.py R2는 met에만 근거를 요구한다
