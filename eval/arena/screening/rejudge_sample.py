"""사람 재판정 15쌍 뽑기 — gold-precondition-sheet §4 「확정 1주 뒤 사용자가 무작위 15쌍을 다시 매김 → 일치율 기록」.

python eval/arena/screening/rejudge_sample.py <gold 폴더> <out 폴더> --snapshots ../radar-snapshots --adr ../adr \
  --repo decision-radar=. --repo finance_verifier=../finance_verifier --repo fineprint=../fineprint \
  --supplement source-hints@2026-10-12=../radar-snapshots/supplements/source-hints@2026-10-12/items.jsonl

- 모집단: dev 질의에서 사람이 매긴 등급(`labels/<qid>.json`의 grades). test 질의는 넣지 않는다 — 봉인(§4)이 「전략을 짜는 사람」에게도
  test 글을 다시 보이지 않게 하려는 것이라서. 등록 문서는 모집단을 적지 않았다 — 이 해석은 sheet §4에 날짜와 함께 남긴다
- 시드 고정(`--seed`, 기본 20261027) · 질의 유형별로 같은 수(15 = B·C·E·J·K × 3)를 정렬된 (qid, doc_id) 목록에서 random.sample
- out/cards.json: 카드(질의 글 · 문서 제목·본문 · url) — 예전 등급은 넣지 않는다 (가림)
- out/key.json: 예전 등급 — 채점 때만 연다 (`rejudge_score.py`)
"""
import argparse
import json
import pathlib
import random
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from arena import corpus  # noqa: E402


def _kv(items):
    return dict(x.split("=", 1) for x in (items or []))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("gold")
    ap.add_argument("out")
    ap.add_argument("--n", type=int, default=15)
    ap.add_argument("--seed", type=int, default=20261027)
    ap.add_argument("--config", default=str(ROOT / "arena" / "corpus" / "c-all@v1.json"))
    ap.add_argument("--snapshots", required=True)
    ap.add_argument("--adr", required=True)
    ap.add_argument("--repo", action="append")
    ap.add_argument("--supplement", action="append")
    a = ap.parse_args()

    gold, out = pathlib.Path(a.gold), pathlib.Path(a.out)
    queries = {q["qid"]: q for q in map(json.loads, (gold / "queries.jsonl").read_text(encoding="utf-8").splitlines()) if q.get("split") == "dev"}
    pop = []
    for qid in sorted(queries):
        lp = gold / "labels" / f"{qid}.json"
        if lp.exists():
            for doc_id, g in sorted(json.loads(lp.read_text(encoding="utf-8")).get("grades", {}).items()):
                if g in (0, 1, 2):
                    pop.append((qid, doc_id, g))
    # 유형별 같은 수 — 그냥 무작위면 사람 라벨이 몰린 B(47/88, B03 한 질의만 20)에 절반이 넘게 간다
    rng, types = random.Random(a.seed), sorted({queries[q]["type"] for q, _, _ in pop})
    per, extra = divmod(a.n, len(types))
    picked = []
    for i, t in enumerate(types):
        picked += rng.sample([p for p in pop if queries[p[0]]["type"] == t], per + (1 if i < extra else 0))
    picked.sort()

    docs = {d["doc_id"]: d for d in corpus.load_corpus(corpus.load_config(a.config), a.snapshots, a.adr, _kv(a.repo), _kv(a.supplement))}
    cards, key = [], {}
    for i, (qid, doc_id, g) in enumerate(picked, 1):
        d = docs[doc_id]
        q = queries[qid]
        cards.append({"card": i, "qid": qid, "type": q["type"], "query": q["text"], "doc_id": doc_id, "title": d.get("title") or "",
                      "text": d.get("text") or "", "url": (d.get("meta") or {}).get("url", ""), "source_type": d.get("source_type", "")})
        key[f"{qid}|{doc_id}"] = g
    out.mkdir(parents=True, exist_ok=True)
    meta = {"gold": gold.name, "split": "dev", "population": len(pop), "n": a.n, "seed": a.seed}
    (out / "cards.json").write_text(json.dumps({**meta, "cards": cards}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    (out / "key.json").write_text(json.dumps({**meta, "grades": key}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"모집단 {len(pop)}쌍 → {a.n}쌍 (seed {a.seed}) · 질의 {len({c['qid'] for c in cards})}개 → {out}")


if __name__ == "__main__":
    main()
