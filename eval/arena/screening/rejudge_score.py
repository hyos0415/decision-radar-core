"""사람 재판정 채점 — 다시 매긴 등급(라벨 페이지 db `grades` 덤프) vs 예전 등급(`key.json`) → 일치율.

python eval/arena/screening/rejudge_score.py <rejudge 폴더> <grades 덤프 폴더>

- grades 덤프: ArtifactData list `grades` --out_dir 로 받은 `<dir>/grades/cNN.json` ({card, qid, doc_id, grade, note})
- 정확 일치율 · ±1 안 일치율 · 혼동표(예전 → 이번) · 유형별 일치. 15장이 다 없으면 멈춘다
- 결과: <rejudge 폴더>/score.json
"""
import collections
import json
import pathlib
import sys


def main(rj, dump):
    rj, dump = pathlib.Path(rj), pathlib.Path(dump)
    cards = {c["card"]: c for c in json.loads((rj / "cards.json").read_text(encoding="utf-8"))["cards"]}
    key = json.loads((rj / "key.json").read_text(encoding="utf-8"))["grades"]
    new = {}
    for p in sorted(dump.rglob("c*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        d = d.get("data", d)
        new[d["card"]] = d
    missing = sorted(set(cards) - set(new))
    if missing:
        raise SystemExit(f"아직 안 매긴 카드: {missing}")
    rows, conf, by_type = [], collections.Counter(), collections.defaultdict(lambda: [0, 0])
    for n, c in sorted(cards.items()):
        before, after = key[f"{c['qid']}|{c['doc_id']}"], new[n]["grade"]
        if (new[n]["qid"], new[n]["doc_id"]) != (c["qid"], c["doc_id"]):
            raise SystemExit(f"카드 {n}의 qid·doc_id가 cards.json과 다르다")
        rows.append({"card": n, "qid": c["qid"], "type": c["type"], "doc_id": c["doc_id"], "before": before, "after": after, "note": new[n].get("note", "")})
        conf[(before, after)] += 1
        by_type[c["type"]][0] += before == after
        by_type[c["type"]][1] += 1
    exact = sum(r["before"] == r["after"] for r in rows)
    near = sum(abs(r["before"] - r["after"]) <= 1 for r in rows)
    out = {"n": len(rows), "exact": exact, "exact_rate": round(exact / len(rows), 3), "within1": near,
           "confusion": {f"{b}->{a}": k for (b, a), k in sorted(conf.items())},
           "by_type": {t: f"{a}/{b}" for t, (a, b) in sorted(by_type.items())}, "rows": rows}
    (rj / "score.json").write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"정확 일치 {exact}/{len(rows)} ({out['exact_rate']}) · ±1 안 {near}/{len(rows)} · 혼동 {out['confusion']} · 유형별 {out['by_type']}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
