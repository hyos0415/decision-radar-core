"""Arena CLI.

  python arena/cli.py stats --snapshots <radar-snapshots> --adr <adr> --repo decision-radar=<경로> ... [--out stats.json]
  python arena/cli.py nodes-check --queries q.jsonl <같은 코퍼스 인자>   # 노드 어휘 nodes@v1 확인 (HYO-34)
  python arena/cli.py run   --strategy s1|s2|s3a|s3a.ko|s3b|s4|s4.int8|s4.bbq|s4.kure|s5a|s5b|s6 [--s5b-sparse s3a|s3a.ko] [--s6-first s5a|s4] --queries q.jsonl --qrels qrels.json --gold-version toy@v0 <같은 코퍼스 인자>

코퍼스 폴더 위치는 전부 인자다 (Windows · M1 Mac 둘 다). 설정(버전·커밋·파일)은 arena/corpus/c-all@v1.json.
gold가 확정되기 전이라 `run`은 장난감 질의로만 돌린다 — gold 버전 이름에 `toy`를 넣는다.
"""
import argparse
import hashlib
import json
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")      # Windows 파이프(cp949)에서도 한글 출력이 안 죽게
sys.path.insert(0, str(HERE.parent))

from arena import chunking, corpus, harness, jhs_metrics   # noqa: E402
from arena.tokens import HFTokenCounter                    # noqa: E402

NODES_DEFAULT = HERE.parent / "eval" / "arena" / "nodes@v1.json"


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


KURE_MODEL_ID = "nlpai-lab/KURE-v1"
KURE_REVISION = "8b418a58414668e75532ed045c22d9ca018ae2b2"          # 고정 — 결과가 받는 날에 따라 달라지지 않게

S5B_DEFAULT_SPARSE = "s3a.ko"                                                    # dev에서 정함 (2026-10-13, 이슈 #83) — s5b_rrf.DEFAULT_SPARSE와 같아야 한다
STRATEGY_CHOICES = ["s1", "s2", "s3a", "s3a.ko", "s3b", "s4", "s4.int8", "s4.bbq", "s4.kure", "s5a", "s5b", "s6"]
DIAG_STRATEGIES = ["s4.flat", "s4.int8flat", "s4.bbqflat"]                   # §6 진단 (격자 밖 — dev에서 한 번, 이슈 #83)
BGE_STRATEGIES = ("s3b", "s4", "s4.int8", "s4.bbq", "s5a", "s5b", "s6", "s4.flat", "s4.int8flat", "s4.bbqflat")          # BGE-M3 임베딩이 필요하다
KURE_STRATEGIES = ("s4.kure",)                                                   # KURE-v1 dense가 필요하다 (BGE-M3 아님)
SPLADE_STRATEGIES = ("s3a", "s5b")                                               # SPLADE 구성요소가 필요하다 (s3a = SPLADE-v3 · HF 토큰. s5b는 기본 KURE-Sparse, --s5b-sparse s3a면 SPLADE-v3)
KURE_SPARSE_STRATEGIES = ("s3a.ko",)                                             # KURE-Sparse-nano가 필요하다
EMBEDDING_STRATEGIES = tuple(dict.fromkeys(BGE_STRATEGIES + KURE_STRATEGIES + SPLADE_STRATEGIES + KURE_SPARSE_STRATEGIES))


def sparse_kind(args):
    """이 실행이 쓰는 SPLADE 구성요소 — "s3a"(SPLADE-v3) · "s3a.ko"(KURE-Sparse-nano) · 없으면 None."""
    if args.strategy == "s5b":
        return getattr(args, "s5b_sparse", None) or S5B_DEFAULT_SPARSE
    return args.strategy if args.strategy in ("s3a", "s3a.ko") else None


