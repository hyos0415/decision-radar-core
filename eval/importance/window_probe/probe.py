"""HYO-138 후보 규칙 탐침 — 창(3,000자) 밖에서 후보 규칙이 앞으로 옮길 줄을 전부 꺼낸다. 모델 호출 없음.
정답지 security 항목마다 근거 줄이 지금 창 안에 드는지도 센다."""
import json, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from pipeline.importance.prompts import window_text, TEXT_MAX

SEC_ANY = re.compile(r"(?i)security|CVE-\d|GHSA-|vulnerab")
CANDS = {
    "tag": re.compile(r"^\s*[*-].*\[security\]", re.I),                                     # 글머리 + [security] 꼬리표
    "fixes": re.compile(r"^\s*[*-].*\bsecurity\s+(?:fix(?:es)?|patch(?:es)?)\b", re.I),     # 글머리 + security fix(es)/patch
    "update": re.compile(r"^\s*[*-].*\bsecurity\s+(?:update|upgrade)s?\b", re.I),
}
golds = {g: json.load(open(f"eval/importance/gold-{g}.json", encoding="utf-8"))["items"] for g in ["w41", "w42", "backfill"]}
moved = {k: [] for k in CANDS}
for g, items in golds.items():
    for it in items:
        t = it.get("text") or ""
        if len(t) <= TEXT_MAX:
            continue
        pos = 0
        for line in t.splitlines(keepends=True):
            start, pos = pos, pos + len(line)
            if start < TEXT_MAX:
                continue
            for k, rx in CANDS.items():
                if rx.search(line):
                    moved[k].append((g, "security" in it["gold_changes"], it["title"][:50], line.strip()[:150]))
for k, rows in moved.items():
    print(f"\n### {k}: 옮길 줄 {len(rows)} · 정답 security 항목 {sum(r[1] for r in rows)} · 아닌 항목 {sum(not r[1] for r in rows)}")
    for r in rows:
        print("  ", "S" if r[1] else ".", r[0], "|", r[2], "|", r[3])
print("\n### 정답 security 항목 — 근거 후보 줄이 지금 창 안에 있나")
for g, items in golds.items():
    for it in items:
        if "security" not in it["gold_changes"]:
            continue
        t = it.get("text") or ""; w = window_text(t)
        lines = [l.strip() for l in t.splitlines() if SEC_ANY.search(l)]
        inwin = [l for l in lines if l[:80] in w]
        print(f"  {g:8} {'창 안' if inwin else ('창 밖' if lines else '줄 없음'):5} {it.get('by','human'):15} {it['title'][:55]}")
