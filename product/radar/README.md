# product/radar — Decision Radar 제품 (LangGraph 비교 모드)

런타임: LangGraph + Claude Sonnet 5.5 · effort medium (ADR 0009). 기준: `docs/design/comparison-protocol.md` §3·§4·§8·§9 · 명세 `docs/spec-work-order.md` R1~R16.
스파이크(`../spike/`)는 기록이다 — 지우거나 고치지 않았다. 여기는 그 위에 올린 제품 패키지다 (HYO-42·43·24·45의 스텁 단계).

```
revisit   list(radar.py list) ─Send─▶ judge(create_agent+Verdict) ─▶ decide(radar.py decide --apply)           [스파이크에서 옮김]
capture   create_agent(match_adr·propose_adr) + HumanInTheLoopMiddleware + SqliteSaver, 2턴은 새 프로세스         [스파이크에서 옮김 — HITL·체크포인트 설계는 HYO-44]
detect    load ─▶ triage ─▶ screen ─Send─▶ draft ─▶ write ─▶ (validate 실패 시) repair ─▶ report              [새로 만듦]
          (코드) (모델)    (코드)         (모델)   (코드)                  (모델+코드)
```

## detect — 누가 무엇을 하나

| 노드 | 하는 일 | 규칙 |
|---|---|---|
| `load` (코드) | 대상 repo의 `CLAUDE.md`·manifest 읽기 + `radar.py snapshot feed` | HYO-45 — 웹 대신 스냅숏 도구 |
| `triage` (모델) | 소식을 거른다 → 후보(technology·dependency·affected) / 버린 것(이유) | A5b(릴리스는 breaking·security·deprecation·major만) · R10 · R16 |
| `screen` (코드) | 후보마다 `radar.py match` — **overlap이면 초안 없이 `skipped_existing`**. affected가 비었거나 대상 repo에 없으면 버린다 | **R14** · R10 — 프롬프트가 아니라 코드·radar.py가 집행 (규율 1) |
| `draft` (모델) | 후보마다 초안 서술(title·use·context·decision·evidence·revisit_when). 병렬 | R17 |
| `write` (코드) | technology·affected·related는 후보·match 결과로 **코드가 덮어쓰고**, `ops.propose_adr`가 next-id → `proposed`로 렌더 → `radar.py validate` | **R8** — status를 쓰는 노드·칸이 없다 |
| `repair` (모델+코드) | `write`에서 `radar.py validate`가 거부한 초안만 — 모델이 오류 메시지와 함께 서술을 **한 번** 고쳐 쓰고, **파일은 코드가** 같은 번호로 다시 쓴 뒤 validate를 다시 돈다. 그래도 실패면 `P3_errors`의 `draft_invalid`로 센다 | 스킬의 「이번 실행에서 새로 쓴 초안만 고친다」(SKILL.md 절대 규칙 7). 모델 도구는 여전히 읽기뿐 |

- 소식이 없으면 모델을 부르지 않는다 (R3 — 조용한 주가 정상). 후보가 없으면 PR도 없다 (`pr.needed = 초안 수 ≥ 1`)
- 모델에게 주는 도구는 읽기(`snapshot_fetch`·`snapshot_search`)뿐이다. 초안 파일은 모델이 아니라 그래프가 쓴다
- 겹침(R14)·status(R8)·validate(R17 형식)·INV-1(기존 ADR 무변경)은 `radar.py`와 코드 몫이라 프롬프트(`detect_prompts.py`)에 규칙을 더 쓰지 않았다
- `radar.py`는 **0줄 수정**이다. 실행마다 P4가 `git diff`와 사본 해시로 확인한다

## 실행

