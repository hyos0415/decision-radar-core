# 검시 판정문 — Project JHS (Engineering Intelligence Agent)

- 판정일: 2026-09-29
- 입력: `<downloads>\project_jhs_autopsy_proposal.md` (1,095줄, 원문 무수정) [FILE]
- 실행: coroner (build-or-use v3), Opus 5.5, 메인 스레드 직접 검색 (서브에이전트 위임 없음)
- 전제 확정 (사용자 응답, Step 1) [INPUT]
  - 사용 주체: **개인 먼저, 서비스는 가설** → 판정 본체는 개인 도구, 서비스는 별도 축으로 보류 판정
  - 1차 목적: **문제 해결** → Retrieval Arena·LangGraph는 목적이 아니라 수단으로 심사
  - 분해안 C1~C12 승인

## 이해충돌 공시

1. **사용자 본인의 기획**이다 [INPUT].
2. **판정을 내리는 스킬 자신이 판정 대상의 부품이다.** 제안서 §18이 Idea-autopsy를 lifecycle 한 단계로
   넣고, §3 사례에도 올라 있다 [FILE]. 이 판정이 C4(대안 탐색)를 "이미 해결"로 닫으면 검시관이 자기
   존재를 근거로 삼는 셈이다. 그래서 C4의 근거를 coroner 하나에 기대지 않고, 판정도 C4 없이 성립하는지
   따로 확인했다(아래 5절).
3. 판정 어휘 오염 가능성: "Failure가 Requirement보다 먼저", "무증상 실패" 같은 이 레포의 규율 어휘가
   제안서 §23("관찰된 실패나 실제 요구가 생길 때만 추가")에 이미 들어 있다 [FILE]. 판정이 제안서의 자기
   규율을 그대로 되돌려 쓰는 경향을 경계했다.

---

## 1. 한 줄 결론

**개인 도구로는 별도 제품(웹앱·백엔드·Retrieval Arena·Agent runtime)이 필요 없다.
ADR 마크다운(git) + Claude Code routines + ADR MCP 조합으로 C1~C12 대부분이 닫힌다.
유일하게 닫히지 않는 것은 "재검토 조건 감시 루프"(C7)이고, 이건 Skill 하나 크기다.**
판정: **Build a Skill** (나머지는 Configure). 서비스 축은 **보류** — 팀 대상 기반 계층은 Backstage가 이미 점유하고 있다.

## 2. Capability 분해표

