# HYO-124 — 등급 분류 입력에 importance-policy §2 예시 넣기

등급 분류 모델이 `docs/design/importance-policy.md` §2-1의 예시 등급을 따르지 않았다. 예를 들어 pydantic·jinja2를 L6으로, uvicorn을 L1로 냈다(예시는 셋 다 L4). requests·typesafe-sdk는 분류 불가로 냈고(예시는 L8·L5), langfuse는 L5만 냈다(§4 예시는 L3+L5). 이 문서는 프롬프트에 예시를 넣기 전과 뒤를 같은 정답에 대어 보는 측정의 등록(§1)과 결과(§2)다.

## 1. 등록 (2026-10-16, 사용자 — 결과 0건)

### 1-1. 비교하는 두 쪽

| 쪽 | 코드 | 등급 프롬프트 (`pipeline/importance/prompts.py` `TIER_INSTRUCTIONS`) |
|---|---|---|
| before | main `be45021` | 등급 표 3칸(등급·이름·무엇) — 예시 없음 |
| after | 이 등록 커밋 (브랜치 `feat/hyo-124-tier-examples`) | 등급 표에 「예 (이 정책이 정한 배정)」 칸 추가(§2-1 예 열) + 규칙 두 줄: 「예 칸의 기술은 그 등급이 기본이다. `usage`가 예와 분명히 다른 쓰임을 말할 때만 다른 등급을 고른다」 · 두 등급 규칙에 「(예: langfuse = L3 호스팅 서비스 + L5 SDK)」 |

- 그 밖의 조건은 두 쪽이 같다. 모델은 `claude-sonnet-5-5`(effort medium, Anthropic API — 주간 재분류와 같다). 스크립트 검증 S1~S3과 사용자 입력(`tier_prompt`)도 같다
- Jev 질문(`TIER_MEANING`)에는 예시를 넣지 않는다. 지금 등급 분류에 쓰지 않는다
- 이 커밋은 지문(`tiers.fingerprint`)에 등급 프롬프트 판(`TIER_PROMPT_VERSION`)도 넣는다(사용자 결정). 측정은 빈 등급 파일에서 시작하므로 측정에는 영향이 없다

### 1-2. 입력과 실행

- 기술: `classify-stack --targets --extra pipeline/importance/stack-extra.json`. `adr/radar-targets.json`의 대상 4개(exclude 포함)와 stack-extra 5개를 합쳐 **95개**다
- 실행마다 빈 등급 파일에서 시작한다(사람 수정 없음 → 95개 모두 모델을 부른다). **k=3**씩 돌린다. 순서는 before 1·2·3, after 1·2·3이고 같은 날 돌린다
- before는 `be45021`을 체크아웃한 worktree에서, after는 이 브랜치에서 돌린다. 대상 repo는 두 쪽 모두 같은 폴더를 읽는다
- 출력: `eval/runs/hyo124/{before,after}-{1,2,3}.json`, 호출 기록 `…-calls.jsonl`(gitignore)
- 예상 비용: 570회, 약 $1.1. **$3를 넘으면 멈춘다**

### 1-3. 정답

- 검토본 `../radar-snapshots/tiers.json`의 **효과 값**(`TierStore.effective` — 자동 분류 위에 사람 수정을 덮은 값)이다. 2026-10-16 등급 검토에서 Claude가 제안하고 사용자가 판정했다. 사람이 등급을 고친 기술은 19개다
- 이 파일은 radar-snapshots에 아직 커밋되지 않았다. 그래서 측정 전에 `eval/runs/hyo124/gold-tiers.json`으로 복사해 얼리고, 해시를 적는다: sha256 `3fc0a396378f7b75c4a3664d79c7edc35d5e4180ff6e23c54e47e324c6f212fd`
- 일치는 등급 집합이 같을 때만 센다. 분류 불가는 틀림으로 세고, 따로도 센다

### 1-4. 채점과 판정 (`eval/importance/score_tier_examples.py`, 이 커밋에 고정)

95개를 둘로 나눠 센다. **예시 기술 24개**는 §2-1 예 열과 §4 두 등급 예에 이름이 나오고 지금 스택에 있는 기술이다(python · vllm · llama.cpp · langfuse · fastapi · pydantic · uvicorn · jinja2 · boto3 · anthropic · openai · google-genai · typesafe-sdk · kiwipiepy · pdfplumber · torch · transformers · pytest · ruff · k6 · python-dotenv · tzdata · requests · httpx). 나머지는 **그 밖 71개**다.