def make_strategy(name, args, cfg, policy, embedder, splade=None, docs=None):
    """전략 객체를 만든다. s5a는 S2와 S4를, s5b는 S3a와 S4를 같은 실행 안에서 새로 만든다 (저장된 결과를 재사용하지 않는다).
    s1은 코퍼스 문서(C-ADR frontmatter)가 필요하다 — docs."""
    from arena.strategies.s2_bm25 import S2BM25
    cv = cfg["version"]
    if name == "s1":
        from arena.strategies.s1_match import S1Match
        return S1Match(docs)
    if name == "s2":
        return S2BM25(cv, es_url=args.es_url)
    if name == "s3a":
        from arena.strategies.s3a_splade import S3aSplade
        return S3aSplade(cv, splade)
    if name == "s3a.ko":
        from arena.strategies.s3a_ko_kure_sparse import S3aKo
        return S3aKo(cv, splade)
    if name == "s3b":
        from arena.strategies.s3b_bgem3_sparse import S3bSparse
        return S3bSparse(cv, embedder)

    def s4(variant="s4"):
        from arena.strategies import s4_dense
        cls = {"s4": s4_dense.S4Dense, "s4.int8": s4_dense.S4Int8, "s4.bbq": s4_dense.S4BBQ, "s4.kure": s4_dense.S4Kure,
               "s4.flat": s4_dense.S4Flat, "s4.int8flat": s4_dense.S4Int8Flat, "s4.bbqflat": s4_dense.S4BBQFlat}[variant]
        return cls(cv, embedder, es_url=args.es_url, m=args.hnsw_m, ef_construction=args.ef_construction,
                   num_candidates=args.num_candidates)
    if name in ("s4", "s4.int8", "s4.bbq", "s4.kure", *DIAG_STRATEGIES):
        return s4(name)
    if name == "s5b":
        from arena.strategies.s3a_ko_kure_sparse import S3aKo
        from arena.strategies.s3a_splade import S3aSplade
        from arena.strategies.s5b_rrf import S5bRRF
        sparse = getattr(args, "s5b_sparse", None) or S5B_DEFAULT_SPARSE
        first = S3aKo(cv, splade) if sparse == "s3a.ko" else S3aSplade(cv, splade)
        return S5bRRF(first, s4(), rrf_k=getattr(args, "rrf_k", None), chunk_k=args.chunk_k, sparse=sparse)
    from arena.strategies.s5a_rrf import RRF_K, S5aRRF
    from arena.strategies.s6_rerank import CrossEncoder, DenseFirstStage, S6Rerank
    if name == "s5a":
        return S5aRRF(S2BM25(cv, es_url=args.es_url), s4(), rrf_k=args.rrf_k or RRF_K, chunk_k=args.chunk_k)
    first = (DenseFirstStage(s4(), chunk_k=args.chunk_k) if getattr(args, "s6_first", "s5a") == "s4"
             else S5aRRF(S2BM25(cv, es_url=args.es_url), s4(), rrf_k=args.rrf_k or RRF_K, chunk_k=args.chunk_k))
    reranker = CrossEncoder(device=args.device, batch_size=args.rerank_batch)
    return S6Rerank(first, reranker, depth=args.rerank_depth)


def make_embedder(args, cfg, policy, kind="bge"):
    """kind "bge" = BGE-M3(S3b · S4 · S5 · S6) · "kure" = KURE-v1 dense(S4.kure — revision 고정)."""
    from arena import embed
    if kind == "kure":
        encoder = embed.SentenceTransformerDenseEncoder(KURE_MODEL_ID, KURE_REVISION, device=args.device, batch_size=args.encode_batch)
    else:
        encoder = embed.BGEM3Encoder(device=args.device, batch_size=args.encode_batch)
    return embed.Embedder(encoder, cfg["version"], policy["version"], use_cache=not args.no_cache)


def make_splade_embedder(args, cfg, policy, kind="s3a"):
    """SPLADE 인코더 — kind "s3a" = SPLADE-v3(.env의 HF_TOKEN이 없으면 모델을 받기 전에 한국어 메시지로 끝낸다) ·
    "s3a.ko" = KURE-Sparse-nano(공개 모델, 토큰 없음 · revision 고정 · trust_remote_code)."""
    from arena.strategies import s3a_splade
    if kind == "s3a.ko":
        from arena.strategies import s3a_ko_kure_sparse as ko
        encoder = ko.KureSparseEncoder(device=args.device, batch_size=args.encode_batch)
        return ko.KureSparseEmbedder(encoder, cfg["version"], policy["version"], use_cache=not args.no_cache)
    try:
        encoder = s3a_splade.SpladeEncoder(device=args.device, batch_size=args.encode_batch)
    except s3a_splade.HFTokenError as e:
        raise SystemExit(str(e))
    return s3a_splade.SpladeEmbedder(encoder, cfg["version"], policy["version"], use_cache=not args.no_cache)