| # | 필요한 기능 | 현재 해결 방법 | 해결 수준 | 남은 문제 |
|---|---|---|---|---|
| C1 | 기술 변화 감지 (스택 기준 필터) | 의존성 안: Renovate가 PR 본문에 changelog 자동 삽입 [WEB] / Release Radar Agent — manifest(requirements.txt·package.json) 읽고 GitHub release를 breaking·deprecation·security·major로 분류, patch 소음 제거 [WEB]. 의존성 밖(새 모델·논문·managed service): Claude Code routines 예약 실행 + GitHub release 이벤트 트리거 [WEB], ChatGPT scheduled tasks(모니터링형·GitHub 이벤트 트리거) [WEB, 2차 출처만] | 부분 해결 | "아직 의존하지 않는 것"의 발견은 routine 프롬프트 품질에 달림. routine 기본 네트워크는 Trusted allowlist라 웹 조사하려면 환경 네트워크를 Custom/Full로 바꿔야 함 [WEB] |
| C2 | 엔지니어링 컨텍스트 보유 | **repo 자체가 컨텍스트다.** routine은 매 실행마다 지정 repo를 clone [WEB]. 스택·평가셋·제약은 repo의 CLAUDE.md/README 한 절로 충분 [INFERENCE] | 해결 | 제안서 §7.1~7.2의 Profile/State 스키마를 **별도 저장소에 복사하면 그 순간부터 stale 문제가 생긴다.** 복사하지 말고 매번 repo에서 읽는 게 stale 문제의 답 [INFERENCE] |
| C3 | 외부 변화 → repo/component 영향 매칭 | repo 단위: Renovate·Release Radar·Dependabit(README·주석·manifest에서 외부 참조 추출, 변경 시 GitHub issue 생성) [WEB]. 함수 단위(보안만): Snyk·Endor Labs reachability — 취약 함수가 내 코드에서 도달 가능한지 판정 [WEB]. 일반 동작 변경(예: vLLM scheduler) → component: 전용 도구 미확인. Claude Code 세션이 changelog + clone된 repo를 같이 읽는 방식 [INFERENCE] | 부분 해결 | 확인한 도구 중 **component 단위 매핑을 문서에 명시한 것은 없다** (Dependabit·Release Radar 둘 다 의존성 단위) [WEB]. 다만 개인 repo 몇 개 규모면 "clone + 읽기"가 매핑을 대신함 — 사용 테스트 필요 |
| C4 | 문제 기반 대안 탐색 | coroner Step 3~4.5 절차 그대로 [SKILL] + Claude/ChatGPT 웹 검색 | 해결 (이해충돌 주의) | coroner는 "착수 전 아이디어"용으로 설계됨. "현재 구현의 대안" 입력에서 쓴 실런 기록은 이번에 확인하지 않음 |
| C5 | 결정 기록 (Adopt/Reject/Hold/Revisit + 근거) | MADR 포맷 ADR(git 마크다운). AdrMcp: search·create·set_status·supersede, 상태 proposed/accepted/rejected/deprecated/superseded, 쓰기는 미리보기 기본값, MIT [WEB]. mcp-server-decisions: 결정 + 측정 가능한 예측 + 결과(success/partial/failure) 기록, JSONL append-only, MIT [WEB] | 해결 (포맷) / 부분 해결 (도구) | Hold·Revisit 상태가 AdrMcp에 기본으로 없다 [WEB]. **두 MCP 모두 초기 단계** (AdrMcp star 1, decisions star 0) [WEB] → 도구에 묶이지 말고 포맷(MADR + 커스텀 frontmatter)에 묶을 것 |
| C6 | 과거 결정 회수 | AdrMcp search_adrs(lexical/semantic)·find_related_adrs [WEB]. 개인 규모면 grep/전체 컨텍스트 [INFERENCE] | 해결 | 없음 (규모가 커지면 재판정) |
| C7 | **재검토 조건 감시 → 알림** | 확인한 ADR 도구 중 revisit 조건·검토일 필드가 있는 것 없음 (AdrMcp·decisions MCP 문서에 없음) [WEB]. AdrMcp `find_stale_adrs`는 **code_refs가 코드에서 사라졌는지만** 본다 — 외부 조건 변화가 아님 [WEB]. Tech Radar 관행은 "Hold 사유를 적어두라"까지만 [WEB] | **미해결** | **이게 유일한 진짜 gap.** 조건 스키마 + 주간 routine이 hold/rejected ADR을 읽고 조건(가격·버전·API 지원·latency)을 웹으로 확인 → 충족 시 issue/PR. 전부 기존 부품으로 조립 가능 = Skill 크기 |
| C8 | 최소 실험 제안 | 범용 LLM + ADR + repo의 평가셋 목록 [INFERENCE]. 전용 도구 미확인 | 사용 테스트 필요 | 제안서 Risk 5("generic하면 일반 assistant와 차이 없음")가 정확히 이 칸 [FILE]. 문서로 판정 불가 — 과거 사례 3건(Jev·Bedrock·LEANN)을 입력으로 재현해 보고 판정 |
| C9 | 실험 실행 | GitHub Actions / routines(셸 실행, `claude/` 브랜치 push, PR 생성) [WEB] / promptfoo [WEB] | 해결 | Finance·Medical 코드가 클라우드 세션에 clone됨 — 프라이버시 판단 필요 (자기 재심문 참조) |
| C10 | 워크플로별 모델 전환 평가 | promptfoo: 테스트 케이스 × 프롬프트 × 모델 격자 평가 [WEB]. 워크플로별 config를 나누면 PARTIAL SWITCH 판단 가능 [INFERENCE] | 부분 해결 | coding 워크플로(repo navigation·bug fixing)는 promptfoo 형태가 아님 — 에이전트형 eval이 필요. 이번 실행에서 탐색 안 함 |
| C11 | AI 대화 조사 결과 저장 (승인 후) | Claude Code 내장 파일 메모리 + Memory MCP(지식 그래프) [WEB]. AdrMcp 쓰기 도구가 미리보기 기본값이라 §17.2의 "승인 후 저장"과 맞음 [WEB] | 부분 해결 | ChatGPT 쪽 쓰기 경로 미확인 — GitHub connector는 읽기만 확인 [WEB, 2차 출처]. Claude 쪽만 닫힘 |
| C12 | 다환경 접근 (교육장 Windows + 개인 Mac) | git repo = source of truth. routines는 웹·CLI·Desktop이 같은 클라우드 계정을 공유 [WEB] | 해결 | **제안서 §20의 웹앱 선호 근거("state가 로컬 머신에 종속되면 안 된다")는 git repo로 충족된다** [INFERENCE] |

