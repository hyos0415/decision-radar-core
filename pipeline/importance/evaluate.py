"""평가 진입점 — importance-policy §8 품질 기준으로 레인 A 분류기를 채점한다.

  python -m pipeline.importance evaluate --gold eval/importance/gold.json --model stub                # 모델 없이 배관 확인 (판정 아님)
  python -m pipeline.importance evaluate --gold ... --model gpt-6-luna --k 3 --plan                   # 호출 수만 센다
  python -m pipeline.importance evaluate --gold ... --model gpt-6-luna --k 3 --real-api               # 실제 API (과금) — 플래그 없으면 거부
  python -m pipeline.importance evaluate --gold ... --model jev-1.13.0 --k 3 --plan                   # Jev: 요청 수 (요청 하나가 질문 여럿 — 비용은 입력 토큰당 $0.042/Mtok)
  python -m pipeline.importance evaluate --gold ... --model gpt-6-luna --fixed-tiers tiers.json ...   # 운영 형태: 등급은 고정 파일, 모델은 변화 종류만 (HYO-130)
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

HYO-169 등록(§8 변경 2026-11-11 — 확정 시트 eval/importance/precondition-hyo169.md)에서 더한 것. 옵션을 줄 때만 판정에 들어간다 — 예전 등록의 판정은 그대로:
  --with-r      R 안전 밖 남김 재현율 (정답이 남김이고 안전 종류가 아닌 소식 중 남긴 비율) ≥ 80%, k번 각각. 옵션 없이도 보고는 한다
  --safety-k N  C1을 N번으로: k번 전체 실행 뒤 정답 안전 항목만 (N − k)번 더 돌린다. 모든 실행에서 0건이어야 통과
  --batch N     구독 CLI 묶음 모드 (소식 N개 = 세션 하나, 실행마다 순서 시드 = --seed + 차례)
"""
import argparse
import hashlib
import importlib.util
import json
import sys
import time
from pathlib import Path

if __name__ == "__main__" and __package__ in (None, ""):   # 파일로 직접 실행
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    __package__ = "pipeline.importance"

from . import policy
from .backends import BoundaryError, CallLog, StubBackend, TechQuery, make_backend
from .gate import Gate
from .signals import norm
from .tiers import TierStore

# --- 등록된 기준 (importance-policy §8). 상수를 바꾸는 것은 기준을 바꾸는 것이다 — 규율 7
C1_MAX_MISSES = 0
C2_MAX_NOISE = 0.15
C3_MIN_AGREE = 0.80
K_REQUIRED = 3
SAFETY_CHANGES = frozenset({"model_retirement", "security"})
R_MIN_RECALL = 0.80          # HYO-169 등록 (2026-11-11) — --with-r일 때만 판정에 들어간다

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
    kept = kept_gold_drop = agree = change_exact = r_n = r_kept = 0
    misses, errors, unclassified, r_misses = [], [], [], []
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
        if it["gold_keep"] and not SAFETY_CHANGES & set(it["gold_changes"]):      # R 대상: 안전 밖 정답 남김 (HYO-169)
            r_n += 1
            r_kept += keep
            if not keep:
                r_misses.append(it["id"])
        if set(p.get("changes") or []) == set(it["gold_changes"]):
            change_exact += 1
    return {"n": n, "kept": kept, "kept_but_gold_drop": kept_gold_drop,
            "c1_misses": misses, "c1_miss_count": len(misses),
            "c2_noise": (kept_gold_drop / kept) if kept else 0.0,
            "c3_agree": agree / n if n else 0.0,
            "r_n": r_n, "r_kept": r_kept, "r_recall": (r_kept / r_n) if r_n else 1.0, "r_misses": r_misses,
            "errors": errors, "unclassified": unclassified,
            "change_label_exact": change_exact / n if n else 0.0}      # 진단 (판정에 안 쓴다)


