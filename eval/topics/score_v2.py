"""화제 묶기 v2 채점 — W41 test, `v2-registration.md`의 채택 기준 셋 그대로.

  python eval/topics/score_v2.py --snapshot ../radar-snapshots/2026-W41 --gold eval/topics/gold-w41.json

1. v2 전체: 정밀도 느슨 ≥ 0.97 · 엄격 ≥ 0.90
2. owner 규칙이 v1에 더한 짝이 전부 정답 같음
3. 더한 짝이 하나 이상 (0이면 확인 불가 — 보류)
W41 판정은 66짝으로 줄였다 (묶는 짝은 다 들어 있다) — 재현율은 내지 않는다.
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))

import build_pairs as bp  # noqa: E402
import score_methods as sm  # noqa: E402
import v2_methods as v2  # noqa: E402
from pipeline import topics  # noqa: E402

D, A = 7, 5


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--gold", required=True)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    its = [it for it, _ in bp.pool_items(topics.read_items(a.snapshot))]
    n = len(its)
    sm.gold_ids.clear()
    sm.gold_ids.extend(it["id"] for it in its)
    idx = {x: i for i, x in enumerate(sm.gold_ids)}
    gold = json.loads(Path(a.gold).read_text(encoding="utf-8"))
    same, child, _ = sm.gold_sets(gold, idx)
    judged = {frozenset((idx[r["a"]], idx[r["b"]])) for r in gold["pairs"]}
    L = [set(topics.item_link_keys(it)[0]) for it in its]
    X = [topics.crosspost_key(it) for it in its]
    v1 = [(i, j) for i in range(n) for j in range(i + 1, n) if L[i] & L[j] or (X[i] and X[i] == X[j])]
    p1 = sm.components(n, v1)
    p2 = sm.components(n, v1 + v2.owner_series_edges(its, D, A))
    unjudged = [p for p in p2 if frozenset(p) not in judged and p not in same]
    m1, m2 = sm.metrics(p1, same, child), sm.metrics(p2, same, child)
    added = p2 - p1
    added_same = len(added & same)
    c1 = m2["pred"] > 0 and m2["loose"] >= 0.97 and m2["strict"] >= 0.90
    c2 = added_same == len(added)
    c3 = len(added) >= 1
    verdict = "채택" if (c1 and c2 and c3) else ("확인 불가 — 보류" if not c3 else "불채택")
    out = {"v1": m1, "v2": m2, "added": len(added), "added_same": added_same, "unjudged_merged": len(unjudged),
           "criteria": {"1_precision": c1, "2_added_all_same": c2, "3_added_at_least_one": c3}, "verdict": verdict,
           "added_pairs": sorted([sm.gold_ids[i], sm.gold_ids[j], ("same" if (i, j) in same else "child" if (i, j) in child else "diff")]
                                 for i, j in added),
           "v1_pairs": sorted([sm.gold_ids[i], sm.gold_ids[j], ("same" if (i, j) in same else "child" if (i, j) in child else "diff")]
                              for i, j in p1)}
    if a.out:
        Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    for k in ("v1", "v2"):
        m = out[k]
        print(f"{k}: 묶음 {m['pred']} · 무관 {m['unrelated']} · 느슨 {m['loose']} · 엄격 {m['strict']}")
    print(f"owner가 더한 짝 {len(added)} · 그중 같음 {added_same} · 판정 안 된 묶음 짝 {len(unjudged)}")
    print("기준", out["criteria"], "→", verdict)
    return 0


if __name__ == "__main__":
    sys.exit(main())
