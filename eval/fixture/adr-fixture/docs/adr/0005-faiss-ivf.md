---
status: superseded
technology: FAISS IVF
use: medical-mate 초기 vector index
affected:
  - {repo: medical-mate, component: retrieval}
revisit_when:
  - id: c1
    check_type: web
    condition: (확인 범위 밖 — superseded)
    source_hint: faiss.example/releases
---
# FAISS IVF → HNSW로 대체됨

> 합성 픽스처 — eval용.
