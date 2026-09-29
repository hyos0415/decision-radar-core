---
status: hold
technology: Jev
use: Finance Verifier 결정 모델 교체
affected:
  - {repo: finance-verifier, component: verifier-core}
evidence:
  - {url: "repo:finance-verifier/eval/results/jev-2026-07.json", source_type: repo}
eval_assets: [finance-verifier/eval/heldout-53.jsonl]
revisit_when:
  - id: c1
    check_type: web
    condition: v0.9.0 이상(SemVer, pre-release 제외) 릴리스가 나온다
    source_hint: jev.example/releases
  - id: c2
    check_type: numeric
    condition: Held-out 재평가 p95 latency가 800ms 미만
    source_hint: "finance-verifier:eval/results/jev-latest.json"
    threshold: {metric: p95_ms, op: "<", value: 800}
  - id: c3
    check_type: human
    condition: confidence 설명가능성 요구가 완화된다
revisit_last_checked:
  - {condition_id: c1, checked_at: "2026-09-21T09:07:00+09:00", result: not_met, reason: null, evidence_urls: [jev.example/releases]}
  - {condition_id: c2, checked_at: "2026-09-21T09:07:00+09:00", result: not_met, reason: null, evidence_urls: ["repo:finance-verifier/eval/results/jev-latest.json"]}
  - {condition_id: c3, checked_at: "2026-09-21T09:07:00+09:00", result: undetermined, reason: human, evidence_urls: []}
---
# Jev를 Finance Verifier 결정 모델로 채택할지

## Context and Problem Statement
새 Decision Model Jev를 Finance Verifier에 적용할 수 있는지 검토했다. 기존 Held-out 53건을 재사용해 성능·지연·confidence를 비교하고 통계 검정까지 했다.

## Decision Outcome
보류. 채택을 확정할 만한 차이가 없었다.

> 합성 픽스처 — eval용. 조건과 수치는 지어낸 것이다.
