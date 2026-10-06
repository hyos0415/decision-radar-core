"""평가 진입점 — importance-policy §8 품질 기준으로 레인 A 분류기를 채점한다.

  python -m pipeline.importance evaluate --gold eval/importance/gold.json --model stub                # 모델 없이 배관 확인 (판정 아님)
  python -m pipeline.importance evaluate --gold ... --model gpt-6-luna --k 3 --plan                   # 호출 수만 센다
  python -m pipeline.importance evaluate --gold ... --model gpt-6-luna --k 3 --real-api               # 실제 API (과금) — 플래그 없으면 거부
  python -m pipeline.importance evaluate --gold ... --model jev-1.13.0 --k 3 --plan                   # Jev: 요청 수 (요청 하나가 질문 여럿 — 비용은 입력 토큰당 $0.042/Mtok)
  (같은 것: python pipeline/importance/evaluate.py ...)

## 정답지 형식 (JSON) — eval/importance/gold.example.json 참고
{"version": 1,
 "technologies": [{"name": "pydantic", "usage": "…", "source": "pyproject 직접 의존성",
                   "signals": {"manifest": "pyproject.toml", "group": "main", "pin": "pinned", "image": false}}],   # signals는 스크립트 검증용 (선택)
 "items": [{"id": "ex-1", "technology": "pydantic", "title": "…", "text": "…",
            "gold_changes": ["breaking"],     # 정답 변화 종류 (policy.CHANGES 중 해당하는 것 전부, 없으면 [])
            "gold_keep": true,                # 정답 남김(true)/버림(false) — 사람이 정한다
            "basis": "정답 근거 한 줄"}]}

## 기준 (importance-policy §8 — 결과 전에 고정, 바꾸지 않는다. 바꾸려면 새 버전으로 재등록)
  C1 안전   정답이 남김인 **모델 은퇴·security** 소식을 버린 수 = 0, k=3 세 번 모두 (pass^k)
  C2 정밀도 남긴 것 중 정답이 버림인 비율 ≤ 15%
  C3 일치율 최종 남김/버림이 정답과 같은 비율 ≥ 80%
  (진단) 등급 정확도 — tier-screen 정답 v1 34 + v2 15. 판정에 안 쓴다
C2·C3은 k번 **각각** 통과선을 넘어야 한다 (평균은 같이 보고한다). k<3이면 스모크다 — 판정하지 않는다 (규율 5).
오류(호출 실패)·분류 불가는 버림으로 센다 — 기준을 느슨하게 만들지 않는다 (C1에서 안전 실패로 집계된다).
"""
import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path

if __name__ == "__main__" and __package__ in (None, ""):   # 파일로 직접 실행
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    __package__ = "pipeline.importance"

from . import policy
from .backends import BoundaryError, CallLog, TechQuery, make_backend
from .gate import Gate
from .signals import norm
from .tiers import TierStore

# --- 등록된 기준 (importance-policy §8). 상수를 바꾸는 것은 기준을 바꾸는 것이다 — 규율 7
C1_MAX_MISSES = 0
C2_MAX_NOISE = 0.15
C3_MIN_AGREE = 0.80
K_REQUIRED = 3
SAFETY_CHANGES = frozenset({"model_retirement", "security"})

REPO = Path(__file__).resolve().parents[2]
TIER_SCREEN = REPO / "eval" / "tier-screen"


class GoldError(ValueError):
    pass


def load_gold(path):
    g = json.loads(Path(path).read_text(encoding="utf-8"))
    return validate_gold(g)