| 기준 | 내용 |
|---|---|
| T1 | 예시 기술의 일치 수(k 평균)가 after에서 **오른다** |
| T2 | 그 밖 기술의 일치 수(k 평균)가 after에서 **2개 넘게 떨어지지 않는다** |
| 판정 | k≥3이고 T1·T2를 모두 통과하면 **채택**한다(프롬프트와 지문 변경을 merge). 아니면 프롬프트를 되돌리고 결과만 남긴다 |

보고에는 쪽별·실행별 일치 수와 분류 불가 수, 그리고 **두 쪽의 다수결 답이 다른 기술**(또는 k번 중 답이 흔들린 기술)의 목록을 정답과 함께 싣는다.

### 1-5. 알려진 편향 · 한계

- **예시 기술 24개는 답이 프롬프트에 있다.** T1은 「예시를 따르는가」만 잰다. 일반화되는지는 T2로만 본다
- **검토본은 before 쪽에 유리하다.** 사람이 고치지 않은 76개의 값은 before와 같은 모델·프롬프트가 낸 답을 사람이 받아들인 것이다. after가 다르게 답하면 받아들일 만한 답이어도 틀림으로 센다. 채점은 바꾸지 않는다. 대신 다르게 나온 기술 목록을 사람이 본다
- 검토본 초안은 Claude가 §2를 보고 제안했다. 그래서 예시 기술의 정답이 예시와 같은 것은 어느 정도 만들어진 결과다
- 예시와 검토본이 다른 기술이 하나 있다. **httpx**는 예시가 L8, 검토본이 L7이다(medical-mate-ai에서 `fastapi.testclient`용으로 쓴다). 등록대로 검토본에 대어 센다
- 95개는 독립 표본이 아니다(같은 repo 4개). 통계 검정은 하지 않고 수치와 목록으로 보고한다

### 1-6. 채택되면

지문이 바뀌므로 다음 주간 실행(10/20 `weekly-tiers`)이 **사람이 등급을 고치지 않은 기술 76개를 한 번 다시 분류한다**(약 $0.15). 사람이 받아들였던 값도 바뀔 수 있다. 결과 PR에서 다르게 나온 목록을 보고, 지킬 값은 그 전에 overrides로 고정한다.

## 2. 결과 (2026-10-16 — before `be45021` · after `ea718a1`)

실행 6회, 모두 95개를 분류했고 호출 오류는 0건이다. 비용은 before $0.33 × 3, after $0.40 × 3, **합계 $2.19**다(after는 프롬프트가 길어 입력 토큰이 115,872 → 149,312). 원자료는 `eval/runs/hyo124/`(gitignore)에 있다. 정답 사본의 sha256은 등록값과 같다.

**등록 이탈 하나**: 실행 스크립트(`run.sh`)의 누적 비용 검사가 Windows 파이썬에 `/c/…` 경로를 넘겨 파일을 못 찾았다. 그래서 매번 $0을 출력했고, **$3 상한은 실제로 지켜지지 않았다**. 실제 합계가 상한 아래라 결과에는 영향이 없다.

### 2-1. 채점 (`score_tier_examples.py`, 등록한 그대로)

| 쪽 | 예시 기술 일치 (/24) | 그 밖 일치 (/71) | 분류 불가 — 예시 | 분류 불가 — 그 밖 |
|---|---|---|---|---|
| before | 16 · 16 · 17 (평균 16.33) | 56 · 56 · 56 (56.00) | 3 · 3 · 3 | 7 · 7 · 6 |
| after | 22 · 22 · 22 (평균 22.00) | 59 · 56 · 56 (57.00) | 0 · 0 · 0 | 7 · 9 · 10 |

- **T1 통과** (16.33 → 22.00) · **T2 통과** (56.00 → 57.00) · k=3 → 등록 기준으로 **채택**
- 예시 기술에서 고쳐진 것: pydantic · jinja2(L6 → L4) · uvicorn(분류 불가 → L4) · requests(→ L8) · typesafe-sdk(→ L5) · transformers(L5 → L6)
- 예시 기술인데 after에서도 틀린 2개:
  - langfuse — 3번 모두 L5만 냈다(정답 L3+L5). 이유: 「Cloud를 쓴다는 정보가 없다」. 프롬프트의 두 등급 예로는 부족했다
  - python — 3번 모두 L0+L1을 냈다(정답 L0). 이유: 「Dockerfile `python:3.12-slim`」. 예시가 이미지 `python:3.12-slim`을 L1에 두고 있어서다

