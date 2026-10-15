"""JHS 보조 지표 (HYO-34) — `docs/design/retrieval-arena.md` §5 「변경 (2026-10-13)」의 정의를 그대로 옮긴다.

전부 보조 지표다 (30칸 적중 판정은 nDCG@10만). 표본이 작아 비율을 내지 않고 질의별 사례 표로만 내는 것이 있다.
  - Correct Version@k (C·E)          정답 릴리스가 상위 k에 있나 · 「Recall과 같음」 표시
  - 버전 혼동 사례 표 (C만)          같은 패키지의 다른 버전(등급 0·판정 없음) — 이전 버전이 정답보다 위면 위험
  - component 근거 사례 표 (E만)     대표 청크가 같은 repo 스택 절의 표 행(기술 별칭 + component) · manifest(디렉터리 = component)
  - 커뮤니티 비율@k                  상위 k 중 reddit·hn
  - Seed Recall@k                    시드를 내는 전략만 — nodes@v1 id 일치
정의를 바꾸려면 등록 문서에 `변경`을 먼저 남긴다 (CLAUDE.md 규율 7).
변경 (2026-10-15, #98): 버전 비교 정규화 · 정답 릴리스를 못 찾는 질의는 「판정 불가」 행 (실행을 멈추지 않는다).
"""
import json
import re
from collections import defaultdict
from pathlib import Path

K = 10                                   # 사례 표의 k — 헤드라인 nDCG@10과 같다
VERSION_KS = (5, 10, 20)
COMMUNITY_KS = (5, 10, 20)
SEED_KS = (1, 3, 5)
COMMUNITY_SOURCE_TYPE = "community"
_VERSION = re.compile(r"v?(\d+(?:\.\d+)*)")


# --- 릴리스 ---------------------------------------------------------------------------------------------------------

def parse_version(s):
    """'v0.23.0' · '1.2.13' → (0, 23, 0). 숫자 버전이 아니면 None (예: llama.cpp 'b11240')."""
    m = _VERSION.fullmatch((s or "").strip())
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def version_key(s):
    """같은 버전인지 비교하는 키 (§5 변경 2026-10-15). 숫자 버전은 끝의 0을 뗀 튜플('1.3' = '1.3.0' = 'v1.3'),
    아니면 앞 v를 뗀 소문자 문자열('B11240' = 'b11240'). 비어 있으면 None."""
    v = parse_version(s)
    if v is not None:
        v = list(v)
        while len(v) > 1 and v[-1] == 0:
            v.pop()
        return tuple(v)
    s = (s or "").strip()
    if s[:1] in ("v", "V") and len(s) > 1:
        s = s[1:]
    return s.casefold() or None


def _numeric(key):
    """순서를 정할 수 있는 키 — 숫자 버전 튜플만."""
    return key if isinstance(key, tuple) else None


class Undetermined(ValueError):
    """정답 릴리스를 기계적으로 뽑지 못한 질의 — 실행을 멈추지 않고 「판정 불가」 행으로 적는다 (§5 변경 2026-10-15)."""


def split_tag(tag):
    """'langchain-anthropic==1.7.5' → ('langchain-anthropic', '1.7.5') · 'v0.23.0' → (None, 'v0.23.0')."""
    pkg, sep, ver = (tag or "").partition("==")
    return (pkg, ver) if sep else (None, tag or "")


def release_index(docs):
    """{doc_id: {repo, package, version, key, tag}} — `github-release:` 문서만. 모노레포는 tag의 `<패키지>==`로 패키지를 가른다."""
    out = {}
    for d in docs:
        if not d["doc_id"].startswith("github-release:"):
            continue
        meta = d.get("meta") or {}
        repo, tag = meta.get("repo"), meta.get("tag")
        if not repo or not tag:
            raise ValueError(f"{d['doc_id']}: 릴리스 meta에 repo·tag가 없다")
        pkg, ver = split_tag(tag)
        out[d["doc_id"]] = {"repo": repo, "package": pkg, "version": parse_version(ver), "key": version_key(ver), "tag": tag}
    return out


def correct_release(q, qrels_q, releases):
    """정답 릴리스 = 등급 2인 릴리스 문서 중 버전(version_key)이 expected_version과 같은 것.
    하나가 아니면 Undetermined — 부르는 쪽이 「판정 불가」로 적는다."""
    want = version_key(q.get("expected_version"))
    if want is None:
        raise Undetermined(f"{q['qid']}: expected_version이 없다")
    hits = [d for d, g in qrels_q.items() if g == 2 and d in releases and releases[d]["key"] == want]
    if len(hits) != 1:
        raise Undetermined(f"{q['qid']}: expected_version {q.get('expected_version')!r}과 같은 등급 2 릴리스가 "
                           f"하나가 아니다 — {len(hits)}개")
    return hits[0]


