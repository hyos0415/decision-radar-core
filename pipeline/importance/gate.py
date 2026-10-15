"""레인 A 관문 — 소식 하나 → (기술 → 등급) × (변화 종류, 모델) → 결정표 → keep/drop (doc-pipeline §2-3).

`pipeline/lanes.py`가 비워 둔 `gate: null` 자리를 채운다. profile_filter.run_filter(gate=Gate(...))가 레인 A 항목마다 부른다.
모델은 변화 종류만 부른다(소식 하나당 1회). 등급은 `TierStore`가 이미 갖고 있다 (스택이 바뀔 때만 따로 분류: `python -m pipeline.importance classify-stack`).
판정(남김/버림·두 등급·보정·5/N)은 policy.py — 모델이 아니라 코드 (규율 1).

status: ok(판정함) · unclassified(소식이 걸린 기술을 모르거나 등급이 분류 불가 — v1.1 U4: 후보에서 빼고 기록) · error(변화 분류 호출 실패 — 안전 실패, 버림 + 기록)
        · other-stack(내 스택에 없고 팀원 스택 — 수집 전용 대상 — 에만 있는 기술. 내 관문 밖이라 버리되 「모르는 기술」과 따로 센다, HYO-122 결정 1)
unclassified 중 technologies가 비었으면 「모르는 기술」, 있으면 「등급 분류 불가」 — 요약 건수는 policy.gate_counts.
"""
import re

from . import policy
from .backends import BoundaryError, NewsQuery
from .signals import norm


def _tokens(name):
    return {t for t in re.split(r"[-_.:/\s]+", norm(name)) if t}


def resolve_technologies(item, names, vendor_terms=None):
    """항목 → 스택 기술 이름들. 의존성 릴리스(meta.dependency)는 그 의존성 하나로만 본다 — 모르면 [] (HYO-122 PR 4:
    `openai/openai-python` 릴리스에 제목의 python이, `langchain-ai/langgraph` 릴리스에 langchain이 붙어 다른 기술의 등급으로 판정되던 것.
    팀원 스택 쪽 Gate._other_stack과 같은 규칙). 그 밖의 소식은 ① 벤더 이름(vendors.yaml의 match 단어)이 든 기술 ② 제목에 이름이 나오는 기술.
    못 찾으면 []. 한 소식이 여러 기술에 걸치면 전부 평가하고 하나라도 남기면 남긴다."""
    meta = item.get("meta") or {}
    by_norm = {norm(n): n for n in names}
    out = []

    def add(n):
        if n and n not in out:
            out.append(n)

    dep = meta.get("dependency")
    if dep:
        return [by_norm[norm(dep)]] if norm(dep) in by_norm else []
    vendor = meta.get("vendor")
    if vendor:
        terms = {t.lower() for t in (vendor_terms or {}).get(vendor, [vendor])}
        for n in names:
            if _tokens(n) & terms:
                add(n)
    title = item.get("title") or ""
    for n in names:
        if len(n) >= 3 and re.search(r"(?<![0-9A-Za-z])" + re.escape(n) + r"(?![0-9A-Za-z])", title, re.I):
            add(n)
    return out