def build_cost(system, embedders, strat):
    """구축 비용 (retrieval-arena §5 변경 2026-10-13). 구축 시간은 캐시 없이 처음 인코딩한 실행만 보고한다 —
    캐시를 하나라도 쓴 실행은 인코딩 시간이 빠져 cold_build_seconds가 None이다. 보고 기준 기기는 M1."""
    enc = [{k: e.info().get(k) for k in ("model_id", "chunks", "cache_hits", "encoded", "chunk_encode_seconds", "device")}
           for e in embedders]
    cold = all(not x["cache_hits"] for x in enc)
    return {
        "cold_build": cold,
        "cold_build_seconds": system["index_build_seconds"] if cold else None,
        "note": "" if cold else "임베딩 캐시를 쓴 실행 — 인코딩 시간 모름. 구축 시간은 캐시 없이(--no-cache) 처음 돈 실행만 보고한다",
        "encoding": enc,
        "build_llm_cost_usd": getattr(strat, "build_llm_cost_usd", 0.0),
        "memory": harness.peak_memory(),
    }


def jhs_block(*a):
    """JHS 보조 지표 -> (블록, 오류 문자열 | None). 계산이 예외로 실패해도 헤드라인 결과는 저장되게
    오류를 블록에 적어 돌려준다 — 부르는 쪽이 저장한 뒤 실패 코드로 끝낸다 (§5 변경 2026-10-15)."""
    try:
        return jhs_metrics.compute(*a), None
    except Exception as e:                                   # noqa: BLE001 — 숨기지 않는다: 저장 뒤 SystemExit(1)
        err = f"{type(e).__name__}: {e}"
        return {"error": err, "traceback": traceback.format_exc()}, err


def cmd_nodes_check(args):
    """nodes@v1 확인 — 별칭이 출처 문서에 글자 그대로 있나 · gold 질의 노드가 어휘에 연결되나.
    test 질의는 연결 안 된 수만 센다(이름을 보고 별칭을 고치지 않는다 — §5 변경 2026-10-13)."""
    _, _, _, docs, _, _, _ = build(args)
    nodes = jhs_metrics.load_nodes(args.nodes)
    bad = jhs_metrics.check_nodes(nodes, {d["doc_id"]: d["title"] + "\n" + d["text"] for d in docs})
    print(f"{nodes.version}: 기술 {len(nodes.tech)} · 별칭 {len(nodes.alias)} · component {len(nodes.components)}")
    for b in bad:
        print("  출처 문제:", b)
    by_split = {}
    for q in harness.load_queries(args.queries):
        if q["type"] == "B":
            continue                                   # B의 nodes는 범주 — Seed Recall에서 뺀다
        ids, missing = nodes.query_node_ids(q)
        s = by_split.setdefault(q.get("split", "?"), {"queries": 0, "nodes": 0, "not_in_graph": 0, "names": []})
        s["queries"] += 1
        s["nodes"] += len(ids) + len(missing)
        s["not_in_graph"] += len(missing)
        if q.get("split") != "test":
            s["names"] += [f"{q['qid']}:{m}" for m in missing]
    for split, s in sorted(by_split.items()):
        names = "" if split == "test" else f" — {', '.join(s['names']) or '없음'}"
        print(f"  {split}: 질의 {s['queries']} · 노드 {s['nodes']} · 그래프에 없음 {s['not_in_graph']}{names}")
    if bad:
        raise SystemExit(1)


