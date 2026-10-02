# Arena 엔진 (HYO-31)

Retrieval Arena가 쓰는 Elasticsearch(nori)와 Neo4j(n10s). **로컬 실험 전용**이다 — 포트는 127.0.0.1에만 열고, 보안·인증은 껐다. 실험할 때만 띄운다.

| 엔진 | 버전 | 메모리 | 포트 |
|---|---|---|---|
| Elasticsearch + `analysis-nori` | 9.5.4, **basic 라이선스 고정** | heap 1GB · 컨테이너 2GB | 9200 |
| Neo4j Community + n10s | 5.26.31 LTS + n10s 5.26.0 | heap 1GB · pagecache 512MB · 컨테이너 2GB | 7474 · 7687 |

```bash
docker compose -f infra/compose.yaml up -d --build   # 첫 기동: ES 이미지 빌드(nori 설치) + n10s 다운로드
python infra/smoke.py                                 # 엔진이 Arena가 기대는 것을 하는지 (표준 라이브러리만)
docker compose -f infra/compose.yaml stop             # 끄기 (데이터는 볼륨에 남는다)
docker compose -f infra/compose.yaml down -v          # 데이터까지 지우기
```

## smoke.py가 확인하는 것

문서로만 확인했던 등급(`docs/design/doc-pipeline.md` §7)을 Basic에서 직접 확인한다.

- **되어야 하는 것**: ES 버전 · basic 라이선스 · nori 토큰화 · BM25 k1·b 지정 · `dense_vector`의 `hnsw`·`int8_hnsw`·`bbq_hnsw`·`flat`·`int8_flat`·`bbq_flat` 색인과 kNN · Neo4j 버전 · n10s 프로시저 · SHACL 검증 프로시저 · `n10s.graphconfig.init`
- **막혀야 하는 것**: RRF retriever · `bbq_disk`
- **기록하는 것**: `index_options`를 적지 않았을 때의 기본값 — 2026-10-02 실측 **`bbq_hnsw`** (1024차원, Basic). 그래서 S4는 `hnsw`를 명시한다 (`retrieval-arena.md` §6 변경)

## 알아 둘 것 (2026-10-02 실측)

- **`bbq_disk`는 매핑 생성은 통과하고 문서를 넣을 때 403**(`current license is non-compliant for [bbq_disk]`)이 난다. 빈 인덱스 검색은 200 + 빈 결과다 — bulk 응답의 오류를 보지 않으면 **조용히 빈 색인**이 된다. 색인 코드는 bulk 응답의 `errors`를 반드시 확인한다
- ES는 기본으로 **와일드카드 삭제를 막는다**(`action.destructive_requires_name`). 인덱스는 이름마다 지운다
- 단일 노드라 기본 복제본(1) 때문에 인덱스 상태가 yellow다. Arena 인덱스는 `number_of_replicas: 0`으로 만든다
- n10s는 `NEO4J_PLUGINS`로 첫 기동 때 인터넷에서 받는다. 오프라인에서 다시 만들려면 `neo4j-plugins` 볼륨을 유지하거나 jar를 직접 넣는다
- 메모리 실측(기동 직후): ES 약 1.5GB · Neo4j 약 1.4GB. M1 16GB 예산(overview Q1)에서 둘을 같이 띄워도 된다. 임베딩 모델은 엔진과 따로 미리 계산한다
