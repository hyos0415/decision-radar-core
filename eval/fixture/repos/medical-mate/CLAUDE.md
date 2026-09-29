# medical-mate (합성 픽스처)

## 스택
- `chat-api`: FastAPI, custom injection guard
- `pii-guard`: regex + Bedrock Guardrails hybrid
- `retrieval`: FAISS HNSW, 코퍼스 통계 `data/corpus_stats.json`

## 의존성 → component
- `fastapi`, `pydantic` → chat-api
- `faiss-cpu` → retrieval
- `boto3` → pii-guard