### 2-2. 그 밖 기술 — 일치 수는 비슷하지만 내용이 바뀌었다

- 나아진 것: pyyaml(L6 → L8) · langgraph-checkpoint-sqlite(L5 → L6) · openai-agents(L4+L5 → L4) · sentencepiece · sentence-transformers(분류 불가 → L6) · @electron-toolkit/utils(흔들림 → L8)
- **나빠진 것**: tailwindcss · tw-animate-css(3번 모두 분류 불가), lucide-react · prettier(3번 중 2번 분류 불가), langgraph-checkpoint(L6 → L4). 분류 불가가 된 쪽의 이유는 「**정책 예시에 없고** usage가 직접 의존성이라는 정보뿐」이었다. 「예 칸의 기술은 그 등급이 기본」이라는 규칙이 거꾸로 읽혀, 예시에 없는 것을 모르는 것으로 다루는 부작용이다. 그 밖 분류 불가 평균이 6.67 → 8.67로 늘었다
- T2는 나아진 것과 나빠진 것이 서로 상쇄돼 통과했다. 일치 수만으로는 이 부작용이 안 보인다

### 2-3. 채택하면 10/20 재분류에서 바뀌는 값 (§1-6)

사람이 등급을 고치지 않은 기술 중 after의 다수결이 검토본과 다른 것은 6개다. 지문이 바뀌면 10/20 주간 실행이 이 값들을 덮는다.

| 기술 | 검토본 | after (3번) |
|---|---|---|
| langgraph-checkpoint | L6 | L4 × 3 |
| @electron-toolkit/preload | L6 | L8 × 3 |
| tailwindcss | L6 | 분류 불가 × 3 |
| tw-animate-css | L8 | 분류 불가 × 3 |
| lucide-react | L8 | L8 × 1 · 분류 불가 × 2 |
| prettier | L7 | L7 × 1 · 분류 불가 × 2 |

분류 불가가 되면 그 기술의 소식은 이번 주 후보에서 빠진다(v1.1 U4).

### 2-4. 판정과 후속 (2026-10-16, 사용자)

- **채택**한다(등록 기준 T1·T2 통과). 프롬프트 예시와 지문의 프롬프트 판을 merge한다
- §2-3의 6개는 10/20 재분류 전에 **검토본 값 그대로 overrides에 고정**했다(`../radar-snapshots/tiers.json` overrides 35 → 41, note 「HYO-124 채택 전 고정」). 고정 전 사본은 `eval/runs/hyo124/tiers-before-pin.json`이다
- 남은 위험: 다른 기술도 k=3 중 1번은 흔들렸다(@fontsource-variable/geist-mono · class-variance-authority · sonner가 1/3 분류 불가). 10/20 한 번의 재분류에서 이런 기술이 분류 불가로 떨어질 수 있다. 주간 요약 첫 줄의 분류 불가 수와 5% 경고로 확인한다
- 부작용(예시에 없는 기술을 분류 불가로)과 langfuse·python 미준수는 **HYO-128**로 넘긴다. 문구를 고치면 새 판으로 다시 등록하고 잰다(before 원자료를 다시 쓴다)

## 3. 새 판 v2 등록 (HYO-128, 2026-10-16 — 결과 0건)

§2의 채택 뒤에 남은 부작용과 예시 미준수 둘을 고치는 문구 v2를 같은 방식으로 다시 잰다. 이 절은 등록이다. 결과는 §4에 따로 쓴다.

### 3-1. 바꾼 것 (`pipeline/importance/prompts.py` `TIER_INSTRUCTIONS`, 판 `1976fab1` → `0937e3ec`)

