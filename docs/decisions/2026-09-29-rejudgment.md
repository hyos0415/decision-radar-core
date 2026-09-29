---
status: proposed
technology: Decision Radar 제품 (LangGraph 에이전트 + Retrieval Arena + 웹 Inbox)
use: 스킬과 같은 조건에서 비교하는 실험군 (학습·연구 목적)
affected:
  - {repo: decision-radar, component: product}
  - {repo: decision-radar, component: eval}
evidence:
  - {url: "repo:decision-radar/docs/decisions/2026-09-29-coroner-verdict.md", source_type: repo}
  - {url: "repo:decision-radar/docs/design/overview.md", source_type: repo}
  - {url: "repo:decision-radar/docs/design/comparison-protocol.md", source_type: repo}
  - {url: "repo:decision-radar/docs/design/retrieval-arena.md", source_type: repo}
eval_assets: [eval/cases/, docs/design/comparison-protocol.md]
revisit_when:
  - id: c1
    check_type: numeric
    condition: 스킬 실사용 4주(W1–4) 동안 새로 쌓인 실제 ADR이 1건 이하
    source_hint: "repo:<ADR repo>/docs/adr"
    threshold: {metric: new_adr_count_w1_w4, op: "<=", value: 1}
  - id: c2
    check_type: human
    condition: 제품이 W4 게이트(공용 eval 안전 케이스 pass^k 전부 통과)를 넘지 못했다
  - id: c3
    check_type: human
    condition: MoSCoW의 Won't 항목이 "재미있어서" 착수됐다 (범위 번짐)
---
# 재판정 — 스킬 기준선 + 제품 실험군으로 간다

> 2026-09-29 · 원 판정: `2026-09-29-coroner-verdict.md` (Build a Skill, 서비스 축 보류)
> **status는 사람만 바꾼다(R8).** 이 기록은 `proposed`로 작성했고, 채택(`adopted`)은 사용자가 한다.

## Context and Problem Statement

검시 판정은 "개인 도구로 별도 제품이 **필요한가**"에 답했다 — 아니다, 스킬 하나면 된다. 그 판단은 여전히 맞다고 본다.
그런데 이 프로젝트의 목적이 바뀌었다. 판정문 H절과 자기 재심문이 미리 둔 출구가 이 경우다:

> 목적을 학습·연구로 바꿈 → 목적 변경 시 재판정. Retrieval Arena·LangGraph가 **학습 트랙으로 살아난다**

바뀐 목적 (사용자, 2026-09-29):

1. 규모가 커졌을 때에도 스킬로 해결되는가
2. 리트리버 전략과 라우팅 — 이 태스크에 최적인 검색 방법은 무엇인가
3. 스킬이 무너지는, 아직 관측하지 못한 지점
4. 통합 UX로 실제로 계속 쓰고 싶다
5. AI 엔지니어로서 state 관리·툴콜·LangGraph를 필요한 곳에서 직접 다뤄 보기 — 최근 프로젝트에서 이런 기술 도입을 스스로 모두 기각했다

질문이 "제품이 필요한가"에서 **"제품과 스킬 중 무엇이 어디서 나은지 재 보고 싶다"**로 바뀌었다. 다른 질문이라 다른 판정이 필요하다.

## Considered Options

| 안 | 내용 | 판단 |
|---|---|---|
| A. 스킬만 유지 | 원 판정 그대로 | 목적 1·2·4·5에 답하지 못한다 |
| B. 제품으로 교체 | 스킬을 버리고 제품을 만든다 | 판정을 뒤집는 것이고, 비교 대상이 사라져 "제품이 나았다"를 말할 근거가 없다. 제안서 §27 H의 경고("기술이 재미있다는 이유만으로 복잡한 Agent를 만드는 것")에 그대로 걸린다 |
| **C. 스킬 = 기준선, 제품 = 실험군** | 같은 명세(R1~R16)·같은 eval·같은 안전 규칙층(`radar.py`) 위에서 두 구현을 비교한다 | **채택 제안** |

## Decision Outcome

**C.** 스킬은 지금 상태를 기준선으로 고정하고(스킬로 고칠 수 있는 틈은 먼저 고친다), 제품을 비교 실험군으로 만든다.

- LangGraph·그래프 DB 도입은 "써 보고 싶어서"가 아니라 **"스킬이 여기서 무너졌고, 이것이 이만큼 고쳤다"**로 정당화한다
- 제안서 §27 H의 경고는 **비교 프로토콜로 지킨다** — 결과 전에 판정 규칙을 등록했고(`comparison-protocol.md`, 47c9ad2), 역수입 규칙으로 허술한 기준선을 막는다
- 아이디어 둘을 흡수한다: retrieval-arena(kb-arena를 포크하지 않고 자체 하네스) · trend-radar(커뮤니티 급증 신호 → 수집 입구). 새 레포를 만들 이유가 없어졌다
- 대외명 Decision Radar, 내부 코드명 JHS 유지
- 서비스 축(팀 대상)은 **여전히 보류**다 — 원 판정과 같다. Phase 0 인터뷰 전에는 재판정하지 않는다

### Consequences

- 좋은 점: 원 판정이 "문서로 판정할 수 없다"며 보류한 반대 논거 E(통합 UX가 가치)를 **직접 검증할 수단**이 생긴다. Retrieval Arena가 "Failure 없는 Requirement"가 아니라 규모 비교(목적 1)의 도구가 된다
- 나쁜 점: 범위가 커졌는데 기간은 2개월 그대로다 — MoSCoW의 Won't를 지키는 게 관건. 제품은 API로 LLM을 불러 **비용이 든다**(구독 밖)
- 위험: 제품 쪽 작업이 늘면 스킬 기준선 보강(E2)이 밀릴 수 있다 → 일정표(overview §10)가 스킬 실사용을 W1에 먼저 켠다

## 재검토 조건 설명

| 조건 | 충족되면 |
|---|---|
| c1 스킬 4주 동안 새 ADR ≤ 1건 | 판정문 H절 "프로젝트 중단" 신호. 쓸 결정이 없으면 제품을 만들어도 비교할 사용 데이터가 없다 — **제품 착수 재검토** |
| c2 W4 게이트 불통과 | 제품이 안전 조항을 못 지킨다 → 실사용으로 가지 않고, 원 판정(Build a Skill)의 근거가 강해진다 |
| c3 Won't 항목 착수 | 목적이 학습에서 "기술 수집"으로 미끄러졌다는 신호 → 범위 재조정 |

c1은 ADR repo가 생긴 뒤(E2) `source_hint`의 `<ADR repo>`를 실제 이름으로 바꾼다.

## 관련

- 설계: `docs/design/overview.md` · `retrieval-arena.md` · `comparison-protocol.md`
- 원 제안서: `<downloads>\project_jhs_autopsy_proposal.md` (레포 밖)
- 흡수한 판정: `<workspace>/retrieval-arena/autopsy-2026-09-17.md` · `<workspace>/trend-radar/autopsy-2026-09-22.md`
- Linear: HYO-13 (E1)

## Research Note

없음. 이 결정의 근거는 전부 레포 안 문서와 사용자 발언이다.
