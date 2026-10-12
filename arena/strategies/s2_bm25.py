"""S2 BM25 (HYO-35 일부) — ES(9.5.4 + analysis-nori, basic)에 청크마다 text를 두 분석기로 색인한다.

- `text` = 한국어 `nori` 분석 · `text.en` = 영어 `english` 분석. 질의는 두 필드에 multi_match(best_fields)
- BM25 k1=1.2 · b=0.75 (ES 기본값이지만 **명시적으로 설정**하고 system.json에 적는다)
- replicas 0 · 샤드 1 · 인덱스 이름에 코퍼스 버전을 넣는다 · bulk 오류 확인 · 색인 뒤 건수 검증
"""
import re
import time

from .. import es

K1, B = 1.2, 0.75


def index_name_for(corpus_version, strategy="s2"):
    """ES 인덱스 이름 규칙: 소문자 · `@` 같은 글자는 `-`로. 코퍼스 버전이 이름에 들어간다."""
    safe = re.sub(r"[^a-z0-9._-]+", "-", f"arena-{corpus_version}-{strategy}".lower())
    return safe


class S2BM25:
    id = "S2"
    version = "v1"

    def __init__(self, corpus_version, es_url=es.DEFAULT_URL, k1=K1, b=B):
        self.es_url = es_url
        self.k1, self.b = k1, b
        self.index_name = index_name_for(corpus_version, "s2")
        self.fields = ["text", "text.en"]
        self.took_ms = []            # 질의마다 ES가 보고한 took
        self.n_indexed = 0

    # -- 설정 (system.json에 그대로 기록한다) --
    def settings(self):
        return {
            "index": self.index_name,
            "similarity": {"type": "BM25", "k1": self.k1, "b": self.b},
            "analyzers": {"text": "nori (decompound_mode=mixed — 원형과 분해형을 둘 다 색인, 사용자 2026-10-12)", "text.en": "english"},
            "query": {"type": "multi_match", "fields": self.fields, "multi_match_type": "best_fields"},
            "number_of_shards": 1, "number_of_replicas": 0,
        }

    def _body(self):
        return {
            "settings": {
                "index": {"number_of_shards": 1, "number_of_replicas": 0,
                          "similarity": {"bm25_s2": {"type": "BM25", "k1": self.k1, "b": self.b}}},
                "analysis": {"analyzer": {"ko_nori": {"type": "nori", "decompound_mode": "mixed"}}},
            },
            "mappings": {"properties": {
                "chunk_id": {"type": "keyword"},
                "doc_id": {"type": "keyword"},
                "source_type": {"type": "keyword"},
                "text": {"type": "text", "analyzer": "ko_nori", "similarity": "bm25_s2",
                         "fields": {"en": {"type": "text", "analyzer": "english", "similarity": "bm25_s2"}}},
            }},
        }

    def index(self, chunks):
        st, _ = es.call(self.es_url, "DELETE", "/" + self.index_name)      # 이름으로 지운다(와일드카드는 막힘). 없으면 404
        st, body = es.call(self.es_url, "PUT", "/" + self.index_name, self._body())
        es.expect(st, body, "인덱스 생성")
        rows = [(c["chunk_id"], {"chunk_id": c["chunk_id"], "doc_id": c["doc_id"], "source_type": c["source_type"],
                                 "text": c["text"]}) for c in chunks]
        es.bulk_index(self.es_url, self.index_name, rows)
        es.expect(*es.call(self.es_url, "POST", f"/{self.index_name}/_refresh"), "refresh")
        st, body = es.call(self.es_url, "GET", f"/{self.index_name}/_count")
        n = es.expect(st, body, "count")["count"]
        if n != len(chunks):
            raise es.ESError(f"색인 건수 {n} ≠ 청크 {len(chunks)} — 조용히 빠진 문서가 있다")
        self.n_indexed = n

    def search(self, query, k):
        body = {"size": k, "_source": False, "query": {"multi_match": {
            "query": query, "fields": self.fields, "type": "best_fields"}}}
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