| 고칠 것 (§2 근거) | v2 문구 |
|---|---|
| 예시에 없는 기술을 분류 불가로 냈다. 이유는 「정책 예시에 없고 usage가 직접 의존성이라는 정보뿐」이었다(tailwindcss · tw-animate-css · lucide-react · prettier · sonner · class-variance-authority · @fontsource-variable/geist-mono) | 규칙 추가: 「예 칸은 몇 개만 보여 준 것이다. **예에 없는 기술은 「무엇」 칸의 정의로 고른다 — 예에 없다는 것은 `unsure`의 이유가 아니다.** 그 기술이 무엇인지(이름으로 널리 알려진 용도)는 아는 대로 판단해도 된다」. `unsure` 규칙의 조건을 「쓰임새 정보가 없거나 정의로 판단이 안 서면」에서 「그 기술이 무엇인지 모르거나, 알려진 용도와 `usage`로도 등급이 정해지지 않으면」으로 바꾼다 |
| python을 L0+L1로 냈다(정답 L0). 이유는 「예시에서 `python:3.12-slim`이 L1」이었다. 베이스 이미지는 등급 파일에 따로 있는 기술이다(`python:3-12-slim`) | 예 칸: L0 「언어 Python 3.12」 · L1 「이미지 `python:3.12-slim`」. 규칙 추가: 「언어와 그 언어의 베이스 이미지는 따로 분류되는 다른 기술이다 — 기술이 언어(`python`)면 L0, 이미지(`python:3.12-slim`)면 L1」 |
| langfuse를 L5만 냈다(정답 L3+L5). 이유는 「usage에 Cloud 정보가 없다」였다 | 예 칸: L5 「langfuse(L3+L5)」. 두 등급 규칙: 「langfuse는 L3+L5다 — SDK가 Langfuse 서비스로 보내므로 `usage`에 Cloud라는 말이 없어도 그렇다」 |

- 표의 등급·이름·무엇 칸과 「예 칸의 기술은 그 등급이 기본」 규칙은 그대로 둔다. 그 밖의 조건(모델 `claude-sonnet-5-5` effort medium · 스크립트 검증 S1~S3 · `tier_prompt` 입력)도 §1-1과 같다
- 판이 바뀌므로 지문(`tiers.fingerprint`)이 바뀐다(§1-1)

### 3-2. 비교와 실행

- **before는 §2의 원자료를 그대로 쓴다**(`be45021`, `eval/runs/hyo124/before-{1,2,3}.json`). 다시 돌리지 않는다. 입력이 같은지는 확인했다: 지금 main(`2328db9`)에서 스텁으로 `classify-stack`을 돌려 보니 기술 95개와 각 기술의 `usage`·신호가 before-1·after-1과 모두 같았다(2026-10-16)
- **after-v2**는 이 등록 커밋에서 k=3으로 돌린다. 빈 등급 파일에서 시작하고 95개 모두 모델을 부른다. 출력은 `eval/runs/hyo128/after-v2-{1,2,3}.json`과 `…-calls.jsonl`이다(gitignore)
- 실행: `bash eval/importance/run_tier_examples.sh eval/runs/hyo128 after-v2 3 2`. 예상 비용은 285회, 약 $1.25다(v1 after가 회당 $0.40). **누적 $2를 넘으면 다음 실행을 시작하지 않는다.** 실행 하나가 끝난 뒤에 검사하므로 마지막 실행만큼은 넘을 수 있다
- 비용 검사를 고쳤다(§2 등록 이탈): 경로를 파이썬 코드 문자열 안에 넣지 않고 인자로 넘긴다. HYO-124 호출 기록($2.19)을 복사한 폴더에 상한 $1을 주자 API를 부르기 전에 멈췄다(2026-10-16)
- v1 after(§2, `ea718a1`)는 다시 돌리지 않는다. 결과 보고에 나란히 싣지만 판정에는 쓰지 않는다

### 3-3. 정답

§1-3과 같은 파일이다(`eval/runs/hyo124/gold-tiers.json`, sha256 `3fc0a396…f212fd` — 측정 전에 다시 확인한다). 그 뒤 overrides에 고정한 6개(§2-4)는 검토본 값 그대로라 효과 값이 같다.

### 3-4. 채점과 판정 (`score_tier_examples.py --t3`)

| 기준 | 내용 |
|---|---|
| T1 | §1-4와 같다 — 예시 기술 일치 수(k 평균)가 before(16.33)보다 **오른다** |
| T2 | §1-4와 같다 — 그 밖 일치 수(k 평균)가 before(56.00)보다 **2개 넘게 떨어지지 않는다** |
| **T3** (새로) | 그 밖 기술의 **분류 불가 수(k 평균)가 before(6.67)보다 늘지 않는다** |
| 판정 | k≥3이고 T1·T2·T3을 모두 통과하면 **채택**한다(v2 문구를 merge). 아니면 v1 문구를 그대로 두고 결과만 남긴다 |

- T3은 §2-2의 부작용을 일치 수가 가리지 못하게 하려고 넣었다. 채점 스크립트의 `--t3`로 집행한다. 이 검사가 잡아야 할 것을 잡는지 §2 원자료로 확인했다: v1 after는 6.67 → 8.67이라 **T3 불통과**가 나온다. `--t3` 없이 돌리면 §2와 같은 판정(채택)이 나온다
- 보고에 함께 싣는 것(판정에는 쓰지 않는다): v1 after와의 쪽별 수치 · langfuse·python이 맞았는지 · §2-2·§2-4의 부작용 기술 각각의 답 · 두 쪽 다수결이 다르거나 흔들린 기술 목록

