"""S5b SPLADE + Dense (RRF) (HYO-35, 이슈 #63) — S3a(SPLADE-v3)와 S4(Dense)의 순위를 **문서 단위**에서 합친다.

S5a와 같은 코드(`s5a_rrf.TwoWayRRF`)다 — 같은 `rrf_fuse`(ranx) · 같은 `chunk_k` · 같은 대표 청크 규칙
(두 구성요소 중 더 높은 순위로 올라온 청크, 같으면 **앞 구성요소(SPLADE)** 쪽). 구성요소만 S2 대신 S3a다.
S5a와 짝 비교로 「SPLADE 확장어가 노이즈가 되는가」를 본다 (retrieval-arena §6 · A2).
`--strategy s5b`는 S3a와 S4를 같은 실행 안에서 새로 색인·검색한다 — 저장된 옛 결과를 재사용하지 않는다.

SPLADE 구성요소는 둘 중 하나다 (이슈 #66, 등록: S5b의 SPLADE 선택은 dev에서 정한다) — `s3a`(SPLADE-v3, 기본) · `s3a.ko`(KURE-Sparse-nano).
기본값은 그대로 s3a라 옛 호출 · 옛 결과 모양이 바뀌지 않는다. 고른 것은 `settings()`의 구성요소 키와 `sparse_component`에 남는다.

RRF k 기본값은 SPLADE 구성요소마다 다르다 — dev에서 정했다 (2026-10-13, 사용자 결정 · 결과 전 적은 규칙):
`s3a` **10** (k=60 대비 nDCG@10 +0.030, 떨어진 유형 최대 −0.030) · `s3a.ko` **60** (k=10이 +0.012 — 잡음 범위라 그대로).
"""
from .s5a_rrf import CHUNK_K, TwoWayRRF

SPARSE_CHOICES = ("s3a", "s3a.ko")
RRF_K_BY_SPARSE = {"s3a": 10, "s3a.ko": 60}


class S5bRRF(TwoWayRRF):
    id = "S5b"
    version = "v1"

    def __init__(self, s3a, s4, rrf_k=None, chunk_k=CHUNK_K, sparse="s3a"):
        if sparse not in SPARSE_CHOICES:
            raise ValueError(f"S5b sparse={sparse!r} — {SPARSE_CHOICES} 중 하나여야 한다")
        if rrf_k is None:
            rrf_k = RRF_K_BY_SPARSE[sparse]
        super().__init__(s3a, s4, rrf_k=rrf_k, chunk_k=chunk_k)
        self.names = (sparse, "s4")
        self.sparse = sparse
        self.s3a, self.s4 = s3a, s4          # s3a = SPLADE 구성요소 자리 (sparse가 s3a.ko여도 이 이름을 쓴다 — 옛 코드 호환)

    def settings(self):
        return {**super().settings(), "sparse_component": self.sparse}
