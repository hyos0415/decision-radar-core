"""화제 묶기 방법 채점 — 확정 시트 §8-1 · §8-2 (등록) 그대로.

  python eval/topics/score_methods.py --snapshot ../radar-snapshots/2026-W42 --run eval/runs/topics-w42 --gold eval/topics/gold-w42.json --out eval/runs/topics-w42/scores.json

방법 (풀 240건 안에서 union-find):
  v1          같은 링크 키 · 크로스포스트 (pipeline.topics — 지금 main)
  v1+name     + 이름 토큰 (build_pairs.names: 같은 이름 토큰이거나 한쪽 제목에 다른 쪽 이름)
  v1+emb@τ    + BGE-M3 제목 코사인 ≥ τ
  v1+name+emb@τ
지표: 정밀도 엄격(묶은 짝 중 같음) · 정밀도 느슨(같음+하위) · 재현율(같음 중 묶은 것). 전체와 「T8 묶음 뺌」 둘 다.
"""
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))

import build_pairs as bp  # noqa: E402
from pipeline import topics  # noqa: E402

TAUS = [round(0.60 + 0.05 * i, 2) for i in range(8)]
ADOPT = {"loose": 0.97, "strict": 0.90, "recall_gain": 0.10}


def components(n, edges):
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, j in edges:
        a, b = find(i), find(j)
        if a != b:
            parent[b] = a
    groups = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)
    pairs = set()
    for g in groups.values():
        for x in range(len(g)):
            for y in range(x + 1, len(g)):
                pairs.add((min(g[x], g[y]), max(g[x], g[y])))
    return pairs


def gold_sets(gold, idx):
    same_clusters = [[idx[x] for x in c if x in idx] for c in gold["same_clusters"]]
    cl = {}
    for k, c in enumerate(same_clusters):
        for i in c:
            cl[i] = k
    same = set()
    for c in same_clusters:
        for x in range(len(c)):
            for y in range(x + 1, len(c)):
                same.add((min(c[x], c[y]), max(c[x], c[y])))
    members = lambda i: same_clusters[cl[i]] if i in cl else [i]  # noqa: E731
    child = set()
    for r in gold["pairs"]:
        if r["label"] in ("child_a", "child_b"):
            for i in members(idx[r["a"]]):
                for j in members(idx[r["b"]]):
                    child.add((min(i, j), max(i, j)))
    t8 = next((set(c) for c in same_clusters if any("storytold" in gold_ids[i] for i in c)), set())
    return same, child - same, t8


def metrics(pred, same, child, drop=frozenset()):
    pred = {p for p in pred if not (p[0] in drop and p[1] in drop)}
    same = {p for p in same if not (p[0] in drop and p[1] in drop)}
    tp = len(pred & same)
    loose = len(pred & (same | child))
    n = len(pred)
    return {"pred": n, "same_hit": tp, "loose_hit": loose, "unrelated": n - loose,
            "strict": round(tp / n, 4) if n else None, "loose": round(loose / n, 4) if n else None,
            "recall": round(tp / len(same), 4) if same else None, "gold_same": len(same)}


def passes(m, base):
    if not m["pred"]:
        return False
    return (m["loose"] >= ADOPT["loose"] and m["strict"] >= ADOPT["strict"]
            and m["recall"] - base["recall"] >= ADOPT["recall_gain"] - 1e-9)


gold_ids = []


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--run", required=True)
    ap.add_argument("--gold", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--device", default=None)
    a = ap.parse_args(argv)

    items = topics.read_items(a.snapshot)
    pool = bp.pool_items(items)
    its = [it for it, _ in pool]
    n = len(its)
    gold_ids.extend(it["id"] for it in its)
    idx = {x: i for i, x in enumerate(gold_ids)}
    gold = json.loads(Path(a.gold).read_text(encoding="utf-8"))
    same, child, t8 = gold_sets(gold, idx)

    L = [set(topics.item_link_keys(it)[0]) for it in its]
    X = [topics.crosspost_key(it) for it in its]
    N = [bp.names(it) for it in its]
    S = [bp.squash(it.get("title")) for it in its]
    v1, name = [], []
    for i in range(n):
        for j in range(i + 1, n):
            if L[i] & L[j] or (X[i] and X[i] == X[j]):
                v1.append((i, j))
            if N[i] & N[j] or any(x in S[j] for x in N[i]) or any(x in S[i] for x in N[j]):
                name.append((i, j))
    ev, rev = bp.embed_titles([it.get("title") or "" for it in its], a.device)
    sims = ev @ ev.T

    def emb(t):
        return [(i, j) for i in range(n) for j in range(i + 1, n) if sims[i][j] >= t]

    rows = {}

    def run(label, edges):
        pred = components(n, edges)
        rows[label] = {"all": metrics(pred, same, child), "no_t8": metrics(pred, same, child, drop=t8),
                       "merged": sorted([gold_ids[i], gold_ids[j]] for i, j in pred)}

    run("v1", v1)
    run("v1+name", v1 + name)
    for t in TAUS:
        run(f"v1+emb@{t}", v1 + emb(t))
        run(f"v1+name+emb@{t}", v1 + name + emb(t))

    base_all, base_no = rows["v1"]["all"], rows["v1"]["no_t8"]
    decision = {}
    for fam in ("v1+name", "v1+emb", "v1+name+emb"):
        cands = [fam] if fam == "v1+name" else [f"{fam}@{t}" for t in TAUS]
        ok = [c for c in cands if passes(rows[c]["all"], base_all)]
        pick = max(ok, key=lambda c: (rows[c]["all"]["recall"], c)) if ok else None
        decision[fam] = {"pick": pick, "passes_all": bool(pick),
                         "passes_no_t8": bool(pick) and passes(rows[pick]["no_t8"], base_no)}
    out = {"meta": {"gold": a.gold, "pool": n, "embedding_revision": rev, "taus": TAUS, "adopt": ADOPT,
                    "gold_same_pairs": len(same), "gold_child_pairs": len(child), "t8_items": len(t8)},
           "rows": rows, "decision": decision}
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")

    def fmt(m):
        if not m["pred"]:
            return "묶음 0"
        return (f"묶음 {m['pred']:>4} · 무관 {m['unrelated']:>3} · 느슨 {m['loose']:.3f} · 엄격 {m['strict']:.3f} · "
                f"재현 {m['recall']:.3f} ({m['same_hit']}/{m['gold_same']})")
    print(f"정답 같음 {len(same)}짝 (T8 묶음 {len(t8)}개 = {len(t8) * (len(t8) - 1) // 2}짝) · 하위 {len(child)}짝")
    for k, r in rows.items():
        print(f"{k:<20} 전체 {fmt(r['all'])}\n{'':<20} T8뺌 {fmt(r['no_t8'])}")
    print(json.dumps(decision, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
