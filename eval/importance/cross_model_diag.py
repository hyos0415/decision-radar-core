"""여러 모델의 판정 결과를 같은 정답지로 나란히 읽는다 — 오류 지점 · 편향 · 흔들림 (HYO-169, 2026-11-12 사용자 「Sol과 어떤 편향 차이가 있는지」).

판정이 아니라 읽기다. 모델 호출 없음. 입력은 evaluate.py가 쓴 summary.json(predictions · safety_predictions).
  python eval/importance/cross_model_diag.py --gold eval/importance/gold-hyo169-test.json --ref sol \
      haiku=<run_dir> sonnet=<run_dir> opus=<run_dir> sol=<run_dir> [--out report.md]

- 다수결: 전체 실행(predictions) 중 과반에서 붙은 라벨 · 남김을 그 모델의 답으로 본다
- 라벨별 표: 정답 수 · 모델이 붙인 수 · 맞힘 · 소음(모델만) · 놓침(정답만) — 붙인 수 / 정답 수가 1보다 크면 그 라벨을 넉넉히, 작으면 아껴 붙인다
- 남김 오류: 과반에서 틀린 소식 목록(모델별 몇 번 중 몇 번) — 모두가 틀린 소식(정답 · 정의 쪽 의심)과 한 모델만 틀린 소식을 가른다
- 기준 모델(--ref)과의 차이: 같은 소식에 붙인 라벨이 다른 경우를 라벨별로 센다(기준만 / 그 모델만)
- 흔들림: 실행마다 남김이 갈린 소식 수 · 안전 항목만 더 돈 실행(묶음 이웃이 다르다)과 전체 실행의 놓침 차이
"""
import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

