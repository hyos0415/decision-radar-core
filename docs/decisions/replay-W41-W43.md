# 같은 주 재생 — W41~W43 입력으로 제품 detect를 다시 돌려 그 주 스킬 detect와 견준다 (HYO-164)

**상태: 실제 실행 · 대조 끝** (2026-11-14). 러너 · 회귀 테스트는 11/11, 사용자가 「비용」을 보고 승인한 뒤 11/14에 돌렸다. 아래 「결과」 참고.

- 등록: `docs/design/comparison-protocol.md` §7 「같은 주 재생」(11/3) + 그 **변경 (2026-11-11)** — 제품 코드를 main 4eab096으로, 자리 질의 켬(11/4 프로필) · 1차 출처 링크 끔
- 묻는 것: 같은 주 입력에 두 체계는 무엇을 냈고 어디서 갈렸나. §8 변경(11/7)으로 **「두 체계를 합치면 각자 혼자일 때보다 더 잡나」**(11/23 합집합 판단, HYO-167)의 근거 — 같은 순간 짝(W46 · W47, HYO-166)과 함께 본다. G4 판정에는 쓰지 않는다
- 러너: `pipeline/replay.py` · 테스트 `pipeline/tests/test_replay.py` · 원자료 `pipeline/out/replay-W41-W43/`(git 밖 — 실행별 결과와 원자료를 모두 남긴다)

## 짝

