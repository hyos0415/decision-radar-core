"""S7 구조 그래프 (HYO-36) — BM25 문서 시드 → graph@v1 위 PPR. 정의는 `docs/design/retrieval-arena.md` §6 변경 (2026-10-27).

1. S2 BM25(같은 색인·설정)가 상위 `chunk_k` 청크를 가져오고 문서 순위로 올린다 (`harness.rollup_chunks`)
2. 시드 = 상위 `seed_k` BM25 문서 중 그래프 노드(release doc=True · adr · document)인 것, 가중치는 BM25 점수 비례.
   시드가 하나도 없으면 결과는 BM25 순서 그대로다 (S7 = BM25)
3. PPR — 방향 무시 · 단순(평행 관계는 하나로) · 가중치 없는 그래프에서 `nx.pagerank(personalization=시드)`
4. 순위 = 문서 노드를 PPR 점수 순으로 먼저(같은 점수는 BM25 순위 → 문서 id), 그 뒤를 BM25 순서로 채운다(이미 나온 문서는 건너뜀)

**α 주의** — 등록한 α는 *순간이동(시드로 돌아가기) 확률*이다. networkx `pagerank(alpha=)`는 *감쇠 계수* = 1 − 순간이동 확률이라
`nx_damping = 1 − teleport_alpha`를 넘긴다. 둘 다 settings()에 적는다.

반환 점수는 순서를 나타내는 합성값(`len(out) - i`, 엄격 감소)이다 — 하네스의 `rollup_chunks`가 점수로 다시 정렬하므로
점수가 순서를 지켜야 한다. 문서마다 청크 하나만 돌려주므로 rollup은 그대로 통과시킨다.
"""
import time

import networkx as nx

from .. import graph as graph_mod
from .. import harness

DOC_KINDS = ("release", "adr", "document")        # 순위에 오르는 노드 종류 (release는 doc=True만 — 합성 version 노드는 문서가 아니다)
SEED_NEIGHBOR_KINDS = ("technology", "component")  # Seed Recall용 시드에 붙는 1홉 이웃
PPR_MAX_ITER = 100                                 # networkx 기본값이지만 명시하고 기록한다
PPR_TOL = 1e-6
SCORE_DIGITS = 12                                  # 같은 점수 판정 — 부동소수 잡음(1e-17)이 동점 규칙을 깨지 않게 반올림해 비교한다
CHUNK_K = 100


def _check_int(name, v):
    if isinstance(v, bool) or not isinstance(v, int) or v < 1:
        raise ValueError(f"{name}={v!r} — 1 이상의 정수여야 한다")


def is_doc_node(G, n):
    d = G.nodes[n]
    return d["kind"] in ("adr", "document") or (d["kind"] == "release" and d.get("doc") is True)