## 3. 기존 해결책 (이번 실행에서 원문을 확인한 것만)

| 해결책 | 확인 수준 | 핵심 사실 | 성숙도 |
|---|---|---|---|
| Claude Code routines | 공식 문서 fetch [WEB] | prompt + repo 여러 개 + connector, 예약·API·GitHub(PR·release) 트리거, 클라우드 실행, 실행마다 새 세션, 계정당 일일 실행 한도 | research preview |
| Renovate changelogs | 공식 문서 fetch [WEB] | PR 본문에 해당 버전 구간 release note 자동 삽입, private 패키지는 source URL·인증 필요 | 성숙 |
| Release Radar Agent (awesome-llm-apps) | README fetch [WEB] | manifest 읽기 → GitHub release 분류, Gemini·Google ADK 기반, Gmail/webhook 출력, 파일 단위 매핑 없음 | 예제 수준 |
| Dependabit | README fetch [WEB] | LLM으로 문서·논문·repo 외부 참조 추출 → GitHub Action이 issue 생성. 변경 모니터링 워크플로는 "coming soon" | pre-1.0, star 1 |
| AdrMcp | README fetch [WEB] | MADR 4.0 파일 기반, search/supersede/find_stale/suggest_adr_from_change, 쓰기는 미리보기 기본값, MIT | star 1 |
| mcp-server-decisions | README fetch [WEB] | 결정 + 예측 + 결과, OUTCOME_GATE 리마인더, revisit 기능 없음 | 1.0.2, star 0 |
| Backstage ADR plugin | README fetch [WEB] | repo 마크다운 ADR을 catalog entity에 annotation으로 연결, MADR 2.x/3.x, 상태 칩(rejected·superseded 등), Backstage Search 통합 | community plugin |
| Backstage Tech Radar | 공식 블로그 검색 결과 [WEB, 원문 미fetch] | Use/Trial/Assess/Hold 4링 | 확인 불가 (요약만 봄) |
| Snyk / Endor Labs reachability | 공식 문서 검색 결과 [WEB, 원문 미fetch] | 취약 함수 도달 가능성 판정 — 보안 한정 | 확인 불가 (요약만 봄) |
| promptfoo | 공식 문서 검색 결과 [WEB, 원문 미fetch] | 케이스 × 모델 격자 비교 | 확인 불가 (요약만 봄) |
| ChatGPT scheduled tasks | 공식 Help 403 → 2차 출처만 [WEB] | 모니터링형 task, GitHub 이벤트 트리거, Plus 5개 등 요금제별 한도 | 확인 불가 (1차 원문 못 엶) |
| LEANN | arXiv·GitHub 검색 결과 [WEB] | 임베딩을 저장하지 않고 즉석 재계산하는 저장 절감형 index. repo 제목에 MLSys 2026 Best Paper 표기 | 실재 확인 |

**통합 제품(C1+C3+C5+C7를 한 곳에서)은 찾지 못했다 → "확인 불가(부재 추정)", 부재 확정 아님.**
시도한 검색어: 사용자 어휘 계열("내 기술 스택 관련 신기술 업데이트만 골라주는 AI 에이전트", "기술 도입 의사결정 기록 채택 기각 재검토 조건 관리 도구") /
개발자 어휘 계열("personalized AI tool monitors new releases relevant to my tech stack…", "tool that matches new AI releases papers to your codebase and suggests experiments tech radar…",
"technology watch OR tech scouting agent… decision log", "technology evaluation decision platform engineering teams… revisit"). 소스: 웹 일반 + GitHub(검색 결과 경유).
찾은 가장 가까운 것은 기업 R&D용 tech scouting(특허·논문·보조금 삼각측량) — 코드 연결 없음 [WEB].

## 4. 남은 Capability Gap