class Gate:
    def __init__(self, backend, store, vendor_terms=None, limit=policy.WEEKLY_LIMIT, other_stack=()):
        self.backend, self.store, self.vendor_terms, self.limit = backend, store, vendor_terms or {}, limit
        self.other_stack = list(other_stack or [])

    def _other_stack(self, item, mine):
        """내 스택에서 못 찾은 소식 → 팀원 스택 기술 이름들 | []. 의존성 릴리스는 그 의존성 이름으로만 본다 —
        내 스택의 모르는 기술(예: 아직 분류 안 한 electron)이 제목에 팀원 기술 이름이 섞였다고 팀원 스택으로 넘어가지 않게."""
        mine_n = {norm(n) for n in mine}
        other = [n for n in self.other_stack if norm(n) not in mine_n]
        dep = (item.get("meta") or {}).get("dependency")
        if dep:
            return [n for n in other if norm(n) == norm(dep)][:1]
        return resolve_technologies(item, other, self.vendor_terms)

    def __call__(self, item, reasons=None):
        names = self.store.names() + [n for n in self.store.data["overrides"] if norm(n) not in {norm(x) for x in self.store.names()}]
        techs = resolve_technologies(item, names, self.vendor_terms)
        base = {"keep": False, "status": "unclassified", "technologies": techs, "tiers": {}, "changes": [], "rows": [], "kept_by": [],
                "defaulted_change": False, "skipped": []}
        if not techs:
            other = self._other_stack(item, names)
            if other:
                return {**base, "status": "other-stack", "technologies": other,
                        "reason": "팀원 스택(수집 전용 대상)에만 있는 기술 → 내 관문 밖 (" + ", ".join(other) + ")"}
            return {**base, "reason": "소식이 걸린 스택 기술을 모른다 → 후보에서 뺌 (technology-unresolved)"}
        effs = {}
        for t in techs:
            e = self.store.effective(t)
            if e is None or e.get("unresolved") or not e.get("tiers"):
                base["skipped"].append({"technology": t, "why": "등급 분류 불가 (v1.1 U4)"})
            else:
                effs[t] = e
        if not effs:
            return {**base, "reason": "등급이 분류 불가 → 후보에서 뺌 (" + ", ".join(techs) + ")"}
        q = NewsQuery(id=item["id"], technology=", ".join(effs), title=item.get("title", ""), text=item.get("text", ""), source=item.get("source", ""))
        try:
            ans = self.backend.classify_changes(q)
        except BoundaryError:
            raise
        except Exception as e:  # noqa: BLE001 — 안전 실패: 버리고 기록한다 (조용히 남기지도, 조용히 사라지게 두지도 않는다)
            return {**base, "status": "error", "tiers": {t: e2["tiers"] for t, e2 in effs.items()},
                    "reason": f"변화 분류 실패: {type(e).__name__}: {str(e)[:120]}"}
        rows, kept_by, defaulted = [], [], False
        for t, e in effs.items():
            ctx = policy.Context(path=e.get("path", "prod"), pin=e.get("pin"), sensitive=e.get("sensitive"), local=e.get("local"))
            d = policy.decide(e["tiers"], ans.labels, ctx)
            defaulted = d["defaulted_change"]
            rows += [{**r, "technology": t} for r in d["rows"]]
            kept_by += [{**r, "technology": t} for r in d["kept_by"]]
        keep = bool(kept_by)
        why = ("; ".join(f"{r['technology']} {r['tier']} × {policy.CHANGE_LABEL[r['change']]}" + (f" ({', '.join(r['adjusted_by'])})" if r["adjusted_by"] else "")
                         for r in kept_by) if keep else
               "남기는 행 없음: " + ", ".join(sorted({f"{r['tier']}×{policy.CHANGE_LABEL[r['change']]}={r['base']}" for r in rows})))
        return {"keep": keep, "status": "ok", "technologies": techs, "tiers": {t: e["tiers"] for t, e in effs.items()}, "changes": ans.labels,
                "rows": rows, "kept_by": kept_by, "defaulted_change": defaulted, "skipped": base["skipped"], "reason": why}


def cap_lane_a(entries, limit=policy.WEEKLY_LIMIT):
    """레인 A 항목들(각각 `gate` 칸을 가짐)에 5/N 상한을 건다. 항목에 `shown`을 쓰고 요약을 돌려준다. 잘린 것은 summary["truncated"] — 로그용."""
    kept = [{"id": e["id"], "kept_by": e["gate"]["kept_by"]} for e in entries if (e.get("gate") or {}).get("keep")]
    shown, cut, label = policy.apply_cap(kept, limit)
    shown_ids, cut_ids = {s["id"] for s in shown}, {c["id"] for c in cut}
    for e in entries:
        e["shown"] = True if e["id"] in shown_ids else (False if e["id"] in cut_ids else None)
    def brief(x):
        best = min(x["kept_by"], key=lambda r: (policy.tier_rank(r["tier"]), policy.severity(r["change"])))
        return {"id": x["id"], "tier": best["tier"], "change": best["change"]}
    return {"label": label, "limit": limit, "total_kept": len(kept), "shown": [brief(s) for s in shown], "truncated": [brief(c) for c in cut],
            "status_counts": policy.gate_counts(entries)}
