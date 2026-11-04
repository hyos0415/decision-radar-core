"""레인 A 관문 — 소식 하나 → (기술 → 등급) × (변화 종류, 모델) → 결정표 → keep/drop (doc-pipeline §2-3).

`pipeline/lanes.py`가 비워 둔 `gate: null` 자리를 채운다. profile_filter.run_filter(gate=Gate(...))가 레인 A 항목마다 부른다.
모델은 변화 종류만 부른다(소식 하나당 1회). 등급은 `TierStore`가 이미 갖고 있다 (스택이 바뀔 때만 따로 분류: `python -m pipeline.importance classify-stack`).
판정(남김/버림·두 등급·보정·5/N)은 policy.py — 모델이 아니라 코드 (규율 1). 후속 모델(successor_model)도 모델이 아니라 successor.py가 제목으로 붙인다 (HYO-129).
eol·모델 은퇴가 우리 버전·우리 모델인지는 scope.py가 기술마다 정한다 — 아니면 그 기술의 라벨에서 뺀다 (HYO-148). 기록은 결과의 `scope`.

status: ok(판정함) · unclassified(소식이 걸린 기술을 모르거나 등급이 분류 불가 — v1.1 U4: 후보에서 빼고 기록) · error(변화 분류 호출 실패 — 안전 실패, 버림 + 기록)
        · other-stack(내 스택에 없고 팀원 스택 — 수집 전용 대상 — 에만 있는 기술. 내 관문 밖이라 버리되 「모르는 기술」과 따로 센다, HYO-122 결정 1)
unclassified 중 technologies가 비었으면 「모르는 기술」, 있으면 「등급 분류 불가」 — 요약 건수는 policy.gate_counts.
"""
import re

from . import policy, scope, successor
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
    def __init__(self, backend, store, vendor_terms=None, limit=policy.WEEKLY_LIMIT, other_stack=(), versions=None):
        """versions: 우리 버전 {정규화 이름: [{version, ...}]} (signals.read_repo_versions — 없으면 eol은 확인 불가로 남긴다).
        사람이 적은 overrides `version`이 이긴다 (HYO-148)."""
        self.backend, self.store, self.vendor_terms, self.limit = backend, store, vendor_terms or {}, limit
        self.other_stack = list(other_stack or [])
        self.auto_versions = versions or {}

    @property
    def versions(self):
        """manifest 버전 + 사람 overrides `version` (매번 등급 파일에서 읽는다 — 실행 중 사람 수정도 반영)."""
        human = {k: ([o["version"]] if isinstance(o["version"], str) else o["version"])
                 for k, o in self.store.data["overrides"].items() if o.get("version")}
        return scope.Versions(self.auto_versions, human)

    def _other_stack(self, item, mine):
        """내 스택에서 못 찾은 소식 → 팀원 스택 기술 이름들 | []. 의존성 릴리스는 그 의존성 이름으로만 본다 —
        내 스택의 모르는 기술(예: 아직 분류 안 한 electron)이 제목에 팀원 기술 이름이 섞였다고 팀원 스택으로 넘어가지 않게."""
        mine_n = {norm(n) for n in mine}
        other = [n for n in self.other_stack if norm(n) not in mine_n]
        dep = (item.get("meta") or {}).get("dependency")
        if dep:
            return [n for n in other if norm(n) == norm(dep)][:1]
        return resolve_technologies(item, other, self.vendor_terms)

    def _successors(self, item, names):
        """제목에 우리 L2 모델의 같은 계열 더 높은 버전이 나오면 {기술: 표기} (§3 변경 2026-10-16, HYO-129 — 모델이 아니라 스크립트)."""
        l2 = [n for n in names if "L2" in ((self.store.effective(n) or {}).get("tiers") or [])]
        return successor.find(item.get("title") or "", l2)

    def __call__(self, item, reasons=None):
        names = self.store.names() + [n for n in self.store.data["overrides"] if norm(n) not in {norm(x) for x in self.store.names()}]
        techs = resolve_technologies(item, names, self.vendor_terms)
        succ = self._successors(item, names)
        techs += [t for t in succ if t not in techs]
        base = {"keep": False, "status": "unclassified", "technologies": techs, "tiers": {}, "changes": [], "rows": [], "kept_by": [],
                "defaulted_change": False, "skipped": [], "successor": [{"technology": t, "mentioned": m} for t, m in succ.items()]}
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
        changes = policy.normalize_changes(ans.labels, item.get("title") or "")   # 프리릴리스 major · 덧붙인 minor_patch 빼기 (§3 변경 2026-10-21, HYO-131)
        rows, kept_by, defaulted, scoped = [], [], False, []
        versions = self.versions
        for t, e in effs.items():
            ctx = policy.Context(path=e.get("path", "prod"), pin=e.get("pin"), sensitive=e.get("sensitive"), local=e.get("local"))
            labels, notes = scope.narrow(changes, t, e["tiers"], item, versions)   # 우리 버전·우리 모델이 아닌 eol·모델 은퇴를 뺀다 (HYO-148)
            line = scope.release_line(t, item, versions)
            if notes or line:
                scoped.append({"technology": t, "notes": notes, **({"release_line": line} if line else {})})
            if t in succ:      # 모델 라벨이 비었을 때의 기본(minor·patch) 행은 그대로 두고 후속 모델 행을 더한다 — 기존 판정을 바꾸지 않게
                labels = (labels or ["minor_patch"]) + ["successor_model"]
            d = policy.decide(e["tiers"], labels, ctx)
            defaulted = not changes
            rows += [{**r, "technology": t} for r in d["rows"]]
            kept_by += [{**r, "technology": t} for r in d["kept_by"]]
        keep = bool(kept_by)
        why = ("; ".join(f"{r['technology']} {r['tier']} × {policy.CHANGE_LABEL[r['change']]}" + (f" ({', '.join(r['adjusted_by'])})" if r["adjusted_by"] else "")
                         for r in kept_by) if keep else
               "남기는 행 없음: " + ", ".join(sorted({f"{r['tier']}×{policy.CHANGE_LABEL[r['change']]}={r['base']}" for r in rows})))
        return {"keep": keep, "status": "ok", "technologies": techs, "tiers": {t: e["tiers"] for t, e in effs.items()}, "changes": changes, "model_changes": list(ans.labels),
                "rows": rows, "kept_by": kept_by, "defaulted_change": defaulted, "skipped": base["skipped"], "successor": base["successor"],
                "scope": scoped, "reason": why}