def rank_of(run_q, doc):
    """1부터. run에 없으면 None."""
    for i, d in enumerate(run_q, 1):
        if d == doc:
            return i
    return None


def recall_l2(run_q, qrels_q, k):
    g2 = {d for d, g in qrels_q.items() if g == 2}
    if not g2:
        return None
    return len(g2 & set(list(run_q)[:k])) / len(g2)


def correct_version(run, qrels, queries, releases, ks=VERSION_KS, types=("C", "E")):
    """-> {"@k": {유형: {"n", "mean", "per_query", "same_as_recall", "undetermined"}}}.
    same_as_recall = 판정된 질의 전부에서 Recall@k(-l2)와 같다. 판정 불가 질의는 n·mean·same_as_recall에서 빼고 이유를 적는다."""
    out = {}
    for k in ks:
        by_type = {}
        for t in types:
            per_q, undetermined, same = {}, {}, True
            for q in (q for q in queries if q["type"] == t and q["qid"] in qrels):
                qid = q["qid"]
                try:
                    c = correct_release(q, qrels[qid], releases)
                except Undetermined as e:
                    undetermined[qid] = str(e)
                    continue
                hit = 1.0 if (rank_of(run.get(qid, {}), c) or k + 1) <= k else 0.0
                per_q[qid] = hit
                same = same and hit == recall_l2(run.get(qid, {}), qrels[qid], k)
            if per_q or undetermined:
                by_type[t] = {"n": len(per_q),
                              "mean": round(sum(per_q.values()) / len(per_q), 4) if per_q else None,
                              "per_query": per_q, "same_as_recall": same if per_q else None,
                              "undetermined": undetermined}
        out[f"@{k}"] = by_type
    return out


def version_confusion(run, qrels, queries, releases, k=K):
    """C 질의마다 사례 한 줄. 형제 = 같은 repo·같은 패키지의 다른 버전 릴리스 중 이 질의 등급 0·판정 없음.
    위험 = 이전 형제가 상위 k 안에서 정답보다 위 (정답이 상위 k 밖이면 상위 k의 이전 형제는 모두 위험).
    이후 형제는 적기만 한다. 숫자 버전이 아니거나 정답과 같은 버전인 형제는 order_unknown — 사람이 본다.
    정답 릴리스를 못 찾은 질의는 「판정 불가」 행 (risk None — 위험 수에 넣지 않는다)."""
    rows = []
    for q in (q for q in queries if q["type"] == "C" and q["qid"] in qrels):
        qid, run_q = q["qid"], run.get(q["qid"], {})
        try:
            c = correct_release(q, qrels[qid], releases)
        except Undetermined as e:
            rows.append({"qid": qid, "correct": None, "correct_rank": None, "earlier": [], "later": [], "order_unknown": [],
                         "risk": None, "risk_docs": [], "note": f"판정 불가 — {e}"})
            continue
        cr = releases[c]
        sib = [d for d, r in releases.items()
               if d != c and r["repo"] == cr["repo"] and r["package"] == cr["package"] and qrels[qid].get(d, 0) == 0]
        groups = {"earlier": [], "later": [], "order_unknown": []}
        ck = _numeric(cr["key"])
        for d in sib:
            v = _numeric(releases[d]["key"])
            g = "order_unknown" if v is None or ck is None or v == ck else "earlier" if v < ck else "later"
            groups[g].append({"doc": d, "tag": releases[d]["tag"], "rank": rank_of(run_q, d)})
        for g in groups.values():
            g.sort(key=lambda x: (x["rank"] is None, x["rank"] or 0, x["doc"]))
        c_rank = rank_of(run_q, c)
        c_eff = c_rank if c_rank is not None and c_rank <= k else k + 1
        risky = [s["doc"] for s in groups["earlier"] if s["rank"] is not None and s["rank"] <= k and s["rank"] < c_eff]
        rows.append({"qid": qid, "correct": c, "correct_rank": c_rank, **groups,
                     "risk": bool(risky), "risk_docs": risky, "note": "형제 없음" if not sib else ""})
    return {"k": k, "rows": rows, "risk_qids": [r["qid"] for r in rows if r["risk"]],
            "undetermined_qids": [r["qid"] for r in rows if r["risk"] is None]}


# --- 노드 어휘 nodes@v1 ---------------------------------------------------------------------------------------------

