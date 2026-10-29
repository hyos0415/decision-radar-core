"""Arena 최종 비교 채점 — 30칸 예측 격자 (retrieval-arena §7-1, HYO-155).

python arena/cli.py grid --results <결과 폴더> --queries <q.jsonl> --qrels <qrels.json> --judgments <judgments.jsonl> [--rank-only-results <폴더>] --out <x.json> --md <x.md>

등록된 채점 (test 결과 0건에서 이 파일로 고정한다 — 2026-10-29):
- 칸 = 격자 행 전략 × 질의 유형(B·C·E·J·K). 칸 값 = 그 유형 질의에서 전략 − S2(BM25)의 질의별 nDCG@10 차이의 평균
  (paired — 같은 질의끼리 뺀다). nDCG@10은 하네스와 같은 ranx `ndcg@10`(등급 그대로 이득)
- 판정: 차이 ≥ +0.05 상 · ≤ −0.05 하 · 그 사이 중 (§7-1 변경 2026-10-12). 부동소수 흔들림을 막으려고 차이를 소수 10자리로 반올림한 뒤 비교한다
- 적중 = ② 최종 격자의 예측 = 판정. 헤드라인은 30칸 공동 적중률. 분해: 사용자 수정 5칸 / 초안 수용 25칸. ① Claude 초안의 적중률도 함께 낸다
- paired bootstrap 95% CI — 유형 안의 질의를 복원 추출 10,000회(시드 20261029), 차이 평균의 2.5·97.5 백분위. 보고만 하고 판정에 쓰지 않는다
- 엄격 채점 — qrels의 1을 0으로 본 nDCG@10(2만 정답, §7-1 변경 2026-10-27). 등급 2가 하나도 없는 질의는 nDCG가 정의되지 않아
  엄격 채점에서 그 질의를 빼고(두 전략 모두에서 — paired라 공정하다) 뺀 수를 적는다. 기본 판정과 갈리는 칸을 따로 적는다
- judged@10 — 상위 10 문서 중 판정(0 포함, `judgments.jsonl` — qrels.json에는 0이 없다)이 있는 비율의 질의 평균.
  잔여 판정 전·후는 gold 판을 바꿔 두 번 돌린다
- 격자 밖 전략(S1 · S3a.ko · S4 변형 · S5b(SPLADE-v3) · S6)은 같은 방법으로 차이·판정만 내고 적중률에 넣지 않는다(탐색적).
  전략이 그 유형 질의를 다 돌리지 않았으면(S1은 K만) 그 칸은 비운다
- `--rank-only-results`를 주면 순위만 뽑은 실행(빈 qrels)의 run.json과 채점 실행의 run.json이 전략마다 같은지 먼저 확인한다 —
  다르면 멈춘다(카드 확정 전후로 순위가 바뀌지 않았다는 증거)
"""
import json
import random
from pathlib import Path

TYPES = ("B", "C", "E", "J", "K")
BASELINE = "S2"
THRESHOLD = 0.05
BOOT_N = 10_000
BOOT_SEED = 20261029

# 격자 행 → 결과 전략 id (S5b 칸은 S5b.ko로 채운다 — dev-tuning ④)
ROWS = {"S3a": "S3a", "S3b": "S3b", "S4": "S4", "S5a": "S5a", "S5b": "S5b.ko", "S7": "S7"}

# ① Claude 초안 (2026-09-29, 고정) · ② 최종 격자 (2026-09-29 등록) — retrieval-arena §7-1, 확증 유형 B·C·E·J·K만
GRID_DRAFT = {
    "S3a": {"B": "중", "C": "하", "E": "중", "J": "중", "K": "중"},
    "S3b": {"B": "중", "C": "중", "E": "중", "J": "중", "K": "중"},
    "S4":  {"B": "상", "C": "하", "E": "중", "J": "상", "K": "중"},
    "S5a": {"B": "상", "C": "중", "E": "중", "J": "상", "K": "상"},
    "S5b": {"B": "상", "C": "중", "E": "중", "J": "상", "K": "중"},
    "S7":  {"B": "하", "C": "하", "E": "중", "J": "상", "K": "상"},
}
GRID_FINAL = {
    "S3a": {"B": "상", "C": "하", "E": "중", "J": "중", "K": "중"},
    "S3b": {"B": "중", "C": "중", "E": "상", "J": "중", "K": "중"},
    "S4":  {"B": "상", "C": "하", "E": "하", "J": "상", "K": "중"},
    "S5a": {"B": "상", "C": "중", "E": "중", "J": "상", "K": "상"},
    "S5b": {"B": "상", "C": "중", "E": "중", "J": "상", "K": "중"},
    "S7":  {"B": "하", "C": "상", "E": "상", "J": "상", "K": "상"},
}
USER_CELLS = {(r, t) for r in GRID_FINAL for t in TYPES if GRID_FINAL[r][t] != GRID_DRAFT[r][t]}