| 주 | 스킬 detect (실행 시각, KST) | 스킬 초안 → 사람 결정 | 제품 입력 |
|---|---|---|---|
| W41 | 10/12 09:38 (adr PR #8) | 0012 vLLM v0.31.0 → adopted · 0013 FastAPI 0.143.0 → adopted (10/12) | corpus@2026-W41 (10/6 얼림) |
| W42 | 10/19 09:39 (adr PR #13) | 0015 Langfuse Python SDK v5.0.0 → adopted (10/20) | corpus@2026-W42 (10/13) |
| W43 | 10/26 09:38 (adr PR #16) | 0016 Anthropic Python SDK v2.0.0 → adopted (10/28 결정 커밋, adr PR #17 merge 11/2) | corpus@2026-W43 (10/20) — 얼려 둔 관문 결과 `detect/2026-W43`도 참고 |

사람 결정 = 그 초안 파일의 status가 proposed에서 처음 바뀐 커밋. 제품만 낸 항목에는 사람 라벨을 새로 붙이지 않는다 (후견지명).

## 방법

한 주마다 (제품 CLI를 그대로 자식 프로세스로 — 주간 사슬 `collector.weekly`와 같은 단계):

1. **prepare** (무료 · 읽기만) — ADR repo = 스킬 초안 커밋의 부모(`git archive`) · 대상 레포 4개와 팀원 스택 = 그 시각 이전 기본 브랜치 마지막 커밋(`git clone --shared` 사본, `pipeline.basis.materialize`의 tree 자리에 `dated_tree`) · 등급 파일 = 지금 `tiers.json`을 그 주 스택으로 자름 · 스킬 초안 파일과 사람 결정
2. **tiers** — 등급 재분류 (claude-sonnet-5-5 API, 지문 바뀐 기술만, 커밋 · push 없음)
3. **gate** — `pipeline.profile_filter run --gate gpt-6.1-sol` (얼린 코퍼스 → 레인 A 관문 · 경보 그물)
4. **slots** — `pipeline.slots run` (구독 `claude -p` 한 세션, 프로필 = adr `radar-slots.yaml` 97cdf63)
5. **feed** — `pipeline.detect_feed --no-fetch` (detect 입력 + 탈락 추적 `trace.jsonl`)
6. **detect** × 3 — `product/radar/run_detect.py --model claude-cli --now <그 주 스킬 실행 시각>` (실행마다 새 폴더 · 새 ADR 사본)

대조 (`python -m pipeline.replay compare`):

- 같은 항목 = 정규화 패키지 이름 + 릴리스 주 버전 (`skill_line.item_key` — 0.x는 둘째 자리까지). 제품 초안은 근거 URL이 가리키는 detect 입력 항목의 package · version으로, 스킬 초안은 스킬 줄과 같은 길(`union.resolve_package`)로 키를 만든다
- 「제품이 냄」 = 3번 중 2번 이상. 1번만 낸 것은 「(1번만)」 줄로 따로 남긴다
- 스킬만 → 갈린 단계 하나: **출처에 없음**(그 주 · 다음 주 코퍼스 어디에도 없음) · **수집 시점**(그 주엔 없고 다음 주에 있음) · **관문 탈락**(탈락 추적이 1 · 경보 그물 · 2a~2d에서 멈춤) · **상한에서 잘림**(3 칸 정리에서 멈춤) · **모델이 뺌**(detect 입력까지 갔는데 실행 과반이 버림 · 누락) · **이미 결정됨**(실행 과반이 「이미 있는 결정」으로 건너뜀). 같은 주 버전 릴리스가 여럿이면 가장 멀리 간 것으로 본다
- 제품만 → 그대로 적는다. detect 입력에 온 길이 11/3 뒤 생긴 길(자리 질의 · 경보 그물 · changelog 날짜 블록)이면 「고친 뒤 경로」

격리: 실제 ADR repo · radar-snapshots · 데스크톱 데이터 폴더(`events.jsonl` 등)를 단계마다 견주고 바뀌면 멈춘다. ADR repo PR · Inbox · 행동 로그 · radar-snapshots에 아무것도 쓰지 않는다 (§9 「두 도구를 같이 켜기」 밖).

## 비용 (무료 계획 실측 — `python -m pipeline.replay plan`, 11/11)

| 주 | 관문 모델 호출 (레인 A + 경보 그물) | 등급 재분류 | 구독 |
|---|---|---|---|
| W41 | 214 (224건 중) | 5 | 자리 질의 1 + detect 3 |
| W42 | 274 (652건 중) | 6 | 자리 질의 1 + detect 3 |
| W43 | 257 (356건 중) | 6 | 자리 질의 1 + detect 3 |
| 합 | 745 ≈ $2.3 (W43~W46 실측 $0.0031/회) | 17 ≈ $0.1 | 자리 질의 3세션 + detect 9번(실행마다 triage 1회 + 초안마다 1회) |

**러너 수정 (2026-11-14, 실제 실행 전 — 등록 내용은 그대로)**: 자리 프로필과 등급 파일을 「지금 HEAD의 마지막 커밋」에서 고르던 것을 **등록 시점 커밋으로 고정**했다 — 자리 프로필 adr `97cdf63`(§7 변경이 적은 그 커밋) · 등급 파일 radar-snapshots `c01954c`(11/11 계획 때 마지막 커밋). 11/13 adr에 자리 프로필 v2(`4ee275a`)가 들어가 그대로 돌렸으면 v1 코드(4eab096)가 v2 프로필을 읽었고, 등급 파일은 W47(11/17) 주간 재분류로 HEAD가 옮겨진다. 고정 뒤 무료 계획을 다시 돌려 위 숫자(관문 745 · 등급 17)가 그대로임을 확인했다. 회귀 테스트 `test_pinned_ignores_newer_head`(HEAD에 새 버전이 있어도 고정 커밋을 읽는다).

## 스텁 배관 확인 (11/11, 모델 없음 — 결과가 아니다)

W41~W43을 `--engine stub`으로 끝까지 돌렸다: 세 주 모두 날짜 기준 basis → 관문(스텁) → 자리 질의(스텁) → detect 입력 → detect 3번 정상 종료, 대조 표까지 나온다. 스텁 관문 · 스텁 detect라 항목 판단은 의미가 없다.

모델과 무관하게 이미 정해지는 것 하나 — **스킬 초안 4건 중 3건은 그 주 코퍼스에 없고 다음 주 코퍼스에 있다**(코퍼스 검색만으로 나온다):

| 스킬 초안 | 그 주 코퍼스 | 다음 주 코퍼스 |
|---|---|---|
| FastAPI 0.143.0 (W41) | 없음 | W42에 `fastapi@0.143.0` |
| Langfuse v5 (W42) | 없음 | W43에 `langfuse-python@v5.0.1` · `v5.0.2` |
| Anthropic Python SDK v2 (W43) | 없음 | W44에 `anthropic-sdk-python@v2.0.0` · `v2.1.0` · `v2.2.0` |
| vLLM v0.31.0 (W41) | `vllm@v0.31.0` 있음 | — |

셋은 「수집 시점」으로 갈린다 — 월요일 스킬과 지난 화요일 코퍼스를 짝지은 재생 설계(6일 차이)가 만든 몫이 크다(아래 한계). 실제 실행이 가르는 것은 **vLLM v0.31.0이 제품에서 어디까지 가나**와 **제품만 낸 항목**이다. 합집합 판단(「합치면 더 잡나」)에 쓰이는 것은 주로 뒤쪽이다.

## 결과 (실제 실행 2026-11-14)

실행: 세 주 × detect 3번, 9번 모두 정상 종료. 격리 검사는 단계마다 통과했다(ADR repo · radar-snapshots · 데스크톱 데이터 폴더 바뀜 없음). 실제 비용은 API $2.47(관문 $2.38 · 등급 재분류 $0.08, 계획 ≈ $2.4)이다. 구독으로는 자리 질의 3세션과 detect 9번(모델 호출 58회, API로 환산하면 약 $2.0)을 썼다. 원자료는 `pipeline/out/replay-W41-W43/`(`compare.json` · `compare.md` · 주별 `detect/k1~k3`)에 있다.

### 합계

| | 건수 |
|---|---|
| 스킬 초안 | 4 |
| 제품이 냄 (3번 중 2번 이상) | 17줄 (주를 걸쳐 다시 나온 것을 하나로 세면 13개) |
| 둘 다 | 1 (vLLM 0.31.0, W41) |
| 스킬만 | 3, 모두 **수집 시점** |
| 제품만 | 16줄, 서로 다른 항목 12개 |

읽은 것:

1. **그 주 입력에 있던 스킬 초안은 제품도 잡았다.** 그 주 코퍼스에 있던 스킬 초안은 vLLM v0.31.0 하나이고, 제품은 이것을 3/3번 냈다. 관문이나 모델이 놓쳐서 「스킬만」이 된 항목은 0건이다.
2. **스킬만 3건은 모두 모델 실행 전에 이미 정해진 「수집 시점」이다**(위 스텁 표). 다음 주 입력에서 제품이 어떻게 했는지는 등록된 대조 밖이라 보조 관찰로만 적는다.
   - FastAPI 0.143.0: W42 detect 입력까지 왔다. 제품은 3/3번 「이미 있는 결정 0013」으로 건너뛰었다(`report.json`의 `skipped_existing`). 스킬이 W41에 내지 않았다면 W42에 냈을지는 이 재생으로 알 수 없다
   - Langfuse v5: W43에 v5.0.1이 들어와 3/3번 초안이 됐다. 다만 0015의 후속 패치 대응 초안(「관련 결정: 0015」)이고, v5 메이저 변경을 새로 잡은 것은 아니다
   - Anthropic Python SDK v2: W44 코퍼스에 있다. 재생 범위 밖이다
3. **제품만 16줄은 서로 다른 항목으로 12개다.** 4건은 주를 걸쳐 다시 나왔다: Bedrock 서울 리전(W41 · W42), Sonnet 5.5 on AWS(W41 · W42), k6 1.8.2(W42 · W43), Claude Haiku 5.5(W42 · W43). 재생은 주마다 ADR repo를 그 주 스킬 상태로 되돌리기 때문에 제품 초안이 쌓이지 않는다. 실제로 매주 돌렸다면 둘째 주에는 「이미 있는 결정」으로 건너뛰었을 것이다. 16줄이 detect 입력에 온 길은 둘로 나뉜다. 7줄(자리 질의 5 · changelog 날짜 블록 2)은 11/3 뒤에 생긴 「고친 뒤 경로」이고, 9줄은 11/3 전에도 있던 관문 길이다. 제품만 항목에는 사람 라벨을 붙이지 않았다(후견지명). 그래서 이 항목들이 쓸모 있었는지는 이 재생이 답하지 않는다.
4. **키 규칙 한계 하나**: W42 Claude Haiku 5.5는 제품이 3번 모두 냈다. 그런데 패키지가 없는 페이지 변경 항목이라 근거 URL로 키를 만든다. 그래서 2/3(model-deprecations 페이지)과 「1번만」(release notes 날짜 블록)으로 갈렸다. 등록된 규칙 그대로 두고 여기에 적는다. 스킬 쪽과 겹치지 않는 항목이라 「둘 다 · 스킬만」 숫자는 바뀌지 않는다.

**합집합 판단(HYO-167)에 넘기는 것**: 이 재생만 보면 두 체계가 서로 더해 주는 것이 다르다. 스킬에 제품을 더하면 **항목**이 늘어난다(스킬이 내지 않은 것 12개). 제품에 스킬을 더하면 **시점**이 당겨진다(3건을 6일 일찍). 그 외에는 제품의 관문이나 모델이 놓친 스킬 항목이 없었다. 열린 것은 둘이다. 6일 차이는 재생 설계(월요일 웹 검색과 지난 화요일 코퍼스)가 만든 몫이 커서 같은 순간 짝(W46 · W47, HYO-166)으로 다시 본다. 제품만 12개가 쓸모 있었는지는 판정하지 않았다.

### 주별 대조 (`compare.md` 그대로)

합계 — 스킬 4 · 제품 17 · 겹침 1 · 합집합 20 (관찰, 문턱 없음)

#### 2026-W41 (스킬 실행 2026-10-12 09:38 · adr PR #8) — 둘 다 1 · 스킬만 1 · 제품만 4
제품 detect 3/3번 정상

| 구분 | 항목 | 스킬 초안 · 사람 결정 | 제품 (냄/K) | 갈린 단계 · 경로 |
|---|---|---|---|---|
| 둘 다 | vLLM 0.31.0 | 0012 · adopted | 3/3 | 관문 |
| 스킬만 | FastAPI 0.143.0 | 0013 · adopted | 0/3 | **수집 시점** — 그 주 코퍼스엔 없고 다음 주 코퍼스에 있다: github-release:fastapi/fastapi@0.143.0 |
| 제품만 | Amazon Bedrock Claude Seoul region  | — | 2/3 | 자리 질의 (11/4 · 고친 뒤 경로) · Amazon Bedrock expands Claude model availability to India, South Korea, and Singapore |
| 제품만 | Claude Sonnet 5.5 5.5 | — | 3/3 | 자리 질의 (11/4 · 고친 뒤 경로) · Claude Sonnet 5.5 now available on AWS |
| 제품만 | GPT-6 Sol  | — | 2/3 | 관문 · OpenAI GPT-6 Sol and GPT-6 Luna are now generally available on Amazon Bedrock |
| 제품만 | GPT-6.1 Sol 6.1 | — | 2/3 | 자리 질의 (11/4 · 고친 뒤 경로) · GPT 6.1 Sol: Near-Astra intelligence for a fifth of the price |
| (1번만) | anthropic 1.11.0 | — | 1/3 | 관문 |

#### 2026-W42 (스킬 실행 2026-10-19 09:39 · adr PR #13) — 둘 다 0 · 스킬만 1 · 제품만 6
제품 detect 3/3번 정상

| 구분 | 항목 | 스킬 초안 · 사람 결정 | 제품 (냄/K) | 갈린 단계 · 경로 |
|---|---|---|---|---|
| 스킬만 | Langfuse 5.0.0 | 0015 · adopted | 0/3 | **수집 시점** — 그 주 코퍼스엔 없고 다음 주 코퍼스에 있다: github-release:langfuse/langfuse-python@v5.0.1; github-release:langfuse/langfuse-python@v5.0.2 |
| 제품만 | k6 1.8.2 | — | 3/3 | 관문 · grafana/k6 v1.8.2 |
| 제품만 | langchain-core 1.6.8 | — | 3/3 | 관문 · langchain-ai/langchain langchain-core==1.6.8 |
| 제품만 | Amazon Bedrock Claude Seoul region  | — | 3/3 | 자리 질의 (11/4 · 고친 뒤 경로) · Amazon Bedrock expands Claude model availability to India, South Korea, and Singapore |
| 제품만 | Claude Sonnet 5.5 on Amazon Bedrock 5.5 | — | 3/3 | 자리 질의 (11/4 · 고친 뒤 경로) · Claude Sonnet 5.5 now available on AWS |
| 제품만 | Claude Haiku 5.5 5.5 | — | 2/3 | 관문 · [anthropic] https://docs.claude.com/en/docs/about-claude/model-deprecations 변경 (2026-W41 → 2026-W42) |
| 제품만 | OpenAI Decisions API  | — | 3/3 | 관문 · changelog 날짜 블록 (11/4 · 고친 뒤 경로) · [openai] platform.openai.com/docs/changelog Oct 6: Feature · gpt-6-luna · v1/decisions · Released the Decisions API in beta with gpt-6-luna… |
| (1번만) | Claude Haiku 5.5 5.5 | — | 1/3 | 관문 · changelog 날짜 블록 (11/4 · 고친 뒤 경로) |

#### 2026-W43 (스킬 실행 2026-10-26 09:38 · adr PR #16) — 둘 다 0 · 스킬만 1 · 제품만 6
제품 detect 3/3번 정상

| 구분 | 항목 | 스킬 초안 · 사람 결정 | 제품 (냄/K) | 갈린 단계 · 경로 |
|---|---|---|---|---|
| 스킬만 | Anthropic Python SDK 2.0.0 | 0016 · adopted | 0/3 | **수집 시점** — 그 주 코퍼스엔 없고 다음 주 코퍼스에 있다: github-release:anthropics/anthropic-sdk-python@v2.0.0; github-release:anthropics/anthropic-sdk-python@v2.1.0; github-release:anthropics/anthropic-sdk-python@v2.2.0 |
| 제품만 | FastAPI 0.145.0 | — | 3/3 | 관문 · fastapi/fastapi 0.145.0 |
| 제품만 | k6 1.8.2 | — | 3/3 | 관문 · grafana/k6 v1.8.2 |
| 제품만 | langfuse 5.0.1 | — | 3/3 | 관문 · langfuse/langfuse-python v5.0.1 |
| 제품만 | llama.cpp 0.7.0 | — | 3/3 | 관문 · ggml-org/llama.cpp v0.7.0 |
| 제품만 | vLLM 0.32.0 | — | 2/3 | 관문 · vllm-project/vllm v0.32.0 |
| 제품만 | Claude Haiku 5.5 5.5 | — | 3/3 | 관문 · changelog 날짜 블록 (11/4 · 고친 뒤 경로) · [anthropic] docs.claude.com/en/release-notes/api October 14, 2026: We've launched OpenTelemetry export in beta, so you can trace Messages AP… |

얼려 둔 W43 관문(10/20 코드) detect 입력 — 참고: anthropics/claude-code v2.1.299 (재생 입력에도); electron/electron electron v45.0.0-beta.4 (재생 입력에도); vllm-project/vllm v0.32.0 (재생 입력에도); [anthropic] https://docs.claude.com/en/release-notes/api 변경 (2026-W42 → 2026-W43) (재생 입력엔 없음); [openai] https://platform.openai.com/docs/deprecations 변경 (2026-W42 → 2026-W43) (재생 입력에도)

## 한계

- 6일 시점 차이: 스킬은 월요일 아침 웹을 찾고, 제품은 지난 화요일에 얼린 코퍼스를 읽는다 — 「수집 시점」 갈림의 일부는 재생 설계가 만든 차이다 (같은 순간 짝이 이 차이를 없앤다)
- 4eab096의 일부 수정은 이 주들의 놓친 것을 보고 만들었다 — 「고친 뒤 경로」 표시로 가른다
- 자리 질의 프로필은 그 주에 없던 파일이다(11/4). 1차 출처 링크는 받지 않았다 — 실제 제품(11/7~)보다 근거가 적을 수 있다
- 대상 레포 시점은 커미터 시각 기준이다 — 그 시각 전에 커밋했지만 늦게 push한 커밋은 그 시각에 원격에 없었을 수 있다
- k=3 · 3주 · 스킬 초안 4건 — 사례 수준이다. 비율이 아니라 건별로 읽는다