class Nodes:
    """정식 id + 별칭. 별칭은 대소문자를 가리지 않고 정확히 같아야 연결된다 (흔들림 흡수는 시드 방식의 몫)."""

    def __init__(self, data):
        self.version = data["version"]
        self.tech = {}
        self.alias = {}
        self.components = {}
        for t in data["technologies"]:
            if t["id"] in self.tech:
                raise ValueError(f"nodes: id가 겹친다 {t['id']}")
            if not t.get("aliases"):
                raise ValueError(f"nodes: {t['id']}에 별칭이 없다")
            self.tech[t["id"]] = t
            for a in t["aliases"]:
                if not a.get("text") or not a.get("source"):
                    raise ValueError(f"nodes: {t['id']} 별칭에 text·source가 다 있어야 한다 — {a}")
                key = a["text"].casefold()
                if self.alias.get(key, t["id"]) != t["id"]:
                    raise ValueError(f"nodes: 별칭 {a['text']!r}이 두 노드에 있다 — {self.alias[key]} · {t['id']}")
                self.alias[key] = t["id"]
        for c in data["components"]:
            cid = component_id(c["repo"], c["name"])
            if c.get("id") != cid:
                raise ValueError(f"nodes: component id는 {cid}이어야 한다 — {c.get('id')}")
            if not c.get("source"):
                raise ValueError(f"nodes: {cid}에 source가 없다")
            self.components[cid] = c

    def tech_id(self, name):
        return self.alias.get((name or "").casefold())

    def aliases_of(self, tid):
        return [a["text"] for a in self.tech[tid]["aliases"]]

    def query_node_ids(self, q):
        """-> (연결된 id 집합, 연결되지 않은 이름 목록). technology는 별칭으로, (repo, component)는 component id로."""
        ids, missing = set(), []
        for n in q.get("nodes") or []:
            if n.get("technology"):
                tid = self.tech_id(n["technology"])
                (ids.add(tid) if tid else missing.append(n["technology"]))
            if n.get("repo") and n.get("component"):
                cid = component_id(n["repo"], n["component"])
                (ids.add(cid) if cid in self.components else missing.append(cid))
        return ids, missing


def component_id(repo, name):
    return f"component:{repo}/{name}"


def load_nodes(path):
    return Nodes(json.loads(Path(path).read_text(encoding="utf-8")))


def check_nodes(nodes, doc_text):
    """별칭이 출처 문서에 글자 그대로(대소문자 무시) 나오는지 · component가 출처 문서에 나오는지. -> 문제 목록."""
    bad = []
    for t in nodes.tech.values():
        for a in t["aliases"]:
            text = doc_text.get(a["source"])
            if text is None:
                bad.append(f"{t['id']}: 별칭 {a['text']!r}의 출처 {a['source']}가 코퍼스에 없다")
            elif a["text"].casefold() not in text.casefold():
                bad.append(f"{t['id']}: 별칭 {a['text']!r}이 출처 {a['source']}에 없다")
    for cid, c in nodes.components.items():
        text = doc_text.get(c["source"])
        if text is None or not mentions(text, c["name"]):
            bad.append(f"{cid}: 출처 {c['source']}에 component 이름이 없다")
    return bad


# --- component 근거 (E만) -------------------------------------------------------------------------------------------

def mentions(text, word):
    """단어 경계로 찾는다 — 'langchain'이 'langchain-anthropic' 안에서 잡히지 않게 하이픈도 경계 밖으로 본다.
    경계는 ASCII 기준이다: 'product에'처럼 한국어 조사가 붙어도 잡는다."""
    return re.search(rf"(?<![A-Za-z0-9_-]){re.escape(word)}(?![A-Za-z0-9_-])", text, re.IGNORECASE) is not None


def _table_rows(text):
    for line in text.split("\n"):
        s = line.strip()
        if s.startswith("|") and not re.fullmatch(r"\|[\s|:-]+\|?", s):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if len(cells) >= 2:
                yield cells[0], cells[-1]


def component_evidence_kind(doc_id, chunk_text, repo, component, aliases):
    """이 청크가 (repo, component)의 근거면 'stack-row' · 'manifest', 아니면 None."""
    prefix = f"stack:{repo}/"
    if not doc_id.startswith(prefix):
        return None
    path = doc_id[len(prefix):]
    if path.endswith("#stack"):
        for dep, comp in _table_rows(chunk_text):
            if mentions(comp, component) and any(mentions(dep, a) for a in aliases):
                return "stack-row"
        return None
    if component in Path(path).parent.parts and any(mentions(chunk_text, a) for a in aliases):
        return "manifest"
    return None