def load_runs(results_dir):
    """결과 폴더 아래 `<전략>.<버전>__<코퍼스>__<gold>__<티어>__seed<n>`마다 run.json — {전략 id: run}.
    코퍼스·티어·시드가 섞였거나 같은 전략이 둘이면 멈춘다."""
    runs, tails = {}, set()
    for d in sorted(Path(results_dir).iterdir()):
        if not (d / "run.json").exists():
            continue
        head, _, tail = d.name.partition("__")
        sid = head.rsplit(".", 1)[0]
        if sid in runs:
            raise SystemExit(f"같은 전략의 결과가 둘이다: {sid} ({d.name})")
        runs[sid] = json.loads((d / "run.json").read_text(encoding="utf-8"))
        corpus_v, _gold, tier, seed = tail.split("__")
        tails.add((corpus_v, tier, seed))
    if len(tails) > 1:
        raise SystemExit(f"코퍼스·티어·시드가 섞였다: {sorted(tails)}")
    return runs


def check_same_runs(runs, rank_only_runs):
    diff = sorted(s for s in set(runs) | set(rank_only_runs) if runs.get(s) != rank_only_runs.get(s))
    if diff:
        raise SystemExit(f"순위만 뽑은 실행과 채점 실행의 run.json이 다르다: {diff}")


def per_query_ndcg(qrels, run, k=10):
    """질의마다 ranx ndcg@k — 질의 하나씩 따로 재서 순서 정렬 문제를 없앤다. 결과가 빈 질의는 0."""
    from ranx import Qrels, Run, evaluate
    out = {}
    for qid, rel in qrels.items():
        docs = run.get(qid) or {"__no_result__": 0.0}
        out[qid] = float(evaluate(Qrels({qid: rel}), Run({qid: docs}), f"ndcg@{k}"))
    return out


def strict_qrels(qrels):
    """1을 0으로 — 등급 2가 없는 질의는 뺀다. (엄격 qrels, 뺀 질의 목록)"""
    kept = {q: {d: (2 if g == 2 else 0) for d, g in rel.items()} for q, rel in qrels.items() if 2 in rel.values()}
    return kept, sorted(set(qrels) - set(kept))


def verdict(diff):
    d = round(diff, 10)
    return "상" if d >= THRESHOLD else "하" if d <= -THRESHOLD else "중"


def bootstrap_ci(diffs, n=BOOT_N, seed=BOOT_SEED):
    if not diffs:
        return None
    rng = random.Random(seed)
    means = sorted(sum(rng.choice(diffs) for _ in diffs) / len(diffs) for _ in range(n))
    return [round(means[int(0.025 * n)], 4), round(means[int(0.975 * n) - 1], 4)]


def load_judged(path):
    """judgments.jsonl → {qid: {판정된 doc_id}} (0 포함)"""
    out = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            out.setdefault(r["qid"], set()).add(r["doc_id"])
    return out


def judged_at(run, judged, k=10):
    vals = [sum(d in judged.get(q, set()) for d in list(docs)[:k]) / len(list(docs)[:k]) for q, docs in run.items() if docs]
    return round(sum(vals) / len(vals), 4) if vals else None


def _cells(scores, qtype, sid):
    """전략 sid의 유형별 칸 — S2와 같은 질의에서 paired."""
    out = {}
    for t in TYPES:
        ids = [q for q in qtype if qtype[q] == t and q in scores[BASELINE]]
        if not ids or any(q not in scores[sid] for q in ids):
            continue
        diffs = [scores[sid][q] - scores[BASELINE][q] for q in ids]
        mean = sum(diffs) / len(diffs)
        out[t] = {"n": len(ids), "mean": round(sum(scores[sid][q] for q in ids) / len(ids), 4),
                  "baseline": round(sum(scores[BASELINE][q] for q in ids) / len(ids), 4),
                  "diff": round(mean, 4), "verdict": verdict(mean), "ci95": bootstrap_ci(diffs)}
    return out


