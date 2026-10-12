"""chunk-policy@v1 — 파일 유형 → 규칙 표(retrieval-arena §2)를 코드로.

- ADR = 파일 하나. 상한을 넘으면 frontmatter 조각 + 헤딩 조각, 각 헤딩 조각 앞에 frontmatter 요약
- 문서(md·html·릴리스 텍스트) · CLAUDE.md 스택 절 = H2/H3 헤딩. 넘으면 문단 경계 → (표는 행 단위 + 머리행 반복) → 줄 → 고정 길이
- manifest = 파일 하나. 상한을 넘으면 줄 경계로 자른다(상한이 우선 — 보고 사항)
- 기타 = 고정 길이
- 모든 조각 앞에 위치 경로("문서 제목 > H2 > H3"), 메타데이터 상속, 조각 id = `<doc_id>#<n>`
- 상한(기본 512토큰)은 **정책 파일의 두 토크나이저 모두에서**, 특수 토큰 포함으로 지킨다

토큰 수는 `counter.count(texts, special=False)`(arena/tokens.py)로 센다. 묶을 때는 조각별 합으로 어림하고,
만든 뒤에 이어 붙인 실제 텍스트를 다시 세어 검증한다 — 넘으면 예산을 줄여 다시 묶는다.
"""
import json
import math
import re
from collections import Counter
from pathlib import Path

from .corpus import split_frontmatter

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
FACTORS = (1.0, 0.9, 0.8, 0.65, 0.5, 0.35, 0.2)
SEP_COST = 2          # 조각 사이 구분자 몫의 어림(토큰)
SAFETY = 4


def load_policy(path):
    p = json.loads(Path(path).read_text(encoding="utf-8"))
    for key in ("version", "max_tokens", "tokenizers"):
        if key not in p:
            raise ValueError(f"청크 정책에 {key!r}가 없다: {path}")
    return p


def _le(a, b):
    return all(x <= y for x, y in zip(a, b))


def _add(a, b):
    return tuple(x + y for x, y in zip(a, b))


# --- 텍스트 구조 읽기 ---------------------------------------------------------------------------------------------

def split_sections(text, levels=(2, 3)):
    """헤딩(H2/H3)으로 자른다. -> [([h2, h3 중 있는 것], 본문)] — 헤딩 줄은 본문에서 빼고 경로로 올린다.
    코드 펜스 안의 `#`는 헤딩이 아니다. 첫 헤딩 앞의 글은 경로가 비어 있는 첫 절이다."""
    sections = [([], [])]
    h2 = h3 = None
    in_fence = False
    for ln in text.split("\n"):
        if _FENCE.match(ln):
            in_fence = not in_fence
        m = None if in_fence else _HEADING.match(ln)
        if m and len(m.group(1)) in levels:
            lvl, name = len(m.group(1)), m.group(2).strip()
            if lvl == 2:
                h2, h3 = name, None
            else:
                h3 = name
            sections.append(([x for x in (h2, h3) if x], []))
            continue
        sections[-1][1].append(ln)
    return [(path, "\n".join(body).strip("\n")) for path, body in sections]


def split_blocks(text):
    """문단(빈 줄 경계) · 표(`|`로 시작하는 연속 줄) · 코드 펜스(통째로)로 나눈다."""
    blocks, cur, in_fence = [], [], False

    def flush():
        if cur:
            blocks.append("\n".join(cur))
            cur.clear()

    for ln in text.split("\n"):
        if in_fence:
            cur.append(ln)
            if _FENCE.match(ln):
                in_fence = False
                flush()
            continue
        if _FENCE.match(ln):
            flush()
            cur.append(ln)
            in_fence = True
            continue
        if not ln.strip():
            flush()
            continue
        is_row = ln.lstrip().startswith("|")
        if cur and (cur[-1].lstrip().startswith("|")) != is_row:
            flush()
        cur.append(ln)
    flush()
    return blocks


def is_table(block):
    lines = block.split("\n")
    return len(lines) >= 2 and all(ln.lstrip().startswith("|") for ln in lines)


class _Unit:
    __slots__ = ("text", "cost", "sep", "solo")

    def __init__(self, text, cost, sep, solo=False):
        self.text, self.cost, self.sep, self.solo = text, cost, sep, solo


