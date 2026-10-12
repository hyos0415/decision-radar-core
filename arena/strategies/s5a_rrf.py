"""S5a BM25 + Dense (RRF) (HYO-35) — S2와 S4의 순위를 **문서 단위**에서 합친다.

1. 두 구성요소가 같은 상위 N 청크(`chunk_k`, 기본 100)를 가져온다
2. 하네스와 같은 규칙(`harness.rollup_chunks`)으로 문서 순위로 올린다 — 문서마다 최고 순위 청크 하나
3. `ranx.fuse(method="rrf", params={"k": 60})` — ES의 RRF retriever는 Enterprise라 쓰지 않는다 (retrieval-arena A7)
4. 융합된 문서마다 **대표 청크**(두 구성요소 중 더 높은 순위로 올라온 청크, 같으면 S2 쪽)를 돌려준다.
   하네스의 rollup은 문서당 하나뿐인 목록을 그대로 통과시킨다.

`--strategy s5a`는 S2와 S4를 **같은 실행 안에서** 새로 색인·검색한다 — 저장된 옛 결과를 재사용하지 않는다.
RRF k=60은 자리표시 기본값이다 (이슈 #58: RRF k는 dev에서 정한다).
"""
import time
from importlib import metadata

from .. import harness

RRF_K = 60
CHUNK_K = 100
PLACEHOLDER = "ph_empty"                 # 고정 폭 8자 (번호 id와 같은 폭)


def rrf_fuse(rankings, k=RRF_K):
    """rankings = [[doc_id, ...] 순위 목록 (1위부터)] → [(doc_id, rrf 점수)] 점수 내림차순.
    ranx.fuse를 그대로 부른다. 동점은 먼저 등장한 문서가 앞이다 (구성요소 순서 → 순위 순서).

    문서 id는 고정 폭(8자) 번호로 바꿔 넘기고 돌려받아 되돌린다 — ranx(numba)는 id 문자열 폭이 달라질 때마다
    다시 컴파일해서(수십 초) 질의 지연이 JIT 시간으로 뒤덮인다. 번호는 첫 등장 순서라 순위·점수에 영향이 없다."""
    from ranx import Run, fuse
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        raise ValueError(f"RRF k={k!r} — 1 이상의 정수여야 한다")
    code, doc_of = {}, {}
    for docs in rankings:
        for d in docs:
            if d not in code:
                code[d] = f"{len(code):08d}"
                doc_of[code[d]] = d
    runs = []
    for i, docs in enumerate(rankings):
        # ranx는 빈 질의 결과를 못 받는다 → 자리표시 문서 하나를 넣고 융합 뒤에 뺀다 (순위 끝이라 다른 문서 점수에 영향 없다)
        scored = {code[d]: float(len(docs) - r) for r, d in enumerate(docs)} or {PLACEHOLDER: 0.0}
        runs.append(Run.from_dict({"q": scored}, name=f"c{i}"))
    fused = fuse(runs=runs, norm="max", method="rrf", params={"k": k}).to_dict()["q"]
    items = [(doc_of[c], sc) for c, sc in fused.items() if c != PLACEHOLDER]
    first_seen = {d: n for n, d in enumerate(code)}
    items.sort(key=lambda kv: (-round(kv[1], 12), first_seen[kv[0]]))
    return items


def warm_up():
    """ranx의 numba 컴파일을 질의 시간 밖에서 미리 끝낸다 (색인 때 한 번). -> 걸린 초"""
    t0 = time.perf_counter()
    rrf_fuse([["a", "b"], ["b", "a"]])
    rrf_fuse([["a"], []])
    return time.perf_counter() - t0


