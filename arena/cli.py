"""Arena CLI.

  python arena/cli.py stats --snapshots <radar-snapshots> --adr <adr> --repo decision-radar=<경로> ... [--out stats.json]
  python arena/cli.py run   --strategy s2|s3b|s4|s5a --queries q.jsonl --qrels qrels.json --gold-version toy@v0 <같은 코퍼스 인자>

코퍼스 폴더 위치는 전부 인자다 (Windows · M1 Mac 둘 다). 설정(버전·커밋·파일)은 arena/corpus/c-all@v1.json.
gold가 확정되기 전이라 `run`은 장난감 질의로만 돌린다 — gold 버전 이름에 `toy`를 넣는다.
"""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")      # Windows 파이프(cp949)에서도 한글 출력이 안 죽게
sys.path.insert(0, str(HERE.parent))

from arena import chunking, corpus, harness   # noqa: E402
from arena.tokens import HFTokenCounter        # noqa: E402


def _kv(items, what):
    out = {}
    for it in items or []:
        if "=" not in it:
            raise SystemExit(f"{what}는 <이름>=<경로> 형태여야 한다: {it}")
        k, v = it.split("=", 1)
        out[k] = v
    return out


def add_corpus_args(p):
    p.add_argument("--config", default=str(HERE / "corpus" / "c-all@v1.json"))
    p.add_argument("--policy", help="청크 정책 파일 (기본: 설정의 chunk_policy 이름으로 arena/corpus/에서)")
    p.add_argument("--snapshots", required=True, help="radar-snapshots 폴더 (그 아래 <주차>/items.jsonl)")
    p.add_argument("--adr", required=True, help="ADR repo 폴더")
    p.add_argument("--repo", action="append", help="C-CODE repo 이름=경로 (여러 번)")
    p.add_argument("--supplement", action="append", help="보충 jsonl 이름=경로 (여러 번)")


def build(args):
    cfg = corpus.load_config(args.config)
    policy_path = Path(args.policy) if args.policy else HERE / "corpus" / f"{cfg['chunk_policy']}.json"
    policy = chunking.load_policy(policy_path)
    if policy["version"] != cfg["chunk_policy"]:
        raise SystemExit(f"설정의 chunk_policy={cfg['chunk_policy']} ≠ 정책 파일 version={policy['version']}")
    docs = corpus.load_corpus(cfg, args.snapshots, args.adr, _kv(args.repo, "--repo"), _kv(args.supplement, "--supplement"))
    counter = HFTokenCounter(policy["tokenizers"])
    t0 = time.perf_counter()
    chunks = chunking.Chunker(policy, counter).chunk_all(docs)
    return cfg, policy, policy_path, docs, chunks, counter, time.perf_counter() - t0


