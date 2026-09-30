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
| `models.py` | 실제 모델 연결 — LiteLLM 어댑터 + 요청 모양 기록 + `.env` 읽기(키를 `os.environ`에 넣지 않음) |

```bash
python -m venv .venv && .venv/Scripts/pip install -r product/requirements.txt   # 레포 루트에서
.venv/Scripts/python product/spike/agents_sdk/run_eval.py --stub                 # 6케이스 + 음성 대조 5 — 종료 코드 0이어야 한다
.venv/Scripts/python product/spike/agents_sdk/run_eval.py --stub JHS-037 --k 3
```

### 실제 모델(LiteLLM) 준비 — 메인 `.venv`와 따로

litellm은 `openai<3`을 선언하고 openai-agents는 `openai>=3`을 요구한다. 설치 순서로 억지로 맞춘다 (선언 충돌은 남는다 — pip가 경고한다).
Windows 경로 길이 제한 때문에 **짧은 경로**에 만든다 (긴 경로에서는 litellm 설치가 `OSError: No such file`로 실패).

```bash
python -m venv <tmp>/p3v
P=<tmp>/p3v/Scripts/python
$P -m pip install -r product/requirements.txt && $P -m pip install litellm==1.103.1 && $P -m pip install openai==3.22.1
# tiktoken cl100k_base 캐시 — 실행 밖에서 한 번 (litellm 1.103.1 번들에 빠져 있다)
D="$LOCALAPPDATA/jhs-radar/tiktoken"; mkdir -p "$D"
curl -sSfL -o "$D/9b5ad71b2ce5302211f9c61530b329a4922fc6a4" https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken
cp $(dirname $($P -c "import litellm,os;print(litellm.__file__)"))/litellm_core_utils/tokenizers/{ec7223a39ce59f226a68acc30dc1af2788490e15,fb374d419588a4632f3f557e76b4b70aebbca790} "$D/"
$P product/spike/agents_sdk/run_eval.py --model litellm JHS-001 [--no-register]   # .env의 ANTHROPIC_API_KEY. 기본은 모델 표 등록
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

### P3 조기 확인 — JHS-001 k=1, Claude Sonnet 5.5 · effort medium, LiteLLM 어댑터 (2026-09-30, API 합계 약 $0.29)

**P3 확인이지 스파이크 판정이 아니다** — P1~P6 전체 실행은 capture HITL까지 만든 뒤.

| 실행 | 설정 | 채점 | P3 | P5 | 토큰 in/out | 비용 | 요청 모양 |
|---|---|---|---|---|---|---|---|
| 154713 | 기본값 | 실패 | 6 (`APIConnectionError`) | **시도 30** — `openaipublic.blob.core.windows.net` | 0 | $0 | Anthropic에 닿기 전에 막힘: tiktoken이 cl100k_base를 받으러 감 |
| 154845 | 기본값 + tiktoken 캐시 | 통과 | 0 | 0 | 24,460 / 1,861 | $0.068 | adaptive thinking + `output_config.effort=medium`, 구조화 출력은 가짜 도구 `json_tool_call`(tool_choice 없음) |
| 154947 | `--register` | 통과 | 0 | **시도 1** — `raw.githubusercontent.com` | 29,428 / 1,423 | $0.073 | 구조화 출력이 네이티브 `output_format`으로 |
| 155126 | 같음 + NetGuard 호출 스택 기록 | 통과 | 0 | 시도 1 | 31,955 / 1,543 | $0.079 | 원인: `anthropic_beta_headers_manager.fetch_remote_beta_headers_config` |
| 155220 | `--register` + `LITELLM_LOCAL_ANTHROPIC_BETA_HEADERS` | 통과 | 0 | 0 | 29,420 / 1,428 | $0.073 | 네이티브 `output_format` 13/13 |

- **예측은 틀렸다.** 코드 읽기로는 기본값에서 400(강제 tool_choice·budget_tokens)을 예상했지만, litellm이 모델 표에 없는 `claude-sonnet-5-5`도 adaptive thinking 모델로 다뤄서 400이 없었다. thinking이 켜지면 구조화 출력용 도구를 **강제하지 않는** 분기로 간다
- 도구 호출 사이의 thinking 블록 왕복에서 400 없음 (요청당 메시지 최대 수 턴)
- **P5는 기본값으로 두 번 샌다** — tiktoken 파일(OpenAI 저장소)과 beta 헤더 표(GitHub). 둘 다 NetGuard가 막아서 알았다. 막지 않았으면 조용히 나갔을 것이다
- `output_format`은 Anthropic에서 폐기 예정인 이름이다 (현행은 `output_config.format`). 지금은 받아 준다
- **채택 (사용자 2026-09-30): 모델 표 등록**이 기본값. 네이티브 구조화 출력이 Anthropic 권장 방식에 가깝고, 가짜 도구 방식은 모델이 그 도구를 스스로 불러야 해서 호출이 늘면 파싱 실패 위험이 있다. `--no-register`로 기본값을 재현한다

## 찾은 것

1. **의존성 충돌**: openai-agents 0.22.3은 `openai>=3`, litellm 최신 1.103.1은 `openai<3`. SDK 문서의 Claude 경로(LiteLLM 어댑터)가 설치 단계에서 이미 어긋난다. **런타임은 돈다** (openai 3.22.1 강제, JHS-001 5회 — 위 P3 표). 선언 충돌이라 litellm·openai 업그레이드 때 깨질 수 있다
2. **eval 틈**: 근거 없이 낸 not_met도 채점을 통과한다(CTRL-no-tool-call). 스킬도 같다. radar.py R2는 met에만 근거를 요구한다
3. **P5 누수 두 곳 (litellm 기본값)**: tiktoken 캐시 누락 → `openaipublic.blob.core.windows.net`, beta 헤더 표 → `raw.githubusercontent.com`. `models.py`가 캐시 경로·`LITELLM_LOCAL_*`로 막는다. LangGraph 쪽(langchain-anthropic)도 같은 NetGuard로 본다
