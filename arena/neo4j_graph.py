"""graph@v1을 Neo4j에 적재하고 다시 읽는다 (HYO-153). 정의는 `docs/design/retrieval-arena.md` §6 변경 (2026-10-28, PPR 위치).

Neo4j가 S7·S8·S9가 함께 쓰는 그래프 원본이다(§8 「같은 Neo4j 인스턴스」). PPR은 dev 파일럿과 같은 networkx로 돌린다 —
S7은 Neo4j에서 꺼낸 그래프에 `nx.pagerank`를 쓴다(GDS 플러그인 없음, 사용자 결정 2026-10-28).
ES와 같이 HTTP(트랜잭션 API)를 urllib로 직접 부른다 — neo4j 드라이버를 넣지 않는다(infra/smoke.py와 같은 방식).

저장 모양
  (:ArenaNode:<Kind> {graph, id, kind, ord, attrs})   attrs = kind를 뺀 속성의 JSON (튜플은 {"__tuple__": [...]})
  -[:<REL> {graph, ord, attrs}]->                       attrs = rel을 뺀 속성의 JSON
  (:ArenaGraph {graph, nodes_version, nodes, edges, fingerprint})
ord는 networkx에 넣은 순서다 — 순서가 바뀌면 PPR의 부동소수 합 순서가 바뀔 수 있어, 읽을 때 같은 순서로 되살린다.
"""
import hashlib
import json
import os
import urllib.error
import urllib.request

import networkx as nx

DEFAULT_URL = os.environ.get("ARENA_NEO4J_URL", "http://127.0.0.1:7474")
DB = "neo4j"
BATCH = 500
LABELS = {"technology": "Technology", "component": "Component", "release": "Release", "adr": "Adr", "document": "Document"}


class Neo4jError(RuntimeError):
    pass


