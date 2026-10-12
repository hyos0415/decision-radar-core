"""S1 정확 일치 (HYO-35, 이슈 #63) — K 유형 기준선. 스킬이 `radar.py match`로 하는 방식 그대로다.

**입력이 다르다**: 다른 전략은 질의 글만 본다. S1은 질의의 구조화된 `nodes`(`{technology, repo?, component?}`)를 쓴다
(settings에 `uses_query_nodes: true` — 하네스가 `nodes`를 넘겨 준다). 질의 글은 보지 않는다.

C-ADR 문서의 frontmatter(`technology` · `affected[{repo, component}]`)와 노드를 `radar.py`의 `norm`(공백 정리 · 소문자)으로 견준다.
ADR마다 등급(아래가 낮은 등급 — 1이 최상):
  1  같은 technology **이고** (repo, component)가 affected에 있다   (radar.py match의 `overlap`)
  2  technology만 같다
  3  (repo, component)만 같다
같은 등급 안에서는 ADR id 순서. 노드가 여럿이면 합집합 — ADR의 등급은 노드 중 **가장 좋은 등급**. 어느 등급에도 안 걸리면 결과에 없다(빈 결과도 정상).
등급 점수는 3.0 / 2.0 / 1.0 (하네스 rollup이 점수 내림차순 · 동점은 들어온 순서로 정렬한다). 반환 청크 = ADR의 첫 청크.

radar.py의 `exact`(use까지 일치)는 쓰지 않는다 — 질의에 use가 없다. 빈 technology · 빈 repo/component는 일치로 세지 않는다
(`norm("")`끼리 같다고 매칭되지 않게 — radar.py는 인자가 필수라 그런 입력이 없다).
"""
import importlib.util
import re
from pathlib import Path

from ..corpus import split_frontmatter

RADAR_PY = Path(__file__).resolve().parents[2] / ".claude" / "skills" / "jhs-decisions" / "scripts" / "radar.py"
TIER_SCORE = {1: 3.0, 2: 2.0, 3: 1.0}


def norm(s):
    """radar.py의 norm과 같은 규칙 — 테스트가 radar.py의 norm과 출력이 같은지 견준다."""
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def load_radar_norm():
    """스킬 radar.py의 norm 함수 (테스트용 — 두 구현이 같은지 확인한다)."""
    spec = importlib.util.spec_from_file_location("_radar_for_norm", RADAR_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.norm


def adr_match_fields(doc):
    """C-ADR 문서 → (technology 정규화, {(repo, component)} 정규화). frontmatter가 없거나 YAML이 깨지면 실패한다."""
    import yaml
    fm_lines, _ = split_frontmatter(doc["text"])
    if fm_lines is None:
        raise ValueError(f"{doc['doc_id']}: frontmatter가 없다")
    fm = yaml.safe_load("\n".join(fm_lines)) or {}
    comps = set()
    for e in fm.get("affected") or []:
        repo, comp = norm(e.get("repo")), norm(e.get("component"))
        if repo and comp:
            comps.add((repo, comp))
    return norm(fm.get("technology")), comps


def validate_nodes(nodes):
    if not isinstance(nodes, list) or not nodes:
        raise ValueError("S1은 질의의 nodes가 필요하다 (비어 있지 않은 목록) — K 유형 질의에만 쓴다")
    for n in nodes:
        if not isinstance(n, dict) or not (norm(n.get("technology")) or (norm(n.get("repo")) and norm(n.get("component")))):
            raise ValueError(f"노드에 technology도 (repo, component)도 없다: {n!r}")
    return nodes


def tier_of(tech, comps, nodes):
    """ADR (tech, comps)의 노드 합집합 최고 등급 (1~3) 또는 None."""
    best = None
    for n in nodes:
        nt = norm(n.get("technology"))
        nc = (norm(n.get("repo")), norm(n.get("component")))
        same_tech = bool(nt) and nt == tech
        same_comp = all(nc) and nc in comps
        t = 1 if same_tech and same_comp else 2 if same_tech else 3 if same_comp else None
        if t is not None and (best is None or t < best):
            best = t
    return best


class S1Match:
    id = "S1"
    version = "v1"
    uses_query_nodes = True          # 하네스가 search(..., nodes=)로 질의 nodes를 넘긴다

    def __init__(self, docs):
        """docs = 코퍼스 문서 목록 (C-ADR만 쓴다)."""
        self.adrs = sorted((d for d in docs if d["corpus"] == "c-adr"), key=lambda d: d["doc_id"])
        if not self.adrs:
            raise ValueError("S1은 C-ADR 문서가 있어야 한다")
        self.fields = {d["doc_id"]: adr_match_fields(d) for d in self.adrs}
        self.first_chunk = {}
        self.took_ms = []
        self.n_indexed = 0

    def settings(self):
        return {
            "uses_query_nodes": True,
            "input": "질의의 구조화된 nodes (질의 글은 쓰지 않는다)",
            "baseline_of": "radar.py match (K 유형 기준선)",
            "tiers": {"1": "같은 technology + (repo, component)가 affected에 있음 (overlap)", "2": "technology만 같음",
                      "3": "(repo, component)만 같음"},
            "tier_scores": {str(k): v for k, v in TIER_SCORE.items()},
            "within_tier": "ADR id 순서", "multi_node": "합집합 — 최고 등급",
            "normalize": "radar.py norm (공백 정리 · 소문자)",
            "no_match": "결과 없음(빈 목록)",
            "docs": "C-ADR만 · 반환 청크 = ADR의 첫 청크",
            "adrs": len(self.adrs),
        }

    def index(self, chunks):
        self.first_chunk = {}
        for c in chunks:
            self.first_chunk.setdefault(c["doc_id"], c["chunk_id"])      # 청크는 문서 안 순서대로 들어온다
        missing = [d["doc_id"] for d in self.adrs if d["doc_id"] not in self.first_chunk]
        if missing:
            raise ValueError(f"ADR에 청크가 없다: {missing[:5]}")
        self.n_indexed = len(self.adrs)

    def search(self, query, k, nodes=None):
        """query(글)는 무시한다. nodes가 없으면 실패한다."""
        nodes = validate_nodes(nodes)
        hits = []
        for d in self.adrs:                                              # id 순서
            tech, comps = self.fields[d["doc_id"]]
            t = tier_of(tech, comps, nodes)
            if t is not None:
                hits.append((t, d["doc_id"]))
        hits.sort(key=lambda h: h[0])                                    # 안정 정렬 — 같은 등급은 id 순서 유지
        return [(self.first_chunk[doc], TIER_SCORE[t]) for t, doc in hits][:k]

    def index_size_bytes(self):
        return 0

    def server_version(self):
        return None

    def drop(self):
        pass