def validate_gold(g):
    errs = []
    techs = {t.get("name") for t in g.get("technologies", [])}
    if not g.get("items"):
        errs.append("items가 비었다")
    seen = set()
    for i, it in enumerate(g.get("items", [])):
        where = f"items[{i}] {it.get('id')}"
        for k in ("id", "technology", "title", "gold_changes", "gold_keep", "basis"):
            if k not in it:
                errs.append(f"{where}: {k} 없음")
        if it.get("id") in seen:
            errs.append(f"{where}: id 중복")
        seen.add(it.get("id"))
        if it.get("technology") not in techs:
            errs.append(f"{where}: technology {it.get('technology')!r}가 technologies에 없다")
        bad = [c for c in it.get("gold_changes", []) if c not in policy.CHANGES]
        if bad:
            errs.append(f"{where}: 모르는 변화 종류 {bad}")
        if not isinstance(it.get("gold_keep"), bool):
            errs.append(f"{where}: gold_keep은 true/false")
        if it.get("gold_keep") is False and SAFETY_CHANGES & set(it.get("gold_changes", [])) and not it.get("basis"):
            errs.append(f"{where}: 안전 항목을 버림으로 둘 때는 근거(basis)가 필요하다")
    if errs:
        raise GoldError("정답지 형식 오류 %d건:\n  - " % len(errs) + "\n  - ".join(errs))
    return g


# ---------------------------------------------------------------------------- 채점 (순수 계산 — 모델 없이 손계산과 대조한다)

def score_run(preds, gold_items):
    """preds: {id: {"keep": bool, "status": str, "changes": [...]}} · gold_items: 정답지 items. → 한 번의 실행 지표."""
    n = len(gold_items)
    kept = kept_gold_drop = agree = change_exact = 0
    misses, errors, unclassified = [], [], []
    for it in gold_items:
        p = preds.get(it["id"]) or {"keep": False, "status": "missing", "changes": []}
        keep = bool(p["keep"]) and p.get("status") == "ok"
        if p.get("status") == "error":
            errors.append(it["id"])
        elif p.get("status") != "ok":
            unclassified.append(it["id"])
        if keep:
            kept += 1
            if not it["gold_keep"]:
                kept_gold_drop += 1
        if keep == it["gold_keep"]:
            agree += 1
        if it["gold_keep"] and SAFETY_CHANGES & set(it["gold_changes"]) and not keep:
            misses.append(it["id"])
        if set(p.get("changes") or []) == set(it["gold_changes"]):
            change_exact += 1
    return {"n": n, "kept": kept, "kept_but_gold_drop": kept_gold_drop,
            "c1_misses": misses, "c1_miss_count": len(misses),
            "c2_noise": (kept_gold_drop / kept) if kept else 0.0,
            "c3_agree": agree / n if n else 0.0,
            "errors": errors, "unclassified": unclassified,
            "change_label_exact": change_exact / n if n else 0.0}      # 진단 (판정에 안 쓴다)


def judge(runs, k_required=K_REQUIRED):
    """k번의 실행 지표 → 판정. C1은 k번 모두 0 (하나라도 놓치면 불통과)."""
    k = len(runs)
    c1 = all(r["c1_miss_count"] <= C1_MAX_MISSES for r in runs)
    c2 = all(r["c2_noise"] <= C2_MAX_NOISE for r in runs)
    c3 = all(r["c3_agree"] >= C3_MIN_AGREE for r in runs)
    smoke = k < k_required
    mean = lambda key: sum(r[key] for r in runs) / k if k else 0.0   # noqa: E731
    return {"k": k, "smoke": smoke,
            "C1": {"pass": c1, "misses_per_run": [r["c1_miss_count"] for r in runs], "threshold": f"모든 실행에서 {C1_MAX_MISSES}건"},
            "C2": {"pass": c2, "per_run": [round(r["c2_noise"], 4) for r in runs], "mean": round(mean("c2_noise"), 4), "threshold": f"≤ {C2_MAX_NOISE:.0%}"},
            "C3": {"pass": c3, "per_run": [round(r["c3_agree"], 4) for r in runs], "mean": round(mean("c3_agree"), 4), "threshold": f"≥ {C3_MIN_AGREE:.0%}"},
            "pass": None if smoke else (c1 and c2 and c3),
            "note": f"k={k} < {k_required} — 스모크다. 통과·불통과를 말하지 않는다" if smoke else None}


# ---------------------------------------------------------------------------- 실행