### 3-5. 알려진 편향 · 한계

- **v2 문구는 §2 결과를 보고 쓴 것이다.** langfuse·python은 문구에 기술 이름이 들어가 있어 사실상 답이 프롬프트에 있다(예시 기술 24개에 속하므로 T1에서 센다). 부작용 기술 7개는 그 밖 71개에 속하는데, 문구가 이들을 겨냥했다. 그래서 T3이 오르는 것은 어느 정도 예상된 결과다. 일반화는 겨냥하지 않은 그 밖 기술의 일치(T2)와 다수결 차이 목록으로 본다
- 「아는 대로 판단해도 된다」는 v1에는 없던 허락이다. 원래 분류 불가가 맞을 수 있는 기술까지 억지로 고를 수 있다. 예를 들어 numpy(정답 L6+L7)와 react-dom(정답 L4)은 before에서도 3번 모두 분류 불가였다. 이런 기술이 틀린 등급으로 바뀌면 T2에서 드러난다. 다수결 차이 목록에도 싣는다
- before는 같은 날 낮에 돌린 원자료다. 모델 쪽 변동(같은 모델 id)은 통제하지 않는다
- §1-5의 한계(검토본이 before 쪽에 유리 · 95개는 독립 표본이 아님 · httpx 예시/검토본 차이)는 그대로다

### 3-6. 채택되면

판이 `0937e3ec`로 바뀌므로 10/20 주간 `weekly-tiers`가 사람이 고치지 않은 기술을 한 번 다시 분류한다. v1 채택 때 예상한 재분류(§1-6)를 v2 판으로 하는 셈이다. 결과 PR에서 v2 다수결이 검토본과 다른 기술을 보고, 지킬 값은 10/20 전에 overrides로 고정한다(§2-4와 같은 방식).

## 4. v2 결과 (2026-10-16 — before `be45021` 원자료 · after-v2 `c16b117`)

after-v2 실행 3회 모두 95개를 분류했고 호출 오류는 0건이다. 비용은 회당 $0.45, **합계 $1.35**다(상한 $2 — 이번에는 비용 검사가 실제로 누적값 $0.45 → $0.90 → $1.35를 읽었다). 입력 토큰은 v1 149,312 → v2 174,582다. 원자료는 `eval/runs/hyo128/`(gitignore)에 있다. 정답 사본의 sha256은 등록값과 같다.

### 4-1. 채점 (`score_tier_examples.py --t3`, 등록한 그대로)

| 쪽 | 예시 기술 일치 (/24) | 그 밖 일치 (/71) | 분류 불가 — 예시 | 분류 불가 — 그 밖 |
|---|---|---|---|---|
| before (`be45021`) | 16 · 16 · 17 (평균 16.33) | 56 · 56 · 56 (56.00) | 3 · 3 · 3 | 7 · 7 · 6 (6.67) |
| v1 after (§2, 참고) | 22 · 22 · 22 (22.00) | 59 · 56 · 56 (57.00) | 0 · 0 · 0 | 7 · 9 · 10 (8.67) |
| **v2 after** | **24 · 24 · 24 (24.00)** | **59 · 60 · 60 (59.67)** | 0 · 0 · 0 | **1 · 1 · 1 (1.00)** |

- **T1 통과** (16.33 → 24.00) · **T2 통과** (56.00 → 59.67) · **T3 통과** (6.67 → 1.00) · k=3 → 등록 기준으로 **채택**
- 예시 기술 24개가 3번 모두 맞았다. v1에서 남았던 langfuse(→ L3+L5)와 python(→ L0)이 고쳐졌다. 둘 다 문구에 이름이 들어간 표적이다(§3-5)
- 남은 분류 불가 1개는 react-dom이다(3/3, 정답 L4). before·v1에서도 분류 불가였다. 이유는 「devDependencies라 번들되는지 알 수 없다」로, 새 `unsure` 조건(알려진 용도와 usage로도 등급이 정해지지 않을 때)에 맞는 답이다

### 4-2. §2의 부작용 기술 — 분류 불가는 사라졌고, 셋은 다른 등급으로 틀린다

