---
status: hold
technology: LEANN
use: vector index 저장 절감
affected:
  - {repo: medical-mate, component: retrieval}
evidence:
  - {url: arxiv.org/abs/2506.08276, source_type: paper}
revisit_when:
  - id: c1
    check_type: web
    condition: incremental insert를 공식 지원한다
    source_hint: github.example/leann/releases
  - id: c2
    check_type: numeric
    condition: 검색 코퍼스가 10만 건 이상
    source_hint: "medical-mate:data/corpus_stats.json"
    threshold: {metric: doc_count, op: ">=", value: 100000}
revisit_last_checked:
  - {condition_id: c1, checked_at: "2026-09-21T09:07:00+09:00", result: not_met, reason: null, evidence_urls: [github.example/leann/releases]}
  - {condition_id: c2, checked_at: "2026-09-21T09:07:00+09:00", result: not_met, reason: null, evidence_urls: ["repo:medical-mate/data/corpus_stats.json"]}
---
# LEANN을 medical-mate vector index로 쓸지

## Decision Outcome
보류. storage / latency / retrieval quality trade-off를 기존 구조와 비교할 필요를 확인했다.

> 합성 픽스처 — eval용. (arXiv 링크는 실재 논문)
