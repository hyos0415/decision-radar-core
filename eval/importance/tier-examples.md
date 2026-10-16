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

## 2. 결과

(측정 뒤 별도 PR)
