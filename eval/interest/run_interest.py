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

CORPUS_VERSION = "c-laneb@2026-W46"      # 임베딩 캐시 · ES 색인 이름에 들어간다 (Arena c-all@v1과 섞이지 않게) — 주마다 corpus_version()
K_DOCS = 30                              # 문서 순위 길이 = 판정 풀 깊이 = 지표 깊이 (등록 문서 「지표」)
CHUNK_K = 200                            # 문서 30 · 재정렬 1단계 100을 채우게 청크를 넉넉히 (S4 num_candidates 200과 같게)
RERANK_DEPTH = 100                       # Arena 등록값 50 — 50이면 S6의 상위 30 문서 집합이 1단계의 상위 50 안에서만 바뀐다
STRATEGIES = ("kw", "s2", "s4", "s3a.ko", "s6")
LANGS = ("en", "ko", "sent")             # sent = 관심사 description을 영어 문장으로 옮긴 것 (등록 문서 §9 변경, 시멘틱 서치 문장 질의)
EXTRA_POOL = (("s4", "sent"),)           # 풀에 더하는 (전략, 질의) — §9 변경


def corpus_version(snapshot):
    return f"c-laneb@{Path(snapshot).name}"

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

# 관심사 description(한국어 문장)을 영어로 그대로 옮긴 것 — 문장 질의 (§9 변경, 결과를 보기 전에 고정. 낱말을 더하거나 빼지 않는다)
DESC_EN = {
    "multimodal": "Models that understand images and documents together, including OCR, document understanding and table extraction.",
    "hybrid-sparse-retrieval": "Methods that combine keyword (BM25) and vector search, learned sparse representations, result fusion such as RRF, and query expansion or rewriting.",
    "embeddings-rerankers": "Embedding and reranking models for document retrieval: multilingual and Korean, small models, long documents, late interaction.",
    "graph-rag": "Methods that build a knowledge graph and use it for retrieval: entity and relation extraction, graph traversal combined with vectors, ontology validation.",
    "vector-index-efficiency": "Techniques that reduce storage and memory such as vector quantization and compression, disk-based ANN, and recomputation-based indexes.",
    "agent-runtime": "Agent orchestration, human approval (interrupt and resume), state persistence (checkpoint) and durable execution, multi-agent patterns.",
    "llm-eval": "Regression eval design, reliability of LLM-as-judge, pass^k and statistical tests, agent trajectory evaluation, eval harnesses.",
    "small-local-models": "Serving 3-8B models locally: quantization such as int4, serving engines like vLLM and llama.cpp, inference speed and memory.",
    "answer-verification": "Judging whether a generated answer matches its evidence: claim decomposition, grounding checks, hallucination detection, small models built for verification.",
    "guardrails-pii": "Prompt injection defense, detecting and masking personal information (PII), including NER-based methods, and output filters.",
    "speech": "Models that turn speech into text (STT, ASR) and models that turn text into speech (TTS): multilingual and Korean, real-time and streaming, local execution.",
    "preference-optimization": "Training methods that align language models with human or AI preference data: RLHF, reward-model-free methods such as DPO and ORPO, their variants and training tools.",
    "code-impact": "Techniques that read repository code to find where an external change has an impact: code search, code graphs, dependency analysis.",
    "model-routing-cost": "Routers that send easy requests to cheaper models, caching, and token reduction.",
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
    if lang == "sent":
        return DESC_EN[it["id"]]
    s = f"{TOPIC_EN[it['id']]}: {', '.join(it.get('aliases') or [])}."
    if it.get("examples"):
        s += f" Examples: {', '.join(it['examples'])}."
    return s


def cmd_queries(a):
    prof = read_json(a.profile)
    rows = []
    for it in active_interests(prof):
        if it["id"] not in TOPIC_EN or it["id"] not in DESC_EN:
            raise SystemExit(f"TOPIC_EN · DESC_EN에 없는 관심사: {it['id']}")
        for lang in (a.langs or LANGS):
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
    chunker = chunking.Chunker(policy, HFTokenCounter(policy["tokenizers"]))
    try:
        chunks = chunker.chunk_all(docs)
    except ValueError:
        # 청크 정책이 거부하는 문서(헤딩이 상한을 거의 다 쓰는 글 — W43 1건)는 모든 전략에서 뺀다 (§9 변경). kw도 같은 문서를 뺀다
        keep, chunks = [], []
        for d in docs:
            try:
                cs = chunker.chunk_all([d])
            except ValueError:
                DROPPED.add(d["doc_id"])
                continue
            keep.append(d)
            chunks += cs
        docs = keep
        items = [it for it in items if it["id"] not in DROPPED]
    return items, docs, chunks, policy


DROPPED = set()      # build_corpus가 뺀 문서 id (meta.json에 적는다)


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


def rank_arena(name, docs, chunks, policy, queries, cv=CORPUS_VERSION):
    from arena import cli, harness
    args = strategy_args(name)
    cfg = {"version": cv}
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
        items = build_corpus(a.snapshot)[0]          # 청크 정책이 거부한 문서를 다른 전략과 똑같이 뺀다
        run, info = rank_kw(items, read_json(a.profile), queries), {"strategy": {"id": "kw", "fields": "kw+aliases", "per_interest": True}}
        n_docs = len(items)
    else:
        items, docs, chunks, policy = build_corpus(a.snapshot)
        run, info = rank_arena(a.strategy, docs, chunks, policy, queries, corpus_version(a.snapshot))
        info["chunks"] = len(chunks)
        n_docs = len(docs)
    info.update({"corpus_version": corpus_version(a.snapshot), "snapshot": str(a.snapshot), "docs": n_docs, "k_docs": K_DOCS,
                 "dropped_by_chunk_policy": sorted(DROPPED),
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


# ---- 판정 (등록 문서 §5) -------------------------------------------------------------------------------------------

JUDGE_SEED = 20261112
BODY_CHARS = 800
BATCH_MAX = 100
STRIP_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")      # 구독으로만 (pipeline.slots와 같다)
JUDGE_SYSTEM = """너는 기술 소식 분류 판정자다. 관심사 하나의 정의와 이번 주 글 목록을 받는다. 글마다 그 관심사와의 관련 등급을 매긴다.

등급
- 2: 이 관심사에 속하는 **특정한 새** 도구·모델·라이브러리·기법·논문·벤치마크를 다룬다(출시, 공개, 큰 업데이트). 실험 후보가 될 수 있는 것.
- 1: 이 관심사가 글의 주제지만 특정한 새것은 아니다(토론, 질문, 경험담, 이미 있는 것의 튜토리얼, 일반 소식).
- 0: 이 관심사가 주제가 아니다(지나가는 언급, 정의의 `제외`에 해당, 무관).

규칙
- 글의 제목과 본문 앞부분만 보고 판단한다. 링크를 따라가지 않는다.
- 관심사 낱말이 나와도 글의 주제가 아니면 0이다. 낱말이 안 나와도 주제가 맞으면 1이나 2다.
- 목록의 모든 번호에 정확히 한 번씩 답한다. why는 한국어 한 줄(40자 안쪽)."""
JUDGE_SCHEMA = {"type": "object", "required": ["labels"], "properties": {"labels": {"type": "array", "items": {
    "type": "object", "required": ["n", "grade", "why"],
    "properties": {"n": {"type": "integer"}, "grade": {"type": "integer", "enum": [0, 1, 2]}, "why": {"type": "string"}}}}}}


def interest_card(it):
    lines = [f"관심사: {it['topic']} ({it['id']})", f"설명: {it.get('description') or ''}",
             f"별칭: {', '.join(it.get('aliases') or [])}", f"예시: {', '.join(it.get('examples') or []) or '없음'}",
             f"제외: {', '.join(it.get('not') or []) or '없음'}"]
    return "\n".join(lines)


def doc_brief(doc, source):
    body = " ".join((doc.get("text") or "").split())[:BODY_CHARS]
    return f"제목: {doc['title']}\n출처: {source}\n본문 앞부분: {body}"


def judge_batches(pool, profile):
    """관심사마다 풀 문서를 시드로 섞어 BATCH_MAX개씩 나눈다 → [(interest, b, [doc_id...])]."""
    import random
    out = []
    for interest, ids in pool.items():
        ids = list(ids)
        random.Random(f"{JUDGE_SEED}:{interest}").shuffle(ids)
        n = -(-len(ids) // BATCH_MAX)
        size = -(-len(ids) // n) if n else 0
        for b in range(n):
            out.append((interest, b, ids[b * size:(b + 1) * size]))
    return out


def call_claude(user, model, timeout=900):
    import os
    import shutil
    import subprocess
    import tempfile
    b = os.environ.get("RADAR_CLAUDE_BIN") or shutil.which("claude") or "claude"
    cmd = [*([sys.executable, b] if b.endswith(".py") else [b]), "-p", "--output-format", "json", "--system-prompt", JUDGE_SYSTEM,
           "--json-schema", json.dumps(JUDGE_SCHEMA, ensure_ascii=False), "--tools", "", "--setting-sources", "",
           "--no-session-persistence", "--model", model, "--effort", "medium", "--permission-mode", "dontAsk"]
    env = {k: v for k, v in os.environ.items() if k not in STRIP_ENV}
    env["PYTHONIOENCODING"] = "utf-8"
    with tempfile.TemporaryDirectory(prefix="radar-interest-") as run:
        p = subprocess.run(cmd, input=user.encode("utf-8"), capture_output=True, cwd=run, env=env, timeout=timeout)
    try:
        res = json.loads(p.stdout.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise RuntimeError(f"claude 결과가 JSON이 아니다 (exit {p.returncode}) {p.stderr.decode('utf-8', 'replace')[-300:]}")
    if res.get("is_error") or res.get("structured_output") is None:
        raise RuntimeError(f"claude 실패: {res.get('subtype')} {str(res.get('result'))[:300]}")
    return res["structured_output"], res


def check_labels(so, n):
    """번호 1..n이 정확히 한 번씩 · 등급 0~2 — 아니면 문제 목록 (규율 1: 스크립트가 검사)."""
    got, problems = {}, []
    for x in (so or {}).get("labels") or []:
        k = x.get("n")
        if not isinstance(k, int) or not 1 <= k <= n:
            problems.append(f"모르는 번호 {k}")
        elif k in got:
            problems.append(f"겹친 번호 {k}")
        elif x.get("grade") not in (0, 1, 2):
            problems.append(f"{k}: 등급 {x.get('grade')}")
        else:
            got[k] = x
    missing = [k for k in range(1, n + 1) if k not in got]
    if missing:
        problems.append(f"빠진 번호 {missing[:10]}")
    return got, problems


def split_pool(pool_raw):
    """풀 v1({관심사: [문서]}) · v2({관심사: [[주, 문서]]}) → ({관심사: [문서]}, {문서: 주})."""
    pool, week_of = {}, {}
    for i, xs in pool_raw.items():
        ids = []
        for x in xs:
            if isinstance(x, list):
                w, d = x
                week_of.setdefault(d, w)
            else:
                d = x
            ids.append(d)
        pool[i] = ids
    return pool, week_of


def cmd_judge(a):
    pool, week_of = split_pool(read_json(a.pool))
    prof = read_json(a.profile)
    interests = {it["id"]: it for it in active_interests(prof)}
    batches = judge_batches(pool, prof)
    print(f"판정 {sum(len(x[2]) for x in batches)}쌍 · 세션 {len(batches)} (관심사 {len(pool)}, 묶음 최대 {BATCH_MAX})")
    if a.dry_run:
        return
    from arena import corpus
    if week_of:
        if not a.snapshots_root:
            raise SystemExit("여러 주 풀이다 — --snapshots-root를 준다")
        snaps = {w: Path(a.snapshots_root) / w for w in sorted(set(week_of.values()))}
        by_week = {w: {it["id"]: it for it in lane_b_items(p)} for w, p in snaps.items()}
        def doc_of(d):
            w = week_of[d]
            return corpus._item_to_doc(by_week[w][d], snaps[w]), by_week[w][d].get("source")
    else:
        items = {it["id"]: it for it in lane_b_items(a.snapshot)}
        def doc_of(d):
            return corpus._item_to_doc(items[d], Path(a.snapshot)), items[d].get("source")
    out = Path(a.out)
    (out / "raw").mkdir(parents=True, exist_ok=True)
    for interest, b, ids in batches:
        raw_path = out / "raw" / f"{interest}-{b}.json"
        if raw_path.exists():
            continue
        listing = "\n\n".join(f"[{i}]\n" + doc_brief(*doc_of(d)) for i, d in enumerate(ids, 1))
        user = f"{interest_card(interests[interest])}\n\n글 {len(ids)}개:\n\n{listing}"
        for attempt in (1, 2):
            t0 = time.perf_counter()
            so, res = call_claude(user, a.model)
            got, problems = check_labels(so, len(ids))
            if not problems:
                break
            print(f"  {interest}-{b} 시도 {attempt} 문제: {problems[:3]}")
        if problems:
            raise SystemExit(f"{interest}-{b}: 두 번 다 답이 어긋났다 — {problems[:5]}")
        labels = {ids[k - 1]: {"grade": x["grade"], "why": x["why"]} for k, x in sorted(got.items())}
        write_json(raw_path, {"interest": interest, "batch": b, "ids": ids, "labels": labels, "model": a.model,
                              "seconds": round(time.perf_counter() - t0, 1), "usage": res.get("usage"),
                              "cost_usd_equiv": res.get("total_cost_usd")})
        print(f"  {interest}-{b}: {len(ids)}건 · 2={sum(1 for v in labels.values() if v['grade'] == 2)} "
              f"1={sum(1 for v in labels.values() if v['grade'] == 1)} · {round(time.perf_counter() - t0)}초")
    merged = {}
    for p in sorted((out / "raw").glob("*.json")):
        r = read_json(p)
        merged.setdefault(r["interest"], {}).update(r["labels"])
    write_json(out / "labels.json", merged)
    print(f"판정 합본 → {out / 'labels.json'}")


ZERO_SAMPLE = 30


def review_set(pool, runs, labels):
    """사람 확인 대상 (등록 문서 §5) — 한 전략만 상위 30(영어)에 올린 문서 중 판정 ≥ 1 전부 + 판정 0 무작위 ZERO_SAMPLE쌍(시드).
    → [{"interest", "doc_id", "kind", "by"}] (관심사 → 문서 id 순)"""
    import random
    found = {}
    for s, run in runs.items():
        for interest in pool:
            for d, _ in (run.get(f"{interest}@en") or [])[:K_DOCS]:
                found.setdefault((interest, d), set()).add(s)
    out, zeros = [], []
    for interest, docs in sorted(labels.items()):
        for d, v in sorted(docs.items()):
            by = sorted(found.get((interest, d), ()))
            if v["grade"] >= 1 and len(by) == 1:
                out.append({"interest": interest, "doc_id": d, "kind": "unique", "by": by})
            elif v["grade"] == 0:
                zeros.append({"interest": interest, "doc_id": d, "kind": "zero-sample", "by": by})
    out += sorted(random.Random(JUDGE_SEED).sample(zeros, min(ZERO_SAMPLE, len(zeros))), key=lambda x: (x["interest"], x["doc_id"]))
    return out


def cmd_review(a):
    pool, labels = read_json(a.pool), read_json(a.labels)
    runs = {s: read_json(Path(a.runs) / s / "run.json") for s in STRATEGIES}
    rows = review_set(pool, runs, labels)
    from arena import corpus
    items = {it["id"]: it for it in lane_b_items(a.snapshot)}
    topics = {it["id"]: it["topic"] for it in active_interests(read_json(a.profile))}
    for n, r in enumerate(rows, 1):
        it = items[r["doc_id"]]
        doc = corpus._item_to_doc(it, Path(a.snapshot))
        lab = labels[r["interest"]][r["doc_id"]]
        r.update({"n": n, "topic": topics[r["interest"]], "title": doc["title"], "url": it.get("url"), "source": it.get("source"),
                  "grade": lab["grade"], "why": lab["why"], "snippet": " ".join((doc.get("text") or "").split())[:BODY_CHARS]})
        r.pop("by")             # 사람에게 전략을 가린다 (어느 전략이 찾았는지는 runs에서 다시 셀 수 있다)
    write_json(Path(a.out), rows)
    kinds = {}
    for r in rows:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    print(f"확인 대상 {len(rows)}건 {kinds} → {a.out}")


# ---- 채점 (등록 문서 §6) -------------------------------------------------------------------------------------------

def per_query_metrics(run_rows, grades, min_grade=1):
    """한 관심사 · 한 전략 → recall@30(등급 ≥ min_grade) · nDCG@10(등급 그대로, 선형 이득) · judged@30. 정답이 없으면 recall None."""
    import math
    top = [d for d, _ in run_rows[:K_DOCS]]
    rel = {d for d, g in grades.items() if g >= min_grade}
    recall = (len(rel & set(top)) / len(rel)) if rel else None
    dcg = sum(grades.get(d, 0) / math.log2(i + 2) for i, d in enumerate(top[:10]))
    ideal = sorted(grades.values(), reverse=True)[:10]
    idcg = sum(g / math.log2(i + 2) for i, g in enumerate(ideal))
    judged = (sum(1 for d in top if d in grades) / len(top)) if top else None
    return {"recall": recall, "ndcg10": (dcg / idcg) if idcg else None, "judged": judged, "returned": len(top)}


def bootstrap_ci(diffs, n=10_000, seed=JUDGE_SEED):
    import random
    if not diffs:
        return None
    rng = random.Random(seed)
    means = sorted(sum(rng.choice(diffs) for _ in diffs) / len(diffs) for _ in range(n))
    return [round(means[int(0.025 * n)], 3), round(means[int(0.975 * n) - 1], 3)]


def cmd_score(a):
    gold = {i: {d: v["grade"] for d, v in docs.items()} for i, docs in read_json(a.gold).items()}
    rows, table = {}, {}
    for s in STRATEGIES:
        run = read_json(Path(a.runs) / s / "run.json")
        for lang in LANGS:
            for interest, grades in gold.items():
                for mg in (1, 2):
                    rows[(s, lang, interest, mg)] = per_query_metrics(run.get(f"{interest}@{lang}") or [], grades, mg)
    def macro(s, lang, key, mg=1):
        vals = [rows[(s, lang, i, mg)][key] for i in gold if rows[(s, lang, i, mg)][key] is not None]
        return (round(sum(vals) / len(vals), 3) if vals else None), len(vals)
    for s in STRATEGIES:
        for lang in LANGS:
            r1, n1 = macro(s, lang, "recall")
            r2, n2 = macro(s, lang, "recall", 2)
            nd, _ = macro(s, lang, "ndcg10")
            jd, _ = macro(s, lang, "judged")
            table[f"{s}@{lang}"] = {"recall30": r1, "n_interests": n1, "recall30_grade2": r2, "n_interests_grade2": n2,
                                    "ndcg10": nd, "judged30": jd}
    ref = "s4"
    ci = {}
    for s in STRATEGIES:
        if s == ref:
            continue
        d = [rows[(s, "en", i, 1)]["recall"] - rows[(ref, "en", i, 1)]["recall"] for i in gold if rows[(ref, "en", i, 1)]["recall"] is not None]
        ci[f"{s}-{ref}"] = {"mean": round(sum(d) / len(d), 3) if d else None, "ci95": bootstrap_ci(d)}
    order = sorted(STRATEGIES, key=lambda s: -(table[f"{s}@en"]["recall30"] or 0))
    out = {"table": table, "diff_vs_s4_recall30_en": ci, "order_recall30_en": order,
           "per_interest": {f"{s}@{lang}@{i}@{mg}": v for (s, lang, i, mg), v in rows.items()}}
    write_json(a.out, out)
    print(json.dumps({"order": order, "table": table, "diff_vs_s4": ci}, ensure_ascii=False, indent=1))


# ---- 여러 주 (등록 문서 §9 변경) -----------------------------------------------------------------------------------

def pool2(runs_by_week, labeled=None):
    """runs_by_week = {주: {전략: run}} → {관심사: [[주, 문서], ...]} — 영어 질의 5전략 상위 30 + EXTRA_POOL 상위 30.
    같은 관심사에서 문서가 여러 주에 나오면 처음 주 하나로(같은 글이면 판정도 같다). labeled({관심사: {문서: …}})에 있는 문서는 뺀다."""
    labeled = labeled or {}
    out = {}
    for week in sorted(runs_by_week):
        runs = runs_by_week[week]
        picks = [(s, "en") for s in STRATEGIES] + list(EXTRA_POOL)
        for s, lang in picks:
            for qid, rows in (runs.get(s) or {}).items():
                interest, _, ql = qid.rpartition("@")
                if ql != lang:
                    continue
                seen = out.setdefault(interest, {})
                for d, _ in rows[:K_DOCS]:
                    if d not in seen and d not in labeled.get(interest, {}):
                        seen[d] = week
    return {i: sorted([[w, d] for d, w in v.items()], key=lambda x: (x[1], x[0])) for i, v in sorted(out.items())}


def load_runs(root, weeks):
    return {w: {s: read_json(Path(root) / w / s / "run.json") for s in STRATEGIES if (Path(root) / w / s / "run.json").exists()}
            for w in weeks}


def cmd_pool2(a):
    pool = pool2(load_runs(a.runs_root, a.weeks), read_json(a.labeled) if a.labeled else None)
    write_json(a.out, pool)
    sizes = {k: len(v) for k, v in pool.items()}
    print(f"새로 판정할 쌍 {sum(sizes.values())} · 관심사별 {sizes}")


def cmd_merge_gold(a):
    base, new = read_json(a.base), read_json(a.new)
    out = {i: dict(v) for i, v in base.items()}
    clash = 0
    for i, docs in new.items():
        for d, v in docs.items():
            if d in out.get(i, {}):
                clash += 1          # 얼린 판정은 그대로 둔다
                continue
            out.setdefault(i, {})[d] = v
    write_json(a.out, out)
    print(f"합친 정답 {sum(len(v) for v in out.values())}쌍 (새 {sum(len(v) for v in new.values()) - clash} · 겹쳐 버린 새 판정 {clash})")


def cluster_ci(per_interest_diffs, n=10_000, seed=JUDGE_SEED):
    """관심사 묶음 bootstrap — 관심사를 다시 뽑고 그 관심사의 주들을 함께 가져간다 (같은 관심사의 주들은 독립이 아니다)."""
    import random
    keys = [k for k, v in per_interest_diffs.items() if v]
    if not keys:
        return None
    rng = random.Random(seed)
    means = []
    for _ in range(n):
        xs = [x for k in (rng.choice(keys) for _ in keys) for x in per_interest_diffs[k]]
        means.append(sum(xs) / len(xs))
    means.sort()
    return [round(means[int(0.025 * n)], 3), round(means[int(0.975 * n) - 1], 3)]


def cmd_score2(a):
    from pipeline import lanes
    from pipeline.profile_filter import read_items
    gold = {i: {d: v["grade"] for d, v in docs.items()} for i, docs in read_json(a.gold).items()}
    runs = load_runs(a.runs_root, a.weeks)
    week_ids = {}
    for w in a.weeks:
        _a, lane_b, _s = lanes.split_lanes(read_items(Path(a.snapshots_root) / w))
        week_ids[w] = {it["id"] for it in lane_b}
    cols = [(s, "en") for s in STRATEGIES] + [(s, "sent") for s in STRATEGIES] + [(s, "ko") for s in STRATEGIES]
    per = {}          # (전략, 질의) -> {관심사: [recall per week]}
    rows = {}
    for s, lang in cols:
        for w in a.weeks:
            run = runs[w].get(s) or {}
            for i, grades in gold.items():
                g = {d: v for d, v in grades.items() if d in week_ids[w]}
                for mg in (1, 2):
                    m = per_query_metrics(run.get(f"{i}@{lang}") or [], g, mg)
                    rows[(s, lang, w, i, mg)] = m
                    if mg == 1 and m["recall"] is not None:
                        per.setdefault((s, lang), {}).setdefault(i, []).append(m["recall"])
    def macro(s, lang, key, mg=1, weeks=None):
        vals = [rows[(s, lang, w, i, mg)][key] for w in (weeks or a.weeks) for i in gold
                if rows[(s, lang, w, i, mg)][key] is not None]
        return (round(sum(vals) / len(vals), 3) if vals else None), len(vals)
    table = {}
    for s, lang in cols:
        r1, n1 = macro(s, lang, "recall")
        r2, _ = macro(s, lang, "recall", 2)
        nd, _ = macro(s, lang, "ndcg10")
        jd, _ = macro(s, lang, "judged")
        table[f"{s}@{lang}"] = {"recall30": r1, "n_queries": n1, "recall30_grade2": r2, "ndcg10": nd, "judged30": jd,
                                "by_week": {w: macro(s, lang, "recall", 1, [w])[0] for w in a.weeks}}
    def diff(x, y):
        d = {}
        for i in gold:
            for w in a.weeks:
                rx, ry = rows[(*x, w, i, 1)]["recall"], rows[(*y, w, i, 1)]["recall"]
                if rx is not None and ry is not None:
                    d.setdefault(i, []).append(rx - ry)
        flat = [v for vs in d.values() for v in vs]
        wins = sum(1 for v in flat if v > 0)
        losses = sum(1 for v in flat if v < 0)
        return {"mean": round(sum(flat) / len(flat), 3) if flat else None, "ci95_cluster": cluster_ci(d),
                "wins": wins, "losses": losses, "ties": len(flat) - wins - losses}
    comps = {"s6-s2": diff(("s6", "en"), ("s2", "en")), "s2-s4": diff(("s2", "en"), ("s4", "en")),
             "s3a.ko-s2": diff(("s3a.ko", "en"), ("s2", "en")), "s2-kw": diff(("s2", "en"), ("kw", "en")),
             "s4sent-s4": diff(("s4", "sent"), ("s4", "en")), "s4sent-s2": diff(("s4", "sent"), ("s2", "en"))}
    order = sorted(STRATEGIES, key=lambda s: -(table[f"{s}@en"]["recall30"] or 0))
    write_json(a.out, {"weeks": a.weeks, "table": table, "comparisons": comps, "order_recall30_en": order,
                       "per_query": {"|".join(map(str, k)): v for k, v in rows.items()}})
    print(json.dumps({"order": order, "comparisons": comps,
                      "table": {k: {x: v[x] for x in ("recall30", "recall30_grade2", "ndcg10", "judged30", "by_week")} for k, v in table.items()}},
                     ensure_ascii=False, indent=1))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("queries")
    p.add_argument("--profile", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--langs", nargs="+", choices=LANGS, help="기본: 전부")
    p = sub.add_parser("pool2", help="여러 주 풀 (§9 변경) — 관심사마다 [주, 문서], 이미 판정한 문서는 뺀다")
    p.add_argument("--runs-root", required=True, help="<root>/<주>/<전략>/run.json")
    p.add_argument("--weeks", nargs="+", required=True)
    p.add_argument("--labeled", help="이미 얼린 정답 (빼고 판정한다)")
    p.add_argument("--out", required=True)
    p = sub.add_parser("merge-gold", help="얼린 정답 + 새 판정 → 합친 정답 (기존 판정은 그대로)")
    p.add_argument("--base", required=True)
    p.add_argument("--new", required=True)
    p.add_argument("--out", required=True)
    p = sub.add_parser("score2", help="여러 주 채점 — (주 × 관심사) 질의 평균 · 관심사 묶음 bootstrap")
    p.add_argument("--runs-root", required=True)
    p.add_argument("--snapshots-root", required=True)
    p.add_argument("--weeks", nargs="+", required=True)
    p.add_argument("--gold", required=True)
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
    p = sub.add_parser("judge", help="풀 판정 — 관심사마다 claude -p (구독, API 키 제거)")
    p.add_argument("--pool", required=True)
    p.add_argument("--snapshot", help="한 주 풀(v1)일 때")
    p.add_argument("--snapshots-root", help="여러 주 풀(v2)일 때 — <root>/<주>")
    p.add_argument("--profile", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--model", default="claude-sonnet-5-5")
    p.add_argument("--dry-run", action="store_true", help="세션 수만 센다")
    p = sub.add_parser("review", help="사람 확인 대상 — 한 전략만 찾은 정답 전부 + 0 무작위 30 (전략은 가린다)")
    p.add_argument("--pool", required=True)
    p.add_argument("--runs", required=True)
    p.add_argument("--labels", required=True)
    p.add_argument("--snapshot", required=True)
    p.add_argument("--profile", required=True)
    p.add_argument("--out", required=True)
    p = sub.add_parser("score", help="얼린 정답으로 채점 — recall@30 · nDCG@10 · judged@30 · s4 대비 차이 CI")
    p.add_argument("--runs", required=True)
    p.add_argument("--gold", required=True)
    p.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    {"queries": cmd_queries, "rank": cmd_rank, "pool": cmd_pool, "judge": cmd_judge, "review": cmd_review, "score": cmd_score,
     "pool2": cmd_pool2, "merge-gold": cmd_merge_gold, "score2": cmd_score2}[a.cmd](a)


if __name__ == "__main__":
    main()
