"""HYO-131 문구 비교 — 이전 문구 vs 새 문구, 같은 정답지(gold-w42 · gold-backfill), 고정 등급 + Luna, k=3.

importance-policy §8 「변경 (2026-10-21 밤, HYO-131)」에 등록한 분석이다. 결과를 보기 전에 커밋했다 — 바꾸면 등록을 바꾸는 것이다 (규율 7).

  python eval/importance/compare_wording.py --old <w42 결과 폴더> <backfill 결과 폴더> --new <w42 결과 폴더> <backfill 결과 폴더> [--out report.md]

입력은 `python -m pipeline.importance evaluate ... --out <폴더>`가 쓴 summary.json(정답지 경로 · 실행별 예측)이다.

(가) 운영 판정 — C1~C3 (§8 그대로)은 evaluate가 이미 낸 것을 옮겨 적는다
(나) 문구 선택 — 사람이 판정한 항목(정답지 `by` 없음)만, 항목마다 k번 중 과반이 breaking이면 breaking으로 본다.
     b = 새 문구만 맞음 · c = 이전 문구만 맞음. 새 문구를 운영에 두는 조건 (셋 다):
       1. b ≥ c
       2. 새 문구의 C1 놓침 = 0 — 두 정답지, 모든 실행
       3. 새 문구의 C3 평균이 이전 문구보다 2%p 넘게 낮지 않다 — 정답지마다
     b·c가 둘 다 5 미만이면 결정은 같은 규칙으로 하되 「근거 약함」으로 적는다
(다) 진단 (판정에 안 쓴다) — 전체 항목의 종류별 정밀도·재현율(과반), 변화 집합 일치(정답 [] ≡ [minor_patch] — decide가 그렇게 친다),
     코드 정리(normalize_changes)가 바꾼 라벨 수, 출처(by)별 breaking 오류
"""
import argparse
import json
import sys
from pathlib import Path

C3_MAX_DROP = 0.02
WEAK_EVIDENCE = 5
KINDS = ["breaking", "deprecation", "security", "eol", "major", "model_retirement", "pricing", "yanked"]


def load(folder):
    s = json.loads((Path(folder) / "summary.json").read_text(encoding="utf-8"))
    gold = json.loads(Path(s["gold"]).read_text(encoding="utf-8"))
    return s, gold


def majority(preds_runs, iid, kind):
    """k번 중 과반이 그 종류를 짚었나. 오류·분류 불가로 예측이 없으면 안 짚은 것으로 센다."""
    hits = sum(kind in ((p.get(iid) or {}).get("changes") or []) for p in preds_runs)
    return hits * 2 > len(preds_runs)


def by_of(it):
    return it.get("by") or "human"


def wording_choice(old, new):
    """old·new: [(summary, gold)] 같은 순서. → (나) 결과 dict."""
    b = c = 0
    rows = []
    for (so, g), (sn, _) in zip(old, new):
        for it in g["items"]:
            if by_of(it) != "human":
                continue
            truth = "breaking" in it["gold_changes"]
            po, pn = majority(so["predictions"], it["id"], "breaking"), majority(sn["predictions"], it["id"], "breaking")
            if (pn == truth) and (po != truth):
                b += 1
                rows.append(("새 문구만 맞음", it, truth))
            elif (po == truth) and (pn != truth):
                c += 1
                rows.append(("이전 문구만 맞음", it, truth))
    c1_new = [r["c1_miss_count"] for sn, _ in new for r in sn["runs"]]
    c3 = [(sum(r["c3_agree"] for r in so["runs"]) / len(so["runs"]), sum(r["c3_agree"] for r in sn["runs"]) / len(sn["runs"])) for (so, _), (sn, _) in zip(old, new)]
    ok1, ok2 = b >= c, all(x == 0 for x in c1_new)
    ok3 = all(n >= o - C3_MAX_DROP for o, n in c3)
    return {"b": b, "c": c, "rows": rows, "c1_new": c1_new, "c3": c3, "conditions": [ok1, ok2, ok3],
            "adopt_new": ok1 and ok2 and ok3, "weak": b < WEAK_EVIDENCE and c < WEAK_EVIDENCE}


def kind_table(arm):
    """전체 항목, 종류별 (정답 수, 과반 예측 수, 맞힌 수)."""
    out = {}
    for kind in KINDS:
        tp = fp = fn = 0
        for s, g in arm:
            for it in g["items"]:
                truth, pred = kind in it["gold_changes"], majority(s["predictions"], it["id"], kind)
                tp += truth and pred
                fp += pred and not truth
                fn += truth and not pred
        out[kind] = (tp, fp, fn)
    return out


def set_agree(arm):
    """변화 집합 일치(실행 평균). 정답 []와 예측 []·[minor_patch]를 같게 본다."""
    def canon(xs):
        xs = set(xs or [])
        return xs or {"minor_patch"}
    tot = n = 0
    for s, g in arm:
        for p in s["predictions"]:
            for it in g["items"]:
                n += 1
                tot += canon((p.get(it["id"]) or {}).get("changes")) == canon(it["gold_changes"])
    return tot / n if n else 0.0