```bash
.venv/Scripts/python product/radar/run_eval.py --stub                       # 53케이스 + 음성 대조 13 (API 없음) — 종료 코드 0
.venv/Scripts/python product/radar/run_eval.py --stub JHS-046 JHS-051 --k 3
.venv/Scripts/python product/radar/run_eval.py --stub JHS-046 --controls    # 지정 케이스 + 음성 대조
.venv/Scripts/python product/radar/run_eval.py --model anthropic JHS-011    # 실제 Claude (API 과금, .env 키) — 케이스 명시. 아직 실행하지 않았다
.venv/Scripts/python product/radar/test_product.py                          # 구조 회귀 (모델 없음)
.venv/Scripts/python product/radar/usage.py eval/runs/<실행>                 # 호출 단위 사용량 요약
```

산출물: `eval/runs/<시각>-product-<모델>/<case>-<i>/` (transcript*.jsonl · grade.json · 작업 디렉터리 사본) + 실행 루트의 `llm_calls.jsonl`.

## 스텁 통과는 배관 검증일 뿐이다

스텁은 정해진 답을 낸다(`stub_script.json`, 스파이크 대본 위에 얹음). 스텁이 53/53을 통과해도 말해 주는 것은 **그래프·도구·`radar.py`·채점기가 이어져 있다**는 것뿐이다.
모델이 소식을 제대로 거르는지, 근거를 지어내지 않는지, 프롬프트가 충분한지는 **재지 못했다.** 그것은 실제 모델 실행(`--model anthropic`)에서만 나온다 — 그 전에 이 수치를 제품의 정확도로 인용하지 않는다.
스텁 코드는 케이스의 `expected`를 읽지 않는다. 값은 사람이 픽스처(스냅숏·대화·소식)를 읽고 적었고, detect는 케이스가 아니라 소식 url이 열쇠라서 7케이스가 같은 판단표를 쓴다.
(LEANN은 일부러 후보로 올린다 — 스텁은 R14를 모른다. JHS-051은 `screen`+`radar.py match`가 겹침을 잡아야 통과한다.)

**채점기는 한 벌이다.** revisit·detect는 `eval/runner/run_case.grade`(detect는 `grade_detect`), capture는 `eval/runner/capture.grade`를 그대로 부른다. 이 패키지에 채점 코드는 없다.
격리도 같다: `radar.py`는 실행 디렉터리의 사본에서 돌고, 제품 trace는 `Ctx.record`가 스킬 transcript와 같은 모양(tool_use)으로 남겨 `answer_key_access`가 같은 코드로 본다.

### 음성 대조 — 채점기가 무력하지 않다는 것 (규율 3)

| 대조 | 일부러 | 떨어져야 할 검사 |
|---|---|---|
| (스파이크 7) | false-alarm · hallucinated-url · malformed-output · no-tool-call · repo-path · capture-no-gate · capture-link-ignored | 스파이크와 같음 |
| `CTRL-detect-false-candidate` (JHS-046) | routine patch(fastapi)를 후보로 | `후보 집합 = []` · `pr.needed = False` · `새 초안 0` |
| `CTRL-detect-widened-affected` (JHS-012) | pydantic affected를 전 component로 (R16) | `pydantic affected 정확 일치` |
| `CTRL-detect-fabricated-basis` (JHS-053) | 소식에 없는 문장을 basis로 (R17) | `basis ⊂ 입력` |
| `CTRL-detect-repair-ok` (JHS-053) | 첫 초안이 validate에 걸림 → repair가 고쳐 **통과해야** 한다 | (통과 통제 — 호출 3, P3 0) |
| `CTRL-detect-repair-fails` (JHS-053) | repair도 같은 오류 | `새 초안 validate 통과` · P3 `draft_invalid` · 재시도 없음(호출 3) |
| `CTRL-repo-path-detect` (JHS-046) | radar.py를 레포 경로에서 호출 | `정답지·원본 레포 접근 0 (실행 격리)` |

폴더 이름의 `CTRL-repo-path`는 `eval/runner/test_leak.py`가 이 실행을 「잡혀야 하는 것」으로 보는 열쇠다 — 러너를 고치지 않고 detect 대조가 회귀 검사에 들어간다.
추가로 변이 검사를 한 번 했다: `screen`의 R14 집행을 끄면 JHS-051이 떨어진다 (`후보 집합 = []` · `skipped_existing (R14)` · `pr.needed = False` · `새 초안 0`).

