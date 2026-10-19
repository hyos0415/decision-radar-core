"""v2 매개변수 고르기 — dev(W42) 정답지로만. 결과를 보고 고른 값은 test(W41) 전에 등록한다.

  python eval/topics/tune_v2.py --snapshot ../radar-snapshots/2026-W42 --gold eval/topics/gold-w42.json
"""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))

import build_pairs as bp  # noqa: E402
import score_methods as sm  # noqa: E402
import v2_methods as v2  # noqa: E402
from pipeline import topics  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--gold", required=True)
    a = ap.parse_args(argv)
    import json
    items = topics.read_items(a.snapshot)
    its = [it for it, _ in bp.pool_items(items)]
    n = len(its)
    sm.gold_ids.clear()
    sm.gold_ids.extend(it["id"] for it in its)
    idx = {x: i for i, x in enumerate(sm.gold_ids)}
    same, child, t8 = sm.gold_sets(json.loads(Path(a.gold).read_text(encoding="utf-8")), idx)
    L = [set(topics.item_link_keys(it)[0]) for it in its]
    X = [topics.crosspost_key(it) for it in its]
    v1 = [(i, j) for i in range(n) for j in range(i + 1, n) if L[i] & L[j] or (X[i] and X[i] == X[j])]

    def show(label, edges):
        pred = sm.components(n, edges)
        al, no = sm.metrics(pred, same, child), sm.metrics(pred, same, child, drop=t8)
        print(f"{label:<26} 묶음 {al['pred']:>4} 무관 {al['unrelated']:>3} 느슨 {al['loose']} 엄격 {al['strict']} "
              f"재현 {al['recall']} | T8뺌 묶음 {no['pred']:>4} 무관 {no['unrelated']:>3} 재현 {no['recall']}")

    show("v1", v1)
    for K in (2, 3, 4, 5, 6, 8):
        show(f"v1+name_rare K={K}", v1 + v2.name_rare_edges(its, K))
    for D in (3, 7, 14):
        for A in (4, 5, 6):
            show(f"v1+owner D={D} A={A}", v1 + v2.owner_series_edges(its, D, A))
    return 0


if __name__ == "__main__":
    sys.exit(main())