1. **C7 재검토 조건 감시** — 유일한 미해결. 조건을 기계가 확인할 수 있는 형태로 적는 스키마 + 주기 실행 + 충족 시 알림.
2. **C8 최소 실험 제안의 품질** — 문서로 판정 불가. 사용 테스트 필요.
3. **C3 component 단위 매핑** — 개인 규모에선 "clone + 읽기"로 대체되는지 사용 테스트 필요. 대체된다면 gap이 아니다.
4. **C10 coding 워크플로 모델 전환 eval** — 이번에 탐색 안 한 영역. gap인지 확인 불가.

**gap이 아닌 것 (제안서가 핵심으로 둔 것 중):**
- **Retrieval Arena (§9~12).** 개인 규모 corpus에서 푸는 문제가 아직 관찰되지 않았다. 제안서 스스로 baseline에 Full Context를 넣었는데 [FILE],
  개인 결정 기록은 제안서 §3 사례 기준 수 건 단위다 [FILE]. Full Context가 실패한 기록이 없으면 Arena는 "Failure 없는 Requirement"다 [SKILL].
- **Persistent Agent State / LangGraph (§16).** "며칠 대기"는 ADR 파일의 status 필드 + git이면 된다. routine은 실행마다 새 세션이고 상태는 파일에 산다 [WEB][INFERENCE].
- **Server-side Source of Truth / 웹앱 (§20).** C12에서 git으로 충족.

## 5. Build Recommendation

### 개인 도구 축 → **Build a Skill** (나머지 Configure)

핵심 reasoning(관련성 판단·대안 탐색·실험 설계)은 범용 LLM이 한다. 필요한 건 workflow 고정(ADR 스키마 + 재검토 루프 프롬프트)과
반복 실행(routine)뿐이다. Custom Product 조건 A~E 중 개인 축에서 성립하는 것은 없다:

- A(핵심 업무 미해결): C7 하나가 미해결이지만 기존 부품 조립으로 닫힌다 → 불성립
- B(조합 비용 과다): 조합 부품이 3종(git ADR, routine, MCP 1개)이라 과다하지 않다 → 불성립 [INFERENCE]
- C(정확성 요구 초과): 최종 결정은 사람이 한다고 제안서 스스로 규정 [FILE] → 불성립
- D(독점 데이터 중심): 결정 기록은 독점 데이터가 맞다. 그러나 **웹앱이 아니라 git 마크다운에 있어도 똑같이 사용자 소유다** → 제품을 만들 근거가 되지 못함
- E(UX가 핵심): **사용 테스트 필요** — 아래 반대 논거 참조

**C4(coroner) 없이도 판정이 서는가:** 선다. C4를 "미해결"로 바꿔도 대안 탐색은 범용 LLM 웹 검색 + 프롬프트 고정 수준이라 Skill 등급을 넘지 않는다 [INFERENCE].

### 서비스 축 → **보류** (Custom Product 조건 미성립, 수요는 이 스킬 범위 밖)

팀 단위의 기반 계층(catalog 연결 ADR + Tech Radar)은 Backstage가 이미 제공한다 [WEB]. 서비스로서의 차별점 후보는
Backstage 위에 얹는 "외부 변화 → catalog entity 영향 + 재검토 조건 감시"뿐이고, 이건 Backstage plugin 형태가 먼저다 [INFERENCE].
제안서 Phase 0(인터뷰)이 맞는 다음 단계다. 이 판정은 그 결과를 끌어올리는 근거로 쓰이면 안 된다 [SKILL].

### 반대 결론의 최선 논거 (의무)

> **"통합 자체가 가치다."** 부품이 각각 있어도 5곳(ChatGPT·Claude·GitHub·ADR·routine 로그)에 흩어져 있으면 결국 안 쓴다.
> 결정 기록 규율은 사람의 성실성에 기대는 순간 죽는다. 한 화면의 Inbox(§22 Phase 6)가 있어야 기록이 쌓이고,
> 기록이 쌓여야 C6·C7이 가치를 낸다 — 즉 E(UX)가 핵심 가치다.

**반박이 아니라 판정 보류로 받는다.** E는 문서로 판정할 수 없는 경험재 속성이다 [SKILL]. 그래서 출구를 둔다:
**Skill 조합을 4주 실사용 → 그 기간에 ADR이 새로 몇 건 쌓였는지, routine 알림을 몇 번 무시했는지 센다.
기록이 안 쌓인 원인이 "흩어짐"이면 E 성립 → 재판정.** 원인이 "쓸 결정 자체가 없음"이면 제품이 아니라 문제 가설이 죽은 것이다.

## 6. 제안서 §27 검토 요청에 대한 답