## 호출 단위 기록 (HYO-46 최소판)

모델 호출이 끝날 때마다 `llm_log.CallLog`가 `{"type":"llm_call","flow","agent","model","input_tokens","output_tokens","estimated","cost_usd"[,"error"]}` 한 줄을 `ctx.events`에 넣는다.
`ctx.events`가 그대로 transcript가 되므로 호출 기록이 도구 호출과 **같은 파일에 일어난 순서대로** 남는다 (capture는 1턴·2턴 파일 둘 다). 채점기는 이 줄을 읽지 않는다.
비용은 `common/env.py`의 단가(Sonnet 5.5 $2/$10 per MTok). 스텁은 토큰을 글자 수/4로 추정하고(`estimated: true`) 비용을 0으로 둔다. `usage.py`가 케이스·flow별로 합산한다.
모델 이름은 응답의 `model_name`을 우선하고 없으면 실행 설정을 쓴다 — 실제 모델에서 이 필드가 채워지는지는 **미확인**.

## 설계 판단 — 스파이크에서 무엇을 옮기고 무엇을 import했나

| 대상 | 처리 | 근거 |
|---|---|---|
| `spike/common/` (프롬프트·스키마·도구 본체·보고서·스텁 판단·가드·**채점 어댑터**) | **import** (`_spike.py`가 경로만 올림) | `agents_sdk`도 쓴다 — 옮기면 건드리지 말라던 쪽이 깨진다. 한 벌이어야 비교에 런타임 차이만 남는다. agents_sdk를 접을 때 `git mv`로 끝난다 |
| revisit·capture 그래프, 도구, `resume.py`, `models.py` | **옮김** (복사 후 수정) | 제품이 직접 고칠 것(HITL — HYO-44, 호출 기록)이라 기록 폴더에 의존시키지 않는다. 로직은 그대로, 바뀐 것은 호출 기록 연결뿐 |
| `run_revisit`·`run_capture` (eval 실행) | **import** | 격리·P3~P6·2턴 프로세스 재개가 거기 있고 제품 변경이 필요 없었다 |
| `run_detect`·53케이스 `main`·스텁 대본 합치기 | **새로** (`evalkit.py` · `script.py`) | 스파이크 어댑터는 revisit·capture 11케이스 고정이다. 같은 `prepare`·`finish`를 쓴다 |
| 호출 기록을 어디에 두나 | transcript 안의 `llm_call` 줄 | 어댑터를 복제하지 않고 두 프로세스(capture 2턴)를 모두 덮는 길이 이것뿐이었다. 대신 `ops.capture_report`가 `ctx.events`를 전부 도구 호출로 가정해서 `capture.py`가 llm_call을 뺀 보기로 부른다 |
| detect의 `technology`·`affected`·`related`를 코드가 덮어씀 | 신규 | triage가 정한 후보의 정체가 draft에서 흔들리지 않게(R16). 대가: 스킬은 모델이 직접 쓴다 — 차이가 구조 효과로 읽히지 않게 판정문에 적을 것 |
| `screen`의 R10(빈 affected·없는 repo 버림) | 신규 | 스킬엔 프롬프트 규칙뿐이다 — **역수입 후보** (규율 8 — 스킬에도 넣을지는 사용자 결정) |

## 한계 · 아직 안 한 것

