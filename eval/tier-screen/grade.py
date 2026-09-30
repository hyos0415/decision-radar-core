"""tier-screen 채점 — 결정론 (집합 일치). 정답지: gold-draft.md 표.

실행: python eval/tier-screen/grade.py <모델 출력 파일>
"""

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ORDER = [f"L{i}" for i in range(9)]


def gold():
    rows = re.findall(r"^\| (T\d\d) \| .*? \| (L\d) \|", open(os.path.join(HERE, "gold-draft.md"), encoding="utf-8").read(), re.M)
    return {i: {t} for i, t in rows}


def parse(path):
    text = open(path, encoding="utf-8").read()
    m = re.search(r"\[\s*\{.*\}\s*\]", text, re.S)  # 앞뒤 문장이 붙어도 배열만
    return {x["id"]: x for x in json.loads(m.group(0))}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    g, out = gold(), parse(sys.argv[1])
    ok, wrong, unsure, missing, over, under = [], [], [], [], [], []
    for i, want in sorted(g.items()):
        x = out.get(i)
        if x is None:
            missing.append(i)
            continue
        if x.get("unsure"):
            unsure.append(i)
            continue
        got = set(x.get("tiers") or [])
        if got == want:
            ok.append(i)
            continue
        wrong.append((i, sorted(want), sorted(got), x.get("reason")))
        w, gt = ORDER.index(next(iter(want))), min((ORDER.index(t) for t in got if t in ORDER), default=None)
        if gt is not None:
            (over if gt < w else under).append(i)  # 숫자가 작을수록 무거운 등급 → 과대 등급 = 소음 쪽
    n = len(g)
    print(json.dumps({"n": n, "correct": len(ok), "wrong": len(wrong), "unsure": len(unsure), "missing": len(missing),
                      "over_tier": over, "under_tier": under,
                      "pass": len(ok) >= 27 and len(unsure) <= 0.3 * n,
                      "wrong_detail": wrong, "unsure_ids": unsure, "missing_ids": missing}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
