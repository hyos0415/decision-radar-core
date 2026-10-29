"""test 잔여 판정 카드 페이지 만들기 (확정 시트 §4 변경 2026-10-12 · 2026-10-27, HYO-155)

python residue_cards.py <residue_dir> --docs-json docs.json --queries test-queries.jsonl --out <out_dir>

- 카드 = 잔여 풀에서 Luna가 1 이상·`?`로 본 (질의, 문서). Luna 0은 카드 없이 0 (절차 3)
- K 질의의 ADR 아닌 문서는 카드에서 뺀다 — 어차피 코드가 0으로 깎는다(G4-K, build_qrels_v11과 같은 규칙)
- 가림: 전략 이름 없음(풀에 출처를 넣지 않는다) · **GPT 등급 없음** · Luna 근거 문장도 넣지 않는다 — 10/27 규칙은 「인용 검사
  통과분」만 보이게 하는데 잔여 판정에는 인용 검사가 없다. 순서는 질의 id → 문서 id (전략 순위 아님)
- 본문은 8,000자에서 자른다(G6 — 판정자가 본 범위와 같게)
- 판정 질문과 유형별 「답」(G4 행)은 페이지 맨 위와 카드마다 펼쳐 둔다 (10/27 규칙 1)
출력: <out>/cards.json (카드 번호 ↔ 질의·문서, 등급 없음) · <out>/index.html (아티팩트로 올릴 페이지 — 등급은 db `grades/cNNN`)
"""
import argparse
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
TEXT_LIMIT = 8000


def build_cards(residue_dir, docs, queries):
    residue_dir = pathlib.Path(residue_dir)
    pool = json.loads((residue_dir / "pool.json").read_text(encoding="utf-8"))
    cards = []
    for qid in sorted(pool):
        q = queries[qid]
        res = json.loads((residue_dir / f"{qid}.json").read_text(encoding="utf-8"))
        luna = {c["doc_id"]: c["grade"] for c in res["cands"]}
        for d in sorted(pool[qid]):
            if luna.get(d, "0") == "0":
                continue
            if q["type"] == "K" and not d.startswith("adr:"):
                continue
            doc = docs[d]
            cards.append({"card": len(cards) + 1, "qid": qid, "type": q["type"], "query": q["text"], "doc_id": d,
                          "title": doc.get("title") or "", "text": (doc.get("text") or "")[:TEXT_LIMIT],
                          "url": doc.get("url") or "", "source_type": doc.get("source_type") or ""})
    return cards


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("residue")
    ap.add_argument("--docs-json", required=True)
    ap.add_argument("--queries", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--template", default=str(HERE / "residue_cards.html"))
    a = ap.parse_args()
    docs = {d["doc_id"]: d for d in json.loads(pathlib.Path(a.docs_json).read_text(encoding="utf-8"))}
    queries = {}
    for line in pathlib.Path(a.queries).read_text(encoding="utf-8").splitlines():
        if line.strip():
            q = json.loads(line)
            queries[q["qid"]] = q
    cards = build_cards(a.residue, docs, queries)
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "cards.json").write_text(json.dumps([{k: c[k] for k in ("card", "qid", "doc_id")} for c in cards], ensure_ascii=False, indent=1) + "\n",
                                    encoding="utf-8", newline="\n")
    data = json.dumps({"n": len(cards), "cards": cards}, ensure_ascii=False).replace("</", "<\\/")
    tpl = pathlib.Path(a.template).read_text(encoding="utf-8")
    if tpl.count("__DATA__") != 1:
        raise SystemExit("템플릿에 __DATA__ 자리가 하나여야 한다")
    (out / "index.html").write_text(tpl.replace("__DATA__", data), encoding="utf-8", newline="\n")
    print(f"카드 {len(cards)}장 · 질의 {len({c['qid'] for c in cards})} → {out / 'index.html'}")


if __name__ == "__main__":
    main()
