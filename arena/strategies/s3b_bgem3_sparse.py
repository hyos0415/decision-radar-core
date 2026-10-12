"""S3b BGE-M3 sparse (HYO-35) — 같은 모델 한 번 통과에서 나온 lexical weights로 **로컬 정확 점수**를 낸다 (ANN 없음).

점수 = 질의 lexical weights · 청크 lexical weights 의 내적 (겹치는 토큰 id의 가중치 곱의 합 — FlagEmbedding
`compute_lexical_matching_score`와 같은 식). 겹치는 토큰이 없는 청크는 점수 0이라 순위에 넣지 않는다.
입력 토큰에만 가중치를 준다(확장어 없음). 색인 = 메모리의 역색인 — ES를 쓰지 않는다.
"""
from collections import defaultdict


def dot(q, d):
    """희소 내적. q · d = {토큰 id 문자열: 가중치}"""
    if len(q) > len(d):
        q, d = d, q
    return sum(w * d[t] for t, w in q.items() if t in d)


class S3bSparse:
    id = "S3b"
    version = "v1"

    def __init__(self, corpus_version, embedder):
        self.embedder = embedder
        self.corpus_version = corpus_version
        self.took_ms = []
        self.n_indexed = 0
        self._ids, self._inv, self._nnz = [], {}, 0

    def settings(self):
        return {
            "embedding_model": f"{self.embedder.encoder.model_id}@{self.embedder.encoder.revision}",
            "scoring": "lexical weights 내적 (정확 점수, ANN 없음)",
            "zero_overlap_chunks": "순위에 넣지 않는다 (점수 0)",
            "index": "메모리 역색인 (토큰 id → [(청크 행, 가중치)])",
            "index_size_estimate": "비영 항목 수 × 8바이트 (int32 토큰 id + float32 가중치) — ES 인덱스 크기와 같은 잣대가 아니다",
        }

    def index(self, chunks):
        _, sparse = self.embedder.encode_chunks(chunks)
        inv = defaultdict(list)
        nnz = 0
        for row, lw in enumerate(sparse):
            for t, w in lw.items():
                inv[t].append((row, float(w)))
                nnz += 1
        self._ids, self._inv, self._nnz = [c["chunk_id"] for c in chunks], dict(inv), nnz
        self.n_indexed = len(chunks)

    def search(self, query, k):
        _, qs = self.embedder.encode_query(query)
        scores = defaultdict(float)
        for t, qw in qs.items():
            for row, dw in self._inv.get(t, ()):
                scores[row] += float(qw) * dw
        top = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:k]
        return [(self._ids[row], s) for row, s in top]

    def index_size_bytes(self):
        return self._nnz * 8

    def server_version(self):
        return None

    def drop(self):
        pass