def component_evidence(run, rep_chunks, chunk_text, queries, qrels, nodes, k=K):
    """E 질의마다 사례 한 줄 — 상위 k 문서의 대표 청크 중 component 근거. 별칭이 없는 기술은 note로 남긴다."""
    rows = []
    for q in (q for q in queries if q["type"] == "E" and q["qid"] in qrels):
        qid, run_q = q["qid"], run.get(q["qid"], {})
        for n in q.get("nodes") or []:
            if not (n.get("repo") and n.get("component")):
                continue
            tid = nodes.tech_id(n.get("technology"))
            row = {"qid": qid, "technology": n.get("technology"), "repo": n["repo"], "component": n["component"],
                   "node": tid, "evidence": [], "first_rank": None, "overlaps_grade2": False, "note": ""}
            if tid is None:
                row["note"] = "nodes@v1에 없는 기술"
                rows.append(row)
                continue
            aliases = nodes.aliases_of(tid)
            for rank, d in enumerate(list(run_q)[:k], 1):
                cid = rep_chunks.get(qid, {}).get(d)
                kind = component_evidence_kind(d, chunk_text.get(cid, ""), n["repo"], n["component"], aliases) if cid else None
                if kind:
                    row["evidence"].append({"doc": d, "rank": rank, "chunk": cid, "via": kind, "grade": qrels[qid].get(d, 0)})
            if row["evidence"]:
                row["first_rank"] = row["evidence"][0]["rank"]
                row["overlaps_grade2"] = any(e["grade"] == 2 for e in row["evidence"])
            rows.append(row)
    return {"k": k, "rows": rows}


# --- 커뮤니티 비율 ---------------------------------------------------------------------------------------------------

def community_share(run, queries, doc_source_types, ks=COMMUNITY_KS):
    """상위 k 중 커뮤니티(reddit·hn) 비율 — 질의별 비율의 평균. 결과가 빈 질의는 뺀다."""
    qtype = {q["qid"]: q["type"] for q in queries}

    def mean_share(qids, k):
        vals = []
        for qid in qids:
            top = list(run.get(qid, {}))[:k]
            if top:
                vals.append(sum(doc_source_types[d] == COMMUNITY_SOURCE_TYPE for d in top) / len(top))
        return round(sum(vals) / len(vals), 4) if vals else None
    out = {}
    for k in ks:
        by_type = defaultdict(list)
        for qid in run:
            by_type[qtype[qid]].append(qid)
        out[f"@{k}"] = {"overall": mean_share(list(run), k),
                        "by_type": {t: mean_share(ids, k) for t, ids in sorted(by_type.items())}}
    return out


# --- Seed Recall (시드를 내는 전략만) --------------------------------------------------------------------------------

def seed_recall(seeds, queries, nodes, ks=SEED_KS):
    """seeds {qid: [node id, … 순위대로]}. B는 뺀다(nodes가 범주). 연결되지 않는 질의 노드는 놓친 것으로 세고 따로 적는다."""
    per_q, unmapped = {}, {}
    for q in queries:
        if q["type"] == "B" or q["qid"] not in seeds:
            continue
        ids, missing = nodes.query_node_ids(q)
        total = len(ids) + len(missing)
        if not total:
            continue
        per_q[q["qid"]] = {f"@{k}": round(len(ids & set(seeds[q["qid"]][:k])) / total, 4) for k in ks}
        if missing:
            unmapped[q["qid"]] = missing
    mean = {f"@{k}": (round(sum(v[f"@{k}"] for v in per_q.values()) / len(per_q), 4) if per_q else None) for k in ks}
    return {"nodes_version": nodes.version, "n": len(per_q), "mean": mean, "per_query": per_q,
            "not_in_graph": unmapped}


# --- 한 번에 -------------------------------------------------------------------------------------------------------

def compute(run, rep_chunks, qrels, queries, docs, chunk_text, nodes):
    """cli run이 부른다. docs = 코퍼스 문서 목록 · chunk_text = {chunk_id: text} (C-CODE 청크만 있어도 된다)."""
    releases = release_index(docs)
    st = {d["doc_id"]: d["source_type"] for d in docs}
    return {
        "definition": "docs/design/retrieval-arena.md §5 변경 (2026-10-13) · 변경 (2026-10-15)",
        "nodes_version": nodes.version,
        "correct_version_at_k": correct_version(run, qrels, queries, releases),
        "version_confusion": version_confusion(run, qrels, queries, releases),
        "component_evidence": component_evidence(run, rep_chunks, chunk_text, queries, qrels, nodes),
        "community_share_at_k": community_share(run, queries, st),
        "impact_fp_rate": "미측정 — F 유형이 gold@v1에 없다",
    }