def cap_lane_a(entries, limit=policy.WEEKLY_LIMIT, mode=None):
    """레인 A 항목들(각각 `gate` 칸을 가짐)에서 detect로 갈 것을 고른다. 항목에 `shown`(= detect로 간다)을 쓰고 요약을 돌려준다.
    잘린 것은 summary["truncated"] — 로그용.
    - topk (기본, 2026-11-04 HYO-168): 「항상」 + 기술마다 top-k 목록(`listed` · `rank` · `always` · `query`), detect는 「항상」 + 기술마다 1위
    - weekly5 (그 전): 5/N 상한 · 기술당 1칸 먼저"""
    mode = mode or policy.CAP_MODE
    kept = [{"id": e["id"], "kept_by": e["gate"]["kept_by"]} for e in entries if (e.get("gate") or {}).get("keep")]

    def brief(x):
        best = min(x["kept_by"], key=lambda r: (policy.tier_rank(r["tier"]), policy.severity(r["change"])))
        t = policy.cap_technology(x)
        return {"id": x["id"], "tier": best["tier"], "change": best["change"], "technology": t if isinstance(t, str) else None}

    base = {"total_kept": len(kept), "status_counts": policy.gate_counts(entries), "newer_line_eol": newer_line_eol(entries)}
    if mode == "weekly5":
        shown, cut, label = policy.apply_cap(kept, limit)
        shown_ids, cut_ids = {s["id"] for s in shown}, {c["id"] for c in cut}
        for e in entries:
            e["shown"] = True if e["id"] in shown_ids else (False if e["id"] in cut_ids else None)
        return {"mode": mode, "label": label, "limit": limit, "per_tech": policy.PER_TECH, **base,
                "shown": [brief(s) for s in shown], "truncated": [brief(c) for c in cut]}
    r = policy.apply_topk(kept)
    listed_ids, detect_ids, always_ids = {x["id"] for x in r["listed"]}, {x["id"] for x in r["detect"]}, {x["id"] for x in r["always"]}
    kept_ids = {x["id"] for x in kept}
    for e in entries:
        if e["id"] not in kept_ids:
            e["shown"] = None
            continue
        e["shown"] = e["id"] in detect_ids
        e["listed"], e["always"] = e["id"] in listed_ids, e["id"] in always_ids
        e["query"], e["rank"] = r["query"].get(e["id"]), r["rank"].get(e["id"])
    label = f"{len(r['detect'])}/{len(kept)} · 목록 {len(r['listed'])}"
    return {"mode": mode, "label": label, "k": policy.TOPK, **base, "always": [brief(x) for x in r["always"]],
            "listed": [dict(brief(x), rank=r["rank"].get(x["id"]), always=x["id"] in always_ids) for x in r["listed"]],
            "shown": [brief(x) for x in r["detect"]], "truncated": [brief(x) for x in r["cut"]]}


def newer_line_eol(entries):
    """「우리 줄보다 새 줄이 지원 종료됐다」 알림 (HYO-148 — 판정이 아니라 요약 한 줄). 그 eol은 다른 줄이라 뺐지만 우리 줄도 지원 밖일 수 있다.
    기술마다 한 번 → [{technology, ours, announced(가장 높은 줄), ids}]."""
    by = {}
    for e in entries:
        for s in (e.get("gate") or {}).get("scope") or []:
            for n in s["notes"]:
                for x in n.get("newer_line_eol") or []:
                    cur = by.setdefault(x["technology"], {"technology": x["technology"], "ours": x["ours"], "announced": x["announced"], "ids": []})
                    if scope.vtuple(x["announced"]) > scope.vtuple(cur["announced"]):
                        cur["announced"] = x["announced"]
                    if e["id"] not in cur["ids"]:
                        cur["ids"].append(e["id"])
    return list(by.values())