def score(runs, qrels, queries, judged):
    qtype = {q["qid"]: q["type"] for q in queries}
    if set(qtype) != set(qrels):
        raise SystemExit("질의와 qrels의 qid가 다르다")
    missing = [s for s in [BASELINE, *ROWS.values()] if s not in runs]
    if missing:
        raise SystemExit(f"격자에 필요한 전략 결과가 없다: {missing} — 빼고 채점하지 않는다")
    sq, dropped = strict_qrels(qrels)
    sub = {s: {q: r for q, r in run.items() if q in qrels} for s, run in runs.items()}
    default = {s: per_query_ndcg({q: qrels[q] for q in run}, run) for s, run in sub.items()}
    strict = {s: per_query_ndcg({q: sq[q] for q in run if q in sq}, run) for s, run in sub.items()}
    strategies = {}
    for s in sorted(runs):
        if s == BASELINE:
            continue
        strategies[s] = {"cells": _cells(default, qtype, s), "strict_cells": _cells(strict, qtype, s),
                         "judged@10": judged_at(sub[s], judged)}
    grid, hits = {}, {"final": [], "draft": [], "user": [], "accepted": []}
    for row, sid in ROWS.items():
        grid[row] = {}
        for t in TYPES:
            c, sc = strategies[sid]["cells"][t], strategies[sid]["strict_cells"].get(t)
            hit = c["verdict"] == GRID_FINAL[row][t]
            grid[row][t] = {"strategy": sid, "predicted": GRID_FINAL[row][t], "draft": GRID_DRAFT[row][t],
                            "verdict": c["verdict"], "diff": c["diff"], "ci95": c["ci95"], "hit": hit,
                            "user_cell": (row, t) in USER_CELLS,
                            "strict_verdict": sc["verdict"] if sc else None,
                            "strict_differs": bool(sc) and sc["verdict"] != c["verdict"]}
            hits["final"].append(hit)
            hits["draft"].append(c["verdict"] == GRID_DRAFT[row][t])
            hits["user" if (row, t) in USER_CELLS else "accepted"].append(hit)

    def rate(xs):
        return {"hit": sum(xs), "n": len(xs), "rate": round(sum(xs) / len(xs), 4) if xs else None}
    return {
        "registered": {"threshold": THRESHOLD, "bootstrap": {"n": BOOT_N, "seed": BOOT_SEED}, "baseline": BASELINE,
                       "rows": ROWS, "user_cells": sorted(f"{r}×{t}" for r, t in USER_CELLS)},
        "n_queries": {t: sum(1 for q in qtype.values() if q == t) for t in TYPES},
        "strict_dropped_queries": len(dropped),
        "baseline": {"judged@10": judged_at(sub[BASELINE], judged)},
        "hit_rate": {"final_30": rate(hits["final"]), "user_5": rate(hits["user"]),
                     "accepted_25": rate(hits["accepted"]), "draft_30": rate(hits["draft"])},
        "grid": grid,
        "strict_differs": sorted(f"{r}×{t}" for r in grid for t in TYPES if grid[r][t]["strict_differs"]),
        "strategies": strategies,
    }


def to_markdown(res):
    h = res["hit_rate"]
    lines = ["| 격자 | " + " | ".join(TYPES) + " |", "|---|" + "---|" * len(TYPES)]
    for row, cells in res["grid"].items():
        def cell(c):
            mark = "✔" if c["hit"] else "✘"
            star = "*" if c["user_cell"] else ""
            return f"{mark} {c['verdict']}{star} (예측 {c['predicted']}) {c['diff']:+.3f}"
        lines.append(f"| {row} ({cells['B']['strategy']}) | " + " | ".join(cell(cells[t]) for t in TYPES) + " |")
    lines += ["", f"- 30칸 공동 적중 {h['final_30']['hit']}/{h['final_30']['n']} · 사용자 수정 {h['user_5']['hit']}/{h['user_5']['n']}"
              f" · 초안 수용 {h['accepted_25']['hit']}/{h['accepted_25']['n']} · ① 초안이었다면 {h['draft_30']['hit']}/{h['draft_30']['n']}",
              f"- 엄격 채점과 판정이 갈린 칸: {', '.join(res['strict_differs']) or '없음'} (엄격 채점에서 뺀 질의 {res['strict_dropped_queries']})",
              "- `*` = 사용자가 고친 칸 · 차이 = 전략 − S2 nDCG@10 평균", "",
              "| 전략 | judged@10 | " + " | ".join(TYPES) + " |", "|---|---|" + "---|" * len(TYPES),
              f"| S2 | {res['baseline']['judged@10']} |" + " — |" * len(TYPES)]
    for s, v in res["strategies"].items():
        cells = " | ".join((f"{v['cells'][t]['verdict']} {v['cells'][t]['diff']:+.3f} {v['cells'][t]['ci95']}" if t in v["cells"] else "—")
                           for t in TYPES)
        lines.append(f"| {s} | {v['judged@10']} | {cells} |")
    return "\n".join(lines) + "\n"


def run_cli(args):
    from arena import harness
    queries = harness.load_queries(args.queries)
    qrels = harness.load_qrels(args.qrels)
    runs = load_runs(args.results)
    if args.rank_only_results:
        check_same_runs(runs, load_runs(args.rank_only_results))
    res = score(runs, qrels, queries, load_judged(args.judgments))
    res["inputs"] = {"results": str(args.results), "qrels": str(args.qrels), "judgments": str(args.judgments), "queries": str(args.queries),
                     "rank_only_checked": bool(args.rank_only_results)}
    text = json.dumps(res, ensure_ascii=False, indent=1) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8", newline="\n")
    md = to_markdown(res)
    if args.md:
        Path(args.md).write_text(md, encoding="utf-8", newline="\n")
    print(md)
