---
status: rejected
technology: Bedrock Guardrails
use: prompt injection 방어
affected:
  - {repo: medical-mate, component: chat-api}
evidence:
  - {url: "repo:medical-mate/eval/injection-114.jsonl", source_type: repo}
eval_assets: [medical-mate/eval/injection-114.jsonl]
revisit_when:
  - id: c1
    check_type: web
    condition: prompt attack 필터가 한국어를 공식 지원한다
    source_hint: docs.cloudvendor.example/guardrails/languages
  - id: c2
    check_type: numeric
    condition: prompt attack 필터(text)의 1k 텍스트 유닛당 가격이 0.10 USD 이하
    source_hint: cloudvendor.example/pricing/guardrails
    threshold: {metric: usd_per_1k_text_units, op: "<=", value: 0.10}
revisit_last_checked:
  - {condition_id: c1, checked_at: "2026-09-21T09:07:00+09:00", result: not_met, reason: null, evidence_urls: [docs.cloudvendor.example/guardrails/languages]}
  - {condition_id: c2, checked_at: "2026-09-21T09:07:00+09:00", result: not_met, reason: null, evidence_urls: [cloudvendor.example/pricing/guardrails]}
---
# Bedrock Guardrails를 prompt injection 방어로 쓸지

## Decision Outcome
기각. Prompt Injection 28건 + 정상 86건 평가에서 기존 custom guard 대비 추가 가치가 없었다.

> 합성 픽스처 — eval용.
