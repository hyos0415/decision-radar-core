"""gold@v1 GPT 전수 스크리닝 — 질의마다 코퍼스 전체 문서를 GPT가 0/1/2/? 판정 (gold 시트 §4).

python screen_single.py <corpus_dir> <queries.jsonl> <out_dir> --qids B03 [--model gpt-6-luna] [--batch 25] [--conc 8]

- 문서 텍스트 = 스냅숏에 저장된 것만 (G6). 6,000자를 넘는 문서는 창(window)으로 나눠 각각 판정하고 최댓값을 쓴다
- 프롬프트 순서 = 규칙 → 문서 묶음 → 질의 (문서 묶음이 같은 앞부분이라 질의가 바뀌어도 캐시가 먹는다)
- reddit·hn은 코드가 최대 1로 자른다 (G5 — 모델 판정에 맡기지 않는다, 규율 1)
- 출력: <out>/<qid>.json = {qid, model, cands: [{doc_id, grade, reason}], usage, cost_usd, seconds}
"""
import argparse
import asyncio
import json
import pathlib
import re
import sys
import time

REPO = pathlib.Path("<workspace>/jhs-decisions")
sys.path.insert(0, str(REPO))
from collector.snapshot import page_file  # noqa: E402

PRICES = {"gpt-6-luna": (0.10, 0.50), "gpt-6.1-sol": (2.00, 10.00)}
WIN = 6000
G4 = {
    "B": "2 = 묻는 일을 하는 기술의 1차 출처(repo·모델 카드·논문·릴리스 노트). 우리 스택에 없어도 된다. 1 = 그 기술을 언급만 하는 글, 비슷하지만 묻는 일을 하지 않는 기술",
    "C": "2 = 그 변경이 들어간 릴리스 항목. 1 = 같은 기능을 언급하는 다른 버전의 릴리스, 그 버전을 다루는 기타 문서",
    "E": "2 = 해당 패키지·버전의 호환성에 닿는 변경(breaking·deprecation·의존성 하한)을 적은 문서, 또는 우리 쪽 버전 고정을 적은 manifest·스택 절. 1 = 같은 패키지의 다른 릴리스, 버전 고정이 없는 스택 절",
    "J": "2 = 재검토 조건의 충족·미충족을 본문만으로 판정할 수 있는 문서. 1 = 조건의 기술을 다루지만 판정 근거가 안 되는 문서, 해당 ADR 자체",
    "K": "2 = technology와 use가 둘 다 같은 ADR. 1 = technology만 같고 use가 다른 ADR",
}
RULES = """너는 검색 평가 정답지의 **스크리닝 판정자**다. 질의 하나와 문서 묶음을 받아, 문서마다 그 질의에 대한 관련 등급을 매긴다.

등급
- 2 = 이 문서 하나로 질의에 답할 수 있다. 질의가 묻는 사실이 문서 본문(또는 제목)에 적혀 있다
- 1 = 답의 일부나 단서만 있다
- 0 = 그 외. 기술 이름만 겹치고 질의의 물음과 무관한 것 포함
- ? = 위 규칙으로 정하지 못하겠다 (사람이 본다)
규칙
- 문서에 **적힌 텍스트만** 본다. 링크를 상상하거나 배경지식으로 등급을 올리지 않는다. 본문이 없는 문서는 제목으로만 판단한다
- reddit·hn 글은 최대 1이다
- 같은 문서의 일부 창(window)만 받을 수 있다 — 받은 부분만 보고 판정한다
출력: 0이 아닌 문서만 `items`에 넣는다 (0은 생략). reason은 한국어 한 문장 — 문서의 어느 부분이 왜 그 등급인지."""
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["items"], "properties": {"items": {"type": "array", "items": {
    "type": "object", "additionalProperties": False, "required": ["key", "grade", "reason"],
    "properties": {"key": {"type": "string"}, "grade": {"type": "string", "enum": ["2", "1", "?"]}, "reason": {"type": "string"}}}}}}


def load_docs(corpus):
    c = pathlib.Path(corpus)
    docs = []
    snap = c / "c-doc"
    for f in ("items.jsonl", "source-hints.jsonl"):
        for line in (snap / f).open(encoding="utf-8"):
            d = json.loads(line)
            text = d.get("text") or ""
            if d["id"].startswith("vendor-page:"):
                p = snap / page_file(d)
                text = p.read_text(encoding="utf-8") if p.exists() else ""
            docs.append({"doc_id": d["id"], "title": d.get("title") or "", "text": text})
    for p in sorted((c / "c-adr").glob("*.md")):
        docs.append({"doc_id": "adr:" + p.name[:4], "title": p.name, "text": p.read_text(encoding="utf-8")})
    for p in sorted((c / "c-code").rglob("*")):
        if p.is_file():
            rel = p.relative_to(c / "c-code")
            did = "stack:" + rel.parts[0] + "/" + "/".join(rel.parts[1:]).replace("__", "/")
            docs.append({"doc_id": did, "title": did, "text": p.read_text(encoding="utf-8")})
    return docs


