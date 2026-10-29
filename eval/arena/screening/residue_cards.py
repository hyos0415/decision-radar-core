"""test 잔여 판정 카드 페이지 만들기 (확정 시트 §4 변경 2026-10-12 · 2026-10-27 · 2026-10-29, HYO-155)

python residue_cards.py <residue_dir> --docs-json docs.json --queries test-queries.jsonl --nodes nodes@v2.json --out <out_dir> [--quotes quotes.json]

- 카드 = 잔여 풀에서 Luna가 1 이상·`?`로 본 (질의, 문서). Luna 0은 카드 없이 0 (절차 3)
- K 질의의 ADR 아닌 문서는 카드에서 뺀다 — 어차피 코드가 0으로 깎는다(G4-K, build_qrels_v11과 같은 규칙)
- 가림: 전략 이름 없음(풀에 출처를 넣지 않는다) · **GPT 등급 없음** · Luna 근거 문장 없음. 순서는 질의 id → 문서 id (전략 순위 아님)
- 본문은 8,000자에서 자른다(G6 — 판정자가 본 범위와 같게)
- 판정 질문과 유형별 「답」(G4 행)은 페이지 맨 위와 질의마다 펼쳐 둔다 (10/27 규칙 1)
- 판정 돕기 (2026-10-29, 사용자 — 전문 8,000자를 그대로 읽게 하면 98장을 판정할 수 없다):
  1. 질의별로 묶고 그 질의의 단서를 위에 — 정답지를 만들 때 사용자가 적은 값 그대로: C·E `expected_version`, 질의 nodes(기술 · repo/component).
     ADR 문서는 frontmatter의 technology · use · status를 따로 보인다(K는 질의 nodes와 나란히)
  2. 발췌 — 질의 낱말 · 기술 이름(nodes@v2 별칭) · 찾는 버전이 나온 줄과 앞뒤 한 줄. 코드가 기계적으로 찾는다(모델 판단 없음). 전문은 펼쳐서
  3. (`--quotes`) 인용 — 모델이 뽑은 「질의에 닿는 줄」 중 **문서(본문·제목)에 글자 그대로 있는 것만**(인용 검사 통과분, 10/27 규칙 2) 등급 없이 표시 +
     한국어 한 줄(판정어 금지). 뽑기는 `residue_quotes.py`(구독 codex exec, GPT-6 Luna)
출력: <out>/cards.json (카드 번호 ↔ 질의·문서, 등급 없음) · <out>/index.html (아티팩트로 올릴 페이지 — 등급은 db `grades/cNNN`)
카드 번호는 판 바뀜에도 그대로다 — 같은 (질의 id, 문서 id) 정렬이라 저장된 등급이 같은 카드에 남는다
"""
import argparse
import json
import pathlib
import re

HERE = pathlib.Path(__file__).resolve().parent
TEXT_LIMIT = 8000
SNIPPET_LINES = 14
LINE_CHARS = 300
STOP = {"the", "and", "for", "with", "from", "that", "this", "are", "was", "has", "have", "not", "can", "use", "using", "into",
        "what", "which", "when", "version", "support", "supports", "new", "api", "sdk", "model", "models"}
TERM_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.+/-]{2,}|\d+\.\d+(?:\.\d+)*")


def alias_map(nodes_vocab):
    out = {}
    for t in nodes_vocab.get("technologies", []):
        names = {t["label"]} | {a["text"] for a in t.get("aliases", [])}
        for n in names:
            out.setdefault(n.lower(), set()).update(names)
    return out


def query_terms(q, aliases):
    terms = set()
    for n in q.get("nodes") or []:
        tech = n.get("technology")
        if tech:
            terms.add(tech)
            terms.update(aliases.get(tech.lower(), ()))
        comp = n.get("component") or ""
        if len(comp) >= 4:
            terms.add(comp)
    ev = q.get("expected_version")
    if ev:
        v = str(ev).lstrip("vV")
        terms.update({v, "v" + v})
    for w in TERM_RE.findall(q["text"]):
        if w.lower() not in STOP:
            terms.add(w.rstrip(".,"))
    return sorted({t for t in terms if len(t) >= 2}, key=lambda s: (-len(s), s.lower()))


def snippet(text, terms, ctx=1, max_lines=SNIPPET_LINES):
    """질의 낱말이 나온 줄 ± ctx줄 — 연속 구간을 묶는다. {blocks: [[줄, …]], hits, more}"""
    lines = text.splitlines()
    low = [t.lower() for t in terms]
    hit = [i for i, line in enumerate(lines) if any(t in line.lower() for t in low)]
    keep = sorted({j for i in hit for j in range(max(0, i - ctx), min(len(lines), i + ctx + 1))})
    blocks, shown = [], 0
    for j in keep:
        if shown >= max_lines:
            break
        line = lines[j].strip()
        if not line:
            continue
        line = line if len(line) <= LINE_CHARS else line[:LINE_CHARS] + "…"
        if blocks and blocks[-1]["last"] == j - 1:
            blocks[-1]["lines"].append(line)
        else:
            blocks.append({"lines": [line]})
        blocks[-1]["last"] = j
        shown += 1
    if not hit:                     # 낱말이 안 걸리면(B — 한국어 질의 ↔ 영어 문서) 앞부분 몇 줄 — 모델 카드·초록은 대개 짧다
        lead = [line.strip() if len(line.strip()) <= LINE_CHARS else line.strip()[:LINE_CHARS] + "…" for line in lines if line.strip()]
        return {"blocks": [lead[:6]] if lead else [], "hits": 0, "more": max(0, len(lead) - 6), "lead": True}
    return {"blocks": [b["lines"] for b in blocks], "hits": len(hit), "more": max(0, len(keep) - shown), "lead": False}


