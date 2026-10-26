"""비교용: 창 밖 줄 중 security·CVE·vulnerab 낱말이 든 줄 전부 (10/06에 기각된 「낱말」 규칙이 옮겼을 줄)."""
import json, re
from pathlib import Path
SEC = re.compile(r"(?i)security|CVE-\d|vulnerab")
snap = Path(__file__).resolve().parents[4] / "radar-snapshots"
n = 0
for wk in ["2026-W41", "2026-W42", "2026-W43"]:
    for line in open(snap / wk / "items.jsonl", encoding="utf-8"):
        it = json.loads(line); t = it.get("text") or ""; pos = 0
        for l in t.splitlines(keepends=True):
            start, pos = pos, pos + len(l)
            if start >= 3000 and SEC.search(l):
                n += 1
                print(f"{wk} | {(it.get('title') or '')[:45]:45} | {l.strip()[:120]}")
print("total", n)
