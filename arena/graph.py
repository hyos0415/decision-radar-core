"""S7 구조 그래프 `graph@v1` (HYO-149) — 코퍼스 문서 + 노드 어휘(nodes@v2)에서 기계적으로 만든다.

스키마는 `docs/design/retrieval-arena.md` §6 변경 (2026-10-27). networkx MultiDiGraph이고, Neo4j 적재(HYO-76 뒤)는
이 노드·관계를 그대로 옮긴다. 질의 글·gold를 보지 않는다 — 입력은 코퍼스와 노드 어휘뿐이다.

노드 (속성 kind)
  technology · component      nodes@v2의 id
  release                     github-release 문서면 그 문서 id, manifest가 고정한 버전인데 문서가 없으면 `version:<기술 id>@<버전>`
  adr                         `adr:NNNN` (문서)
  document                    C-CODE(스택 절 · manifest) · 재검토 조건이 가리키는 C-DOC 문서
관계 (속성 rel)
  USES          component → technology   스택 표 한 행 (의존성 칸의 별칭 × component 칸의 이름)
  DECLARES      document → technology · component   그 스택 절·manifest가 적은 것
  HAS_RELEASE   technology → release     릴리스 repo(모노레포는 tag의 `<패키지>==`)가 그 기술의 별칭
  NEXT          release → release        같은 기술의 숫자 버전 순서 (숫자가 아닌 tag는 잇지 않는다)
  PINS          document → release       manifest 한 줄 `이름==버전`·`이름>=버전` (속성 op) · compose 이미지 tag
  DECIDES       adr → technology         frontmatter technology
  AFFECTS       adr → component          frontmatter affected
  REVISIT_SOURCE adr → document          revisit_when 조건의 source_hint와 URL이 같은 문서 (속성 cond — 조건을 노드로 두지 않고 접었다)
  RECORDED_AS   component → component    스택 표 행이 ADR을 인용하는데 그 ADR affected의 이름이 다르다 (같은 repo).
                                         「같은 것」이 아니라 같은 기술을 다른 이름 아래 기록했다는 뜻
멘션(본문에 별칭이 나오는 문서 → 기술)은 넣지 않는다 — 커뮤니티·벤더 글은 그래프 밖이고, S7 순위에서 BM25가 뒤를 채운다.
"""
import re

import networkx as nx
import yaml

from arena import jhs_metrics as jm

