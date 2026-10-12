"""2단계: 유형의 후보 풀(1단계 묶음 판정에서 그 유형 어떤 질의에든 걸린 문서)만 질의 하나씩 다시 판정한다."""
import argparse, asyncio, json, pathlib, sys, time
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import stage1_multi as sm


async def run(a):
    from openai import AsyncOpenAI
    wins = sm.windows(sm.load_docs(a.corpus))
    key2doc = {w["key"]: w["doc_id"] for w in wins}
    allq = [json.loads(l) for l in open(a.queries, encoding="utf-8")]
    s1, out = pathlib.Path(a.stage1), pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    client = AsyncOpenAI(api_key=sm.openai_key(), max_retries=0)
    sem = asyncio.Semaphore(a.conc)
    total = 0.0
    for t in a.types:
        qs = [q for q in allq if q["type"] == t]
        pool = sorted({c["doc_id"] for q in qs for c in json.load(open(s1 / f"{q['qid']}.json", encoding="utf-8"))["cands"]})
        pw = [w for w in wins if w["doc_id"] in set(pool)]
        batches = [pw[i:i + a.batch] for i in range(0, len(pw), a.batch)]
        usage = {"in": 0, "out": 0, "cached": 0, "calls": 0, "errors": []}
        t0 = time.time()
        jobs = [(q, b) for q in qs for b in batches]
        res = await asyncio.gather(*(sm.judge(client, a.model, [q], b, sem, usage) for q, b in jobs))
        best = {q["qid"]: {} for q in qs}
        for (q, _), items in zip(jobs, res):
            for it in items:
                did, g = key2doc.get(it["key"]), it["grade"]
                if did is None:
                    usage["errors"].append(f"모르는 key {it['key']}")
                    continue
                if did.startswith(("reddit:", "hn:")) and g == "2":
                    g = "1"
                r = {"2": 3, "?": 2, "1": 1}[g]
                cur = best[q["qid"]].get(did)
                if cur is None or r > cur[0]:
                    best[q["qid"]][did] = (r, g, it["reason"], it["what"])
        pin, pout = sm.PRICES[a.model]
        cost = usage["in"] * pin / 1e6 + usage["out"] * pout / 1e6
        total += cost
        for qid, b in best.items():
            cands = sorted(({"doc_id": d, "grade": v[1], "reason": v[2], "what": v[3]} for d, v in b.items()),
                           key=lambda x: ({"2": 0, "?": 1, "1": 2}[x["grade"]], x["doc_id"]))
            (out / f"{qid}.json").write_text(json.dumps({"qid": qid, "model": a.model, "mode": f"stage2-pool{len(pool)}", "cands": cands}, ensure_ascii=False, indent=1), encoding="utf-8")
            g = {k: sum(1 for c in cands if c["grade"] == k) for k in ("2", "1", "?")}
            print(f"  {qid} 후보 {len(cands)} (2:{g['2']} 1:{g['1']} ?:{g['?']})")
        (out / f"_usage-{t}.json").write_text(json.dumps({"type": t, "pool": len(pool), "usage": usage, "cost_usd_est": round(cost, 4)}, ensure_ascii=False), encoding="utf-8")
        print(f"{t}: 풀 {len(pool)} 문서 · 호출 {usage['calls']} · 입력 {usage['in']:,} · 출력 {usage['out']:,} · ${cost:.3f} · {time.time()-t0:.0f}s · 오류 {len(usage['errors'])}")
    print(f"합계 ${total:.3f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    for x in ("corpus", "queries", "stage1", "out"):
        ap.add_argument(x)
    ap.add_argument("--types", nargs="+", required=True)
    ap.add_argument("--model", default="gpt-6-luna")
    ap.add_argument("--batch", type=int, default=25)
    ap.add_argument("--conc", type=int, default=8)
    asyncio.run(run(ap.parse_args()))