class S7Graph:
    id = "S7"
    version = "v1"

    def __init__(self, bm25, docs, nodes, seed_k, alpha, chunk_k=CHUNK_K, graph_builder=None):
        """bm25 = S2BM25 · docs = 코퍼스 문서 · nodes = nodes@v2 · alpha = 순간이동 확률 (0, 1) 열린 구간.
        graph_builder = (docs, nodes) → (그래프, 보고) — 테스트가 그래프를 주입하는 틈, 기본은 graph.build."""
        _check_int("seed_k", seed_k)
        _check_int("chunk_k", chunk_k)
        if isinstance(alpha, bool) or not isinstance(alpha, (int, float)) or not 0 < alpha < 1:
            raise ValueError(f"alpha={alpha!r} — 순간이동 확률은 0과 1 사이(열린 구간)여야 한다")
        self.bm25, self.docs, self.nodes = bm25, docs, nodes
        self.seed_k, self.teleport_alpha, self.chunk_k = seed_k, float(alpha), chunk_k
        self.nx_damping = 1.0 - self.teleport_alpha    # networkx alpha = 감쇠 = 1 − 순간이동
        self.graph_builder = graph_builder or graph_mod.build
        self.G = self.Gu = None
        self.graph_stats, self.graph_build_seconds = None, None
        self.chunk_to_doc, self.first_chunk = {}, {}
        self.n_indexed = 0
        self.last_seeds = []

    @property
    def took_ms(self):
        return self.bm25.took_ms

    def settings(self):
        return {
            "first_stage": {"id": self.bm25.id, "version": self.bm25.version, **self.bm25.settings()},
            "first_stage_chunk_k": self.chunk_k,
            "graph": {"version": graph_mod.VERSION, "nodes_version": self.nodes.version, "stats": self.graph_stats,
                      "ppr_graph": "undirected simple unweighted (방향 무시 · 평행 관계는 하나로 · 가중치 없음)",
                      "build_seconds": None if self.graph_build_seconds is None else round(self.graph_build_seconds, 3),
                      "build_seconds_note": "색인 구축 시간(index_build_seconds)에 포함"},
            "seed_k": self.seed_k,
            "teleport_alpha": self.teleport_alpha,
            "nx_damping": self.nx_damping,
            "alpha_note": "teleport_alpha = 시드로 돌아갈 확률(등록한 α) · nx_damping = networkx pagerank(alpha=) = 1 − teleport_alpha",
            "max_iter": PPR_MAX_ITER, "tol": PPR_TOL,
            "seed_weight": "BM25 문서 점수 비례 (합 1)",
            "ranking": "PPR>0인 문서 노드(release doc=True·adr·document)를 PPR 점수 내림차순 (동점: BM25 순위 → 문서 id), 그 뒤를 BM25 순서로 채움 (중복 건너뜀). 시드 없으면 BM25 그대로",
        }

    def index(self, chunks):
        self.bm25.index(chunks)
        t0 = time.perf_counter()                      # 그래프 구축도 색인 구축 시간 안이다 (§6: 그래프 구축 시간은 색인 구축 시간에 넣는다)
        self.G, _ = self.graph_builder(self.docs, self.nodes)
        Gu = nx.Graph()
        Gu.add_nodes_from(self.G.nodes)
        Gu.add_edges_from((u, v) for u, v in self.G.edges())      # 방향·평행 관계를 접는다
        self.Gu = Gu
        self.graph_build_seconds = time.perf_counter() - t0
        self.graph_stats = graph_mod.stats(self.G)
        self.chunk_to_doc, self.first_chunk = {}, {}
        for c in chunks:
            self.chunk_to_doc[c["chunk_id"]] = c["doc_id"]
            self.first_chunk.setdefault(c["doc_id"], c["chunk_id"])
        self.n_indexed = self.bm25.n_indexed

    def _doc_node(self, n):
        return n in self.G and is_doc_node(self.G, n)

    def search(self, query, k):
        hits = self.bm25.search(query, self.chunk_k)
        bm25_docs = harness.rollup_chunks(hits, self.chunk_to_doc)         # [(doc, 대표 chunk, 점수)] BM25 순서
        rank = {d: i for i, (d, _, _) in enumerate(bm25_docs)}
        rep = {d: c for d, c, _ in bm25_docs}

        top = bm25_docs[:self.seed_k]
        seed_docs = [(d, s) for d, _, s in top if self._doc_node(d)]
        self.last_seeds = self._seed_nodes([d for d, _ in seed_docs])
        order = []                                    # 최종 문서 순서
        if seed_docs:
            total = sum(s for _, s in seed_docs)
            pers = ({d: s / total for d, s in seed_docs} if total > 0
                    else {d: 1 / len(seed_docs) for d, _ in seed_docs})    # 점수가 전부 0 이하면 균등 (BM25는 양수라 방어용)
            # networkx는 personalization에 없는 노드를 0으로 본다 (dangling도 같은 분포를 쓴다)
            ppr = nx.pagerank(self.Gu, alpha=self.nx_damping, personalization=pers, max_iter=PPR_MAX_ITER, tol=PPR_TOL)
            cand = [(n, v) for n, v in ppr.items() if v > 0 and n in self.first_chunk and self._doc_node(n)]
            cand.sort(key=lambda nv: (-round(nv[1], SCORE_DIGITS), rank.get(nv[0], len(rank)), nv[0]))
            order = [n for n, _ in cand]
        listed = set(order)
        order += [d for d, _, _ in bm25_docs if d not in listed]
        out = [(rep.get(d, self.first_chunk[d]), 0.0) for d in order[:k]]
        n = len(out)
        return [(c, float(n - i)) for i, (c, _) in enumerate(out)]          # 엄격 감소 합성 점수 — rollup이 순서를 바꾸지 않는다

    def _seed_nodes(self, seed_docs):
        """Seed Recall용 — BM25 순서로 [문서 노드, 그 1홉 technology·component (id순)] 이어 붙이고 중복은 처음 것만."""
        out, seen = [], set()
        for d in seed_docs:
            nbrs = sorted(n for n in self.Gu.neighbors(d) if self.G.nodes[n]["kind"] in SEED_NEIGHBOR_KINDS)
            for n in [d, *nbrs]:
                if n not in seen:
                    seen.add(n)
                    out.append(n)
        return out

    def entity_seeds(self, seed_nodes):
        """Seed Recall용 — 시드 목록에서 문서 노드를 뺀 technology·component만 (순서 유지). 문서 노드는 질의 노드와
        일치할 수 없어 @k 칸만 차지한다 (§5 변경 2026-10-28). seeds.json에는 문서 노드를 포함한 원래 목록을 저장한다."""
        return [n for n in seed_nodes if n in self.G and self.G.nodes[n]["kind"] in SEED_NEIGHBOR_KINDS]

    def index_size_bytes(self):
        return self.bm25.index_size_bytes()           # 그래프는 메모리 안

    def server_version(self):
        return self.bm25.server_version()

    def drop(self):
        self.bm25.drop()