| 기술 | 정답 | v1 after | v2 after (3번) |
|---|---|---|---|
| tw-animate-css | L8 | 분류 불가 × 3 | **L8** × 3 |
| prettier | L7 | L7 × 1 · 분류 불가 × 2 | **L7** × 3 |
| sonner · class-variance-authority · @fontsource-variable/geist-mono | L6 · L8 · L8 | 분류 불가 × 1씩 | 맞음 × 3 |
| tailwindcss | L6 | 분류 불가 × 3 | L7 × 3 — 「빌드 시점에 CSS를 생성하는 빌드 도구 성격」 |
| lucide-react | L8 | L8 × 1 · 분류 불가 × 2 | L6 × 3 — 「아이콘 렌더링이라는 특정 기능 하나」 |

tailwindcss·lucide-react는 이제 이유를 대며 등급을 고른다. 두 답 모두 정의로 설명은 되지만 검토본과 다르다. 둘은 §2-4에서 overrides로 고정했으므로 효과 값은 검토본 그대로다.

### 4-3. 두 쪽 다수결이 다른 기술 (before 대비, 정답과 함께)

- 나아진 것(정답으로 바뀐 것): 예시 기술 jinja2 · pydantic · uvicorn · requests · typesafe-sdk · transformers · langfuse · python. 그 밖 기술은 분류 불가에서 정답으로 바뀐 flagembedding · huggingface-hub · sentence-transformers · sentencepiece와, 다른 등급에서 정답으로 바뀐 @electron-toolkit/utils · langgraph-checkpoint-sqlite · langsmith · pyyaml이다
- 분류 불가에서 **정답의 일부만** 맞힌 것: numpy · pandas → L6(정답 L6+L7). 「아는 대로 판단」 허락(§3-5)으로 답을 내게 됐지만 L7 쪽은 놓쳤다. 둘 다 overrides에 있다
- **나빠진 것**(before는 맞았는데 v2는 틀림): @radix-ui/react-slot L6 → L8(「asChild용 작은 유틸 프리미티브」) · langgraph-checkpoint L6 → L4(v1과 같음, overrides에 있음) · tailwindcss · lucide-react(§4-2)
- 계속 틀린 것: openai-agents — before L4+L5, v1 L4(맞음), v2 L5(정답 L4). 「OpenAI 모델 API를 부르는 에이전트 SDK」라는 이유다. v1 다수결은 맞혔는데 v2는 3번 모두 L5를 냈다
- 흔들림만: @radix-ui/react-label(L6 × 2 · L4 × 1)

### 4-4. 채택하면 10/20 재분류에서 바뀌는 값 (§3-6)

v2 다수결이 정답과 다른 기술은 11개다. 그중 9개는 이미 overrides에 있어 효과 값이 바뀌지 않는다(@electron-toolkit/preload · electron · langgraph-checkpoint · lucide-react · numpy · openapi-fetch · pandas · react-dom · tailwindcss). **overrides에 없는 2개는 10/20 `weekly-tiers`가 덮는다.**

| 기술 | 검토본 | v2 (3번) |
|---|---|---|
| @radix-ui/react-slot | L6 | L8 × 3 |
| openai-agents | L4 | L5 × 3 |

### 4-5. 판정과 후속 (2026-10-16, 사용자)

- **채택**한다(등록 기준 T1·T2·T3 통과). 등급 프롬프트 v2(판 `0937e3ec`)를 merge한다
- §4-4의 2개는 10/20 재분류 전에 **검토본 값 그대로 overrides에 고정**했다(`../radar-snapshots/tiers.json` overrides 41 → 43, note 「HYO-128 채택 전 고정」). 고정 전 사본은 `eval/runs/hyo128/tiers-before-pin.json`이다. 고정한 뒤 v2 다수결이 검토본과 다른 기술은 모두 overrides에 있다 — 효과 값은 바뀌지 않는다
- 10/20 `weekly-tiers`는 지문이 바뀐 **68개**를 다시 분류한다(`--plan`, 2026-10-16. v2 회당 비용으로 약 $0.32). 남은 위험은 한 번짜리 실행의 흔들림이다(이번 k=3에서 흔들린 기술은 @radix-ui/react-label 하나). 주간 요약 첫 줄의 분류 불가 수와 5% 경고로 확인한다
- 남은 것: 정의로 설명은 되지만 검토본과 다른 답(tailwindcss L7 · lucide-react L6 · numpy·pandas L6만 · openai-agents L5)은 overrides가 막고 있다. 문구를 더 고치지 않는다