VERSION = "graph@v1"
_PIN = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9_.-]*)(?:\[[^\]]*\])?\s*(==|>=)\s*([^\s#;,]+)")
_IMAGE = re.compile(r"^\s*image:\s*[\"']?([^\s\"':]+):([^\s\"']+)")
_ADR_REF = re.compile(r"ADR\s+(\d{4})")


def norm_url(u):
    """'https://www.x.com/a/' · 'x.com/a' → 'x.com/a' (소문자)."""
    u = re.sub(r"^[a-z]+://", "", (u or "").strip().casefold())
    return (u[4:] if u.startswith("www.") else u).rstrip("/")


def frontmatter(text):
    if not text.startswith("---\n"):
        return {}
    fm, sep, _ = text[4:].partition("\n---\n")
    return (yaml.safe_load(fm) or {}) if sep else {}


def _add(G, nid, kind, **attrs):
    if nid in G and G.nodes[nid]["kind"] != kind:
        raise ValueError(f"graph: 노드 {nid}의 종류가 겹친다 — {G.nodes[nid]['kind']} · {kind}")
    G.add_node(nid, kind=kind, **attrs)


def _edge(G, a, b, rel, **attrs):
    """같은 (a, b, rel)은 한 번만. 새로 더했으면 True."""
    if any(d["rel"] == rel for d in G.get_edge_data(a, b, default={}).values()):
        return False
    G.add_edge(a, b, rel=rel, **attrs)
    return True


def _techs_in(cell, nodes):
    return sorted({tid for key, tid in nodes.alias.items() if jm.mentions(cell, key)})


def build(docs, nodes):
    """docs = corpus.load_corpus(...) 결과 · nodes = jhs_metrics.Nodes(nodes@v2). → (G, report)."""
    G = nx.MultiDiGraph(version=VERSION, nodes_version=nodes.version)
    report = {"unlinked_releases": [], "unparsed_pins": [], "unmatched_hints": [], "recorded_as": []}
    for tid, t in nodes.tech.items():
        _add(G, tid, "technology", label=t["label"])
    for cid, c in nodes.components.items():
        _add(G, cid, "component", repo=c["repo"], name=c["name"])
    by_id = {d["doc_id"]: d for d in docs}
    adr_affected = {}

    # 릴리스 — 문서 하나가 노드 하나
    releases = jm.release_index(docs)
    for did, r in releases.items():
        tid = nodes.tech_id(r["package"]) if r["package"] else nodes.tech_id(r["repo"])
        _add(G, did, "release", tech=tid, tag=r["tag"], key=r["key"], doc=True)
        if tid:
            _edge(G, tid, did, "HAS_RELEASE")
        else:
            report["unlinked_releases"].append(did)

    # ADR
    for d in docs:
        if d["corpus"] != "c-adr":
            continue
        fm = frontmatter(d["text"])
        _add(G, d["doc_id"], "adr", status=fm.get("status"), use=fm.get("use"))
        tid = nodes.tech_id(fm.get("technology"))
        if tid:
            _edge(G, d["doc_id"], tid, "DECIDES")
        adr_affected[d["doc_id"]] = []
        for a in fm.get("affected") or []:
            cid = jm.component_id(a["repo"], a["component"])
            if cid in nodes.components:
                _edge(G, d["doc_id"], cid, "AFFECTS")
                adr_affected[d["doc_id"]].append(cid)

    # C-CODE — 스택 절 표 행 · manifest 줄
    for d in docs:
        if d["corpus"] != "c-code":
            continue
        repo, did = d["meta"]["repo"], d["doc_id"]
        _add(G, did, "document", source_type=d["source_type"])
        if did.endswith("#stack"):
            for first, last in jm._table_rows(d["text"]):
                comps = [cid for cid, c in nodes.components.items() if c["repo"] == repo and jm.mentions(last, c["name"])]
                techs = _techs_in(first, nodes)
                for tid in techs:
                    _edge(G, did, tid, "DECLARES")
                    for cid in comps:
                        _edge(G, cid, tid, "USES", source=did)
                for cid in comps:
                    _edge(G, did, cid, "DECLARES")
                    for n in _ADR_REF.findall(first + " " + last):
                        for other in adr_affected.get(f"adr:{n}", []):
                            if (other != cid and other.startswith(f"component:{repo}/")
                                    and _edge(G, cid, other, "RECORDED_AS", source=did, adr=f"adr:{n}")):
                                report["recorded_as"].append((cid, other, f"adr:{n}"))
            continue
        top = d["meta"]["path"].split("/")[0]
        comp = jm.component_id(repo, top)
        if "/" in d["meta"]["path"] and comp in nodes.components:
            _edge(G, did, comp, "DECLARES")
        for line in d["text"].split("\n"):
            m = _PIN.match(line) or _IMAGE.match(line)
            if not m:
                continue
            name, op, ver = (m.group(1), m.group(2), m.group(3)) if m.re is _PIN else (m.group(1).rsplit("/", 1)[-1], "==", m.group(2))
            tid = nodes.tech_id(name)
            if not tid:
                report["unparsed_pins"].append(f"{did}: {line.strip()}")
                continue
            _edge(G, did, tid, "DECLARES")
            key = jm.version_key(ver)
            rid = next((r for r in G.successors(tid) if G.nodes[r]["kind"] == "release" and G.nodes[r]["key"] == key),
                       f"version:{tid}@{ver}")
            if rid not in G:
                _add(G, rid, "release", tech=tid, tag=ver, key=key, doc=False)
                _edge(G, tid, rid, "HAS_RELEASE")
            _edge(G, did, rid, "PINS", op=op)

    # 재검토 조건 → 근거 문서 (URL이 같은 C-DOC · `repo:<repo>/<경로>`는 C-CODE 문서)
    by_url = {norm_url(d["meta"].get("url")): d["doc_id"] for d in docs if d["corpus"] == "c-doc" and d["meta"].get("url")}
    for d in docs:
        if d["corpus"] != "c-adr":
            continue
        for c in frontmatter(d["text"]).get("revisit_when") or []:
            hint = c.get("source_hint")
            if not hint:
                continue
            if hint.startswith("repo:"):
                base = "stack:" + hint[len("repo:"):]
                target = next((x for x in (base, base + "#stack") if x in by_id), None)
            else:
                target = by_url.get(norm_url(hint))
            if target is None:
                report["unmatched_hints"].append(f"{d['doc_id']} {c.get('id')}: {hint}")
                continue
            if target not in G:
                _add(G, target, "document", source_type=by_id[target]["source_type"])
            _edge(G, d["doc_id"], target, "REVISIT_SOURCE", cond=c.get("id"))

    # 같은 기술의 버전 순서
    for tid in nodes.tech:
        rel = sorted(((jm.parse_version(G.nodes[r]["tag"].partition("==")[2] or G.nodes[r]["tag"]), r)
                      for r in G.successors(tid) if G.nodes[r]["kind"] == "release"), key=lambda x: (x[0] is None, x[0] or ()))
        numeric = [r for v, r in rel if v is not None]
        for a, b in zip(numeric, numeric[1:]):
            _edge(G, a, b, "NEXT")
    return G, report


def stats(G):
    kinds, rels = {}, {}
    for _, k in G.nodes(data="kind"):
        kinds[k] = kinds.get(k, 0) + 1
    for _, _, r in G.edges(data="rel"):
        rels[r] = rels.get(r, 0) + 1
    und = G.to_undirected(as_view=True)
    hubs = sorted(((und.degree(n), n) for n in G), reverse=True)[:5]
    return {"version": G.graph["version"], "nodes": G.number_of_nodes(), "edges": G.number_of_edges(),
            "kinds": dict(sorted(kinds.items())), "rels": dict(sorted(rels.items())),
            "hubs": [{"node": n, "degree": d} for d, n in hubs]}


def hops_from(G, sources, cutoff):
    """방향을 무시한 최단 홉 수 {노드: 홉} — 여러 시작 노드 중 가장 가까운 것."""
    und = G.to_undirected(as_view=True)
    src = [s for s in sources if s in G]
    return nx.multi_source_dijkstra_path_length(und, src, cutoff=cutoff, weight=lambda *_: 1) if src else {}


def reachability(G, queries, qrels, nodes, depths=(1, 2, 3)):
    """스키마만의 상한 — gold 질의 노드에서 등급 2·1 문서까지 경로가 깊이 d 안에 있나. 시드·순위는 보지 않는다.
    dev 질의만 넘긴다(부르는 쪽 책임 — test는 봉인)."""
    rows = []
    for q in queries:
        ids, missing = nodes.query_node_ids(q)
        dist = hops_from(G, ids, max(depths))
        row = {"qid": q["qid"], "type": q["type"], "seeds": sorted(ids), "not_in_graph": missing}
        for g in (2, 1):
            docs = sorted(d for d, v in qrels.get(q["qid"], {}).items() if v == g)
            row[f"g{g}"] = {"n": len(docs), "in_graph": sum(d in G for d in docs),
                            **{f"@{d}": sum(dist.get(x, 99) <= d for x in docs) for d in depths}}
        row["neighborhood"] = {f"@{d}": sum(v <= d for v in dist.values()) for d in depths}
        rows.append(row)
    return rows
