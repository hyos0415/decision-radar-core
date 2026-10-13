#  프론트엔드 기술 결정 기록 (ADR)

이 폴더는 프론트엔드(데스크톱 앱)에서 내린 기술 결정을 기록합니다. `jhs-decisions` 스킬이 읽는 폴더가 아닙니다. 스킬이 읽는 ADR은 `hyos0415/adr` 저장소에 있고, 번호도 그쪽과 따로 매깁니다. 템플릿은 MADR을 기본 틀로 삼았습니다. 근거 문서는 [https://github.com/architecture-decision-record/architecture-decision-record](https://github.com/architecture-decision-record/architecture-decision-record) 입니다.

## 파일 이름

`NNNN-현재형-명령문.md` 형식으로 짓습니다. 번호는 이 폴더 안에서 `0001`부터 매기고, 이름에는 소문자와 하이픈만 씁니다. 예: `0001-render-adr-markdown-in-frontend.md`

## 작성 규칙

1. ADR 하나에는 결정 하나만 씁니다.
2. 선택한 이유는 배경, 선택지별 장단점, 비용과 효과로 나눠 씁니다.
3. 이미 쓴 내용은 고치지 않습니다. 새로 알게 된 내용은 날짜와 함께 `## 갱신 기록` 절에 덧붙입니다. 결정한 뒤에 알게 된 정보라면 그렇다고 적습니다.
4. 결정 자체가 바뀌면 기존 ADR을 고치지 않고 새 ADR을 만듭니다. 기존 ADR의 상태는 `superseded`로 바꿉니다.
5. `status`는 사람만 바꿉니다.

## 상태 값

`proposed`, `adopted`, `rejected`, `hold`, `revisit`, `superseded` 여섯 가지입니다.

## 템플릿

```markdown
---
status: proposed                 # proposed, adopted, rejected, hold, revisit, superseded 중 하나. 사람만 바꾼다
date: YYYY-MM-DD                 # 결정이 마지막으로 바뀐 날
deciders: [ ]                    # 결정에 참여한 사람
---

# [어떤 문제를 어떻게 해결했는지 현재형 명령문으로]

관련 작업: [Linear 키와 제목, GitHub PR 주소]

## Context and Problem Statement
<!-- 지금 상황, 우선순위, 제약(담당 영역, 플랫폼, 보안), 팀 역량을 쓴다.
     장단점은 우리 목표를 기준으로 쓴다. 마지막은 질문 한 문장으로 끝낸다 -->

## Decision Drivers
- 

## Decision Outcome
<!-- 채택안을 한 줄로 쓴다. 이유는 다시 쓰지 않고 아래 비교표를 가리킨다.
     비교표에 없는 세부 사항(함께 정한 것, 이번 결정에서 뺀 것)만 이어서 쓴다 -->
채택: [선택지 이름]. 근거는 아래 비교표에 있습니다.

### Consequences
- 쉬워지는 것:
- 어려워지는 것과 치르는 비용:

## Pros and Cons of the Options

| 기준 | 선택지 1 | 선택지 2 | 선택지 3 |
| --- | --- | --- | --- |
| [Decision Driver 1] |  |  |  |
| [Decision Driver 2] |  |  |  |
| 도입 비용 |  |  |  |
| 판정 | 채택 | 기각 | 기각 |

### 선택지 1
- 장점:
- 단점:

### 선택지 2
- 장점:
- 단점:

## 근거 자료
<!-- 출처 종류: official_doc, release_note, repo, paper, issue_pr 중 하나. 커뮤니티 글은 근거로 쓰지 않는다 -->
- [종류] 주소 — 무엇을 확인했는지

## Links
- 대체 관계: supersedes 또는 superseded by [ADR-NNNN](NNNN-....md)
- 관련 ADR, 설계 문서, 이어서 작성할 ADR

## 갱신 기록
<!-- 이미 쓴 내용은 고치지 않는다. 날짜를 붙여 덧붙이고, 결정한 뒤에 알게 된 정보면 그렇다고 적는다 -->

## Research Note
<!-- 출처 링크가 없는 주장, 커뮤니티 글, 모델의 답변을 적는다. 근거로 쓰지 않는다 -->
```

## ADR 목록


| 번호                                              | 제목                                     | 상태       | 날짜         | 관련 이슈   |
| ----------------------------------------------- | -------------------------------------- | -------- | ---------- | ------- |
| [0001](0001-render-adr-markdown-in-frontend.md) | 프론트에서 react-markdown으로 ADR 마크다운을 렌더링한다 | proposed | 2026-10-13 | HYO-102 |