def windows(docs):
    out = []
    for d in docs:
        t = d["text"]
        parts = [t[i:i + WIN] for i in range(0, max(len(t), 1), WIN)] or [""]
        for n, part in enumerate(parts):
            out.append({"key": f"{d['doc_id']}##{n}", "doc_id": d["doc_id"], "title": d["title"], "text": part,
                        "note": f"(창 {n + 1}/{len(parts)})" if len(parts) > 1 else ""})
    return out


def batch_text(batch):
    return "\n\n".join(f"<doc key=\"{w['key']}\">\n제목: {w['title']} {w['note']}\n{w['text'] or '(본문 없음)'}\n</doc>" for w in batch)


def openai_key():
    for line in (REPO / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("OPENAI_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"')
    raise SystemExit(".env에 OPENAI_API_KEY가 없다")


async def judge(client, model, q, batch, sem, usage):
    msg = [{"role": "system", "content": RULES},
           {"role": "user", "content": f"<docs>\n{batch_text(batch)}\n</docs>\n\n유형별 기준({q['type']}): {G4[q['type']]}\n\n질의: {q['text']}"}]
    for attempt in range(5):
        try:
            async with sem:
                r = await client.responses.create(model=model, input=msg,
                                                  text={"format": {"type": "json_schema", "name": "screen", "schema": SCHEMA, "strict": True}})
            u = r.usage
            usage["in"] += u.input_tokens
            usage["out"] += u.output_tokens
            usage["cached"] += getattr(getattr(u, "input_tokens_details", None), "cached_tokens", 0) or 0
            usage["calls"] += 1
            return json.loads(r.output_text)["items"]
        except Exception as e:  # 429·일시 오류 — 물러났다 다시
            usage["errors"].append(f"{type(e).__name__}: {str(e)[:120]}")
            await asyncio.sleep(2 ** attempt + 1)
    raise RuntimeError(f"{q['qid']} 배치 실패 5회")


async def run(args):
    from openai import AsyncOpenAI
    docs = load_docs(args.corpus)
    wins = windows(docs)
    qs = [json.loads(line) for line in open(args.queries, encoding="utf-8")]
    qs = [q for q in qs if q["qid"] in set(args.qids)]
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    client = AsyncOpenAI(api_key=openai_key(), max_retries=0)
    sem = asyncio.Semaphore(args.conc)
    batches = [wins[i:i + args.batch] for i in range(0, len(wins), args.batch)]
    print(f"문서 {len(docs)} · 창 {len(wins)} · 배치 {len(batches)}")
    for q in qs:
        t0 = time.time()
        usage = {"in": 0, "out": 0, "cached": 0, "calls": 0, "errors": []}
        res = await asyncio.gather(*(judge(client, args.model, q, b, sem, usage) for b in batches))
        best = {}
        for items in res:
            for it in items:
                key = it["key"]
                did = key.split("##")[0]
                if did not in {w["doc_id"] for w in wins if w["key"] == key}:
                    usage["errors"].append(f"모르는 key {key}")
                    continue
                g = it["grade"]
                if did.startswith(("reddit:", "hn:")) and g == "2":
                    g = "1"  # G5 — 코드가 집행
                rank = {"2": 3, "?": 2, "1": 1}[g]
                if did not in best or rank > best[did][0]:
                    best[did] = (rank, g, it["reason"])
        cands = sorted(({"doc_id": d, "grade": v[1], "reason": v[2]} for d, v in best.items()), key=lambda x: ({"2": 0, "?": 1, "1": 2}[x["grade"]], x["doc_id"]))
        pin, pout = PRICES[args.model]
        cost = (usage["in"] - usage["cached"]) * pin / 1e6 + usage["cached"] * pin * 0.1 / 1e6 + usage["out"] * pout / 1e6
        rec = {"qid": q["qid"], "model": args.model, "cands": cands, "usage": usage, "cost_usd_est": round(cost, 4),
               "seconds": round(time.time() - t0, 1), "n_windows": len(wins), "batch": args.batch}
        (out / f"{q['qid']}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        g = {k: sum(1 for c in cands if c["grade"] == k) for k in ("2", "1", "?")}
        print(f"{q['qid']} 후보 {len(cands)} (2:{g['2']} 1:{g['1']} ?:{g['?']}) · 입력 {usage['in']:,} (캐시 {usage['cached']:,}) · 출력 {usage['out']:,} · ${cost:.3f} · {rec['seconds']}s · 오류 {len(usage['errors'])}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus")
    ap.add_argument("queries")
    ap.add_argument("out")
    ap.add_argument("--qids", nargs="+", required=True)
    ap.add_argument("--model", default="gpt-6-luna")
    ap.add_argument("--batch", type=int, default=25)
    ap.add_argument("--conc", type=int, default=8)
    asyncio.run(run(ap.parse_args()))
