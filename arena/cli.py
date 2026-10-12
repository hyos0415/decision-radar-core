"""Arena CLI.

  python arena/cli.py stats --snapshots <radar-snapshots> --adr <adr> --repo decision-radar=<경로> ... [--out stats.json]
  python arena/cli.py run   --strategy s2 --queries q.jsonl --qrels qrels.json --gold-version toy@v0 <같은 코퍼스 인자>

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


def cmd_run(args):
    from arena.strategies.s2_bm25 import S2BM25
    strategies = {"s2": S2BM25}
    cfg, policy, policy_path, docs, chunks, counter, chunk_secs = build(args)
    queries = harness.load_queries(args.queries)
    qrels = harness.load_qrels(args.qrels)
    harness.check_gold(queries, qrels, [d["doc_id"] for d in docs])
    key = harness.result_key(strategies[args.strategy].id, strategies[args.strategy].version, cfg["version"],
                             args.gold_version, args.tier, args.seed)
    if (Path(args.results) / key).exists():            # 색인을 만들기 전에 먼저 막는다
        raise SystemExit(f"결과 폴더가 이미 있다 — 덮어쓰지 않는다: {Path(args.results) / key}")

    strat = strategies[args.strategy](cfg["version"], es_url=args.es_url)
    t0 = time.perf_counter()
    strat.index(chunks)
    build_secs = time.perf_counter() - t0

    chunk_to_doc = {c["chunk_id"]: c["doc_id"] for c in chunks}
    run, lat = harness.run_queries(strat, queries, chunk_to_doc, k_docs=args.k)
    metrics = harness.compute_metrics(run, qrels, queries, {d["doc_id"]: d["source_type"] for d in docs})
    system = {
        "index_build_seconds": round(build_secs, 3),
        "chunking_seconds": round(chunk_secs, 3),
        "index_size_bytes": strat.index_size_bytes(),
        "index_chunks": strat.n_indexed,
        "index_docs": len(docs),
        "query_latency_ms": harness.latency_summary(lat),
        "es_took_ms": harness.latency_summary(strat.took_ms),
        "latency_note": "client = 요청~응답 벽시계(질의 순차 1회씩, 워밍업 없음) · es_took = ES가 보고한 처리 시간",
        "strategy": {"id": strat.id, "version": strat.version, **strat.settings()},
    }
    env = harness.env_info({
        "corpus_version": cfg["version"], "chunk_policy": policy["version"],
        "chunk_policy_sha256": hashlib.sha256(policy_path.read_bytes()).hexdigest()[:16],
        "elasticsearch": strat.server_version(),
        "gold_version": args.gold_version, "tier": args.tier, "seed": args.seed,
    })
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
    p.add_argument("--strategy", choices=["s2"], required=True)
    p.add_argument("--queries", required=True)
    p.add_argument("--qrels", required=True)
    p.add_argument("--gold-version", required=True)
    p.add_argument("--tier", default="T0")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--k", type=int, default=20, help="문서 순위 길이")
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