# --- 청커 --------------------------------------------------------------------------------------------------------

class Chunker:
    def __init__(self, policy, counter):
        self.policy = policy
        self.cap = int(policy["max_tokens"])
        self.counter = counter
        if tuple(counter.names) != tuple(policy["tokenizers"]):
            raise ValueError(f"카운터 토크나이저 {counter.names} ≠ 정책 {policy['tokenizers']}")
        self.overhead = tuple(counter.overhead)
        self.levels = tuple(policy.get("split_heading_levels", (2, 3)))
        self.header_rows = int(policy.get("table_header_rows", 2))

    # -- 계량 --
    def _count(self, texts, special=False):
        return self.counter.count(texts, special=special)

    def full_cost(self, text):
        return self._count([text], special=True)[0]

    def _fits(self, text):
        return all(c <= self.cap for c in self.full_cost(text))

    @staticmethod
    def _render(prefix, body):
        head = "\n".join(prefix)
        if not body:
            return head
        return head + "\n" + body if head else body

    # -- 쪼개기 --
    def _fixed_windows(self, text, budget):
        """공백 단위로 예산까지 채워 자른다. 공백 없는 긴 토막(URL·해시)은 글자 단위로 다시 자른다."""
        words = text.split(" ")
        costs = self._count(words)
        out, cur, cur_cost = [], [], tuple(0 for _ in budget)
        for w, c in zip(words, costs):
            if not _le(c, budget):
                if cur:
                    out.append(" ".join(cur))
                    cur, cur_cost = [], tuple(0 for _ in budget)
                out += self._slice_chars(w, budget)
                continue
            add = _add(c, tuple(1 for _ in budget)) if cur else c
            if cur and not _le(_add(cur_cost, add), budget):
                out.append(" ".join(cur))
                cur, cur_cost, add = [], tuple(0 for _ in budget), c
            cur.append(w)
            cur_cost = _add(cur_cost, add)
        if cur:
            out.append(" ".join(cur))
        return [_Unit(t, None, "", solo=True) for t in out if t.strip()]

    def _slice_chars(self, word, budget):
        cost = self._count([word])[0]
        if _le(cost, budget) or len(word) <= 1:
            return [word]
        ratio = max(c / b for c, b in zip(cost, budget))
        n = max(2, math.ceil(ratio) + 1)
        step = max(1, math.ceil(len(word) / n))
        out = []
        for i in range(0, len(word), step):
            out += self._slice_chars(word[i:i + step], budget)
        return out

    def _table_pieces(self, block, budget):
        """표는 행 단위로 자르고 머리행(헤더 + 구분선)을 조각마다 반복한다."""
        lines = block.split("\n")
        head = lines[:self.header_rows] if len(lines) > self.header_rows and _TABLE_SEP.match(lines[1]) else []
        rows = lines[len(head):]
        head_text = "\n".join(head)
        head_cost = self._count([head_text])[0] if head else tuple(0 for _ in budget)
        room = tuple(b - h - SEP_COST for b, h in zip(budget, head_cost))
        if min(room) < 4:
            raise ValueError("표 머리행이 예산을 거의 다 쓴다 — 상한을 늘리거나 표를 고쳐야 한다")
        row_costs = self._count(rows)
        pieces, cur, cur_cost = [], [], tuple(0 for _ in budget)

        def emit():
            if cur:
                pieces.append("\n".join(([head_text] if head else []) + cur))

        for row, rc in zip(rows, row_costs):
            if not _le(rc, room):                       # 한 행이 통째로 예산을 넘는다 → 행 안을 고정 길이로
                emit()
                cur, cur_cost = [], tuple(0 for _ in budget)
                for u in self._fixed_windows(row, room):
                    pieces.append("\n".join(([head_text] if head else []) + [u.text]))
                continue
            add = _add(rc, tuple(1 for _ in budget))
            if cur and not _le(_add(cur_cost, add), room):
                emit()
                cur, cur_cost = [], tuple(0 for _ in budget)
            cur.append(row)
            cur_cost = _add(cur_cost, add)
        emit()
        return [_Unit(t, None, "", solo=True) for t in pieces]

    def _expand(self, block, budget):
        if is_table(block):
            return self._table_pieces(block, budget)
        lines = block.split("\n")
        if len(lines) > 1:
            units = []
            for i, (ln, c) in enumerate(zip(lines, self._count(lines))):
                sep = "\n"
                if _le(c, budget):
                    units.append(_Unit(ln, c, sep))
                else:
                    units += self._fixed_windows(ln, budget)
            return units
        return self._fixed_windows(block, budget)

    def _units(self, body, budget):
        units = []
        blocks = split_blocks(body)
        for block, cost in zip(blocks, self._count(blocks)):
            if _le(cost, budget):
                units.append(_Unit(block, cost, "\n\n"))
            else:
                sub = self._expand(block, budget)
                if sub and not sub[0].solo:
                    sub[0].sep = "\n\n"
                units += sub
        # 비용을 못 구한(= solo) 조각은 이미 예산 안이다
        return units

    @staticmethod
    def _greedy(units, budget):
        pieces, cur, cur_cost = [], [], tuple(0 for _ in budget)

        def flush():
            if cur:
                pieces.append(cur[0].text + "".join(u.sep + u.text for u in cur[1:]))

        sep_cost = tuple(SEP_COST for _ in budget)
        for u in units:
            if u.solo:
                flush()
                cur, cur_cost = [], tuple(0 for _ in budget)
                pieces.append(u.text)
                continue
            add = _add(u.cost, sep_cost) if cur else u.cost
            if cur and not _le(_add(cur_cost, add), budget):
                flush()
                cur, cur_cost, add = [], tuple(0 for _ in budget), u.cost
            cur.append(u)
            cur_cost = _add(cur_cost, add)
        flush()
        return pieces

    def split_body(self, prefix, body, fixed=False):
        """-> (본문 조각들, 상한 때문에 잘렸나). prefix(위치 경로 등)는 조각마다 앞에 붙을 줄들이다."""
        if self._fits(self._render(prefix, body)):
            return [body], False
        head_cost = self._count(["\n".join(prefix) + "\n"])[0]
        for f in FACTORS:
            budget = tuple(int((self.cap - ov - hc - SAFETY) * f) for ov, hc in zip(self.overhead, head_cost))
            if min(budget) < 8:
                raise ValueError(f"위치 경로·요약이 상한을 거의 다 쓴다: {prefix!r}")
            units = self._fixed_windows(body.replace("\n", " \n "), budget) if fixed else self._units(body, budget)
            pieces = self._greedy(units, budget)
            if fixed:
                pieces = [p.replace(" \n ", "\n") for p in pieces]
            if all(self._fits(self._render(prefix, p)) for p in pieces):
                return pieces, True
        raise RuntimeError(f"상한 {self.cap}토큰 안으로 못 자른다: {prefix!r}")

    # -- 문서 하나 --
    @staticmethod
    def kind_of(doc):
        if doc["corpus"] == "c-adr":
            return "adr"
        if doc["corpus"] == "c-code":
            return "claude_md" if doc["doc_id"].endswith("CLAUDE.md#stack") else "manifest"
        if doc["corpus"] == "c-doc":
            return "doc"
        return "other"

    @staticmethod
    def _only_title(body):
        """제목(H1) 줄뿐인 본문 — 위치 경로가 이미 말해 주는 내용이라 조각을 만들지 않는다."""
        lines = [ln for ln in body.split("\n") if ln.strip()]
        return all(re.match(r"^#\s+\S", ln) for ln in lines)

    def chunk_document(self, doc):
        kind = self.kind_of(doc)
        title = doc["title"]
        parts = []        # (prefix 줄들, 위치 경로, 본문 조각들, 잘림)

        def add(prefix, path, body, fixed=False):
            pieces, cut = self.split_body(prefix, body, fixed=fixed)
            for p in pieces:
                parts.append((prefix, path, p, cut))

        text = doc["text"].strip("\n")
        if kind == "adr":
            if self._fits(self._render([title], text)):
                add([title], title, text)
            else:
                fm, body = split_frontmatter(text)
                summary = self._adr_summary(doc)
                if fm:
                    path = f"{title} > frontmatter"
                    add([path], path, "\n".join(fm))
                for sec_path, sec_body in split_sections(body or "", self.levels):
                    if not sec_body.strip() or self._only_title(sec_body):
                        continue
                    path = " > ".join([title] + sec_path)
                    add([summary, path], path, sec_body)
        elif kind in ("doc", "claude_md"):
            secs = split_sections(text, self.levels)
            for sec_path, sec_body in secs:
                if not sec_body.strip() or self._only_title(sec_body):
                    continue
                path = " > ".join([title] + sec_path)
                add([path], path, sec_body)
            if not parts:                                   # 본문이 비었다 — 제목만 가진 조각 하나
                parts.append(([title], title, "", False))
        elif kind == "manifest":
            add([title], title, text)
        else:
            add([title], title, text, fixed=True)
        if not parts:
            parts.append(([title], title, "", False))

        chunks = []
        for n, (prefix, path, body, cut) in enumerate(parts):
            rendered = self._render(prefix, body)
            cost = self.full_cost(rendered)
            chunks.append({
                "chunk_id": f"{doc['doc_id']}#{n}", "doc_id": doc["doc_id"], "corpus": doc["corpus"],
                "source_type": doc["source_type"], "title": title, "path": path, "text": rendered,
                "meta": dict(doc["meta"]), "kind": kind, "cut": cut,
                "tokens": dict(zip(self.counter.names, cost)),
            })
        return chunks

    @staticmethod
    def _adr_summary(doc):
        m = doc["meta"]
        fields = [f"{k}: {m[k]}" for k in ("status", "date", "technology", "use") if m.get(k)]
        s = f"[{doc['doc_id']} | " + " | ".join(fields) + "]"
        return s if len(s) <= 300 else s[:297] + "...]"

    def chunk_all(self, docs):
        out = []
        for d in docs:
            out += self.chunk_document(d)
        return out


