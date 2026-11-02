"""ES kNN 결정론 실험 — S4 계열 (레포 코드 무변경, 메인 체크아웃 루트에서 실행)

python eval/arena/knn-det-2026-10-29/knn_det.py <out_dir> <queries.jsonl> [변형 ...]   (변형: s4 s4.int8 s4.bbq s4.kure)
2026-10-29 실행: test 36질의(split_gold 출력), 결과 요약 = 같은 폴더 summary.json · 해설 README.md

질의 벡터는 질의마다 한 번만 인코딩해 고정한다(질의 인코딩 흔들림과 ES 흔들림을 가른다 — 인코딩 결정론은 따로 잰다).
설정마다:
  plain  : 인덱스를 새로 만듦(DELETE→PUT→bulk 100→refresh, 하네스와 같다) × 3회, 첫 인덱스에는 같은 질의 두 번
  merged : 위 + _forcemerge?max_num_segments=1 → refresh × 2회
기준 exact: 캐시된 청크 임베딩 numpy 코사인 top-100 청크 → 문서 상위 10
인덱스 이름은 하네스 이름 + "-knndet" — 끝나면 지운다.
"""
import json
import pathlib
import sys
import time
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, ".")
from arena import cli, es, harness  # noqa: E402
from arena.strategies import s4_dense  # noqa: E402
from arena.strategies.s2_bm25 import index_name_for  # noqa: E402

OUT = pathlib.Path(sys.argv[1])
QUERIES = pathlib.Path(sys.argv[2])
VARIANTS = sys.argv[3:] or ["s4", "s4.int8"]
P = pathlib.Path("..").resolve()
URL = es.DEFAULT_URL
CHUNK_K, K_DOCS = 100, 20
CLS = {"s4": s4_dense.S4Dense, "s4.int8": s4_dense.S4Int8, "s4.bbq": s4_dense.S4BBQ, "s4.kure": s4_dense.S4Kure}

args = SimpleNamespace(
    config=str(pathlib.Path("arena/corpus/c-all@v1.json")), policy=None,
    snapshots=str(P / "radar-snapshots"), adr=str(P / "adr"),
    repo=[f"decision-radar={pathlib.Path('.').resolve()}", f"finance_verifier={P / 'finance_verifier'}", f"fineprint={P / 'fineprint'}"],
    supplement=[f"source-hints@2026-10-12={P / 'radar-snapshots/supplements/source-hints@2026-10-12/items.jsonl'}"],
    device=None, encode_batch=16, no_cache=False, es_url=URL, hnsw_m=16, ef_construction=100, num_candidates=200)

cfg, policy, _, docs, chunks, _, _ = cli.build(args)
chunk_to_doc = {c["chunk_id"]: c["doc_id"] for c in chunks}
queries = harness.load_queries(str(QUERIES))
print(f"청크 {len(chunks)} · 질의 {len(queries)}", flush=True)


def segments(index):
    st, body = es.call(URL, "GET", f"/_cat/segments/{index}?format=json&h=segment,docs.count")
    return [int(s["docs.count"]) for s in body] if st == 200 else body


def docs_top(hits):
    return [d for d, _, _ in harness.rollup_chunks(hits, chunk_to_doc)[:K_DOCS]]


class Fixed:
    """질의 벡터를 고정한 임베더 대리 — S4.search가 encode_query를 부른다."""
    def __init__(self, emb, qvec):
        self.emb, self.qvec, self.encoder = emb, qvec, emb.encoder

    def encode_query(self, text):
        return self.qvec[text], None

    def encode_chunks(self, ch):
        return self.emb.encode_chunks(ch)


def run_all(strat):
    return {q["qid"]: docs_top(strat.search(q["text"], CHUNK_K)) for q in queries}


results = {}
emb_cache = {}
for v in VARIANTS:
    kind = "kure" if v == "s4.kure" else "bge"
    if kind not in emb_cache:
        emb = cli.make_embedder(args, cfg, policy, kind)
        dense, _ = emb.encode_chunks(chunks)
        q1 = {q["text"]: np.asarray(emb.encode_query(q["text"])[0], dtype=np.float32) for q in queries}
        q2 = {q["text"]: np.asarray(emb.encode_query(q["text"])[0], dtype=np.float32) for q in queries}
        qdiff = max(float(np.abs(q1[t] - q2[t]).max()) for t in q1)
        emb_cache[kind] = (emb, np.asarray(dense, dtype=np.float32), q1, qdiff)
    emb, dense, qvec, qdiff = emb_cache[kind]
    # 정확 코사인 기준
    dn = dense / np.linalg.norm(dense, axis=1, keepdims=True)
    exact = {}
    for q in queries:
        qv = qvec[q["text"]] / np.linalg.norm(qvec[q["text"]])
        sims = dn @ qv
        idx = np.argsort(-sims, kind="stable")[:CHUNK_K]
        exact[q["qid"]] = docs_top([(chunks[i]["chunk_id"], float(sims[i])) for i in idx])
    rec = {"query_encode_maxdiff": qdiff, "plain": [], "merged": []}
    for mode, reps in (("plain", 3), ("merged", 2)):
        for r in range(reps):
            s = CLS[v](cfg["version"], Fixed(emb, qvec), es_url=URL)
            s.index_name = index_name_for(cfg["version"], s.index_suffix + "-knndet")
            t0 = time.perf_counter()
            s.index(chunks)
            if mode == "merged":
                es.expect(*es.call(URL, "POST", f"/{s.index_name}/_forcemerge?max_num_segments=1", timeout=600), "forcemerge")
                es.expect(*es.call(URL, "POST", f"/{s.index_name}/_refresh"), "refresh")
            segs = segments(s.index_name)
            a = run_all(s)
            b = run_all(s) if r == 0 else None
            rec[mode].append({"segments": segs, "run": a, "same_index_repeat_identical": (a == b) if b is not None else None,
                              "seconds": round(time.perf_counter() - t0, 1)})
            s.drop()
            print(v, mode, r, "segments", segs, flush=True)
    rec["exact"] = exact
    results[v] = rec

# 요약
summary = {}
for v, rec in results.items():
    ex = rec["exact"]
    row = {"query_encode_maxdiff": rec["query_encode_maxdiff"]}
    for mode in ("plain", "merged"):
        runs = [x["run"] for x in rec[mode]]
        base = runs[0]
        row[mode] = {
            "segments": [x["segments"] for x in rec[mode]],
            "same_index_repeat_identical": rec[mode][0]["same_index_repeat_identical"],
            "rebuild_top10_identical_queries": [sum(1 for q in base if base[q][:10] == r[q][:10]) for r in runs[1:]],
            "rebuild_top10_set_identical_queries": [sum(1 for q in base if set(base[q][:10]) == set(r[q][:10])) for r in runs[1:]],
            "rebuild_top20_identical_queries": [sum(1 for q in base if base[q] == r[q]) for r in runs[1:]],
            "vs_exact_top10_identical_queries": [sum(1 for q in r if r[q][:10] == ex[q][:10]) for r in runs],
            "vs_exact_top10_overlap_mean": [round(float(np.mean([len(set(r[q][:10]) & set(ex[q][:10])) / 10 for q in r])), 4) for r in runs],
        }
    summary[v] = row
OUT.mkdir(parents=True, exist_ok=True)
(OUT / "runs.json").write_text(json.dumps(results, ensure_ascii=False) + "\n", encoding="utf-8")
(OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
print(json.dumps(summary, ensure_ascii=False, indent=1))
