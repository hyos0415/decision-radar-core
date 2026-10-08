---
status: hold
date: 2026-09-02
technology: Tailwind CSS
use: Tailwind v4 업그레이드
affected:
  - {repo: Andbread_Frontend, component: web-app}
evidence:
  - {url: "repo:Andbread_Frontend/tailwind.config.ts", source_type: repo}
revisit_when:
  - id: c1
    check_type: web
    condition: tailwindcss-preset-px-to-rem이 Tailwind v4를 지원한다
    source_hint: example.invalid/tailwindcss-preset-px-to-rem/releases
  - id: c2
    check_type: human
    condition: 디자인 팀이 rem 기준 간격 체계 전환을 확정한다
revisit_last_checked:
  - {condition_id: c1, checked_at: "2026-09-28T09:07:00+09:00", result: not_met, reason: null, evidence_urls: [example.invalid/tailwindcss-preset-px-to-rem/releases]}
  - {condition_id: c2, checked_at: "2026-09-28T09:07:00+09:00", result: undetermined, reason: human, evidence_urls: []}
---
# (예시) Tailwind CSS v4 업그레이드 보류

## Context and Problem Statement
`tailwind.config.ts`가 `tailwindcss-preset-px-to-rem` 프리셋에 기대어 px 단위 디자인 값을 rem으로 바꿔 쓴다. Tailwind v4는 설정 방식이 CSS 중심으로 바뀌어 이 프리셋을 그대로 쓸 수 있는지 확인이 필요하다.

## Decision Outcome
보류. 프리셋이 v4를 지원하기 전에는 올리지 않는다. 지원 여부는 프리셋 릴리스 노트로 확인한다.

> 예시 — Decision Radar 개발 데이터용으로 레포 주인이 지어 쓴 ADR이다. Andbread 팀의 실제 결정이 아니다.