### A. 이미 commoditized된 부분

| §27-A 항목 | 상태 |
|---|---|
| 1 personalized change detection | 의존성 안쪽은 commoditized (Renovate·Release Radar). 바깥쪽은 routine 프롬프트로 조립 |
| 2 repo impact matching | repo 단위는 commoditized. 보안 한정 함수 단위도 commoditized(reachability). 일반 component 단위는 전용 도구 미확인 |
| 3 alternative discovery | LLM 웹 검색 + 절차 고정 (coroner — 이해충돌) |
| 4 persistent decision memory | 포맷은 commoditized (ADR/MADR). 도구는 초기 단계 여럿 |
| 5 Adopt/Reject/Revisit history | ADR status + supersede. Hold/Revisit 상태는 커스텀 필드 필요 |
| 6 조건 변화 시 자동 재검토 | **미해결 — 유일한 gap** |
| 7 minimum experiment recommendation | 사용 테스트 필요 |
| 8 model-switch evaluation | promptfoo로 부분 해결, coding 워크플로는 확인 불가 |
| 9 ChatGPT/Claude 조사 저장 | Claude 쪽 해결, ChatGPT 쪽 쓰기 미확인 |
| 10 multi-user personalization | 서비스 축 — 보류 |

### B. Standalone Agent가 필요한가 → 아니다

persistent state / interrupt / resume / checkpoint가 **실제로 필요해지는 조건** (지금은 어느 것도 관찰되지 않음):
1. 하나의 결정 흐름 안에서 **외부 콜백(CI 결과 등)을 기다리다 자동 재개**해야 하고, 그 중간 상태를 파일 한 장으로 직렬화할 수 없을 때
2. **여러 사용자가 같은 결정 상태를 동시에 쓴다** (트랜잭션 필요) — 서비스 축 진입 시점
3. 재시도·사이클이 **사람 개입 없이** 여러 번 돌아야 할 때 — 제안서 v1 Non-goal("autonomous adoption")과 충돌

1은 GitHub Actions 완료 이벤트 → routine API 트리거로도 대부분 닫힌다 [WEB][INFERENCE]. 셋 다 관찰되기 전에는 plain orchestration(=routine + 파일)으로 충분.

### C. Retrieval 타당성

- **더 적절한 baseline: "ADR frontmatter 필터 + grep + 전체 컨텍스트"**. version-aware·source-type·temporal 필터(§9.5)는 frontmatter 필드로 흉내 낼 수 있다 [INFERENCE].
- **Gold query를 미리 50~100개 만들지 말고, 실사용 중 "회수 실패한 질의"를 기록해 모은다.** 미리 만든 gold set은 실제 실패 분포를 반영하지 못한다 [SKILL].
- **LEANN 비교는 플랫폼이 아니라 결정 한 건이다.** vector index를 쓰는 기존 프로젝트에서 ADR 하나로 돌리면 된다 — 이 파이프라인의 첫 실사용 케이스로 적합.
- graph expansion 보류는 제안서 판단(§9.6)이 옳다.

### D. Engineering State — 최소 유효 state

- **Profile/Project State(§7.1~7.2)는 v1에 과하다.** repo마다 CLAUDE.md에 「스택·평가셋·알려진 병목」 절 하나로 대체. 복사본을 두지 않으니 stale이 생기지 않는다.
- **Decision Memory(§7.3)만 남긴다.** MADR frontmatter 확장안:
  `status` (proposed/adopted/rejected/hold/revisit/superseded) · `affected` (repo/component) · `evidence` (링크 — LLM 발언은 evidence 아님, §7.3 원칙 유지) ·
  `eval_assets` (재사용 가능한 평가셋 경로) · **`revisit_when`** (기계가 확인 가능한 조건 목록 — C7의 입력)
- 빠진 state: **`revisit_when`의 마지막 확인 시각과 결과.** 이게 없으면 같은 조건을 매주 다시 확인하고 같은 알림을 반복한다.

### E. HITL — 전부 실제 요구, 그러나 전부 기존 메커니즘으로 표현된다

| HITL | 기존 메커니즘 |
|---|---|
| user override | 사용자가 그냥 요청 (Discovery Override를 별도 상태로 둘 이유 없음) |
| experiment approval | routine이 여는 PR/issue — **merge = 승인** |
| waiting | ADR `status` 필드 |
| result interpretation | 사람이 ADR 본문·status를 수정 |
| revisit | C7 routine (만들어야 할 유일한 것) |