def adr_meta(text):
    m = re.match(r"---\n(.*?)\n---", text, re.S)
    if not m:
        return None
    meta = {}
    for line in m.group(1).splitlines():
        k, _, v = line.partition(":")
        if k in ("status", "technology", "use") and v.strip():
            meta[k] = v.strip()
    return meta or None


def checked_quotes(quotes, text):
    """인용 검사 — 문서 본문(8,000자 안)에 글자 그대로 있는 문장만 남긴다 (공백 차이는 하나로 접어 비교)."""
    norm = " ".join(text.split())
    return [s for s in quotes if len(s) >= 8 and " ".join(s.split()) in norm]


def build_cards(residue_dir, docs, queries, aliases, quotes=None):
    residue_dir = pathlib.Path(residue_dir)
    pool = json.loads((residue_dir / "pool.json").read_text(encoding="utf-8"))
    cards, groups = [], []
    for qid in sorted(pool):
        q = queries[qid]
        res = json.loads((residue_dir / f"{qid}.json").read_text(encoding="utf-8"))
        luna = {c["doc_id"]: c["grade"] for c in res["cands"]}
        terms = query_terms(q, aliases)
        group = {"qid": qid, "type": q["type"], "query": q["text"], "terms": terms,
                 "expected_version": q.get("expected_version"),
                 "nodes": [{k: n.get(k) for k in ("technology", "repo", "component") if n.get(k)} for n in q.get("nodes") or []],
                 "cards": []}
        for d in sorted(pool[qid]):
            if luna.get(d, "0") == "0":
                continue
            if q["type"] == "K" and not d.startswith("adr:"):
                continue
            doc = docs[d]
            text = (doc.get("text") or "")[:TEXT_LIMIT]
            card = {"card": len(cards) + 1, "qid": qid, "doc_id": d, "title": doc.get("title") or "", "text": text,
                    "url": doc.get("url") or "", "source_type": doc.get("source_type") or "",
                    "snippet": snippet(text, terms), "adr": adr_meta(text) if d.startswith("adr:") else None}
            if quotes is not None:
                ev = (quotes.get(qid) or {}).get(d) or {}
                ev = ev if isinstance(ev, dict) else {"quotes": ev}
                qs = ev.get("quotes") or []
                card["quotes"] = checked_quotes(qs, text) + [s for s in checked_quotes(qs, card["title"]) if s not in checked_quotes(qs, text)]
                card["ko"] = ev.get("ko") or ""
            cards.append(card)
            group["cards"].append(card)
        if group["cards"]:
            groups.append(group)
    return cards, groups


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("residue")
    ap.add_argument("--docs-json", required=True)
    ap.add_argument("--queries", required=True)
    ap.add_argument("--nodes", default=str(HERE.parent / "nodes@v2.json"))
    ap.add_argument("--quotes", help="{qid: {doc_id: {quotes: [문장, …], ko}}} (residue_quotes.py merge) — 인용 검사를 통과한 것만 보인다")
    ap.add_argument("--out", required=True)
    ap.add_argument("--template", default=str(HERE / "residue_cards.html"))
    a = ap.parse_args()
    docs = {d["doc_id"]: d for d in json.loads(pathlib.Path(a.docs_json).read_text(encoding="utf-8"))}
    queries = {}
    for line in pathlib.Path(a.queries).read_text(encoding="utf-8").splitlines():
        if line.strip():
            q = json.loads(line)
            queries[q["qid"]] = q
    aliases = alias_map(json.loads(pathlib.Path(a.nodes).read_text(encoding="utf-8")))
    quotes = json.loads(pathlib.Path(a.quotes).read_text(encoding="utf-8")) if a.quotes else None
    cards, groups = build_cards(a.residue, docs, queries, aliases, quotes)
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "cards.json").write_text(json.dumps([{k: c[k] for k in ("card", "qid", "doc_id")} for c in cards], ensure_ascii=False, indent=1) + "\n",
                                    encoding="utf-8", newline="\n")
    data = json.dumps({"n": len(cards), "groups": groups}, ensure_ascii=False).replace("</", "<\\/")
    tpl = pathlib.Path(a.template).read_text(encoding="utf-8")
    if tpl.count("__DATA__") != 1:
        raise SystemExit("템플릿에 __DATA__ 자리가 하나여야 한다")
    (out / "index.html").write_text(tpl.replace("__DATA__", data), encoding="utf-8", newline="\n")
    nq = sum(1 for c in cards if c.get("quotes"))
    print(f"카드 {len(cards)}장 · 질의 {len(groups)} · 발췌 있는 카드 {sum(1 for c in cards if c['snippet']['blocks'])}"
          + (f" · 인용 있는 카드 {nq}" if quotes is not None else "") + f" → {out / 'index.html'}")


if __name__ == "__main__":
    main()