def normalized_count(arm):
    """코드 정리가 라벨을 바꾼 (항목 × 실행) 수 — 프리릴리스 major · 덧붙인 minor_patch."""
    k = 0
    for s, _ in arm:
        for p in s["predictions"]:
            for v in p.values():
                mc = v.get("model_changes")
                k += mc is not None and list(mc) != list(v.get("changes") or [])
    return k


def breaking_errors_by_source(arm):
    out = {}
    for s, g in arm:
        for it in g["items"]:
            truth = "breaking" in it["gold_changes"]
            err = majority(s["predictions"], it["id"], "breaking") != truth
            o = out.setdefault(by_of(it), [0, 0])
            o[0] += err
            o[1] += 1
    return out


def pct(x):
    return f"{x * 100:.1f}%"


def report(old, new):
    w = wording_choice(old, new)
    L = ["# HYO-131 문구 비교 — 이전 vs 새 (고정 등급 + Luna, k=3)", ""]
    L += ["## (가) 운영 판정 — C1~C3 (importance-policy §8 그대로)", "", "| 정답지 | 문구 | C1 놓침 | C2 소음 (≤15%) | C3 일치 (≥80%) | 남김 | 종합 |", "|---|---|---|---|---|---|---|"]
    for name, arm in (("이전", old), ("새", new)):
        for s, g in arm:
            rs = s["runs"]
            L.append(f"| {g.get('group', s['gold'])} | {name} | {' · '.join(str(r['c1_miss_count']) for r in rs)} | {' · '.join(pct(r['c2_noise']) for r in rs)} | "
                     f"{' · '.join(pct(r['c3_agree']) for r in rs)} | {' · '.join(str(r['kept']) for r in rs)} | {'통과' if s['judgement']['pass'] else '불통과'} |")
    L += ["", "## (나) 문구 선택 — 사람 판정 항목, breaking 과반", "",
          f"- b(새 문구만 맞음) = **{w['b']}** · c(이전 문구만 맞음) = **{w['c']}**" + (" — 둘 다 5 미만: **근거 약함**" if w["weak"] else ""),
          f"- 조건 1 b ≥ c: {'충족' if w['conditions'][0] else '불충족'}",
          f"- 조건 2 새 문구 C1 놓침 0 (모든 실행): {w['c1_new']} → {'충족' if w['conditions'][1] else '불충족'}",
          "- 조건 3 새 문구 C3 평균 ≥ 이전 − 2%p: " + " · ".join(f"{pct(o)} → {pct(n)}" for o, n in w["c3"]) + f" → {'충족' if w['conditions'][2] else '불충족'}",
          f"- **결정 (등록 규칙): {'새 문구를 운영에 둔다' if w['adopt_new'] else '이전 문구를 유지한다'}**", ""]
    if w["rows"]:
        L += ["| 갈린 쪽 | 소식 | 정답 breaking |", "|---|---|---|"]
        L += [f"| {k} | {it['title'][:80]} | {'예' if t else '아니오'} |" for k, it, t in w["rows"]]
        L.append("")
    L += ["## (다) 진단 — 판정에 안 씀", "", "종류별 (전체 항목, 과반) — 맞힘 / 소음 / 놓침", "", "| 종류 | 이전 | 새 |", "|---|---|---|"]
    ko, kn = kind_table(old), kind_table(new)
    L += [f"| {k} | {ko[k][0]} / {ko[k][1]} / {ko[k][2]} | {kn[k][0]} / {kn[k][1]} / {kn[k][2]} |" for k in KINDS if any(ko[k]) or any(kn[k])]
    L += ["", f"- 변화 집합 일치 (실행 평균, [] ≡ [minor_patch]): 이전 {pct(set_agree(old))} · 새 {pct(set_agree(new))}",
          f"- 코드 정리가 라벨을 바꾼 (항목 × 실행): 이전 {normalized_count(old)} · 새 {normalized_count(new)}"]
    eo, en = breaking_errors_by_source(old), breaking_errors_by_source(new)
    L.append("- breaking 오류 / 항목 수 (출처별): " + " · ".join(f"{src} 이전 {eo[src][0]}/{eo[src][1]} → 새 {en[src][0]}/{en[src][1]}" for src in sorted(eo)))
    return "\n".join(L) + "\n", w


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--old", nargs="+", required=True, help="이전 문구 결과 폴더들 (정답지 순서를 --new와 맞춘다)")
    ap.add_argument("--new", nargs="+", required=True)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    if len(a.old) != len(a.new):
        ap.error("--old와 --new의 폴더 수가 같아야 한다")
    old, new = [load(f) for f in a.old], [load(f) for f in a.new]
    for (so, _), (sn, _) in zip(old, new):
        if Path(so["gold"]).resolve() != Path(sn["gold"]).resolve():
            ap.error(f"같은 순서의 정답지가 다르다: {so['gold']} vs {sn['gold']}")
    text, _ = report(old, new)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8", newline="\n")
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
