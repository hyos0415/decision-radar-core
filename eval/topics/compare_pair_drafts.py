"""두 모델의 짝 초안 비교 — 사람이 볼 짝을 고른다 (HYO-135).

  python eval/topics/compare_pair_drafts.py --run <eval/runs/topics-W..> [--first drafts.json] [--second drafts.opus-5-5.json]

사람이 볼 짝 (`review-queue.json`):
  disagree   두 초안 라벨이 다름
  low        둘이 같지만 한쪽이라도 확신 낮음
  agree-pos  둘 다 같음·하위 (같은 회사 두 모델이 같이 틀리는 것 — 양성은 다 본다)
  agree-neg-sample  둘 다 「확신 있는 다름」인 짝 중 무작위 SAMPLE개 (같이 놓친 같음 비율 재기)
두 번째 초안이 일부 짝에만 있으면(--second-opinion) 그 밖의 짝은 첫 초안만으로 「다름」 — 그 비율도 낸다.
"""
import argparse
import json
import random
from collections import Counter
from pathlib import Path

SAMPLE = 30
SEED = 20261020


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--first", default="drafts.json")
    ap.add_argument("--second", default="drafts.opus-5-5.json")
    a = ap.parse_args(argv)
    run = Path(a.run)
    f = json.loads((run / a.first).read_text(encoding="utf-8"))["pairs"]
    s = json.loads((run / a.second).read_text(encoding="utf-8"))["pairs"]
    queue, kinds, agree_neg = [], Counter(), []
    for pid, x in f.items():
        y = s.get(pid)
        if y is None or "label" not in x or "label" not in y:
            kinds["first-only" if y is None else "error"] += 1
            continue
        lx, ly = x["label"], y["label"]
        if lx != ly:
            kind = "disagree"
        elif "low" in (x.get("confidence"), y.get("confidence")):
            kind = "low"
        elif lx != "diff":
            kind = "agree-pos"
        else:
            agree_neg.append(pid)
            kinds["agree-neg"] += 1
            continue
        kinds[kind] += 1
        queue.append({"pair": pid, "kind": kind, "a": x["a"], "b": x["b"], "first": lx, "second": ly,
                      "first_reason": x.get("reason"), "second_reason": y.get("reason")})
    for pid in random.Random(SEED).sample(sorted(agree_neg), min(SAMPLE, len(agree_neg))):
        x = f[pid]
        queue.append({"pair": pid, "kind": "agree-neg-sample", "a": x["a"], "b": x["b"], "first": "diff", "second": "diff",
                      "first_reason": x.get("reason"), "second_reason": s[pid].get("reason")})
        kinds["agree-neg-sample"] += 1
    out = run / "review-queue.json"
    out.write_text(json.dumps({"first": a.first, "second": a.second, "counts": dict(kinds), "queue": queue},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    both = sum(v for k, v in kinds.items() if k in ("disagree", "low", "agree-pos", "agree-neg"))
    print(f"두 초안이 다 있는 짝 {both} · 라벨 일치 {both - kinds['disagree']} · {dict(kinds)} → 사람이 볼 짝 {len(queue)} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