def cmd_stats(args):
    cfg, policy, _, docs, chunks, counter, secs = build(args)
    stats = chunking.chunk_stats(docs, chunks, counter.names, policy["max_tokens"])
    print(f"코퍼스 {cfg['version']} · {policy['version']} · 상한 {policy['max_tokens']}토큰 · 토크나이저 {', '.join(counter.names)}")
    print(chunking.format_stats(stats, counter.names))
    print(f"(청킹 {secs:.1f}초)")
    if args.out:
        Path(args.out).write_text(json.dumps({"corpus_version": cfg["version"], "chunk_policy": policy, "stats": stats},
                                             ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


STRATEGY_CHOICES = ["s2", "s3b", "s4", "s5a"]
EMBEDDING_STRATEGIES = ("s3b", "s4", "s5a")


def make_strategy(name, args, cfg, policy, embedder):
    """전략 객체를 만든다. s5a는 S2와 S4를 같은 실행 안에서 새로 만든다 (저장된 결과를 재사용하지 않는다)."""
    from arena.strategies.s2_bm25 import S2BM25
    cv = cfg["version"]
    if name == "s2":
        return S2BM25(cv, es_url=args.es_url)
    if name == "s3b":
        from arena.strategies.s3b_bgem3_sparse import S3bSparse
        return S3bSparse(cv, embedder)

    def s4():
        from arena.strategies.s4_dense import S4Dense
        return S4Dense(cv, embedder, es_url=args.es_url, m=args.hnsw_m, ef_construction=args.ef_construction,
                       num_candidates=args.num_candidates)
    if name == "s4":
        return s4()
    from arena.strategies.s5a_rrf import S5aRRF
    return S5aRRF(S2BM25(cv, es_url=args.es_url), s4(), rrf_k=args.rrf_k, chunk_k=args.chunk_k)


def make_embedder(args, cfg, policy):
    from arena import embed
    encoder = embed.BGEM3Encoder(device=args.device, batch_size=args.encode_batch)
    return embed.Embedder(encoder, cfg["version"], policy["version"], use_cache=not args.no_cache)


def cmd_run(args):
    cfg, policy, policy_path, docs, chunks, counter, chunk_secs = build(args)
    queries = harness.load_queries(args.queries)
    qrels = harness.load_qrels(args.qrels)
    harness.check_gold(queries, qrels, [d["doc_id"] for d in docs])
    embedder = make_embedder(args, cfg, policy) if args.strategy in EMBEDDING_STRATEGIES else None
    strat = make_strategy(args.strategy, args, cfg, policy, embedder)
    key = harness.result_key(strat.id, strat.version, cfg["version"], args.gold_version, args.tier, args.seed)
    if (Path(args.results) / key).exists():            # 색인을 만들기 전에 먼저 막는다
        raise SystemExit(f"결과 폴더가 이미 있다 — 덮어쓰지 않는다: {Path(args.results) / key}")

    t0 = time.perf_counter()
    strat.index(chunks)                       # 임베딩 인코딩 시간도 여기 들어간다 — 따로 embedding.chunk_encode_seconds로 적는다
    build_secs = time.perf_counter() - t0

    chunk_to_doc = {c["chunk_id"]: c["doc_id"] for c in chunks}
    run, lat = harness.run_queries(strat, queries, chunk_to_doc, k_docs=args.k, chunk_k=args.chunk_k)
    metrics = harness.compute_metrics(run, qrels, queries, {d["doc_id"]: d["source_type"] for d in docs})
    system = {
        "index_build_seconds": round(build_secs, 3),
        "chunking_seconds": round(chunk_secs, 3),
        "index_size_bytes": strat.index_size_bytes(),
        "index_chunks": strat.n_indexed,
        "index_docs": len(docs),
        "chunk_k": args.chunk_k,
        "query_latency_ms": harness.latency_summary(lat),
        "es_took_ms": harness.latency_summary(strat.took_ms),
        "latency_note": "client = 요청~응답 벽시계(질의 순차 1회씩, 워밍업 없음, 임베딩 전략은 질의 인코딩 포함) · es_took = ES가 보고한 처리 시간",
        "strategy": {"id": strat.id, "version": strat.version, **strat.settings()},
    }
    if hasattr(strat, "size_breakdown"):
        system["index_size_by_component"] = strat.size_breakdown()
    extra = {
        "corpus_version": cfg["version"], "chunk_policy": policy["version"],
        "chunk_policy_sha256": hashlib.sha256(policy_path.read_bytes()).hexdigest()[:16],
        "elasticsearch": strat.server_version(),
        "gold_version": args.gold_version, "tier": args.tier, "seed": args.seed,
    }
    if embedder:
        info = embedder.info()
        system["embedding"] = info
        extra["device"] = info["device"]
        extra["embedding"] = {k: info[k] for k in ("model_id", "revision", "dtype", "max_length", "device", "libraries")}
    env = harness.env_info(extra)
    out = harness.save_results(args.results, key, run, metrics, system, env)
    print(f"결과: {out}")
    print(json.dumps({"overall": metrics["overall"], "latency_ms": system["query_latency_ms"]}, ensure_ascii=False))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("stats", help="코퍼스 로드 + 청크 통계")
    add_corpus_args(p)
    p.add_argument("--out", help="통계 JSON 저장 경로")
    p.set_defaults(fn=cmd_stats)
    p = sub.add_parser("run", help="전략 하나를 색인·질의·채점·저장")
    add_corpus_args(p)
    p.add_argument("--strategy", choices=STRATEGY_CHOICES, required=True)
    p.add_argument("--queries", required=True)
    p.add_argument("--qrels", required=True)
    p.add_argument("--gold-version", required=True)
    p.add_argument("--tier", default="T0")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--k", type=int, default=20, help="문서 순위 길이")
    p.add_argument("--chunk-k", type=int, default=100,
                   help="전략이 가져오는 상위 청크 수 (S5a는 구성요소 둘 다 같은 값). 기본 100")
    p.add_argument("--device", choices=["cuda", "mps", "cpu"], default=None, help="임베딩 기기 (기본: cuda > mps > cpu 자동)")
    p.add_argument("--encode-batch", type=int, default=16, help="임베딩 배치 크기 (기본 16)")
    p.add_argument("--no-cache", action="store_true", help="임베딩 디스크 캐시를 쓰지 않는다")
    p.add_argument("--hnsw-m", type=int, default=16, help="S4 HNSW m (자리표시 — dev에서 정한다)")
    p.add_argument("--ef-construction", type=int, default=100, help="S4 HNSW ef_construction (자리표시)")
    p.add_argument("--num-candidates", type=int, default=100, help="S4 kNN num_candidates (자리표시, chunk-k 이상이어야 한다)")
    p.add_argument("--rrf-k", type=int, default=60, help="S5a RRF k (자리표시 — dev에서 정한다)")
    p.add_argument("--results", default=str(HERE / "results"))
    p.add_argument("--es-url", default=None)
    p.set_defaults(fn=cmd_run)
    args = ap.parse_args(argv)
    if getattr(args, "es_url", 1) is None:
        from arena import es
        args.es_url = es.DEFAULT_URL
    args.fn(args)


if __name__ == "__main__":
    main()