### F. 비즈니스 가치 → 이 스킬 범위 밖

수요·지불의사는 판정하지 않는다 [SKILL]. 확인한 사실 하나만: 플랫폼 팀의 현재 workaround 후보는 Backstage(ADR plugin + Tech Radar)다 [WEB].
인터뷰 질문에 "Backstage를 쓰는가, 쓴다면 ADR plugin의 revisit이 비어 있어서 불편한가"를 넣으면 서비스 축 재판정 입력이 된다.

### G. 약 2개월 v1 범위

| 구분 | 항목 |
|---|---|
| **Must** | MADR 확장 스키마(`revisit_when` 포함) · 과거 사례 3건(Jev·Bedrock Guardrails·LEANN)을 ADR로 옮기기 · C7 재검토 routine(주간) · C1 탐지 routine(주간, 의존성 밖 기술 대상) |
| **Should** | AdrMcp 연결(Claude 대화에서 조회·승인 후 기록) · C8 실험 제안 프롬프트를 과거 3건으로 재현 테스트 · LEANN 비교를 첫 실사용 결정으로 진행 |
| **Stretch** | promptfoo 워크플로별 모델 전환 config · Release Radar류 의존성 브리핑 연결 |
| **Cut** | 웹앱 · 백엔드 · 서버 측 Retrieval Index · Retrieval Arena 전체 · LangGraph · Profile/Project State 저장소 · Experiment Runner 신규 구축 · multi-user |

**교육 종료 후 유지 가능성:** Must·Should는 git repo + 구독 기능뿐이라 호스팅 비용이 없다. 웹앱 백엔드는 교육 환경이 끝나면 호스팅 주체가 사라진다 [INFERENCE] — Cut의 추가 근거.

### H. Kill Criteria

| 결과 | 조건 |
|---|---|
| **프로젝트 중단** | 4주 실사용 동안 새 결정 기록이 **거의 생기지 않음** — 쓸 결정이 없다면 문제 가설(§28 Working Thesis)이 틀린 것 |
| **Skill/MCP로 축소 (=현재 판정의 기본값)** | 기록은 쌓이는데 흩어짐이 원인인 불편이 관찰되지 않음 |
| **Retrieval Arena만 남김** | 결정 기록이 전체 컨텍스트에 안 들어갈 만큼 커지고, grep/frontmatter 필터로 **회수 실패가 실제로 기록됨**. 또는 목적을 학습·연구로 바꿈(→ 목적 변경 시 재판정) |
| **다른 문제로 pivot** | C7 routine 알림의 대부분이 무시됨 → "재검토"가 아니라 다른 병목(예: 실험 실행 비용)이 진짜 문제라는 신호 |
| **Custom Product 재판정** | 반대 논거의 E 성립(흩어짐이 기록 중단의 원인으로 관찰) 또는 서비스 축 인터뷰에서 Backstage 사용자가 revisit gap을 보고 |

## 7-1. 장기기증 카드 — 가져갈 것

| 발견물 | 가져올 것 (Step 4.5 어휘) |
|---|---|
| AdrMcp | **구조 차용**: `supersede_adr`(교체+링크를 한 번에), `find_stale_adrs`의 code_refs 개념을 `revisit_when`의 외부 조건판으로 확장 · 쓰기 미리보기 기본값(= §17 승인 원칙과 동일) |
| mcp-server-decisions | **분류 체계 차용**: 결정마다 측정 가능한 예측 + 결과(success/partial/failure) + OUTCOME_GATE 리마인더 → 제안서의 Hold 사례(Jev)에 딱 맞는 형식 |
| Release Radar Agent | **패턴 차용**: breaking/deprecation/security/yanked/major만 남기고 routine patch는 버리는 소음 필터 규칙 — C1 routine 프롬프트에 그대로 |
| Backstage ADR plugin | **포맷 호환**: MADR 3.x frontmatter를 지키면 나중에 서비스 축으로 갈 때 Backstage에 그대로 얹힌다 |
| Tech Radar (Thoughtworks) | **분류 체계**: Hold 사유를 반드시 적는 관행 — `revisit_when`의 선행 조건 |
| Snyk/Endor reachability | **개념**: "있다"가 아니라 "도달 가능한가" — C3을 component 단위로 확장할 때의 판별 질문 |
| promptfoo | **실행 도구**: 케이스 × 모델 격자 — C10 PARTIAL SWITCH 판정의 표 형식 |

