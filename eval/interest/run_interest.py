"""관심사 질의 실험 (HYO-187) — 레인 B 관심사 순위를 검색 전략 5개로 비교한다. 등록: `eval/interest/prereg-2026-11-12.md`.

Arena 코드(arena/cli.py의 전략 공장 · harness의 청크→문서 순위)를 그대로 쓰고, 코퍼스만 「얼린 주의 레인 B 후보」로 바꾼다.

  python eval/interest/run_interest.py queries --profile <profile.resolved.json> --out eval/interest/queries-w46.jsonl
  python eval/interest/run_interest.py rank --strategy kw|s2|s4|s3a.ko|s6 --snapshot <radar-snapshots>/2026-W46 \
      --profile <profile.resolved.json> --queries eval/interest/queries-w46.jsonl --out <작업 폴더>/runs
  python eval/interest/run_interest.py pool --runs <작업 폴더>/runs --out <작업 폴더>/pool.json

kw = 지금 제품의 키워드 매칭(pipeline.keyword_match, kw+aliases)을 관심사마다 그 관심사 점수로 줄 세운 것 — 질의 글을 쓰지 않는다.
나머지는 Arena 등록 고정값 그대로이고, 다른 것은 S6 재정렬 깊이(50 → 100)뿐이다 (등록 문서 「전략」).
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

CORPUS_VERSION = "c-laneb@2026-W46"      # 임베딩 캐시 · ES 색인 이름에 들어간다 (Arena c-all@v1과 섞이지 않게)
K_DOCS = 30                              # 문서 순위 길이 = 판정 풀 깊이 = 지표 깊이 (등록 문서 「지표」)
CHUNK_K = 200                            # 문서 30 · 재정렬 1단계 100을 채우게 청크를 넉넉히 (S4 num_candidates 200과 같게)
RERANK_DEPTH = 100                       # Arena 등록값 50 — 50이면 S6의 상위 30 문서 집합이 1단계의 상위 50 안에서만 바뀐다
STRATEGIES = ("kw", "s2", "s4", "s3a.ko", "s6")
LANGS = ("en", "ko")

# 관심사 topic의 영어 이름 — 질의 글의 머리 (등록 때 고정, 결과를 보고 바꾸지 않는다)
TOPIC_EN = {
    "multimodal": "Multimodal models",
    "hybrid-sparse-retrieval": "Hybrid and sparse retrieval",
    "embeddings-rerankers": "Embedding and reranker models",
    "graph-rag": "Graph-based retrieval (GraphRAG)",
    "vector-index-efficiency": "Reducing vector index storage and search cost",
    "agent-runtime": "Agent runtimes and human-in-the-loop",
    "llm-eval": "LLM evaluation and LLM judges",
    "small-local-models": "Small local models and quantized serving",
    "answer-verification": "Answer verification and factuality judgment",
    "guardrails-pii": "Guardrails and PII masking",
    "speech": "Speech recognition and synthesis (STT, TTS)",
    "preference-optimization": "Reinforcement learning and preference optimization (DPO, ORPO)",
    "code-impact": "Code understanding and impact analysis",
    "model-routing-cost": "Model routing and cost reduction",
}


def read_json(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def write_json(p, obj):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")


def active_interests(profile):
    return [it for it in profile["interests"] if it.get("active", True)]


def query_text(it, lang):
    """영어 = 「영어 topic: 별칭들. Examples: 예시들.」 (기계적 틀) · 한국어 = topic 그대로."""
    if lang == "ko":
        return it["topic"]
    s = f"{TOPIC_EN[it['id']]}: {', '.join(it.get('aliases') or [])}."
    if it.get("examples"):
        s += f" Examples: {', '.join(it['examples'])}."
    return s


def cmd_queries(a):
    prof = read_json(a.profile)
    rows = []
    for it in active_interests(prof):
        if it["id"] not in TOPIC_EN:
            raise SystemExit(f"TOPIC_EN에 없는 관심사: {it['id']}")
        for lang in LANGS:
            rows.append({"qid": f"{it['id']}@{lang}", "interest": it["id"], "lang": lang, "type": "interest", "text": query_text(it, lang)})
    Path(a.out).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8", newline="\n")
    print(f"질의 {len(rows)}개 → {a.out}")


def lane_b_items(snapshot):
    from pipeline import lanes
    from pipeline.profile_filter import read_items
    _a, lane_b, _skipped = lanes.split_lanes(read_items(snapshot))
    return lane_b


def build_corpus(snapshot):
    """레인 B 후보 → Arena 문서 · 청크 (chunk-policy@v1)."""
    from arena import chunking, corpus
    from arena.tokens import HFTokenCounter
    items = lane_b_items(snapshot)
    docs = [corpus._item_to_doc(it, Path(snapshot)) for it in items]
    policy_path = ROOT / "arena" / "corpus" / "chunk-policy@v1.json"
    policy = chunking.load_policy(policy_path)
    chunks = chunking.Chunker(policy, HFTokenCounter(policy["tokenizers"])).chunk_all(docs)
    return items, docs, chunks, policy


def rank_kw(items, profile, queries):
    """지금 제품의 키워드 매칭 — 관심사마다 그 관심사 점수(우선순위 가중 · 그 관심사의 not 감점) 내림차순, 동점은 전체 점수 · id."""
    from pipeline import keyword_match
    scored = []
    for it in items:
        s = keyword_match.score_item(it, profile, "kw+aliases")
        if s:
            scored.append((it["id"], s))
    out = {}
    for q in queries:
        rows = []
        for doc_id, s in scored:
            if q["interest"] not in s["interests"]:
                continue
            pts = sum(m["points"] for m in s["matched_by"]
                      if m.get("interest") == q["interest"] and m["kind"] in ("interest", "penalty") and m.get("source") != "exclude")
            rows.append((doc_id, pts, s["score"]))
        rows.sort(key=lambda r: (-r[1], -r[2], r[0]))
        out[q["qid"]] = [[d, round(p, 4)] for d, p, _ in rows[:K_DOCS]]
    return out


def strategy_args(name):
    from arena import es
    return argparse.Namespace(strategy=name, es_url=es.DEFAULT_URL, device=None, encode_batch=16, no_cache=False, s5b_sparse=None,
                              hnsw_m=16, ef_construction=100, num_candidates=200, rrf_k=None, rerank_depth=RERANK_DEPTH,
                              rerank_batch=16, s6_first="s5a", chunk_k=CHUNK_K, s7_seed_k=None, s7_alpha=None, s7_graph=None,
                              neo4j_url=None)


def rank_arena(name, docs, chunks, policy, queries):
    from arena import cli, harness
    args = strategy_args(name)
    cfg = {"version": CORPUS_VERSION}
    sparse = cli.sparse_kind(args)
    splade = cli.make_splade_embedder(args, cfg, policy, sparse) if sparse else None
    embedder = cli.make_embedder(args, cfg, policy) if name in cli.BGE_STRATEGIES else None
    strat = cli.make_strategy(name, args, cfg, policy, embedder, splade=splade, docs=docs)
    t0 = time.perf_counter()
    strat.index(chunks)
    build = time.perf_counter() - t0
    chunk_to_doc = {c["chunk_id"]: c["doc_id"] for c in chunks}
    run, lat = harness.run_queries(strat, queries, chunk_to_doc, k_docs=K_DOCS, chunk_k=CHUNK_K)
    ordered = {qid: [[d, round(s, 6)] for d, s in sorted(r.items(), key=lambda x: -x[1])] for qid, r in run.items()}
    info = {"strategy": {"id": strat.id, "version": strat.version, **strat.settings()}, "index_build_seconds": round(build, 2),
            "query_latency_ms": harness.latency_summary(lat)}
    return ordered, info


def cmd_rank(a):
    queries = [json.loads(l) for l in Path(a.queries).read_text(encoding="utf-8").splitlines() if l.strip()]
    out = Path(a.out) / a.strategy
    if (out / "run.json").exists():
        raise SystemExit(f"이미 있다 — 덮어쓰지 않는다: {out / 'run.json'}")
    t0 = time.perf_counter()
    if a.strategy == "kw":
        items = lane_b_items(a.snapshot)
        run, info = rank_kw(items, read_json(a.profile), queries), {"strategy": {"id": "kw", "fields": "kw+aliases", "per_interest": True}}
        n_docs = len(items)
    else:
        items, docs, chunks, policy = build_corpus(a.snapshot)
        run, info = rank_arena(a.strategy, docs, chunks, policy, queries)
        info["chunks"] = len(chunks)
        n_docs = len(docs)
    info.update({"corpus_version": CORPUS_VERSION, "snapshot": str(a.snapshot), "docs": n_docs, "k_docs": K_DOCS,
                 "seconds": round(time.perf_counter() - t0, 1)})
    write_json(out / "run.json", run)
    write_json(out / "meta.json", info)
    empty = [q for q, r in run.items() if not r]
    print(f"{a.strategy}: 문서 {n_docs} · 질의 {len(run)} · 빈 순위 {len(empty)} → {out}")


def cmd_pool(a):
    """판정 풀 = 관심사마다 영어 질의 5전략 상위 30의 합집합 (한국어 질의는 풀에 넣지 않는다 — 등록 문서)."""
    runs = {s: read_json(Path(a.runs) / s / "run.json") for s in STRATEGIES}
    pool = {}
    for s, run in runs.items():
        for qid, rows in run.items():
            if not qid.endswith("@en"):
                continue
            interest = qid[: -len("@en")]
            pool.setdefault(interest, set()).update(d for d, _ in rows[:K_DOCS])
    pool = {k: sorted(v) for k, v in sorted(pool.items())}
    write_json(a.out, pool)
    sizes = {k: len(v) for k, v in pool.items()}
    print(f"풀 {sum(sizes.values())}쌍 · 관심사별 {sizes}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("queries")
    p.add_argument("--profile", required=True)
    p.add_argument("--out", required=True)
    p = sub.add_parser("rank")
    p.add_argument("--strategy", choices=STRATEGIES, required=True)
    p.add_argument("--snapshot", required=True)
    p.add_argument("--profile", required=True)
    p.add_argument("--queries", required=True)
    p.add_argument("--out", required=True)
    p = sub.add_parser("pool")
    p.add_argument("--runs", required=True)
    p.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    {"queries": cmd_queries, "rank": cmd_rank, "pool": cmd_pool}[a.cmd](a)


if __name__ == "__main__":
    main()
