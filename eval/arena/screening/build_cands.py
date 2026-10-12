"""2단계 스크리닝 결과 → 라벨 페이지 cands/<qid> 문서 + queries 진행 칸(ncand).

- 순서: GPT 2 → ? → 초안만(스크리닝이 놓친 초안 정답 후보) → 1
- 1점 중 무작위 10%(최소 1, 시드 qid 고정)에 sample=True — 사람이 확인해 GPT 1점의 정확도를 잰다
- 문서 본문: 직접 볼 카드(2·?·스크리닝 누락·표본)는 저장된 전문, 접힌 1점은 1,500자 (10/12 — 1,500자에서 잘려 판정 못 하는 카드가 84/158)
"""
import json
import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import stage1_multi as sm  # noqa: E402

S = pathlib.Path(__file__).parent
ST = {"github-release:": "release_note", "github:": "repo", "hf-model:": "model_card", "hf-paper:": "paper", "vendor:": "official_doc",
      "vendor-page:": "official_doc", "source-hint:": "official_doc", "reddit:": "community", "hn:": "community", "adr:": "adr", "stack:": "stack"}


def stype(d):
    return next((v for k, v in ST.items() if d.startswith(k)), "?")


docs = {d["doc_id"]: d for d in sm.load_docs(S / "goldq" / "corpus")}
urls = {}
for f in ("items.jsonl", "source-hints.jsonl"):
    for line in (S / "goldq/corpus/c-doc" / f).open(encoding="utf-8"):
        x = json.loads(line)
        urls[x["id"]] = x.get("url")
queries = {json.loads(l)["qid"]: json.loads(l) for l in open(S / "queries-gold-v1.jsonl", encoding="utf-8")}
drafts = {}
for f in (S / "dbq2" / "queries").glob("*.json"):
    drafts[f.stem] = json.load(open(f, encoding="utf-8")).get("gpt_grade2") or []
out = S / "cands-up"
out.mkdir(exist_ok=True)
summary = {}
for qid in sorted(queries):
    rec = json.load(open(S / "screen-s2" / f"{qid}.json", encoding="utf-8"))
    cands = rec["cands"]
    have = {c["doc_id"] for c in cands}
    for d in drafts.get(qid, []):
        if d not in have:
            cands.append({"doc_id": d, "grade": "0", "reason": "스크리닝은 이 문서를 고르지 않았다 (초안 GPT는 정답 후보로 적었다)", "what": "", "draft_only": True})
    ones = [c for c in cands if c["grade"] == "1"]
    k = max(1, round(len(ones) * 0.1)) if ones else 0
    sample = {c["doc_id"] for c in random.Random(f"sample-{qid}").sample(ones, k)} if k else set()
    items = []
    order = {"2": 0, "?": 1, "0": 2, "1": 3}
    for c in sorted(cands, key=lambda c: (order[c["grade"]], c["doc_id"] not in sample, c["doc_id"])):
        d = docs.get(c["doc_id"], {})
        items.append({"doc_id": c["doc_id"], "title": d.get("title", ""), "text": (d.get("text") or "") if (c["grade"] != "1" or c["doc_id"] in sample or c.get("draft_only")) else (d.get("text") or "")[:1500],
                      "url": urls.get(c["doc_id"]), "source_type": stype(c["doc_id"]), "gpt_grade": c["grade"],
                      "gpt_reason": c["reason"], "explain": {"what": c.get("what", "")} if c.get("what") else None,
                      "sample": c["doc_id"] in sample, "draft_only": bool(c.get("draft_only"))})
    body = {"items": items, "screened_by": f"{rec['model']} · {rec['mode']} (2026-10-12)"}
    raw = json.dumps(body, ensure_ascii=False)
    assert len(raw.encode()) < 250_000, (qid, len(raw))
    (out / f"{qid}.json").write_text(raw, encoding="utf-8")
    must = sum(1 for i in items if i["gpt_grade"] != "1" or i["sample"] or i["draft_only"])
    (out / f"{qid}.q.json").write_text(json.dumps({"ncand": must}), encoding="utf-8")
    summary[qid] = (len(items), must, sum(1 for i in items if i["draft_only"]))
tot = sum(v[1] for v in summary.values())
print("질의", len(summary), "· 후보 합", sum(v[0] for v in summary.values()), "· 직접 확인 합", tot, "· 초안만", sum(v[2] for v in summary.values()))
for q, v in summary.items():
    print(q, v)