def judge(runs, k_required=K_REQUIRED, with_r=False, safety_runs=()):
    """k번의 실행 지표 → 판정. C1은 k번 모두 0 (하나라도 놓치면 불통과) — safety_runs(안전 항목만 더 돈 실행)도 모두 0이어야 한다.
    R은 늘 보고하고, with_r일 때만 종합 판정에 넣는다 (HYO-169 등록)."""
    k = len(runs)
    c1 = all(r["c1_miss_count"] <= C1_MAX_MISSES for r in list(runs) + list(safety_runs))
    c2 = all(r["c2_noise"] <= C2_MAX_NOISE for r in runs)
    c3 = all(r["c3_agree"] >= C3_MIN_AGREE for r in runs)
    rr = all(r.get("r_recall", 1.0) >= R_MIN_RECALL for r in runs)
    smoke = k < k_required
    mean = lambda key: sum(r[key] for r in runs) / k if k else 0.0   # noqa: E731
    return {"k": k, "smoke": smoke,
            "C1": {"pass": c1, "misses_per_run": [r["c1_miss_count"] for r in runs], "threshold": f"모든 실행에서 {C1_MAX_MISSES}건",
                   **({"safety_only_runs": [r["c1_miss_count"] for r in safety_runs]} if safety_runs else {})},
            "C2": {"pass": c2, "per_run": [round(r["c2_noise"], 4) for r in runs], "mean": round(mean("c2_noise"), 4), "threshold": f"≤ {C2_MAX_NOISE:.0%}"},
            "C3": {"pass": c3, "per_run": [round(r["c3_agree"], 4) for r in runs], "mean": round(mean("c3_agree"), 4), "threshold": f"≥ {C3_MIN_AGREE:.0%}"},
            "R": {"pass": rr, "judged": with_r, "per_run": [round(r.get("r_recall", 1.0), 4) for r in runs],
                  "kept_per_run": [r.get("r_kept") for r in runs], "n": runs[0].get("r_n") if runs else 0, "threshold": f"≥ {R_MIN_RECALL:.0%}"},
            "pass": None if smoke else (c1 and c2 and c3 and (rr or not with_r)),
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


def fixed_store(path, gold):
    """고정 등급 파일(운영의 tiers.json — 사람 수정 포함 효과 값) → 정답지 기술만 담은 메모리 TierStore. 파일은 고치지 않는다.
    파일에 없거나 분류 불가인 기술은 정답지 등급(gold_tiers·gold_path·gold_pin)으로 보충하고 목록을 돌려준다 (사용자 2026-10-16 —
    보강 세트의 가상 기술은 실제 스택에 없다). gold_tiers가 비었으면 분류 불가로 둔다."""
    src, store, supplemented = TierStore(path), TierStore(), []
    for t in gold["technologies"]:
        e = src.effective(t["name"])
        if e is not None and not e.get("unresolved") and e.get("tiers"):
            rec = {k: e.get(k) for k in ("tiers", "path", "pin", "sensitive", "local")}
            rec.update({"technology": t["name"], "unresolved": False, "source": "fixed"})
        else:
            supplemented.append(t["name"])
            gt = list(t.get("gold_tiers") or [])
            rec = {"technology": t["name"], "tiers": gt, "unresolved": not gt, "path": t.get("gold_path") or "prod",
                   "pin": t.get("gold_pin"), "sensitive": False, "local": False, "source": "gold-supplement"}
        store.data["technologies"][norm(t["name"])] = rec
    return store, supplemented


def run_once(backend, gold, fixed=None):
    """한 번의 실행: 정답지 기술의 등급 분류(+스크립트 검증) → 소식마다 관문. 등급은 실행마다 새로 매긴다 (등급 분류의 변동도 잰다).
    fixed(=fixed_store의 TierStore)가 있으면 등급을 부르지 않고 그 값을 쓴다 — 모델은 변화 종류만 짚는다."""
    if fixed is not None:
        return _gate_items(backend, gold, fixed)
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
    return _gate_items(backend, gold, store)


def _gate_items(backend, gold, store):
    items = [{"id": it["id"], "title": it["title"], "text": it.get("text", ""), "source": it.get("source", ""), "meta": {"dependency": it["technology"]}}
             for it in gold["items"]]
    if getattr(backend, "batch", None) and hasattr(backend, "prefetch_changes"):
        # 묶음 모드 (HYO-169): 관문이 모델에 물을 질문을 먼저 모은다(모델 부르지 않음) → 한꺼번에 묶어 묻는다 → 같은 관문으로 다시 판정
        asked = []
        collect = Gate(StubBackend(changes=lambda q: asked.append(q) or []), store)
        for item in items:
            collect(item, [])
        backend.prefetch_changes(asked)
    gate = Gate(backend, store)
    preds = {}
    for it, item in zip(gold["items"], items):
        g = gate(item, [])
        preds[it["id"]] = {"keep": g["keep"], "status": g["status"], "changes": g["changes"], "model_changes": g.get("model_changes"), "tiers": g["tiers"], "reason": g["reason"],
                           "kept_by": [{k: r[k] for k in ("technology", "tier", "change")} for r in g["kept_by"]]}
    return preds, store.data["technologies"]


def safety_items(gold_items):
    return [it for it in gold_items if it["gold_keep"] and SAFETY_CHANGES & set(it["gold_changes"])]


def plan(gold, k, diag, fixed=False, safety_k=None, batch=None):
    """호출 수. 묶음이면 변화 분류는 세션 수(소식 batch개 = 1)로 센다."""
    sessions = (lambda n: -(-n // batch)) if batch else (lambda n: n)   # noqa: E731
    n_tech, n_items = (0 if fixed else len(gold["technologies"])), len(gold["items"])
    per_run = n_tech + sessions(n_items) + (sum(len(q) for _, q, _ in diag) if diag else 0)
    extra_runs = max(0, (safety_k or 0) - k)
    extra = extra_runs * (n_tech + sessions(len(safety_items(gold["items"]))))
    return {"per_run": {"tier_calls": n_tech, "change_calls": sessions(n_items), "tier_diag_calls": per_run - n_tech - sessions(n_items)}, "k": k,
            "safety_extra": {"runs": extra_runs, "calls": extra}, "batch": batch, "total_calls": per_run * k + extra}


def subsets(gold_items):
    """보강 세트(`synthetic: true`)가 섞였으면 {"실데이터": [...], "보강": [...]} — 판정은 합친 정답지로 내고 이 수치는 같이 보고만 한다
    (importance-policy §8 변경 2026-10-06 밤, D7). 섞이지 않았으면 {}."""
    syn = [it for it in gold_items if it.get("synthetic")]
    if not syn or len(syn) == len(gold_items):
        return {}
    return {"실데이터": [it for it in gold_items if not it.get("synthetic")], "보강": syn}


def evaluate(backend, gold, k, diag=None, fixed=None, safety_k=None, with_r=False):
    runs, all_preds, techs, diags, parts = [], [], [], [], {}
    for _ in range(k):
        preds, tech_recs = run_once(backend, gold, fixed)
        runs.append(score_run(preds, gold["items"]))
        for name, items in subsets(gold["items"]).items():
            parts.setdefault(name, []).append(score_run(preds, items))
        all_preds.append(preds)
        techs.append(tech_recs)
        if diag:
            diags.append(run_tier_diag(backend, diag))
    s_runs, s_preds = [], []
    s_items = safety_items(gold["items"])
    for _ in range(max(0, (safety_k or 0) - k)):         # C1을 safety_k번으로 — 정답 안전 항목만 더 돈다 (HYO-169)
        preds, _ = run_once(backend, {**gold, "items": s_items}, fixed)
        s_runs.append(score_run(preds, s_items))
        s_preds.append(preds)
    return {"runs": runs, "judgement": judge(runs, with_r=with_r, safety_runs=s_runs), "subsets": parts, "predictions": all_preds,
            "safety_runs": s_runs, "safety_predictions": s_preds, "technologies": techs, "tier_diag": diags}


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
        if key == "C1" and v.get("safety_only_runs"):
            extra = f" + 안전 항목만 {len(v['safety_only_runs'])}번 {v['safety_only_runs']}"
        lines.append(f"  {label}: {vals}{extra} — 기준 {v['threshold']} → {mark(v['pass'])}")
    r = j.get("R")
    if r:
        lines.append(f"  R 안전 밖 남김 재현율 ({r['n']}건): {r['per_run']} 남김 {r['kept_per_run']} — 기준 {r['threshold']} → {mark(r['pass'])}"
                     + ("" if r["judged"] else " (보고만 — 판정 밖)"))
    lines.append("  종합: " + (j["note"] if j["smoke"] else mark(j["pass"])))
    for r_i, r in enumerate(res["runs"], 1):
        if r["c1_misses"] or r["errors"] or r["unclassified"]:
            lines.append(f"  실행 {r_i}: C1 놓침 {r['c1_misses']} · 오류 {r['errors']} · 분류 불가 {r['unclassified']}")
    for name, rs in res.get("subsets", {}).items():
        lines.append(f"  (보고) {name} {rs[0]['n']}건 — C1 놓침 {[r['c1_miss_count'] for r in rs]} · C2 {[round(r['c2_noise'], 4) for r in rs]}"
                     f" · C3 {[round(r['c3_agree'], 4) for r in rs]} · 남김 {[r['kept'] for r in rs]}")
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
    ap.add_argument("--model", required=True, help="stub | gpt-6-luna | gpt-6.1-sol | jev-1.13.0 | claude-sonnet-5-5 | claude-cli/claude-sonnet-5-5 (고정 ID — alias 거부)")
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--real-api", action="store_true", help="실제 API 호출(과금)을 허용한다. 없으면 stub 외에는 거부")
    ap.add_argument("--plan", action="store_true", help="호출 수만 세고 끝 (API·키 접근 없음)")
    ap.add_argument("--no-tier-diag", action="store_true", help="등급 정확도 진단(tier-screen 49개)을 건너뛴다")
    ap.add_argument("--flex", action="store_true", help="GPT: service_tier=flex (Batch와 같은 반값, 느리다). Jev에는 없다")
    ap.add_argument("--out", help="결과 폴더 (기본 eval/runs/<시각>-importance-<모델>/)")
    ap.add_argument("--fixed-tiers", help="고정 등급 파일 — 등급을 부르지 않고 이 파일(효과 값)로 판정한다. 없는 기술은 정답지 등급으로 보충")
    ap.add_argument("--with-r", action="store_true", help="R(안전 밖 남김 재현율 ≥ 80%%)을 종합 판정에 넣는다 (HYO-169 등록)")
    ap.add_argument("--safety-k", type=int, help="C1 반복 수 — k번 뒤 정답 안전 항목만 (N − k)번 더 (HYO-169 등록: 8)")
    ap.add_argument("--batch", type=int, help="구독 CLI 묶음 크기 (소식 N개 = 세션 하나, HYO-169 등록: 10)")
    ap.add_argument("--seed", type=int, default=0, help="묶음 순서 시드의 시작값 — 실행마다 +1")
    a = ap.parse_args(argv)
    if a.batch and not a.model.startswith("claude-cli/"):
        print(f"거부: --batch는 구독 CLI 백엔드(claude-cli/…)에만 있다 ({a.model})", file=sys.stderr)
        return 2
    if a.model != "stub" and not a.real_api and not a.plan:
        print(f"거부: {a.model}은 실제 API를 부른다. 호출 수는 --plan, 실행은 --real-api로 명시한다", file=sys.stderr)
        return 2
    if a.flex and not a.model.startswith("gpt-"):
        print(f"거부: --flex는 GPT 모델에만 있다 ({a.model})", file=sys.stderr)
        return 2
    gold = load_gold(a.gold)
    diag = None if a.no_tier_diag else tier_diag_sets()
    fixed, fixed_info = None, None
    if a.fixed_tiers:
        fixed, sup = fixed_store(a.fixed_tiers, gold)
        sup_items = sum(1 for it in gold["items"] if it["technology"] in sup)
        fixed_info = {"path": str(a.fixed_tiers), "sha256": hashlib.sha256(Path(a.fixed_tiers).read_bytes()).hexdigest(),
                      "supplemented_from_gold": sup, "supplemented_items": sup_items}
        print(f"고정 등급 파일 {a.fixed_tiers} (sha256 {fixed_info['sha256'][:12]}…) · 정답지 등급으로 보충 {len(sup)}개 {sup} — 소식 {sup_items}건")
    pl = plan(gold, a.k, diag, fixed is not None, a.safety_k, a.batch)
    print(f"호출 계획: 실행당 등급 {pl['per_run']['tier_calls']} + 변화 {pl['per_run']['change_calls']}{' 세션(묶음 ' + str(a.batch) + ')' if a.batch else ''}"
          f" + 진단 {pl['per_run']['tier_diag_calls']} = {sum(pl['per_run'].values())}회 × k={a.k}"
          + (f" + 안전 항목만 {pl['safety_extra']['runs']}번 {pl['safety_extra']['calls']}회" if pl["safety_extra"]["runs"] else "")
          + f" = {pl['total_calls']}회")
    if a.plan:
        return 0
    out = Path(a.out) if a.out else REPO / "eval" / "runs" / f"{time.strftime('%Y%m%d-%H%M%S')}-importance-{a.model.replace('/', '-')}"   # claude-cli/… (HYO-169)
    out.mkdir(parents=True, exist_ok=True)
    backend = make_backend(a.model, allow_real=a.real_api, log=CallLog(out / "calls.jsonl"), **({"service_tier": "flex"} if a.flex else {}),
                           **({"batch": a.batch, "seed": a.seed} if a.batch else {}))
    try:
        res = evaluate(backend, gold, a.k, diag, fixed, safety_k=a.safety_k, with_r=a.with_r)
    finally:
        trace = backend.trace_info() if hasattr(backend, "trace_info") else None
        info = backend.run_info() if hasattr(backend, "run_info") else None
        backend.close()
    calls = backend.log.totals()
    (out / "summary.json").write_text(json.dumps({"model": a.model, "gold": str(a.gold), "fixed_tiers": fixed_info, "plan": pl, "calls": calls, "trace": trace, "backend_info": info, **res},
                                                 ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(format_report(a.model, res, calls, trace))
    if info:
        print(f"  백엔드: {info}")
    print(f"-> {out}")
    return 0 if (res["judgement"]["pass"] or res["judgement"]["smoke"]) else 1


if __name__ == "__main__":
    sys.exit(main())