# --- 통계 --------------------------------------------------------------------------------------------------------

def _pct(sorted_vals, q):
    if not sorted_vals:
        return 0
    return sorted_vals[min(len(sorted_vals) - 1, math.ceil(q * len(sorted_vals)) - 1)]


def chunk_stats(docs, chunks, names, cap):
    """코퍼스별 조각 수 · 길이 분포(p50/p90/max, 토크나이저별) · 상한 때문에 잘린 조각 수 · 본문 없는 문서 수."""
    out = {}
    for corpus in sorted({d["corpus"] for d in docs}):
        cd = [d for d in docs if d["corpus"] == corpus]
        cc = [c for c in chunks if c["corpus"] == corpus]
        lens = {n: sorted(c["tokens"][n] for c in cc) for n in names}
        out[corpus] = {
            "docs": len(cd),
            "empty_docs": sum(1 for d in cd if not d["text"].strip()),
            "chunks": len(cc),
            "cut_chunks": sum(1 for c in cc if c["cut"]),
            "cut_docs": len({c["doc_id"] for c in cc if c["cut"]}),
            "over_cap_chunks": sum(1 for c in cc if any(c["tokens"][n] > cap for n in names)),
            "tokens": {n: {"p50": _pct(v, .5), "p90": _pct(v, .9), "max": v[-1] if v else 0} for n, v in lens.items()},
            "by_kind": dict(Counter(c["kind"] for c in cc)),
        }
    return out


def format_stats(stats, names):
    cols = [f"{'corpus':<7}{'docs':>6}{'empty':>6}{'chunks':>7}{'cut':>6}{'cutdoc':>7}{'>cap':>5}"]
    for n in names:
        cols[0] += f"  {n[:12]:>12} p50/p90/max"
    lines = [cols[0]]
    tot = Counter()
    for corpus, s in stats.items():
        row = f"{corpus:<7}{s['docs']:>6}{s['empty_docs']:>6}{s['chunks']:>7}{s['cut_chunks']:>6}{s['cut_docs']:>7}{s['over_cap_chunks']:>5}"
        for n in names:
            t = s["tokens"][n]
            row += f"  {t['p50']:>5}/{t['p90']:>4}/{t['max']:>4}  "
        lines.append(row)
        for k in ("docs", "empty_docs", "chunks", "cut_chunks", "cut_docs", "over_cap_chunks"):
            tot[k] += s[k]
    lines.append(f"{'합계':<7}{tot['docs']:>6}{tot['empty_docs']:>6}{tot['chunks']:>7}{tot['cut_chunks']:>6}{tot['cut_docs']:>7}{tot['over_cap_chunks']:>5}")
    return "\n".join(lines)