SAFE = {"security", "model_retirement"}


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def majority(preds, iid):
    """과반 라벨 · 과반 남김 · 남긴 횟수 · 실행 수 · 첫 실행 이유."""
    rows = [p.get(iid) or {} for p in preds if iid in p]
    n = len(rows)
    lab = Counter(c for r in rows for c in set(r.get("changes") or []))
    keep = sum(1 for r in rows if r.get("keep") and r.get("status") == "ok")
    return {"labels": {c for c, k in lab.items() if k * 2 > n}, "keep": keep * 2 > n, "kept": keep, "n": n,
            "reason": (rows[0].get("model_reason") or "") if rows else "", "raw": [sorted(r.get("changes") or []) for r in rows]}


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", required=True)
    ap.add_argument("--ref", help="기준 모델 이름 (편향 차이를 이 모델과 견준다)")
    ap.add_argument("--out")
    ap.add_argument("runs", nargs="+", help="이름=실행 폴더")
    a = ap.parse_args(argv)
    gold = load(a.gold)
    items = gold["items"]
    models = {}
    for spec in a.runs:
        name, d = spec.split("=", 1)
        s = load(Path(d) / "summary.json")
        models[name] = {"full": s["predictions"], "safety": s.get("safety_predictions") or [], "info": s.get("backend_info") or {}}
    names = list(models)
    M = {m: {it["id"]: majority(models[m]["full"], it["id"]) for it in items} for m in names}
    out = [f"# 모델 나란히 읽기 — {Path(a.gold).name} ({len(items)}건)", ""]

    # 1. 한눈에
    out += ["## 1. 한눈에 (전체 실행 다수결)", "", "| 모델 | 남김 | 남김 일치 | 남겼는데 정답 버림 | 정답 남김인데 버림 | 그중 안전 | 실행마다 남김이 갈린 소식 |", "|---|---|---|---|---|---|---|"]
    for m in names:
        fk = [it for it in items if M[m][it["id"]]["keep"] and not it["gold_keep"]]
        mk = [it for it in items if it["gold_keep"] and not M[m][it["id"]]["keep"]]
        ms = [it for it in mk if SAFE & set(it["gold_changes"])]
        unstable = sum(1 for it in items if 0 < M[m][it["id"]]["kept"] < M[m][it["id"]]["n"])
        kept = sum(1 for it in items if M[m][it["id"]]["keep"])
        out.append(f"| {m} | {kept} | {len(items) - len(fk) - len(mk)}/{len(items)} | {len(fk)} | {len(mk)} | {len(ms)} | {unstable} |")
    out += ["", f"정답 남김 {sum(1 for it in items if it['gold_keep'])} (안전 {sum(1 for it in items if it['gold_keep'] and SAFE & set(it['gold_changes']))})", ""]

    # 2. 라벨별 — 넉넉히 · 아껴
    labels = sorted({c for it in items for c in it["gold_changes"]} | {c for m in names for v in M[m].values() for c in v["labels"]})
    out += ["## 2. 라벨별 (다수결 라벨 vs 정답 라벨, 정리 뒤)", "", "칸 = 붙인 수 / 맞힘 / 소음(모델만) / 놓침(정답만)", "",
            "| 라벨 | 정답 수 | " + " | ".join(names) + " |", "|---|---|" + "---|" * len(names)]
    for c in labels:
        g = sum(1 for it in items if c in it["gold_changes"])
        cells = []
        for m in names:
            put = sum(1 for it in items if c in M[m][it["id"]]["labels"])
            hit = sum(1 for it in items if c in M[m][it["id"]]["labels"] and c in it["gold_changes"])
            cells.append(f"{put} / {hit} / {put - hit} / {g - hit}")
        out.append(f"| {c} | {g} | " + " | ".join(cells) + " |")
    out.append("")

    # 3. 기준 모델과의 편향 차이
    if a.ref and a.ref in M:
        out += [f"## 3. {a.ref}와 라벨이 다른 곳 (다수결)", "", f"칸 = {a.ref}만 붙임 / 그 모델만 붙임", "",
                "| 라벨 | " + " | ".join(m for m in names if m != a.ref) + " |", "|---|" + "---|" * (len(names) - 1)]
        for c in labels:
            cells = []
            for m in names:
                if m == a.ref:
                    continue
                r_only = sum(1 for it in items if c in M[a.ref][it["id"]]["labels"] and c not in M[m][it["id"]]["labels"])
                m_only = sum(1 for it in items if c in M[m][it["id"]]["labels"] and c not in M[a.ref][it["id"]]["labels"])
                cells.append(f"{r_only} / {m_only}")
            out.append(f"| {c} | " + " | ".join(cells) + " |")
        out.append("")

    # 4. 남김 오류 목록 — 몇 모델이 틀렸나
    wrong = defaultdict(list)
    for it in items:
        bad = [m for m in names if M[m][it["id"]]["keep"] != it["gold_keep"]]
        if bad:
            wrong[len(bad)].append((it, bad))
    out += ["## 4. 남김이 정답과 다른 소식 (다수결) — 틀린 모델 수별", ""]
    for k in sorted(wrong, reverse=True):
        out += [f"### {k}개 모델이 틀림 ({len(wrong[k])}건)", ""]
        for it, bad in sorted(wrong[k], key=lambda x: x[0]["id"]):
            side = "놓침" if it["gold_keep"] else "소음"
            safe = " · 안전" if SAFE & set(it["gold_changes"]) else ""
            out.append(f"- **{side}{safe}** {it['title'][:70]} — 정답 {sorted(it['gold_changes'])}")
            for m in names:
                v = M[m][it["id"]]
                mark = "✗" if m in bad else "✓"
                out.append(f"  - {mark} {m} 남김 {v['kept']}/{v['n']} · 라벨 {sorted(v['labels'])} · {v['reason'][:150]}")
        out.append("")

    # 5. 안전 항목: 전체 실행 vs 안전만 실행
    safe_items = [it for it in items if it["gold_keep"] and SAFE & set(it["gold_changes"])]
    out += ["## 5. 안전 항목 놓침 — 전체 실행 vs 안전 항목만 더 돈 실행 (묶음 이웃이 다르다)", "",
            "| 모델 | 전체 실행 놓침(실행별) | 안전만 실행 놓침(실행별) | 8번 중 놓친 소식(횟수) |", "|---|---|---|---|"]
    for m in names:
        f = [sum(1 for it in safe_items if not ((p.get(it["id"]) or {}).get("keep"))) for p in models[m]["full"]]
        s = [sum(1 for it in safe_items if not ((p.get(it["id"]) or {}).get("keep"))) for p in models[m]["safety"]]
        cnt = Counter(it["title"][:40] for p in models[m]["full"] + models[m]["safety"] for it in safe_items if not ((p.get(it["id"]) or {}).get("keep")))
        out.append(f"| {m} | {f} | {s} | " + " · ".join(f"{t} {n}" for t, n in cnt.most_common()) + " |")
    out.append("")
    out += ["## 실행 정보", ""] + [f"- {m}: {json.dumps(models[m]['info'], ensure_ascii=False)}" for m in names]
    text = "\n".join(out)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
