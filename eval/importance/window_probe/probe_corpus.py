"""HYO-138 후보 규칙 — 라벨 없는 주간 수집(W41~W43) 전체에서 창 밖에서 옮겨질 줄을 전부 꺼낸다 (소음 확인, 모델 호출 없음)."""
import json, re, sys, collections
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from probe import CANDS, TEXT_MAX  # noqa: E402

snap = Path(__file__).resolve().parents[4] / "radar-snapshots"
cnt = collections.Counter()
for wk in ["2026-W41", "2026-W42", "2026-W43"]:
    for line in open(snap / wk / "items.jsonl", encoding="utf-8"):
        it = json.loads(line)
        t = it.get("text") or ""
        cnt[wk, "items"] += 1
        if len(t) <= TEXT_MAX:
            continue
        cnt[wk, "long"] += 1
        pos = 0
        for l in t.splitlines(keepends=True):
            start, pos = pos, pos + len(l)
            if start < TEXT_MAX:
                continue
            for k, rx in CANDS.items():
                if rx.search(l):
                    print(f"{k:6} {wk} | {it.get('source','')[:8]:8} | {(it.get('title') or '')[:45]:45} | {l.strip()[:140]}")
print(dict(cnt))