class TwoWayRRF:
    """두 구성요소를 문서 단위 RRF로 합치는 공통 몸통 — S5a(BM25 + Dense)와 S5b(SPLADE + Dense)가 같은 코드를 쓴다.
    `names` = settings()["components"]의 키 (구성요소 순서 — 대표 청크 동점은 앞쪽이 이긴다)."""
    id = "S?"
    version = "v1"
    names = ("first", "second")

    def __init__(self, first, second, rrf_k=RRF_K, chunk_k=CHUNK_K):
        if isinstance(rrf_k, bool) or not isinstance(rrf_k, int) or rrf_k < 1:
            raise ValueError(f"RRF k={rrf_k!r} — 1 이상의 정수여야 한다")
        if isinstance(chunk_k, bool) or not isinstance(chunk_k, int) or chunk_k < 1:
            raise ValueError(f"chunk_k={chunk_k!r} — 1 이상의 정수여야 한다")
        self.components = (first, second)
        self.rrf_k, self.chunk_k = rrf_k, chunk_k
        self.chunk_to_doc = {}
        self.n_indexed = 0
        self.warmup_seconds = None

    @property
    def took_ms(self):
        return [ms for c in self.components for ms in c.took_ms]

    def settings(self):
        try:
            ranx_version = metadata.version("ranx")
        except metadata.PackageNotFoundError:
            ranx_version = None
        return {
            "fusion": {"library": "ranx", "ranx_version": ranx_version, "method": "rrf", "params": {"k": self.rrf_k},
                       "level": "document (청크→문서 rollup 뒤 융합 · 문서마다 최고 순위 청크)", "norm": "max (RRF는 순위만 쓴다)",
                       "status": "자리표시 기본값 — RRF k는 dev에서 정한다 (이슈 #58)"},
            "component_chunk_k": self.chunk_k,
            "components": {n: {"id": c.id, "version": c.version, **c.settings()} for n, c in zip(self.names, self.components)},
            "reuse_of_saved_results": False,
            "ranx_jit_warmup_seconds": None if self.warmup_seconds is None else round(self.warmup_seconds, 3),
        }

    def index(self, chunks):
        self.chunk_to_doc = {c["chunk_id"]: c["doc_id"] for c in chunks}
        for c in self.components:
            c.index(chunks)
        self.warmup_seconds = warm_up()
        self.n_indexed = self.components[0].n_indexed

    def _first_stage(self, query):
        """-> (fused [(doc, 점수)], rep {doc: (순위, 구성요소 순서, 대표 chunk_id)},
        seen {doc: [chunk_id]} 두 구성요소 상위 chunk_k 청크 목록에 나온 청크 (구성요소 순서 → 순위 순서, 중복 없음))"""
        ranked, seen = [], {}
        for comp in self.components:
            hits = comp.search(query, self.chunk_k)
            for cid, _ in hits:
                if cid not in self.chunk_to_doc:          # rollup과 같게 모르는 청크 id는 실패
                    raise KeyError(f"전략이 모르는 청크 id를 돌려줬다: {cid}")
                lst = seen.setdefault(self.chunk_to_doc[cid], [])
                if cid not in lst:
                    lst.append(cid)
            ranked.append(harness.rollup_chunks(hits, self.chunk_to_doc))
        rep = {}             # doc -> (순위, 구성요소 순서, chunk_id) 가장 좋은 것
        for ci, docs in enumerate(ranked):
            for rank, (doc, chunk, _) in enumerate(docs):
                cur = rep.get(doc)
                if cur is None or (rank, ci) < cur[:2]:
                    rep[doc] = (rank, ci, chunk)
        fused = rrf_fuse([[d for d, _, _ in docs] for docs in ranked], self.rrf_k)
        return fused, rep, seen

    def search(self, query, k):
        if k != self.chunk_k:
            raise ValueError(f"{self.id}는 구성요소마다 같은 상위 N 청크(chunk_k={self.chunk_k})를 쓴다 — 요청 k={k}와 달라 거부한다")
        fused, rep, _ = self._first_stage(query)
        return [(rep[d][2], round(s, 12)) for d, s in fused]

    def first_stage_candidates(self, query):
        """S6용 — 융합 순서의 [(doc, [후보 chunk_id])]. 후보 = 두 구성요소 상위 chunk_k 청크 목록에 나온 그 문서의 청크 전부."""
        fused, _, seen = self._first_stage(query)
        return [(d, seen[d]) for d, _ in fused]

    def index_size_bytes(self):
        return sum(c.index_size_bytes() for c in self.components)

    def size_breakdown(self):
        return {n: c.index_size_bytes() for n, c in zip(self.names, self.components)}

    def server_version(self):
        for c in self.components:                 # ES를 쓰는 구성요소의 버전 (로컬 전략은 None)
            v = c.server_version()
            if v is not None:
                return v
        return None

    def drop(self):
        for c in self.components:
            c.drop()


class S5aRRF(TwoWayRRF):
    id = "S5a"
    version = "v1"
    names = ("s2", "s4")

    def __init__(self, s2, s4, rrf_k=RRF_K, chunk_k=CHUNK_K):
        super().__init__(s2, s4, rrf_k=rrf_k, chunk_k=chunk_k)
        self.s2, self.s4 = s2, s4