def cmd_run(args):
    cfg, policy, policy_path, docs, chunks, counter, chunk_secs = build(args)
    queries = harness.load_queries(args.queries)
    qrels = harness.load_qrels(args.qrels)
    harness.check_gold(queries, qrels, [d["doc_id"] for d in docs])
    nodes = jhs_metrics.load_nodes(args.nodes)                 # 색인을 만들기 전에 — 어휘 파일이 깨졌으면 먼저 멈춘다
    if args.strategy == "s1":                         # S1은 질의의 nodes를 쓴다 — 없는 질의가 섞이면 시작 전에 알린다
        no_nodes = [q["qid"] for q in queries if not q.get("nodes")]
        if no_nodes:
            raise SystemExit(f"s1은 nodes가 있는 질의(K 유형)만 돌린다 — nodes가 없는 질의: {no_nodes[:5]}")
    sparse = sparse_kind(args)
    splade = make_splade_embedder(args, cfg, policy, sparse) if sparse else None     # 토큰 확인을 먼저
    embedder = (make_embedder(args, cfg, policy, "kure") if args.strategy in KURE_STRATEGIES
                else make_embedder(args, cfg, policy) if args.strategy in BGE_STRATEGIES else None)
    strat = make_strategy(args.strategy, args, cfg, policy, embedder, splade=splade, docs=docs)
    key = harness.result_key(strat.id, strat.version, cfg["version"], args.gold_version, args.tier, args.seed)
    if (Path(args.results) / key).exists():            # 색인을 만들기 전에 먼저 막는다
        raise SystemExit(f"결과 폴더가 이미 있다 — 덮어쓰지 않는다: {Path(args.results) / key}")

    t0 = time.perf_counter()
    strat.index(chunks)                       # 임베딩 인코딩 시간도 여기 들어간다 — 따로 embedding.chunk_encode_seconds로 적는다
    build_secs = time.perf_counter() - t0

    chunk_to_doc = {c["chunk_id"]: c["doc_id"] for c in chunks}
    rep_chunks = {}
    run, lat = harness.run_queries(strat, queries, chunk_to_doc, k_docs=args.k, chunk_k=args.chunk_k, rep_chunks=rep_chunks)
    metrics = harness.compute_metrics(run, qrels, queries, {d["doc_id"]: d["source_type"] for d in docs})
    stack_text = {c["chunk_id"]: c["text"] for c in chunks if c["doc_id"].startswith("stack:")}
    metrics["jhs"], jhs_error = jhs_block(run, rep_chunks, qrels, queries, docs, stack_text, nodes)
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
    if hasattr(strat, "reranker"):
        rinfo = strat.reranker.info()
        system["reranker"] = {**rinfo, "rerank_ms_per_query": harness.latency_summary(strat.rerank_ms),
                              "pairs_per_query": strat.pairs_per_query, "truncated_pairs": 0,
                              "truncation_note": "채점 전 쌍마다 길이를 세어 넘으면 실패한다 — 이 실행은 잘림 0 (실패 없이 끝남)"}
    if splade:
        sinfo = splade.info()
        system["splade"] = sinfo
        extra["device"] = sinfo["device"]
        extra["splade"] = {k: sinfo[k] for k in ("model_id", "revision", "dtype", "max_length", "device", "libraries")}
        if sparse == "s3a.ko":
            extra["splade"].update({k: sinfo[k] for k in ("trust_remote_code", "prompts")})
    if embedder:
        info = embedder.info()
        system["embedding"] = info
        extra["device"] = info["device"]
        extra["embedding"] = {k: info[k] for k in ("model_id", "revision", "dtype", "max_length", "device", "libraries")}
        if "reranker" in system:
            extra["reranker"] = {k: system["reranker"][k] for k in ("model_id", "revision", "dtype", "max_length", "device", "libraries")}
    system["build_cost"] = build_cost(system, [x for x in (embedder, splade) if x], strat)
    env = harness.env_info(extra)
    out = harness.save_results(args.results, key, run, metrics, system, env, extra={"rep_chunks.json": rep_chunks})
    print(f"결과: {out}")
    print(json.dumps({"overall": metrics["overall"], "latency_ms": system["query_latency_ms"]}, ensure_ascii=False))
    if jhs_error:
        print(f"JHS 보조 지표 실패 — 헤드라인 결과는 저장했다 ({out / 'metrics.json'}의 jhs.error): {jhs_error}", file=sys.stderr)
        raise SystemExit(1)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("stats", help="코퍼스 로드 + 청크 통계")
    add_corpus_args(p)
    p.add_argument("--out", help="통계 JSON 저장 경로")
    p.set_defaults(fn=cmd_stats)
    p = sub.add_parser("run", help="전략 하나를 색인·질의·채점·저장")
    add_corpus_args(p)
    p.add_argument("--strategy", choices=STRATEGY_CHOICES + DIAG_STRATEGIES, required=True,
                   help="s4.flat · s4.int8flat · s4.bbqflat은 §6 진단 변형 (격자 밖)")
    p.add_argument("--queries", required=True)
    p.add_argument("--qrels", required=True)
    p.add_argument("--gold-version", required=True)
    p.add_argument("--tier", default="T0")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--k", type=int, default=20, help="문서 순위 길이")
    p.add_argument("--chunk-k", type=int, default=100,
                   help="전략이 가져오는 상위 청크 수 (S5a·S5b·S6는 구성요소 둘 다 같은 값). 기본 100")
    p.add_argument("--device", choices=["cuda", "mps", "cpu"], default=None, help="임베딩 기기 (기본: cuda > mps > cpu 자동)")
    p.add_argument("--encode-batch", type=int, default=16, help="임베딩 배치 크기 (기본 16)")
    p.add_argument("--no-cache", action="store_true", help="임베딩 디스크 캐시를 쓰지 않는다")
    p.add_argument("--s5b-sparse", choices=["s3a", "s3a.ko"], default=None,
                   help="S5b의 SPLADE 구성요소 — s3a.ko(KURE-Sparse-nano, 기본 · 결과 id S5b.ko — dev 2026-10-13) · s3a(SPLADE-v3, 결과 id S5b)")
    p.add_argument("--hnsw-m", type=int, default=16, help="S4(·변형) HNSW m (자리표시 — dev에서 정한다)")
    p.add_argument("--ef-construction", type=int, default=100, help="S4 HNSW ef_construction (자리표시)")
    p.add_argument("--num-candidates", type=int, default=200,
                   help="S4 kNN num_candidates (dev 2026-10-13에 200으로 정함 — chunk-k 이상이어야 한다)")
    p.add_argument("--rrf-k", type=int, default=None,
                   help="S5a·S5b·S6(1단계 S5a) RRF k. 생략하면 dev에서 정한 기본값 (2026-10-13) — S5a 60 · S5b(s3a) 10 · S5b(s3a.ko) 60")
    p.add_argument("--rerank-depth", type=int, default=50, help="S6 재정렬할 1단계(S5a) 문서 수 (기본 50)")
    p.add_argument("--rerank-batch", type=int, default=16, help="S6 reranker 배치 크기 (기본 16)")
    p.add_argument("--s6-first", choices=["s5a", "s4"], default="s5a",
                   help="S6 1단계 — s5a(BM25+Dense RRF, 기본 · 결과 id S6) · s4(Dense 하나, 결과 id S6.dense). dev 비교용 (이슈 #75)")
    p.add_argument("--results", default=str(HERE / "results"))
    p.add_argument("--es-url", default=None)
    p.add_argument("--nodes", default=str(NODES_DEFAULT), help="노드 어휘 (JHS 지표 — component 근거의 별칭)")
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser("nodes-check", help="노드 어휘 nodes@v1 확인 — 별칭 출처 · gold 질의 노드 연결")
    add_corpus_args(p)
    p.add_argument("--nodes", default=str(NODES_DEFAULT))
    p.add_argument("--queries", required=True)
    p.set_defaults(fn=cmd_nodes_check)
    args = ap.parse_args(argv)
    if getattr(args, "s5b_sparse", None) is not None and args.strategy != "s5b":
        ap.error(f"--s5b-sparse {args.s5b_sparse}는 --strategy s5b에서만 뜻이 있다 (지금 {args.strategy}) — 조용히 무시하지 않는다")
    if getattr(args, "s6_first", "s5a") != "s5a" and args.strategy != "s6":
        ap.error(f"--s6-first {args.s6_first}는 --strategy s6에서만 뜻이 있다 (지금 {args.strategy}) — 조용히 무시하지 않는다")
    if getattr(args, "es_url", 1) is None:
        from arena import es
        args.es_url = es.DEFAULT_URL
    args.fn(args)


if __name__ == "__main__":
    main()