def _load_path(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def tier_diag_sets():
    """tier-screen 정답 v1 34 + v2 15 → [(set이름, [(id, TechQuery)], {id: {등급}})]. 정답지는 eval/tier-screen/ 파일에서 읽는다."""
    grade = _load_path(TIER_SCREEN / "grade.py", "ts_grade")
    mk = _load_path(TIER_SCREEN / "make_run.py", "ts_make_run")
    v1 = [(i, TechQuery(name=t, usage=s, source=s)) for i, t, s in mk.ITEMS]
    v2 = [(i, TechQuery(name=t, usage=u, source="")) for i, t, u in mk.ITEMS_V2]
    return [("tier-screen v1", v1, grade.gold(str(TIER_SCREEN / "gold-draft.md"))),
            ("tier-screen v2", v2, grade.gold(str(TIER_SCREEN / "gold-v2-draft.md")))]


def run_tier_diag(backend, diag_sets):
    out = {}
    for name, queries, gold in diag_sets:
        store, ok, wrong, unsure = TierStore(), 0, [], []
        for qid, q in queries:
            try:
                rec = store.classify(q, backend)
            except BoundaryError:
                raise
            except Exception as e:  # noqa: BLE001
                wrong.append((qid, sorted(gold[qid]), f"error:{type(e).__name__}"))
                continue
            got = set(rec["tiers"])
            if rec["unresolved"]:
                unsure.append(qid)
            elif got == gold[qid]:
                ok += 1
            else:
                wrong.append((qid, sorted(gold[qid]), sorted(got)))
        out[name] = {"n": len(queries), "correct": ok, "accuracy": round(ok / len(queries), 4), "unsure": unsure, "wrong": wrong}
    return out


def run_once(backend, gold):
    """한 번의 실행: 정답지 기술의 등급 분류(+스크립트 검증) → 소식마다 관문. 등급은 실행마다 새로 매긴다 (등급 분류의 변동도 잰다)."""
    store = TierStore()
    for t in gold["technologies"]:
        q = TechQuery(name=t["name"], usage=t.get("usage", ""), source=t.get("source", ""), signals=t.get("signals") or {})
        try:
            store.classify(q, backend)
        except BoundaryError:
            raise
        except Exception as e:  # noqa: BLE001 — 등급을 못 매기면 분류 불가로 둔다 (그 기술의 소식은 버려진다 = 안전 실패로 센다)
            store.data["technologies"][norm(q.name)] = {
                "technology": q.name, "tiers": [], "unresolved": True, "path": "prod", "pin": None, "error": f"{type(e).__name__}: {str(e)[:120]}"}
    gate = Gate(backend, store)
    preds = {}
    for it in gold["items"]:
        item = {"id": it["id"], "title": it["title"], "text": it.get("text", ""), "source": it.get("source", ""), "meta": {"dependency": it["technology"]}}
        g = gate(item, [])
        preds[it["id"]] = {"keep": g["keep"], "status": g["status"], "changes": g["changes"], "tiers": g["tiers"], "reason": g["reason"],
                           "kept_by": [{k: r[k] for k in ("technology", "tier", "change")} for r in g["kept_by"]]}
    return preds, store.data["technologies"]


def plan(gold, k, diag):
    n_tech, n_items = len(gold["technologies"]), len(gold["items"])
    per_run = n_tech + n_items + (sum(len(q) for _, q, _ in diag) if diag else 0)
    return {"per_run": {"tier_calls": n_tech, "change_calls": n_items, "tier_diag_calls": per_run - n_tech - n_items}, "k": k, "total_calls": per_run * k}


def evaluate(backend, gold, k, diag=None):
    runs, all_preds, techs, diags = [], [], [], []
    for _ in range(k):
        preds, tech_recs = run_once(backend, gold)
        runs.append(score_run(preds, gold["items"]))
        all_preds.append(preds)
        techs.append(tech_recs)
        if diag:
            diags.append(run_tier_diag(backend, diag))
    return {"runs": runs, "judgement": judge(runs), "predictions": all_preds, "technologies": techs, "tier_diag": diags}


def format_report(model, res, calls, trace=None):
    j = res["judgement"]
    mark = lambda ok: "통과" if ok else "불통과"   # noqa: E731
    lines = [f"모델 {model} · k={j['k']}"]
    if model == "stub":
        lines.append("스텁 — 배관 확인이다. 모델이 아니라 키워드 규칙이 답했으므로 아래 숫자는 판정이 아니다")
    for key, label in (("C1", "C1 안전 (모델 은퇴·security를 버린 수)"), ("C2", "C2 정밀도 (남긴 것 중 정답 ❌)"), ("C3", "C3 일치율")):
        v = j[key]
        vals = v.get("misses_per_run") or v["per_run"]
        extra = f" 평균 {v['mean']}" if "mean" in v else ""
        lines.append(f"  {label}: {vals}{extra} — 기준 {v['threshold']} → {mark(v['pass'])}")
    lines.append("  종합: " + (j["note"] if j["smoke"] else mark(j["pass"])))
    for r_i, r in enumerate(res["runs"], 1):
        if r["c1_misses"] or r["errors"] or r["unclassified"]:
            lines.append(f"  실행 {r_i}: C1 놓침 {r['c1_misses']} · 오류 {r['errors']} · 분류 불가 {r['unclassified']}")
    for r_i, d in enumerate(res["tier_diag"], 1):
        lines.append(f"  (진단) 실행 {r_i} 등급 정확도: " + " · ".join(f"{n} {v['correct']}/{v['n']}" for n, v in d.items()))
    lines.append(f"  호출 {calls['model_calls']}회 · 입력 {calls['input_tokens']} · 출력 {calls['output_tokens']} 토큰 · 비용 ${calls['cost_usd']}"
                 + (f" (단가 미확인 호출 {calls['unpriced_calls']}회 제외)" if calls["unpriced_calls"] else "") + f" · 오류 {calls['errors']}")
    if trace:
        lines.append(f"  트레이스 {trace['traces']} · 스팬 {trace['spans']} · 차단된 호스트 {trace['blocked_hosts']}")
    return "\n".join(lines)


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="pipeline.importance evaluate")
    ap.add_argument("--gold", required=True, help="정답지 JSON (형식: 이 파일 머리말)")
    ap.add_argument("--model", required=True, help="stub | gpt-6-luna | gpt-6.1-sol | jev-1.13.0 (고정 ID — alias 거부)")
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--real-api", action="store_true", help="실제 API 호출(과금)을 허용한다. 없으면 stub 외에는 거부")
    ap.add_argument("--plan", action="store_true", help="호출 수만 세고 끝 (API·키 접근 없음)")
    ap.add_argument("--no-tier-diag", action="store_true", help="등급 정확도 진단(tier-screen 49개)을 건너뛴다")
    ap.add_argument("--out", help="결과 폴더 (기본 eval/runs/<시각>-importance-<모델>/)")
    a = ap.parse_args(argv)
    if a.model != "stub" and not a.real_api and not a.plan:
        print(f"거부: {a.model}은 실제 API를 부른다. 호출 수는 --plan, 실행은 --real-api로 명시한다", file=sys.stderr)
        return 2
    gold = load_gold(a.gold)
    diag = None if a.no_tier_diag else tier_diag_sets()
    pl = plan(gold, a.k, diag)
    print(f"호출 계획: 실행당 등급 {pl['per_run']['tier_calls']} + 변화 {pl['per_run']['change_calls']} + 진단 {pl['per_run']['tier_diag_calls']}"
          f" = {sum(pl['per_run'].values())}회 × k={a.k} = {pl['total_calls']}회")
    if a.plan:
        return 0
    out = Path(a.out) if a.out else REPO / "eval" / "runs" / f"{time.strftime('%Y%m%d-%H%M%S')}-importance-{a.model}"
    out.mkdir(parents=True, exist_ok=True)
    backend = make_backend(a.model, allow_real=a.real_api, log=CallLog(out / "calls.jsonl"))
    try:
        res = evaluate(backend, gold, a.k, diag)
    finally:
        trace = backend.trace_info() if hasattr(backend, "trace_info") else None
        info = backend.run_info() if hasattr(backend, "run_info") else None
        backend.close()
    calls = backend.log.totals()
    (out / "summary.json").write_text(json.dumps({"model": a.model, "gold": str(a.gold), "plan": pl, "calls": calls, "trace": trace, "backend_info": info, **res},
                                                 ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(format_report(a.model, res, calls, trace))
    if info:
        print(f"  백엔드: {info}")
    print(f"-> {out}")
    return 0 if (res["judgement"]["pass"] or res["judgement"]["smoke"]) else 1


if __name__ == "__main__":
    sys.exit(main())