def tx(base, statements, timeout=120):
    """[(cypher, params)] 한 트랜잭션 → 문장마다 [행]. 오류가 하나라도 있으면 Neo4jError (커밋되지 않는다)."""
    body = {"statements": [{"statement": s, "parameters": p or {}} for s, p in statements]}
    req = urllib.request.Request(f"{base.rstrip('/')}/db/{DB}/tx/commit", data=json.dumps(body).encode("utf-8"),
                                 method="POST", headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = json.loads(r.read() or b"{}")
    except urllib.error.URLError as e:
        raise Neo4jError(f"Neo4j({base})에 닿지 않는다 — `docker compose -f infra/compose.yaml up -d neo4j` 했나: {e}") from e
    if out.get("errors"):
        raise Neo4jError("; ".join(f"{x.get('code')}: {x.get('message')}" for x in out["errors"]))
    return [[row["row"] for row in res["data"]] for res in out["results"]]


# ── 속성 직렬화 — 튜플까지 그대로 되살린다 ───────────────────────────────

def _enc(v):
    if isinstance(v, tuple):
        return {"__tuple__": [_enc(x) for x in v]}
    if isinstance(v, list):
        return [_enc(x) for x in v]
    if isinstance(v, dict):
        return {k: _enc(x) for k, x in v.items()}
    return v


def _dec(v):
    if isinstance(v, dict):
        if set(v) == {"__tuple__"}:
            return tuple(_dec(x) for x in v["__tuple__"])
        return {k: _dec(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_dec(x) for x in v]
    return v


def dump_attrs(d, skip):
    return json.dumps(_enc({k: v for k, v in d.items() if k != skip}), ensure_ascii=False, sort_keys=True)


def load_attrs(s):
    return _dec(json.loads(s))


# ── 그래프 ↔ 행 ─────────────────────────────────────────────────────

def to_rows(G):
    """MultiDiGraph → (노드 행, 관계 행). 순서는 networkx 순서 그대로(ord)."""
    nodes = [{"id": n, "kind": d["kind"], "ord": i, "attrs": dump_attrs(d, "kind")} for i, (n, d) in enumerate(G.nodes(data=True))]
    edges = [{"a": a, "b": b, "rel": d["rel"], "ord": i, "attrs": dump_attrs(d, "rel")}
             for i, (a, b, d) in enumerate(G.edges(data=True))]
    return nodes, edges


def from_rows(graph_attrs, nodes, edges):
    G = nx.MultiDiGraph(**graph_attrs)
    for r in sorted(nodes, key=lambda r: r["ord"]):
        G.add_node(r["id"], kind=r["kind"], **load_attrs(r["attrs"]))
    for r in sorted(edges, key=lambda r: r["ord"]):
        G.add_edge(r["a"], r["b"], rel=r["rel"], **load_attrs(r["attrs"]))
    return G


def fingerprint(G):
    """노드·관계의 순서·속성까지 담은 sha256 — 둘이 같으면 S7이 같은 PPR 입력을 받는다."""
    nodes, edges = to_rows(G)
    blob = json.dumps({"graph": dict(sorted(G.graph.items())), "nodes": nodes, "edges": edges}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ── 적재 · 읽기 ─────────────────────────────────────────────────────

def _chunks(rows):
    for i in range(0, len(rows), BATCH):
        yield rows[i:i + BATCH]


def load(G, base=DEFAULT_URL):
    """같은 graph 이름의 옛 적재를 지우고 다시 넣는다. → 적재 요약."""
    g = G.graph["version"]
    nodes, edges = to_rows(G)
    unknown = sorted({r["kind"] for r in nodes} - set(LABELS))
    if unknown:
        raise Neo4jError(f"라벨이 없는 노드 종류: {unknown}")
    tx(base, [("CREATE INDEX arena_node_id IF NOT EXISTS FOR (n:ArenaNode) ON (n.graph, n.id)", None)])
    tx(base, [("MATCH (n:ArenaNode {graph: $g}) DETACH DELETE n", {"g": g}),
              ("MATCH (m:ArenaGraph {graph: $g}) DELETE m", {"g": g})])
    for kind, label in LABELS.items():
        rows = [r for r in nodes if r["kind"] == kind]
        for part in _chunks(rows):
            tx(base, [(f"UNWIND $rows AS r CREATE (n:ArenaNode:{label} {{graph: $g, id: r.id, kind: r.kind, ord: r.ord, attrs: r.attrs}})",
                       {"rows": part, "g": g})])
    for rel in sorted({r["rel"] for r in edges}):
        if not rel.replace("_", "").isalpha() or not rel.isupper():
            raise Neo4jError(f"관계 이름이 이상하다: {rel!r}")       # Cypher에 그대로 넣으므로 확인한다
        rows = [r for r in edges if r["rel"] == rel]
        for part in _chunks(rows):
            tx(base, [(f"UNWIND $rows AS r MATCH (a:ArenaNode {{graph: $g, id: r.a}}), (b:ArenaNode {{graph: $g, id: r.b}}) "
                       f"CREATE (a)-[:{rel} {{graph: $g, ord: r.ord, attrs: r.attrs}}]->(b)", {"rows": part, "g": g})])
    fp = fingerprint(G)
    tx(base, [("CREATE (:ArenaGraph {graph: $g, nodes_version: $nv, nodes: $n, edges: $e, fingerprint: $fp})",
               {"g": g, "nv": G.graph.get("nodes_version"), "n": len(nodes), "e": len(edges), "fp": fp})])
    return {"graph": g, "nodes": len(nodes), "edges": len(edges), "fingerprint": fp}


def read(graph="graph@v1", base=DEFAULT_URL):
    """Neo4j → MultiDiGraph (적재 때 순서 그대로). 적재된 것이 없으면 Neo4jError."""
    meta, nodes, edges = tx(base, [
        ("MATCH (m:ArenaGraph {graph: $g}) RETURN m.nodes_version, m.nodes, m.edges, m.fingerprint", {"g": graph}),
        ("MATCH (n:ArenaNode {graph: $g}) RETURN n.id, n.kind, n.ord, n.attrs", {"g": graph}),
        ("MATCH (a:ArenaNode {graph: $g})-[e]->(b:ArenaNode {graph: $g}) WHERE e.graph = $g "
         "RETURN a.id, b.id, type(e), e.ord, e.attrs", {"g": graph}),
    ])
    if len(meta) != 1:
        raise Neo4jError(f"Neo4j에 {graph} 적재 기록이 {len(meta)}개다 — `arena/cli.py graph-load`를 먼저 돌린다")
    nv, n_nodes, n_edges, fp = meta[0]
    G = from_rows({"version": graph, "nodes_version": nv},
                  [{"id": i, "kind": k, "ord": o, "attrs": a} for i, k, o, a in nodes],
                  [{"a": a, "b": b, "rel": r, "ord": o, "attrs": s} for a, b, r, o, s in edges])
    if (G.number_of_nodes(), G.number_of_edges()) != (n_nodes, n_edges):
        raise Neo4jError(f"Neo4j {graph}: 기록 노드 {n_nodes}·관계 {n_edges} ≠ 읽은 노드 {G.number_of_nodes()}·관계 {G.number_of_edges()}")
    return G, fp


def builder(base=DEFAULT_URL):
    """S7Graph(graph_builder=)용 — Neo4j 그래프를 쓰되, 코퍼스로 만든 graph@v1과 지문이 다르면 거부한다(옛 적재로 재지 않게)."""
    from arena import graph as graph_mod

    def build(docs, nodes):
        G0, report = graph_mod.build(docs, nodes)
        G, stored_fp = read(G0.graph["version"], base)
        fp0, fp = fingerprint(G0), fingerprint(G)
        if not (fp0 == fp == stored_fp):
            raise Neo4jError(f"Neo4j의 {G0.graph['version']}가 지금 코퍼스로 만든 그래프와 다르다 (코퍼스 {fp0[:12]} · Neo4j {fp[:12]} · "
                             f"적재 기록 {stored_fp[:12]}) — `arena/cli.py graph-load`로 다시 적재한다")
        return G, {**report, "graph_source": "neo4j", "neo4j_url": base, "fingerprint": fp}

    return build
