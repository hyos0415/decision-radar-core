"""화제 계층(부모 찾기) 채점 — HYO-135. 정답지의 판정된 짝마다 hier_methods.parent를 대 본다.

  python eval/topics/score_hier.py --snapshot ../radar-snapshots/2026-W42 --gold eval/topics/gold-w42.json [--rules P1,P2] [--show]

하위로 낸 짝을 정답과 대조한다: 맞음(하위 · 방향까지) · 방향 틀림 · 정답 같음 · 정답 다름. 놓친 하위 짝도 센다.
정답지는 후보 신호로 고른 짝만 판정했으므로 판정 밖 짝은 채점하지 않는다 — 판정 밖에서 하위로 낸 수는 진단으로만 낸다.
"""
import argparse
import itertools
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))

import hier_methods as hm  # noqa: E402
from pipeline import topics  # noqa: E402


def score(items, gold, rules):
    out = {"correct": [], "wrong_dir": [], "gold_same": [], "gold_diff": [], "missed": [], "gold_child": 0}
    for p in gold["pairs"]:
        a, b = items.get(p["a"]), items.get(p["b"])
        if a is None or b is None:
            continue
        pred = hm.parent(a, b, rules)
        lab = p["label"]
        child = lab in ("child_a", "child_b")
        out["gold_child"] += child
        row = (lab, pred, a.get("title", "")[:60], b.get("title", "")[:60])
        if pred is None:
            if child:
                out["missed"].append(row)
        elif child:
            out["correct" if (pred == "a") == (lab == "child_a") else "wrong_dir"].append(row)
        else:
            out["gold_same" if lab == "same" else "gold_diff"].append(row)
    fired = len(out["correct"]) + len(out["wrong_dir"]) + len(out["gold_same"]) + len(out["gold_diff"])
    out["fired"] = fired
    out["precision"] = len(out["correct"]) / fired if fired else None
    out["recall"] = len(out["correct"]) / out["gold_child"] if out["gold_child"] else None
    return out


def unjudged_fires(items, gold, rules, pool_ids):
    judged = {frozenset((p["a"], p["b"])) for p in gold["pairs"]}
    fires = []
    for x, y in itertools.combinations(pool_ids, 2):
        if frozenset((x, y)) in judged:
            continue
        r = hm.parent(items[x], items[y], rules)
        if r:
            p, c = (items[x], items[y]) if r == "a" else (items[y], items[x])
            fires.append((p.get("title", "")[:55], c.get("title", "")[:55]))
    return fires


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--gold", required=True)
    ap.add_argument("--rules", default="P1,P2")
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args(argv)
    rules = tuple(a.rules.split(","))
    items = {it["id"]: it for it in topics.read_items(a.snapshot)}
    gold = json.loads(Path(a.gold).read_text(encoding="utf-8"))
    s = score(items, gold, rules)
    pool = sorted({x for p in gold["pairs"] for x in (p["a"], p["b"]) if x in items})
    fires = unjudged_fires(items, gold, rules, pool)
    pr = f"{s['precision']:.3f}" if s["precision"] is not None else "—"
    rc = f"{s['recall']:.3f}" if s["recall"] is not None else "—"
    print(f"{Path(a.gold).name} · 규칙 {'+'.join(rules)} · 하위로 낸 짝 {s['fired']} → 맞음 {len(s['correct'])} · 방향 틀림 {len(s['wrong_dir'])} · "
          f"정답 같음 {len(s['gold_same'])} · 정답 다름 {len(s['gold_diff'])} | 정밀도 {pr} · 재현율 {rc} (정답 하위 {s['gold_child']}) | 판정 밖 발화 {len(fires)}")
    if a.show:
        for k in ("wrong_dir", "gold_same", "gold_diff", "missed"):
            for row in s[k]:
                print(f"  {k:9} 정답 {row[0]:7} 예측 {row[1]} | {row[2]} || {row[3]}")
        for f in fires:
            print(f"  판정밖    P: {f[0]} || C: {f[1]}")
    return s


if __name__ == "__main__":
    main()
