"""벤더 changelog 페이지 diff → 날짜 블록마다 항목 하나 (HYO-168 가).

수집기는 바뀐 벤더 페이지마다 page-diff 항목 하나를 만든다(`collector/snapshot.py`). changelog는 한 주에 여러 소식이
더해지는데 한 항목으로 묶이면 관문이 그중 한 줄로만 판정한다 — 2026-W44 OpenAI changelog diff(더해진 줄 34)에
「Oct 26 · Released GPT-6.1 Terra」와 「Oct 22 · gpt-6-luna Decisions API」가 함께 있었고, 관문은 「luna 가격·한도 변경」으로
남겨 Terra는 아무도 보지 않았다.

얼린 코퍼스는 그대로 두고 읽을 때 쪼갠다 — 지난 주 스냅숏에도 같은 코드가 바로 걸린다 (재생 HYO-164).
- 대상: URL에 changelog · release-notes가 든 페이지만. deprecations 표는 행마다 날짜가 있어 쪼개지 않는다
- 날짜 줄(「Oct 26」 · 「October 23, 2026」 · 「2026-10-26」, 앞의 # · 뒤의 아이콘 글자는 무시)에서 블록을 나눈다.
  내용 없는 블록(목차의 날짜 줄)은 버리고, 같은 날짜 블록은 합친다
- 블록 항목: id · url에 `#<날짜>`를 붙인다(detect 입력의 index가 url로 찾는다). 첫 날짜 앞의 줄(메뉴 등)과 빠진 줄은
  원래 항목(원래 id)에 남긴다 — 둘 다 없으면 원래 항목은 없어진다
- 날짜 줄이 없으면 그대로 둔다
"""
import re

SPLIT_URL = re.compile(r"(?i)changelog|release-notes|release_notes")
_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
DATE_LINE = re.compile(r"(?i)^(?:" + _MONTH + r"\s+\d{1,2}(?:st|nd|rd|th)?(?:,\s*\d{4})?|\d{4}-\d{2}-\d{2})$")
TITLE_CHARS = 140


def date_of(line):
    """날짜만 있는 줄이면 그 날짜 문구, 아니면 None. 앞의 #과 공백, 끝의 아이콘 글자(사용자 영역 U+E000~U+F8FF)는 뗀다."""
    s = re.sub(r"[-]", "", line).strip().lstrip("#").strip()
    return s if DATE_LINE.match(s) else None


def slug(date):
    return re.sub(r"[^0-9a-z]+", "-", date.lower()).strip("-")


def _lines(v):
    if not v:
        return []
    return v.splitlines() if isinstance(v, str) else [str(x) for x in v]


def split_item(item):
    """page-diff 항목 하나 → 항목 목록. 쪼갤 게 없으면 [item]."""
    meta = item.get("meta") or {}
    if meta.get("kind") != "page-diff" or not SPLIT_URL.search(item.get("url") or ""):
        return [item]
    head, blocks, cur = [], {}, None
    for line in _lines(item.get("text")):
        d = date_of(line)
        if d:
            cur = d
            blocks.setdefault(cur, [])
        elif cur is None:
            head.append(line)
        elif line.strip():
            blocks[cur].append(line)
    blocks = {d: ls for d, ls in blocks.items() if ls}
    if not blocks:
        return [item]
    vendor = meta.get("vendor", "?")
    path = re.sub(r"^https?://", "", item.get("url") or "").rstrip("/")
    out = []
    if any(l.strip() for l in head) or _lines(meta.get("removed")):
        out.append(dict(item, text="\n".join(head)))
    for d, ls in blocks.items():
        gist = " · ".join(l.strip() for l in ls[:4])
        title = f"[{vendor}] {path} {d}: {gist}"
        out.append(dict(item, id=f"{item['id']}#{slug(d)}", url=f"{item['url']}#{slug(d)}",
                        title=title if len(title) <= TITLE_CHARS else title[:TITLE_CHARS].rstrip() + "…",
                        text="\n".join(ls), signals={"added_lines": len(ls), "removed_lines": 0},
                        meta=dict(meta, removed="", entry_date=d, parent=item["id"])))
    return out


def expand(items):
    """항목 목록에서 changelog diff만 쪼갠다. 순서는 원래 자리에 블록을 끼워 넣는다."""
    out = []
    for it in items:
        out.extend(split_item(it))
    return out
