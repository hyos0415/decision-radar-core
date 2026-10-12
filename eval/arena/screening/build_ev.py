"""근거 문장 검사·병합 — ev-out/batch*.json → ev/<qid>.json = {doc_id: {ko, quotes, spans, dropped}}

- 인용은 본문(또는 제목)의 연속 부분 문자열이어야 한다 (공백만 정규화). 아니면 버리고 dropped에 센다 — 모델이 지어낸 근거를 사람에게 보이지 않는다 (규율 1)
- spans = 라벨 페이지가 본문에 하이라이트할 [시작, 끝] (화면에 보이는 본문 기준)
"""
import json
import pathlib
import re

S = pathlib.Path(__file__).parent
texts, titles = {}, {}
for f in (S / "cands-up").glob("*.json"):
    if f.name.endswith(".q.json"):
        continue
    for it in json.load(open(f, encoding="utf-8"))["items"]:
        texts[(f.stem, it["doc_id"])] = it["text"] or ""
        titles[(f.stem, it["doc_id"])] = it["title"] or ""


def find(quote, text):
    toks = quote.split()
    if not toks:
        return None
    m = re.search(r"\s+".join(map(re.escape, toks)), text)
    return [m.start(), m.end()] if m else None


out, stats = {}, {"cards": 0, "quotes": 0, "ok": 0, "title_only": 0, "dropped": 0, "empty": 0, "unknown": 0}
for f in sorted((S / "ev-out").glob("batch*.json")):
    for r in json.load(open(f, encoding="utf-8")):
        key = (r["qid"], r["doc_id"])
        if key not in texts:
            stats["unknown"] += 1
            print("모르는 카드", key)
            continue
        stats["cards"] += 1
        quotes, spans, dropped = [], [], 0
        for q in r.get("quotes") or []:
            stats["quotes"] += 1
            sp = find(q, texts[key])
            if sp:
                quotes.append(q)
                spans.append(sp)
                stats["ok"] += 1
            elif find(q, titles[key]):
                quotes.append(q)
                stats["title_only"] += 1
            else:
                dropped += 1
                stats["dropped"] += 1
                print("버림", key, q[:80])
        if not quotes:
            stats["empty"] += 1
        out.setdefault(r["qid"], {})[r["doc_id"]] = {"ko": r.get("ko", ""), "quotes": quotes, "spans": spans, "dropped": dropped}

need = {k for k in texts}
must = set()
for f in (S / "cands-up").glob("*.json"):
    if f.name.endswith(".q.json"):
        continue
    for it in json.load(open(f, encoding="utf-8"))["items"]:
        if it["gpt_grade"] != "1" or it["sample"] or it["draft_only"]:
            must.add((f.stem, it["doc_id"]))
have = {(q, d) for q, m in out.items() for d in m}
print("빠진 카드", sorted(must - have))
(S / "ev").mkdir(exist_ok=True)
for qid, m in out.items():
    (S / "ev" / f"{qid}.json").write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
print(stats)
