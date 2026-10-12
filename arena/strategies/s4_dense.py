"""S4 Dense (HYO-35) — BGE-M3 dense(1024차원, 로컬 임베딩) → ES kNN.

색인 설정은 retrieval-arena §6 「변경 (2026-10-02)」에 등록돼 있다 — `dense_vector` dims 1024 · similarity cosine ·
`index_options: hnsw` **float32, 양자화 없음**. (ES 기본값은 라이선스·버전에 따라 bbq_hnsw 등으로 바뀐다 — 그래서 명시한다.)

HNSW 파라미터(m · ef_construction · num_candidates)는 **dev에서 정하고 test 전에 고정**한다 (모든 S4 변형이 같은 값).
지금 기본값(m=16 · ef_construction=100 · num_candidates=100)은 장난감 실행용 자리표시다 — 정한 값이 아니다.
같은 인덱스 이름 규칙(코퍼스 버전 포함) · 색인 건수 검증 · bulk 오류 확인은 S2와 같다.
"""
import numpy as np

from .. import es
from .s2_bm25 import index_name_for

DIMS = 1024
FIELD = "embedding"
M, EF_CONSTRUCTION, NUM_CANDIDATES = 16, 100, 100


class S4Dense:
    id = "S4"
    version = "v1"

    def __init__(self, corpus_version, embedder, es_url=es.DEFAULT_URL, m=M, ef_construction=EF_CONSTRUCTION,
                 num_candidates=NUM_CANDIDATES):
        for name, v, lo, hi in (("m", m, 2, 512), ("ef_construction", ef_construction, 1, 3200),
                                ("num_candidates", num_candidates, 1, 10000)):
            if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
                raise ValueError(f"S4 {name}={v!r} — {lo}~{hi} 정수여야 한다")
        self.es_url, self.embedder = es_url, embedder
        self.m, self.ef_construction, self.num_candidates = m, ef_construction, num_candidates
        self.index_name = index_name_for(corpus_version, "s4")
        self.took_ms = []
        self.n_indexed = 0

    def settings(self):
        return {
            "index": self.index_name,
            "embedding_model": f"{self.embedder.encoder.model_id}@{self.embedder.encoder.revision}",
            "dense_vector": {"dims": DIMS, "index": True, "similarity": "cosine",
                             "index_options": {"type": "hnsw", "m": self.m, "ef_construction": self.ef_construction}},
            "quantization": None,
            "query": {"type": "knn", "num_candidates": self.num_candidates,
                      "note": "k = 요청한 청크 수(하네스 chunk_k) · num_candidates ≥ k여야 한다 · ES cosine 점수는 (1+cos)/2"},
            "hnsw_status": "자리표시 기본값 — dev에서 정하고 test 전에 고정한다 (retrieval-arena §6 변경 2026-10-02)",
            "number_of_shards": 1, "number_of_replicas": 0,
        }

    def _body(self):
        return {
            "settings": {"index": {"number_of_shards": 1, "number_of_replicas": 0}},
            "mappings": {"properties": {
                "chunk_id": {"type": "keyword"},
                "doc_id": {"type": "keyword"},
                "source_type": {"type": "keyword"},
                FIELD: {"type": "dense_vector", "dims": DIMS, "index": True, "similarity": "cosine",
                        "index_options": {"type": "hnsw", "m": self.m, "ef_construction": self.ef_construction}},
            }},
        }

    def index(self, chunks):
        dense, _ = self.embedder.encode_chunks(chunks)
        if dense.shape != (len(chunks), DIMS):
            raise ValueError(f"임베딩 모양 {dense.shape} ≠ ({len(chunks)}, {DIMS})")
        if not np.isfinite(dense).all():
            raise ValueError("임베딩에 NaN/inf가 있다")
        es.call(self.es_url, "DELETE", "/" + self.index_name)
        st, body = es.call(self.es_url, "PUT", "/" + self.index_name, self._body())
        es.expect(st, body, "인덱스 생성")
        rows = [(c["chunk_id"], {"chunk_id": c["chunk_id"], "doc_id": c["doc_id"], "source_type": c["source_type"],
                                 FIELD: dense[i].tolist()}) for i, c in enumerate(chunks)]
        es.bulk_index(self.es_url, self.index_name, rows, batch=100)
        es.expect(*es.call(self.es_url, "POST", f"/{self.index_name}/_refresh"), "refresh")
        st, body = es.call(self.es_url, "GET", f"/{self.index_name}/_count")
        n = es.expect(st, body, "count")["count"]
        if n != len(chunks):
            raise es.ESError(f"색인 건수 {n} ≠ 청크 {len(chunks)} — 조용히 빠진 문서가 있다")
        self.n_indexed = n

    def search(self, query, k):
        if k > self.num_candidates:
            raise ValueError(f"k={k} > num_candidates={self.num_candidates} — ES가 거부한다. chunk_k를 줄이거나 --num-candidates를 늘린다")
        qd, _ = self.embedder.encode_query(query)
        body = {"size": k, "_source": False,
                "knn": {"field": FIELD, "query_vector": qd.tolist(), "k": k, "num_candidates": self.num_candidates}}
        st, res = es.call(self.es_url, "POST", f"/{self.index_name}/_search", body)
        es.expect(st, res, "search")
        self.took_ms.append(res.get("took", 0))
        return [(h["_id"], float(h["_score"])) for h in res["hits"]["hits"]]

    def index_size_bytes(self):
        st, body = es.call(self.es_url, "GET", f"/{self.index_name}/_stats/store")
        es.expect(st, body, "stats")
        return body["_all"]["primaries"]["store"]["size_in_bytes"]

    def server_version(self):
        st, body = es.call(self.es_url, "GET", "/")
        return es.expect(st, body, "info")["version"]["number"]

    def drop(self):
        es.call(self.es_url, "DELETE", "/" + self.index_name)
