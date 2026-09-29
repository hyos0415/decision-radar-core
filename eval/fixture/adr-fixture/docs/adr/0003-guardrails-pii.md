---
status: adopted
technology: Bedrock Guardrails
use: PII masking
affected:
  - {repo: medical-mate, component: pii-guard}
evidence:
  - {url: "repo:medical-mate/eval/pii-eval.jsonl", source_type: repo}
revisit_when:
  - id: c1
    check_type: web
    condition: 한국 주민등록번호 패턴을 기본 제공한다
    source_hint: docs.cloudvendor.example/guardrails/pii-patterns
---
# Bedrock Guardrails를 PII masking에 쓸지

## Decision Outcome
채택. regex + Guardrails hybrid로 적용했다.

> 합성 픽스처 — eval용.