## 7-2. 로컬라이즈 기회 주석

해당 없음. 최적 해결책이 전부 가변 인공물(OSS·MCP·Skill)이고, 사용자 locale 요구(한국어 의료 채팅 PII 등)는
대안 탐색의 **입력 제약**이지 도구의 locale gap이 아니다 [INFERENCE].

## 자기 재심문 (1회)

| 축 | 평가했나 | 보완 |
|---|---|---|
| 사용 주체 | 했음 (Step 1 확정) | — |
| 가변성 | 했음 (전부 가변) | — |
| locale | 7-2에서 짧게 | 한국어 문서가 source인 기술(국내 managed service 등)을 C1 routine이 잡는지는 미확인 — 한계 |
| **프라이버시** | **판정 본체에서 약했음** | routine은 private repo를 Anthropic 클라우드 세션에 clone한다 [WEB]. Finance Verifier·Medical Mate 코드가 대상이면 **그 repo는 routine에서 빼고 ADR repo만 넣는 구성**이 필요할 수 있음. 판정 등급은 안 바뀌지만 Must 구성에 영향 |
| 비용 | 부분 | routine은 계정당 일일 실행 한도가 있고 구독 사용량에서 차감 [WEB]. 주간 2개 routine이면 한도 문제 가능성 낮음 [INFERENCE]. ChatGPT tasks 한도는 2차 출처만 |
| 목적 변경 | — | 목적이 "학습·포트폴리오"로 바뀌면 Retrieval Arena·LangGraph가 **학습 트랙으로 살아난다**. 그때는 이 판정이 아니라 재판정 |

---

## Failure Log

- [유형: 근거 부재]
  상황: C1·C11에서 ChatGPT scheduled tasks 사양 확인
  실패: 공식 Help Center(help.openai.com/…/10291617) WebFetch가 HTTP 403. 요금제별 한도·GitHub 이벤트 트리거는 2차 출처(검색 결과 요약)로만 확인
  확인 방법: 재fetch 안 함. 해당 항목은 "확인 불가(1차 원문 못 엶)"로 표기

- [유형: 근거 부재]
  상황: Backstage Tech Radar, Snyk/Endor reachability, promptfoo
  실패: 검색 결과 요약만 보고 원문을 fetch하지 않음 (탐색 축소)
  확인 방법: 3절에 "확인 불가(요약만 봄)"로 표기. 판정을 가르는 근거가 아니라 보조 근거라 축소함 — 판정을 가르는 routines·AdrMcp·Renovate·Backstage ADR은 원문 확인

- [유형: Search Failure 후보]
  상황: 통합 제품(C1+C3+C5+C7) 탐색
  실패: 사용자 어휘 2회 + 개발자 어휘 4회 모두 통합 제품 미발견. 검색 엔진이 "personalized"를 뉴스·뉴스레터 쪽으로 끌고 감
  확인 방법: "확인 불가(부재 추정)"로 종료. 부재 확정 아님. Product Hunt·HN 같은 런칭 채널은 탐색하지 않음 — 거기서 나오면 Search Failure로 확정

- [유형: 관찰 (failure 아님)]
  상황: 어휘 2계열
  실패: 사용자 어휘("기술 도입 의사결정 기록 채택 기각 재검토 조건") 검색은 일반 ADR 가이드·기술검토 의견서 양식만 반환. 개발자 어휘("Architecture Decision Record MCP server")에서 AdrMcp·decisions MCP가 히트
  확인 방법: 기존 실증 패턴("사용자 어휘 실패, 개발자 어휘 히트") 재현

- [유형: 관찰 (failure 아님)]
  상황: 검색 결과 요약의 정확도
  실패: Tech Radar 검색 요약이 Adopt 링 정의를 Assess 설명으로 잘못 채움
  확인 방법: 같은 요약 안의 모순으로 발견. 요약 문장은 판정 근거로 쓰지 않음

- [유형: 관찰 — 이해충돌 처리]
  상황: C4를 coroner 자신으로 닫음
  실패: 없음. 다만 이 판정이 "coroner가 이미 해결"을 근거로 쓰는 구조 자체가 이해충돌
  확인 방법: 5절에서 C4를 미해결로 바꿔도 등급이 유지됨을 따로 확인
