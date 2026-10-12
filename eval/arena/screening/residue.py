"""gold@v1.1 잔여 판정 (확정 시트 §4 「스크리닝 감사 재구성」, 사전 등록 2026-10-12)

python residue.py <results_dir>... --queries <queries.jsonl> --out <out_dir> [--model gpt-6-luna]

1. 풀 = 등록 전략 run들의 문서 단위 상위 10 중 gold@v1 판정(judgments.jsonl)이 없는 (질의, 문서)
2. 판정자에게 전략을 가린다 — 질의와 문서만, 문서는 id 정렬
3. 질의 하나씩 판정 (screen.py의 단건 프롬프트 · G4 앵커 그대로). reddit·hn 최대 1은 코드가
출력: <out>/pool.json (질의별 문서 목록 — 어느 전략이 가져왔는지는 따로 pool_sources.json) · <out>/<qid>.json · _usage.json
"""
import argparse
import asyncio
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import screen_single as screen  # noqa: E402 — 질의 하나씩 판정 (스크리닝 단건 프롬프트 그대로)

REPO = pathlib.Path("<workspace>/jhs-decisions")


def build_pool(result_dirs):
    judged = {}
    for line in open(REPO / "eval/arena/gold@v1/judgments.jsonl", encoding="utf-8"):
        r = json.loads(line)
        judged.setdefault(r["qid"], set()).add(r["doc_id"])
    pool, sources = {}, {}
    for rd in result_dirs:
        for d in sorted(pathlib.Path(rd).iterdir()):
            run = json.load(open(d / "run.json", encoding="utf-8"))
            strat = d.name.split("__")[0]
            for qid, docs in run.items():
                for doc in list(docs)[:10]:
                    if doc not in judged.get(qid, set()):
                        pool.setdefault(qid, set()).add(doc)
                        sources.setdefault(f"{qid}|{doc}", []).append(strat)
    return {q: sorted(v) for q, v in sorted(pool.items())}, sources


async def run(args):
    from openai import AsyncOpenAI
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    pool, sources = build_pool(args.results)
    (out / "pool.json").write_text(json.dumps(pool, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "pool_sources.json").write_text(json.dumps(sources, ensure_ascii=False, indent=1), encoding="utf-8")
    docs = {d["doc_id"]: d for d in screen.load_docs(args.corpus)}
    qs = {json.loads(l)["qid"]: json.loads(l) for l in open(args.queries, encoding="utf-8")}
    client = AsyncOpenAI(api_key=screen.openai_key(), max_retries=0)
    sem = asyncio.Semaphore(args.conc)
    usage = {"in": 0, "out": 0, "cached": 0, "calls": 0, "errors": []}
    t0 = time.time()

    async def one(qid, ids):
        wins = screen.windows([docs[i] for i in ids])         # 문서 id 정렬 순서 그대로 — 전략 순위 아님
        batches = [wins[i:i + args.batch] for i in range(0, len(wins), args.batch)]
        res = await asyncio.gather(*(screen.judge(client, args.model, qs[qid], b, sem, usage) for b in batches))
        key2doc = {w["key"]: w["doc_id"] for w in wins}
        best = {}
        for items in res:
            for it in items:
                did = key2doc.get(it["key"])
                if did is None:
                    usage["errors"].append(f"모르는 key {qid} {it['key']}")
                    continue
                g = it["grade"]
                if did.startswith(("reddit:", "hn:")) and g == "2":
                    g = "1"
                rank = {"2": 3, "?": 2, "1": 1}[g]
                if did not in best or rank > best[did][0]:
                    best[did] = (rank, g, it["reason"])
        cands = [{"doc_id": d, "grade": v[1], "reason": v[2]} for d, v in sorted(best.items())]
        rec = {"qid": qid, "model": args.model, "mode": "residue-single", "n_pool": len(ids), "cands": cands}
        (out / f"{qid}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        return qid, len(ids), cands

    results = await asyncio.gather(*(one(q, ids) for q, ids in pool.items()))
    pin, pout = screen.PRICES[args.model]
    cost = (usage["in"] - usage["cached"]) * pin / 1e6 + usage["cached"] * pin * 0.1 / 1e6 + usage["out"] * pout / 1e6
    (out / "_usage.json").write_text(json.dumps({"usage": usage, "cost_usd_est": round(cost, 4), "seconds": round(time.time() - t0, 1)},
                                                ensure_ascii=False, indent=1), encoding="utf-8")
    from collections import Counter
    g = Counter(c["grade"] for _, _, cs in results for c in cs)
    print(f"질의 {len(pool)} · 쌍 {sum(n for _, n, _ in results)} · 1 이상·? {sum(len(cs) for _, _, cs in results)} (2:{g['2']} 1:{g['1']} ?:{g['?']}) · "
          f"입력 {usage['in']:,} · 출력 {usage['out']:,} · ${cost:.3f} · {time.time() - t0:.0f}s · 오류 {len(usage['errors'])}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("results", nargs="+")
    ap.add_argument("--queries", required=True)
    ap.add_argument("--corpus", default=str(pathlib.Path(__file__).parent / "goldq" / "corpus"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="gpt-6-luna")
    ap.add_argument("--batch", type=int, default=25)
    ap.add_argument("--conc", type=int, default=8)
    asyncio.run(run(ap.parse_args()))
