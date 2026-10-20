"""두 정답지 초안(모델 A · B)을 견준다 — 변화 종류 집합이나 남김이 다른 항목만 한국어 표로 낸다 (HYO-131).

  python eval/importance/compare_drafts.py <A gold-draft.json> <B gold-draft.json> [--out review-diff.md]

같은 표본(같은 seed)이어야 견줄 수 있다 — 한쪽에만 있는 id는 따로 센다. 둘 다 Claude 계열이 달았으므로
일치는 「정답」의 증거가 아니라 「두 모델이 같은 말을 했다」는 뜻일 뿐이다. 사람 검토를 줄이는 우선순위 표로 쓴다.
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path


def load(path):
    g = json.loads(Path(path).read_text(encoding="utf-8"))
    return g, {it["id"]: it for it in g["items"]}


def esc(s):
    return (s or "").replace("|", "\\|").replace("\n", " ")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--out", help="표를 저장할 .md (기본: 표준출력)")
    args = ap.parse_args(argv)
    ga, A = load(args.a)
    gb, B = load(args.b)
    na, nb = ga.get("draft_model", "A"), gb.get("draft_model", "B")
    both = sorted(set(A) & set(B))
    only_a, only_b = sorted(set(A) - set(B)), sorted(set(B) - set(A))

    diff_ch, diff_keep = [], []
    cnt = Counter()
    per_label = {}
    for i in both:
        a, b = A[i], B[i]
        sa, sb = set(a["gold_changes"]), set(b["gold_changes"])
        same_ch, same_keep = sa == sb, a["gold_keep"] == b["gold_keep"]
        cnt["변화 집합 일치"] += same_ch
        cnt["남김 일치"] += same_keep
        cnt["둘 다 일치"] += same_ch and same_keep
        if not same_ch or not same_keep:
            diff_ch.append(i)
        if not same_keep:
            diff_keep.append(i)
        for c in sa | sb:
            p = per_label.setdefault(c, [0, 0, 0])       # [둘 다, A만, B만]
            p[0] += c in sa and c in sb
            p[1] += c in sa and c not in sb
            p[2] += c in sb and c not in sa

    lines = [f"# 정답지 초안 비교 — {na} (A) vs {nb} (B)", "",
             "> 둘 다 Claude 초안이다. 일치 = 정답이 아니라 두 모델의 같은 답. 다른 항목이 사람이 먼저 볼 곳이다.", "",
             f"- 공통 항목 {len(both)}건 (A만 {len(only_a)} · B만 {len(only_b)})",
             f"- 변화 집합 일치 {cnt['변화 집합 일치']}/{len(both)} · 남김 일치 {cnt['남김 일치']}/{len(both)} · 둘 다 일치 {cnt['둘 다 일치']}/{len(both)}",
             f"- 남김이 다른 항목 {len(diff_keep)}건 · 변화나 남김이 다른 항목 {len(diff_ch)}건",
             "", "| 변화 종류 | 둘 다 | A만 | B만 |", "|---|---|---|---|"]
    for c, (x, y, z) in sorted(per_label.items(), key=lambda kv: -(kv[1][1] + kv[1][2])):
        lines.append(f"| {c} | {x} | {y} | {z} |")
    lines += ["", "## 다른 항목 (남김이 다른 것 먼저)", "",
              f"| id | 기술 | A 변화 | B 변화 | A 남김 | B 남김 | A 요약 | B 요약 |", "|---|---|---|---|---|---|---|---|"]
    order = sorted(diff_ch, key=lambda i: (A[i]["gold_keep"] == B[i]["gold_keep"], i))
    for i in order:
        a, b = A[i], B[i]
        k = lambda x: "남김" if x["gold_keep"] else "버림"  # noqa: E731
        lines.append(f"| `{esc(i)}` | {esc(a['technology'])} | {', '.join(a['gold_changes']) or '(없음)'} | {', '.join(b['gold_changes']) or '(없음)'} | "
                     f"{k(a)} | {k(b)} | {esc(a['ko_summary'])} | {esc(b['ko_summary'])} |")
    text = "\n".join(lines) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8", newline="\n")
    else:
        sys.stdout.write(text)
    print(f"공통 {len(both)} · 변화 일치 {cnt['변화 집합 일치']} · 남김 일치 {cnt['남김 일치']} · 둘 다 {cnt['둘 다 일치']} · 남김 다름 {len(diff_keep)} · 표 {len(diff_ch)}행"
          + (f" → {args.out}" if args.out else ""), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
