"""S4 Dense (HYO-35) — BGE-M3 dense(1024차원, 로컬 임베딩) → ES kNN.

색인 설정은 retrieval-arena §6 「변경 (2026-10-02)」에 등록돼 있다 — `dense_vector` dims 1024 · similarity cosine ·
`index_options: hnsw` **float32, 양자화 없음**. (ES 기본값은 라이선스·버전에 따라 bbq_hnsw 등으로 바뀐다 — 그래서 명시한다.)

HNSW 파라미터(m · ef_construction · num_candidates)는 **dev에서 정하고 test 전에 고정**한다 (모든 S4 변형이 같은 값).
**num_candidates = 200** — dev에서 정했다 (2026-10-13, 사용자 결정 · `eval/arena/dev-tuning-2026-10-13.md`): 100이면 하네스가
청크 100개를 요청해 근사 여유가 없어 한 질의의 상위 10이 정확 검색과 절반 달랐다. 200 이상은 정확 검색(4000)과 같고 지연도 같다.
m=16 · ef_construction=100은 그대로 둔다 (코퍼스 3,856청크에서 num_candidates 200이면 이미 정확 검색과 같아 더 볼 것이 없다).
같은 인덱스 이름 규칙(코퍼스 버전 포함) · 색인 건수 검증 · bulk 오류 확인은 S2와 같다.

양자화 변형 (§6 변경 2026-10-02 「Should 변형」, 이슈 #63) — 임베딩 · 청크 · HNSW 파라미터 · cosine은 S4와 같고 `index_options.type`만 다르다.
  `S4.int8` = `int8_hnsw`  ·  `S4.bbq` = `bbq_hnsw` + knn 질의의 `rescore_vector.oversample` **3.0 명시**
인덱스 이름이 따로라(`...-s4-int8` · `...-s4-bbq`) 같은 실행에서 S4와 나란히 둘 수 있다.
"""
import numpy as np

from .. import es
from .s2_bm25 import index_name_for

DIMS = 1024
FIELD = "embedding"
M, EF_CONSTRUCTION, NUM_CANDIDATES = 16, 100, 200


OVERSAMPLE = 3.0                 # S4.bbq 재채점 배수 (등록값 — 바꾸면 변형 새 버전)


class S4Dense:
    id = "S4"
    version = "v1"
    index_type = "hnsw"              # ES index_options.type — 변형은 이 칸만 바꾼다
    index_suffix = "s4"
    oversample = None                # 값이 있으면 knn 질의에 rescore_vector를 넣는다
    quantization = None

    def __init__(self, corpus_version, embedder, es_url=es.DEFAULT_URL, m=M, ef_construction=EF_CONSTRUCTION,
                 num_candidates=NUM_CANDIDATES):
        for name, v, lo, hi in (("m", m, 2, 512), ("ef_construction", ef_construction, 1, 3200),
                                ("num_candidates", num_candidates, 1, 10000)):
            if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
                raise ValueError(f"S4 {name}={v!r} — {lo}~{hi} 정수여야 한다")
        self.es_url, self.embedder = es_url, embedder
        self.m, self.ef_construction, self.num_candidates = m, ef_construction, num_candidates
        self.index_name = index_name_for(corpus_version, self.index_suffix)
        self.took_ms = []
        self.n_indexed = 0

    def settings(self):
        return {
            "index": self.index_name,
            "embedding_model": f"{self.embedder.encoder.model_id}@{self.embedder.encoder.revision}",
            "dense_vector": {"dims": DIMS, "index": True, "similarity": "cosine",
                             "index_options": {"type": self.index_type, "m": self.m, "ef_construction": self.ef_construction}},
            "quantization": self.quantization,
            "query": {"type": "knn", "num_candidates": self.num_candidates,
                      **({"rescore_vector": {"oversample": self.oversample}} if self.oversample is not None else {}),
                      "note": "k = 요청한 청크 수(하네스 chunk_k) · num_candidates ≥ k여야 한다 · ES cosine 점수는 (1+cos)/2"},
            "hnsw_status": "dev에서 정함 (2026-10-13 — num_candidates 200 · m 16 · ef_construction 100, eval/arena/dev-tuning-2026-10-13.md)",
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
                        "index_options": {"type": self.index_type, "m": self.m, "ef_construction": self.ef_construction}},
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
        knn = {"field": FIELD, "query_vector": qd.tolist(), "k": k, "num_candidates": self.num_candidates}
        if self.oversample is not None:
            knn["rescore_vector"] = {"oversample": self.oversample}
        body = {"size": k, "_source": False, "knn": knn}
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


class S4Int8(S4Dense):
    """S4.int8 — `int8_hnsw` (손실이 가장 작은 압축). 재채점 설정은 ES 기본값에 맡기지 않고 쓰지 않는다(질의 본문은 S4와 같다)."""
    id = "S4.int8"
    index_type = "int8_hnsw"
    index_suffix = "s4-int8"
    quantization = "int8"


class S4BBQ(S4Dense):
    """S4.bbq — `bbq_hnsw` (1비트 양자화, Basic의 기본값) + 재채점 `oversample` 3.0 명시."""
    id = "S4.bbq"
    index_type = "bbq_hnsw"
    index_suffix = "s4-bbq"
    quantization = "bbq"
    oversample = OVERSAMPLE


class S4Kure(S4Dense):
    """S4.kure — 임베딩 모델만 `nlpai-lab/KURE-v1`로 바꾼 변형 (등록된 조정 항목 「임베딩 모델」, 이슈 #66). BGE-M3(XLM-R)를 한국어로 더 학습한
    dense 모델이라 차원 1024 · cosine · hnsw float32 · HNSW 파라미터 · 청크가 S4와 같다. 인덱스 이름만 따로(`...-s4-kure`).
    모델 · revision은 `embedder`(= `embed.SentenceTransformerDenseEncoder`)가 정하고 settings의 `embedding_model`에 적힌다."""
    id = "S4.kure"
    index_suffix = "s4-kure"