- 실제 모델로 돌린 적이 없다 — detect의 `ProviderStrategy(Triage/DraftBody)` 요청 모양(`output_config.format` 허용 스키마)과 effort medium에서의 판단은 미검증
- detect의 repair는 1회다. 그래도 `validate`에 걸리면 `draft_invalid`로 세고 파일은 남는다(실패한 초안). 스텁은 repair 성공·실패 경로를 각각 음성 대조로 본다 — 실제 모델에서 repair가 얼마나 도는지는 미측정
- **capture에는 repair 경로가 없다 (확인만, 고치지 않음)**: `propose_adr`가 승인 뒤 파일을 쓰고 `validate_errors`를 도구 결과로 돌려주지만, 모델이 고칠 도구가 `propose_adr`뿐이라 다시 제안하면 새 승인과 새 번호가 필요하다(같은 번호로 고쳐 쓰지 못한다). 스킬은 승인 뒤 그 초안 파일만 고친다. HITL 설계(HYO-44)와 함께 정할 것
- 라이브 모드(WebFetch)·PR/issue 생성은 범위 밖이다 — `check_scope`가 스냅숏 모드·dry-run만 허용한다
- detect의 의존성 → component 매핑을 코드로 읽지 않는다(모델이 CLAUDE.md를 읽고 R16을 지킨다). radar.py에 `affected` 검사 하위 명령이 생기면 코드로 옮긴다 — radar.py 수정은 P4 위반이라 하지 않았다

## 스킬과의 비대칭 (비교 판정문에 옮겨 적을 것)

같은 명세·같은 모델·같은 채점기·같은 `radar.py`지만 오케스트레이션이 다른 곳이다. 제품 쪽 차이가 구조의 효과인지 작업량의 차이인지 읽으려면 아래를 같이 적는다 (comparison-protocol §1 역수입 규칙).

| # | 비대칭 | 스킬 | 제품 | 처리 |
|---|---|---|---|---|
| 1 | **R10 집행** — 빈 affected·대상 repo에 없는 repo를 버림 | 프롬프트 지시뿐 | `screen` 노드의 코드 | 결정(2026-10-06): `radar.py` 하위 명령으로 옮겨 **skill@v2(HYO-75)에서 둘 다 부른다** — 그때까지 비대칭 |
| 2 | **후보의 정체를 코드가 덮어씀** — `write`가 technology·affected·related를 triage·match 결과로 | 모델이 초안에 직접 씀 | draft는 서술만(DraftBody), 나머지는 코드 | 제품만. 초안 중 affected 오류는 제품에서 구조적으로 못 생긴다 |
| 3 | **R14 match 호출 강제** | 프롬프트가 「남은 후보마다 match」라고 지시 — 모델이 건너뛸 수 있다 | `screen`이 후보마다 반드시 호출하고 overlap이면 초안 없이 `skipped_existing` | 제품만. 스킬의 match 누락률은 별도 계측 대상 |
| 4 | **초안 번호·파일 쓰기** | 모델이 `next-id`·Write·`validate`를 직접 부른다 | `write` 노드(코드)가 순서·번호·파일을 정한다. 모델 도구는 읽기뿐 | 제품만. R8/INV-1을 어길 도구 자체가 없다 |
| 5 | **repair** | 모델이 파일을 직접 고친다(횟수 제한 없음) | 모델은 서술만, 파일은 코드, **1회** | 사용자 결정(2026-10-06) |
| 6 | **run_report 작성** | 모델이 JSON 블록을 쓴다(옮겨 적다 틀릴 수 있다) | 코드가 만든다 | 제품만 — 전사(轉寫) 오류가 제품에선 없다 |
| 7 | **후보 하나당 컨텍스트** | 한 컨텍스트에서 전 후보를 처리 | triage 1회 + 후보마다 draft(병렬, 자기 소식만 봄) | 제품만 — 토큰·속도 비교에 영향 |
| 8 | **소식이 없을 때** | 모델이 돈다 | 모델을 부르지 않는다(R3) | 제품만 — 조용한 주의 비용 비교에 영향 |
| 9 | **스택·매핑 읽기** | 모델이 `CLAUDE.md`를 도구로 연다 | `load`(코드)가 읽어 프롬프트에 넣는다 | 제품만 — 도구 호출 수(R12)가 다르게 센다 |
| 10 | **비용 단위** | `claude -p` 구독 | API 호출, `llm_call`로 토큰·달러 | 프로토콜 §3 — 토큰으로 나란히 두고 달러는 API 단가로 환산 |
| 11 | **R16 매핑 집행** | 모델 | 모델 (같음) | 대칭 — `radar.py`에 `affected` 검사가 생기면 R10과 함께 옮길 후보 |
